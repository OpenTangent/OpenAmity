import os
import sys
import time
from email.message import EmailMessage
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.email_archive import EmailArchive
from core.email_monitor import EmailMonitor
from core.events import Signal
from core.orchestrator import AmityOrchestrator
from core.pulse_engine import PulseEngine
from core.settings_manager import SettingsManager
from tools.email_tool import EmailTool


@pytest.fixture
def email_pulse_env(tmp_path, monkeypatch):
    agent_id = "test-email-routing-agent"
    agent_dir = tmp_path / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(agent_dir))

    SettingsManager.remove_instance(agent_id=agent_id)

    orch = MagicMock()
    orch.agent_id = agent_id
    orch.is_paused = False
    orch.is_busy = False
    orch.on_paused_state_changed = Signal()
    orch.has_queued_pulse = MagicMock(return_value=False)

    sm = SettingsManager(agent_id=agent_id)
    sm.set("core.tools.email", True)
    sm.set("core.auto-pulse.email.pulse-all-emails", False)
    sm.set("core.auto-pulse.email.whitelist", [])
    sm.set("core.auto-pulse.email.check-interval-seconds", 60)
    sm.set("core.auto-pulse.email.buffer-seconds", 0.05)
    sm.save()
    orch.settings_manager = sm

    engine = PulseEngine(orch)
    engine.settings_manager = sm
    yield orch, sm, engine, agent_dir
    engine.stop()
    SettingsManager.remove_instance(agent_id=agent_id)


def _make_raw_headers(from_addr: str, message_id: str = "<msg-1@example.com>", date: str = "Sun, 22 Nov 2026 12:00:00 +0000") -> bytes:
    return (
        f"From: {from_addr}\r\n"
        f"Message-ID: {message_id}\r\n"
        f"Date: {date}\r\n\r\n"
    ).encode("utf-8")


def _make_email_bytes(from_addr: str = "alice@example.com", subject: str = "Hello", message_id: str = "<m1@example.com>") -> bytes:
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = "agent@example.com"
    msg["Subject"] = subject
    msg["Date"] = "Sun, 22 Nov 2026 12:00:00 +0000"
    msg["Message-ID"] = message_id
    msg.set_content("Test body")
    return msg.as_bytes()


# --- PulseEngine Email Routing & Debouncing ---

def test_email_routing_default_ignores_all(email_pulse_env):
    _, _, engine, _ = email_pulse_env
    engine.handle_email_message("stranger@example.com", "Stranger", uid="10", message_id="<10@example.com>")
    assert engine.email_timer is None


def test_email_routing_pulse_all_emails(email_pulse_env):
    _, sm, engine, _ = email_pulse_env
    sm.set("core.auto-pulse.email.pulse-all-emails", True)
    sm.save()

    engine.handle_email_message("anyone@random.org", "Anyone", uid="11", message_id="<11@random.org>")
    assert engine.email_timer is not None
    engine.cancel_pending_email_pulse()


def test_email_routing_exact_and_domain_whitelist_matching(email_pulse_env):
    _, sm, engine, _ = email_pulse_env
    sm.set("core.auto-pulse.email.whitelist", ["Alice@Example.com", "@company.com", "*@partner.org"])
    sm.save()

    # 1. Exact address match (case-insensitive + Display Name <addr> format)
    engine.handle_email_message("Alice Smith <ALICE@example.com>", uid="1")
    assert engine.email_timer is not None
    engine.cancel_pending_email_pulse()

    # 2. Domain match with @company.com
    engine.handle_email_message("ceo@company.com", uid="2")
    assert engine.email_timer is not None
    engine.cancel_pending_email_pulse()

    # 3. Domain match with *@partner.org
    engine.handle_email_message("Support <help@PARTNER.ORG>", uid="3")
    assert engine.email_timer is not None
    engine.cancel_pending_email_pulse()

    # 4. Spoofed / lookalike / subdomain / non-whitelisted senders MUST be rejected
    for rejected in [
        "bob@evilcompany.com",
        "bob@company.com.evil.com",
        "bob@sub.company.com",
        "bob@example.com",
        "not-an-email",
        "",
    ]:
        engine.handle_email_message(rejected, uid="99")
        assert engine.email_timer is None, f"Sender {rejected!r} should NOT have matched whitelist"


def test_email_fixed_window_batching_and_deduplication(email_pulse_env):
    _, sm, engine, _ = email_pulse_env
    sm.set("core.auto-pulse.email.pulse-all-emails", True)
    sm.set("core.auto-pulse.email.buffer-seconds", 0.5)
    sm.save()

    engine.handle_email_message("a@example.com", uid="101", message_id="<101@example.com>")
    first_timer = engine.email_timer
    assert first_timer is not None

    # Second email joins the running fixed-window timer without resetting it
    engine.handle_email_message("b@example.com", uid="102", message_id="<102@example.com>")
    assert engine.email_timer is first_timer

    # Duplicate UID/Message-ID does not duplicate batch entry
    engine.handle_email_message("b@example.com", uid="102", message_id="<102@example.com>")
    assert len(engine._pending_email_messages) == 2

    engine.cancel_pending_email_pulse()


def test_email_cumulative_slowdown_privacy_and_silence_reset(email_pulse_env, monkeypatch):
    _, sm, engine, _ = email_pulse_env
    sm.set("core.auto-pulse.email.pulse-all-emails", True)
    sm.set("core.auto-pulse.email.buffer-seconds", 0.05)
    sm.save()

    emitted = []
    engine.trigger_pulse.connect(lambda prompt, purpose=None: emitted.append((prompt, purpose)))

    fake_now = 2000.0
    monkeypatch.setattr(time, "time", lambda: fake_now)

    assert engine._email_extra_delay == 0.0

    # First pulse
    engine.handle_email_message("secret-vip@private-client.com", "Secret VIP", uid="50", message_id="<50@private-client.com>")
    assert engine.email_timer is not None
    engine.execute_email_pulse()

    assert len(emitted) == 1
    prompt, purpose = emitted[0]
    assert purpose == "Email received"
    assert "Email_fetch_emails" in prompt
    assert "Email_mark_as_read" in prompt
    # Strict privacy: no sender email or name leaked
    assert "secret-vip@private-client.com" not in prompt
    assert "Secret VIP" not in prompt

    assert engine._email_extra_delay == 10.0
    assert engine._email_last_pulse_time == 2000.0

    # Second pulse 2 minutes later escalates by +10s
    fake_now = 2120.0
    engine.handle_email_message("other@example.com", uid="51")
    engine.execute_email_pulse()
    assert engine._email_extra_delay == 20.0

    # 30 minutes (1800s) of silence resets extra delay back to 0
    fake_now = 2120.0 + 1801.0
    engine.handle_email_message("other@example.com", uid="52")
    assert engine._email_extra_delay == 0.0
    engine.cancel_pending_email_pulse()


def test_email_coalescing_when_pulse_already_queued(email_pulse_env):
    orch, sm, engine, _ = email_pulse_env
    sm.set("core.auto-pulse.email.pulse-all-emails", True)
    sm.save()

    emitted = []
    engine.trigger_pulse.connect(lambda prompt, purpose=None: emitted.append((prompt, purpose)))
    orch.has_queued_pulse.return_value = True

    engine.handle_email_message("alice@example.com", uid="70")
    engine.execute_email_pulse()

    assert len(emitted) == 0
    assert engine._email_extra_delay == 0.0


def test_email_pre_dispatch_unread_checker_skips_when_already_read(email_pulse_env):
    orch, sm, engine, agent_dir = email_pulse_env
    sm.set("core.auto-pulse.email.pulse-all-emails", True)
    sm.save()

    tool = EmailTool(orchestrator=orch)
    tool._archive = EmailArchive(agent_id=orch.agent_id, db_path=str(agent_dir / "email_archive.db"))
    engine.email_unread_checker = tool.has_unread_pending_emails

    emitted = []
    engine.trigger_pulse.connect(lambda prompt, purpose=None: emitted.append((prompt, purpose)))

    # Archive email UID 80 and mark as already read (simulating user/agent reading during buffer)
    raw = _make_email_bytes(from_addr="alice@example.com", message_id="<80@example.com>")
    tool.archive.save_email(folder="INBOX", raw_bytes=raw, server_uid="80", is_read=True)

    engine.handle_email_message("alice@example.com", uid="80", message_id="<80@example.com>")
    engine.execute_email_pulse()
    assert len(emitted) == 0
    assert engine._email_extra_delay == 0.0

    # If a pending email is archived but still unread (is_read=False), pulse MUST fire
    raw2 = _make_email_bytes(
        from_addr="alice@example.com",
        subject="Follow-up Question",
        message_id="<81@example.com>",
    )
    tool.archive.save_email(folder="INBOX", raw_bytes=raw2, server_uid="81", is_read=False)
    engine.handle_email_message("alice@example.com", uid="81", message_id="<81@example.com>")
    engine.execute_email_pulse()
    assert len(emitted) == 1

    # If a pending email is not yet in archive (still waiting on IMAP), pulse MUST fire
    engine.handle_email_message("alice@example.com", uid="82", message_id="<82@example.com>")
    engine.execute_email_pulse()
    assert len(emitted) == 2


def test_email_pause_and_reset_cancel_pending_timer(email_pulse_env):
    orch, sm, engine, _ = email_pulse_env
    sm.set("core.auto-pulse.email.pulse-all-emails", True)
    sm.set("core.auto-pulse.email.buffer-seconds", 10.0)
    sm.save()

    engine.handle_email_message("alice@example.com", uid="90")
    assert engine.email_timer is not None

    # Pausing cancels pending email countdown
    orch.is_paused = True
    orch.on_paused_state_changed.emit(True)
    assert engine.email_timer is None

    # Messages received while paused are ignored
    engine.handle_email_message("alice@example.com", uid="91")
    assert engine.email_timer is None

    # Unpause, schedule again, then disable auto-pulse and reset_email_slowdown(cancel_if_disabled=True)
    orch.is_paused = False
    orch.on_paused_state_changed.emit(False)
    engine._email_extra_delay = 30.0
    engine.handle_email_message("alice@example.com", uid="92")
    assert engine.email_timer is not None

    sm.set("core.auto-pulse.email.pulse-all-emails", False)
    sm.save()
    engine.reset_email_slowdown(cancel_if_disabled=True)
    assert engine.email_timer is None
    assert engine._email_extra_delay == 0.0


# --- EmailMonitor Tests ---

def _build_configured_tool_and_monitor(tmp_path, monkeypatch, agent_id="monitor-test-agent"):
    agent_dir = tmp_path / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(agent_dir))
    SettingsManager.remove_instance(agent_id=agent_id)

    sm = SettingsManager(agent_id=agent_id)
    sm.set("core.tools.email", True)
    sm.set("core.tools.email_archive_local", True)
    sm.set("core.auto-pulse.email.pulse-all-emails", True)
    sm.set("core.auto-pulse.email.whitelist", [])
    sm.set("core.auto-pulse.email.check-interval-seconds", 60)
    sm.save()

    sm.set_env("EMAIL_ADDRESS", "agent@example.com")
    sm.set_env("EMAIL_AUTH_TYPE", "password")
    sm.set_env("EMAIL_PASSWORD", "app-secret")
    sm.set_env("EMAIL_IMAP_HOST", "imap.example.com")
    sm.set_env("EMAIL_IMAP_PORT", "993")
    sm.set_env("EMAIL_IMAP_SECURITY", "SSL/TLS")
    sm.set_env("EMAIL_ARCHIVE_LOCAL", "true")

    orch = MagicMock()
    orch.agent_id = agent_id
    orch.is_paused = False
    orch.settings_manager = sm

    tool = EmailTool(orchestrator=orch)
    tool._archive = EmailArchive(agent_id=agent_id, db_path=str(agent_dir / "email_archive.db"))

    received = []
    monitor = EmailMonitor(
        email_tool=tool,
        orchestrator=orch,
        folder="INBOX",
        email_received_callback=lambda **kw: received.append(kw),
        min_interval_seconds=0.05,
    )
    return orch, sm, tool, monitor, received


def test_email_monitor_dormancy_guards(tmp_path, monkeypatch):
    orch, sm, tool, monitor, _ = _build_configured_tool_and_monitor(tmp_path, monkeypatch, "mon-dormant")
    try:
        assert monitor.should_monitor() is True

        # Paused -> dormant
        orch.is_paused = True
        assert monitor.should_monitor() is False
        orch.is_paused = False

        # Tool disabled -> dormant
        sm.set("core.tools.email", False)
        assert monitor.should_monitor() is False
        sm.set("core.tools.email", True)

        # Neither pulse-all nor whitelist -> dormant
        sm.set("core.auto-pulse.email.pulse-all-emails", False)
        sm.set("core.auto-pulse.email.whitelist", [])
        assert monitor.should_monitor() is False

        # Whitelist non-empty -> active
        sm.set("core.auto-pulse.email.whitelist", ["boss@company.com"])
        assert monitor.should_monitor() is True

        # Missing password -> dormant
        sm.set_env("EMAIL_PASSWORD", "")
        assert monitor.should_monitor() is False

        # OAuth2 auth type requires client_id, client_secret, refresh_token
        sm.set_env("EMAIL_AUTH_TYPE", "oauth2")
        assert monitor.should_monitor() is False
        sm.set_env("EMAIL_OAUTH_CLIENT_ID", "cid")
        sm.set_env("EMAIL_OAUTH_CLIENT_SECRET", "csec")
        sm.set_env("EMAIL_OAUTH_REFRESH_TOKEN", "rtok")
        assert monitor.should_monitor() is True
    finally:
        monitor.stop()
        SettingsManager.remove_instance(agent_id="mon-dormant")


def test_email_monitor_baseline_then_detect_new_and_dedup_archive(tmp_path, monkeypatch):
    orch, sm, tool, monitor, received = _build_configured_tool_and_monitor(tmp_path, monkeypatch, "mon-poll")
    try:
        # 1. Initial baseline poll: UIDVALIDITY=42, UIDNEXT=101 -> baseline last_seen_uid=100, zero pulses
        imap_baseline = MagicMock()
        imap_baseline.select.return_value = ("OK", [b"5"])
        imap_baseline.response.side_effect = lambda code: (
            ("UIDVALIDITY", [b"42"]) if code == "UIDVALIDITY" else ("UIDNEXT", [b"101"])
        )

        with patch.object(tool, "_get_imap_connection", return_value=(True, imap_baseline)):
            cands = monitor.poll_once()

        assert cands == []
        assert received == []
        assert monitor._initialized is True
        assert monitor._uidvalidity == 42
        assert monitor._last_seen_uid == 100
        imap_baseline.select.assert_called_once_with('"INBOX"', readonly=True)
        imap_baseline.logout.assert_called_once()

        # 2. Fast-path poll: UIDNEXT still 101 -> exits immediately with zero SEARCH/FETCH
        imap_idle = MagicMock()
        imap_idle.select.return_value = ("OK", [b"5"])
        imap_idle.response.side_effect = lambda code: (
            ("UIDVALIDITY", [b"42"]) if code == "UIDVALIDITY" else ("UIDNEXT", [b"101"])
        )

        with patch.object(tool, "_get_imap_connection", return_value=(True, imap_idle)):
            cands = monitor.poll_once()

        assert cands == []
        imap_idle.uid.assert_not_called()

        # Pre-archive UID 102 to simulate an email already synced by EmailTool on Gmail (delete_from_server=False)
        tool.archive.save_email(
            folder="INBOX",
            raw_bytes=_make_email_bytes(from_addr="already@archived.com", message_id="<102@archived.com>"),
            server_uid="102",
            is_read=True,
        )

        # 3. Poll with new emails: UID 100 (RFC 3501 quirk echo), 101 (new unseen), 102 (already archived), 103 (marked Seen)
        imap_new = MagicMock()
        imap_new.select.return_value = ("OK", [b"8"])
        imap_new.response.side_effect = lambda code: (
            ("UIDVALIDITY", [b"42"]) if code == "UIDVALIDITY" else ("UIDNEXT", [b"104"])
        )

        def fake_uid(cmd, *args):
            if cmd == "search":
                return ("OK", [b"100 101 102 103"])
            if cmd == "fetch":
                uid_b = args[0]
                if uid_b == b"101":
                    return ("OK", [(b"6 (FLAGS (\\Recent) BODY[HEADER.FIELDS (FROM MESSAGE-ID DATE)] {80}", _make_raw_headers("Alice <alice@company.com>", "<101@company.com>")), b")"])
                if uid_b == b"102":
                    return ("OK", [(b"7 (FLAGS () BODY[HEADER.FIELDS (FROM MESSAGE-ID DATE)] {80}", _make_raw_headers("already@archived.com", "<102@archived.com>")), b")"])
                if uid_b == b"103":
                    return ("OK", [(b"8 (FLAGS (\\Seen) BODY[HEADER.FIELDS (FROM MESSAGE-ID DATE)] {80}", _make_raw_headers("seen@example.com", "<103@example.com>")), b")"])
            return ("NO", [])

        imap_new.uid.side_effect = fake_uid

        with patch.object(tool, "_get_imap_connection", return_value=(True, imap_new)):
            cands = monitor.poll_once()

        assert len(cands) == 1
        assert cands[0]["uid"] == "101"
        assert cands[0]["sender_email"] == "alice@company.com"
        assert cands[0]["sender_name"] == "Alice"
        assert len(received) == 1
        assert monitor._last_seen_uid == 103
        # Verify BODY.PEEK was used so server \Seen flags are never mutated
        fetch_calls = [c for c in imap_new.uid.call_args_list if c.args[0] == "fetch"]
        assert all("BODY.PEEK" in c.args[2] for c in fetch_calls)

        # 4. UIDVALIDITY change forces clean re-baseline without pulsing
        imap_reval = MagicMock()
        imap_reval.select.return_value = ("OK", [b"2"])
        imap_reval.response.side_effect = lambda code: (
            ("UIDVALIDITY", [b"99"]) if code == "UIDVALIDITY" else ("UIDNEXT", [b"10"])
        )
        with patch.object(tool, "_get_imap_connection", return_value=(True, imap_reval)):
            cands = monitor.poll_once()

        assert cands == []
        assert monitor._uidvalidity == 99
        assert monitor._last_seen_uid == 9
    finally:
        monitor.stop()
        SettingsManager.remove_instance(agent_id="mon-poll")


def test_email_monitor_backoff_and_recovery(tmp_path, monkeypatch):
    orch, sm, tool, monitor, _ = _build_configured_tool_and_monitor(tmp_path, monkeypatch, "mon-backoff")
    try:
        base_wait = monitor._compute_next_wait()
        assert base_wait == 60.0

        with patch.object(tool, "_get_imap_connection", return_value=(False, "Connection timed out")):
            monitor.poll_once()
        assert monitor._consecutive_errors == 1
        assert monitor._compute_next_wait() == 120.0

        with patch.object(tool, "_get_imap_connection", return_value=(False, "Connection timed out")):
            monitor.poll_once()
        assert monitor._consecutive_errors == 2
        assert monitor._compute_next_wait() == 240.0

        # Recovery resets backoff immediately
        imap_ok = MagicMock()
        imap_ok.select.return_value = ("OK", [b"0"])
        imap_ok.response.side_effect = lambda code: ("UIDNEXT", [b"1"]) if code == "UIDNEXT" else ("UIDVALIDITY", [b"1"])
        with patch.object(tool, "_get_imap_connection", return_value=(True, imap_ok)):
            monitor.poll_once()
        assert monitor._consecutive_errors == 0
        assert monitor._compute_next_wait() == 60.0
    finally:
        monitor.stop()
        SettingsManager.remove_instance(agent_id="mon-backoff")


def test_email_monitor_fallback_baseline_without_uidnext_and_account_switch(tmp_path, monkeypatch):
    orch, sm, tool, monitor, received = _build_configured_tool_and_monitor(tmp_path, monkeypatch, "mon-fallback")
    try:
        # Server omits UIDNEXT -> falls back to UID SEARCH ALL
        imap_no_uidnext = MagicMock()
        imap_no_uidnext.select.return_value = ("OK", [b"3"])
        imap_no_uidnext.response.return_value = ("UIDNEXT", [None])
        imap_no_uidnext.uid.return_value = ("OK", [b"5 12 25"])

        with patch.object(tool, "_get_imap_connection", return_value=(True, imap_no_uidnext)):
            assert monitor.poll_once() == []

        assert monitor._initialized is True
        assert monitor._last_seen_uid == 25
        assert received == []

        # Changing email account resets watermark and re-baselines cleanly
        sm.set_env("EMAIL_ADDRESS", "other-agent@example.com")
        imap_new_acct = MagicMock()
        imap_new_acct.select.return_value = ("OK", [b"1"])
        imap_new_acct.response.side_effect = lambda code: ("UIDNEXT", [b"8"]) if code == "UIDNEXT" else ("UIDVALIDITY", [b"2"])
        with patch.object(tool, "_get_imap_connection", return_value=(True, imap_new_acct)):
            assert monitor.poll_once() == []

        assert monitor._last_seen_uid == 7
        assert monitor._uidvalidity == 2
        assert received == []
    finally:
        monitor.stop()
        SettingsManager.remove_instance(agent_id="mon-fallback")


def test_email_tool_sync_monitor_lifecycle_and_shutdown(tmp_path, monkeypatch):
    orch, sm, tool, _, _ = _build_configured_tool_and_monitor(tmp_path, monkeypatch, "mon-lifecycle")
    try:
        imap_stub = MagicMock()
        imap_stub.select.return_value = ("OK", [b"0"])
        imap_stub.response.side_effect = lambda code: ("UIDNEXT", [b"1"]) if code == "UIDNEXT" else ("UIDVALIDITY", [b"1"])

        with patch.object(tool, "_get_imap_connection", return_value=(True, imap_stub)):
            # Starts monitor when enabled
            assert tool.sync_monitor_state() is True
            assert tool.monitor is not None
            assert tool.monitor.is_running is True

            # Pausing orchestrator stops monitor cleanly
            orch.is_paused = True
            assert tool.sync_monitor_state() is False
            assert tool.monitor.is_running is False

            # Resuming restarts monitor
            orch.is_paused = False
            assert tool.sync_monitor_state() is True
            assert tool.monitor.is_running is True

            # Shutdown stops monitor and clears reference
            tool.shutdown()
            assert tool.monitor is None
    finally:
        tool.shutdown()
        SettingsManager.remove_instance(agent_id="mon-lifecycle")


# --- Orchestrator Category & Coalescing Tests ---

def test_orchestrator_process_pulse_email_category_and_coalescing():
    orch = AmityOrchestrator.__new__(AmityOrchestrator)
    orch.agent_id = "test-orch-email"
    orch.is_paused = False
    orch.is_busy = True
    orch.is_thinking = True
    orch._is_truly_busy = MagicMock(return_value=True)
    orch.event_queue = []

    # 1. Busy-queue coalescing for "Email received"
    orch.process_pulse("prompt 1", purpose="Email received")
    orch.process_pulse("prompt 2", purpose="Email received")
    assert len(orch.event_queue) == 1
    assert orch.event_queue[0]["purpose"] == "Email received"

    # 2. Idle dispatch logs [Autonomy Pulse: Email received]
    orch.is_busy = False
    orch.is_thinking = False
    orch.event_queue.clear()
    orch.gemini_worker = MagicMock()
    orch.gemini_worker.available = True
    orch.gemini_worker.running = True
    orch.settings_manager = MagicMock()
    orch.settings_manager.get.return_value = False
    orch.budget_lock = MagicMock()
    orch.budget_lock.__enter__ = MagicMock(return_value=None)
    orch.budget_lock.__exit__ = MagicMock(return_value=None)
    orch.session_fatigue_tokens = 0
    orch.append_to_conversation = MagicMock()
    orch.set_busy_state = MagicMock()
    orch.save_session_state = MagicMock()

    orch.process_pulse(
        "[CHANNEL: SYSTEM_SCHEDULE]\n[AGENT_PULSE] Event: New emails received\nContext:\nYou have new unread emails.",
        purpose="Email received",
    )
    orch.gemini_worker.send_prompt.assert_called_once()
    orch.append_to_conversation.assert_called_with("System", "[Autonomy Pulse: Email received]")

    # 3. A scheduled routine mentioning the Email tool in step 4 MUST NOT be misclassified as "Email received"
    orch.is_busy = False
    orch.is_thinking = False
    orch.append_to_conversation.reset_mock()
    orch.gemini_worker.send_prompt.reset_mock()
    orch.process_pulse(
        "[CHANNEL: SYSTEM_SCHEDULE]\n[AGENT_PULSE] Event: Morning Kickoff\n4. Check your active social tools (Email) for new messages.",
        purpose=None,
    )
    orch.gemini_worker.send_prompt.assert_called_once()
    orch.append_to_conversation.assert_called_with("System", "[Autonomy Pulse: Scheduled routine]")


def test_email_monitor_skips_malformed_header_and_case_insensitive_flags(tmp_path, monkeypatch):
    """Regression: a single malformed email header must not stall the batch, and IMAP flags match case-insensitively."""
    orch, sm, tool, monitor, received = _build_configured_tool_and_monitor(tmp_path, monkeypatch, "mon-malformed")
    try:
        monitor._initialized = True
        monitor._uidvalidity = 7
        monitor._last_seen_uid = 200
        monitor._account_key = ("imap.example.com", 993, "agent@example.com", "INBOX")

        imap_mock = MagicMock()
        imap_mock.select.return_value = ("OK", [b"10"])
        imap_mock.response.side_effect = lambda code: (
            ("UIDVALIDITY", [b"7"]) if code == "UIDVALIDITY" else ("UIDNEXT", [b"204"])
        )

        def fake_uid(cmd, *args):
            if cmd == "search":
                return ("OK", [b"201 202 203"])
            if cmd == "fetch":
                uid_b = args[0]
                if uid_b == b"201":
                    return ("OK", [(b"1 (FLAGS () BODY[HEADER.FIELDS (FROM MESSAGE-ID DATE)] {20}", b"BROKEN_POISON_HDR"), b")"])
                if uid_b == b"202":
                    return ("OK", [(b"2 (FLAGS (\\SEEN) BODY[HEADER.FIELDS (FROM MESSAGE-ID DATE)] {80}", _make_raw_headers("seen@example.com", "<202@example.com>")), b")"])
                if uid_b == b"203":
                    return ("OK", [(b"3 (FLAGS () BODY[HEADER.FIELDS (FROM MESSAGE-ID DATE)] {80}", _make_raw_headers("Bob <bob@example.com>", "<203@example.com>")), b")"])
            return ("NO", [])

        imap_mock.uid.side_effect = fake_uid

        real_parse = __import__("email").message_from_bytes

        def flaky_parse(raw_bytes, *args, **kwargs):
            if raw_bytes == b"BROKEN_POISON_HDR":
                raise ValueError("Invalid RFC822 header")
            return real_parse(raw_bytes, *args, **kwargs)

        with patch.object(tool, "_get_imap_connection", return_value=(True, imap_mock)), \
             patch("core.email_monitor.email.message_from_bytes", side_effect=flaky_parse):
            cands = monitor.poll_once()

        assert len(cands) == 1
        assert cands[0]["uid"] == "203"
        assert cands[0]["sender_email"] == "bob@example.com"
        assert monitor._last_seen_uid == 203
        assert monitor._consecutive_errors == 0
    finally:
        monitor.stop()
        SettingsManager.remove_instance(agent_id="mon-malformed")
