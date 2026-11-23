import time
import pytest
from unittest.mock import MagicMock
from core.pulse_engine import PulseEngine
from core.settings_manager import SettingsManager
from core.address_book import AddressBookManager


@pytest.fixture
def pulse_env(tmp_path, monkeypatch):
    agent_id = "test-wa-routing-agent"
    agent_dir = tmp_path / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(agent_dir))

    orch = MagicMock()
    orch.agent_id = agent_id
    orch.is_paused = False
    orch.is_busy = False
    orch.has_queued_pulse = MagicMock(return_value=False)

    sm = SettingsManager(agent_id=agent_id)
    sm.set("core.auto-pulse.whitelist", [])
    sm.set("core.auto-pulse.group-whitelist", [])
    sm.set("core.auto-pulse.pulse-all-dms", False)
    sm.set("core.auto-pulse.pulse-all-groups", False)
    sm.set("core.auto-pulse.pulse-on-mentions", False)
    sm.set("core.auto-pulse.buffer-seconds", 0.05)
    sm.save()

    engine = PulseEngine(orch)
    engine.settings_manager = sm
    yield orch, sm, engine
    engine.stop()


def test_routing_default_ignores_all(pulse_env):
    orch, sm, engine = pulse_env

    # DM from unwhitelisted sender ignored
    engine.handle_whatsapp_message("27112223333@c.us", "Stranger")
    assert engine.whatsapp_timer is None

    # Group message ignored
    engine.handle_whatsapp_message("12345-67890@g.us", "Group Chat", is_group=True, group_name="Devs")
    assert engine.whatsapp_timer is None

    # Broadcast ignored
    engine.handle_whatsapp_message("status@broadcast", "Status")
    assert engine.whatsapp_timer is None


def test_routing_pulse_all_dms(pulse_env):
    orch, sm, engine = pulse_env
    sm.set("core.auto-pulse.pulse-all-dms", True)
    sm.save()

    # Any DM qualifies
    engine.handle_whatsapp_message("27999888777@c.us", "Random Contact")
    assert engine.whatsapp_timer is not None
    engine.whatsapp_timer.cancel()
    engine.whatsapp_timer = None

    # Group still ignored
    engine.handle_whatsapp_message("12345-67890@g.us", "Group", is_group=True, group_name="Devs")
    assert engine.whatsapp_timer is None


def test_routing_pulse_all_groups(pulse_env):
    orch, sm, engine = pulse_env
    sm.set("core.auto-pulse.pulse-all-groups", True)
    sm.save()

    # Any group qualifies
    engine.handle_whatsapp_message("12345-67890@g.us", "Group", is_group=True, group_name="Random Group")
    assert engine.whatsapp_timer is not None
    engine.whatsapp_timer.cancel()
    engine.whatsapp_timer = None

    # DM from unwhitelisted sender still ignored
    engine.handle_whatsapp_message("27999888777@c.us", "Stranger")
    assert engine.whatsapp_timer is None


def test_routing_group_whitelist_verbatim_matching(pulse_env):
    orch, sm, engine = pulse_env
    sm.set("core.auto-pulse.group-whitelist", ["Project Amity", "Dev Team"])
    sm.save()

    # 1. Exact match (case-insensitive & trimmed) qualifies
    engine.handle_whatsapp_message("111@g.us", "Alice", is_group=True, group_name="  project amity  ")
    assert engine.whatsapp_timer is not None
    engine.whatsapp_timer.cancel()
    engine.whatsapp_timer = None

    # 2. Substring does NOT qualify
    engine.handle_whatsapp_message("222@g.us", "Bob", is_group=True, group_name="Project Amity Discussion")
    assert engine.whatsapp_timer is None

    # 3. Non-whitelisted group ignored
    engine.handle_whatsapp_message("333@g.us", "Charlie", is_group=True, group_name="Marketing")
    assert engine.whatsapp_timer is None


def test_routing_pulse_on_mentions(pulse_env):
    orch, sm, engine = pulse_env
    sm.set("core.auto-pulse.pulse-on-mentions", True)
    sm.save()

    # 1. Mention in an unwhitelisted group qualifies
    engine.handle_whatsapp_message("111@g.us", "Alice", is_group=True, group_name="Random Group", mentioned=True)
    assert engine.whatsapp_timer is not None
    engine.whatsapp_timer.cancel()
    engine.whatsapp_timer = None

    # 2. Non-mention in that same group is ignored
    engine.handle_whatsapp_message("111@g.us", "Alice", is_group=True, group_name="Random Group", mentioned=False)
    assert engine.whatsapp_timer is None

    # 3. Mention in an unwhitelisted DM qualifies
    engine.handle_whatsapp_message("27111222333@c.us", "Stranger", is_group=False, mentioned=True)
    assert engine.whatsapp_timer is not None
    engine.whatsapp_timer.cancel()


def test_fixed_window_buffer_behavior(pulse_env):
    orch, sm, engine = pulse_env
    sm.set("core.auto-pulse.pulse-all-dms", True)
    sm.set("core.auto-pulse.buffer-seconds", 0.5)
    sm.save()

    # First message starts timer
    engine.handle_whatsapp_message("27111111111@c.us", "Contact 1")
    initial_timer = engine.whatsapp_timer
    assert initial_timer is not None

    # Second message arrives while timer is running; timer must NOT be replaced/restarted
    engine.handle_whatsapp_message("27222222222@c.us", "Contact 2")
    assert engine.whatsapp_timer is initial_timer

    initial_timer.cancel()


def test_cumulative_buffer_slowdown_and_reset(pulse_env, monkeypatch):
    orch, sm, engine = pulse_env
    sm.set("core.auto-pulse.pulse-all-dms", True)
    sm.set("core.auto-pulse.buffer-seconds", 0.05)
    sm.save()

    pulses_emitted = []
    engine.trigger_pulse.connect(lambda p, purpose=None: pulses_emitted.append((p, purpose)))

    current_fake_time = 1000.0
    monkeypatch.setattr(time, "time", lambda: current_fake_time)

    assert engine._wa_extra_delay == 0.0

    # 1. Fire first pulse
    engine.handle_whatsapp_message("27111111111@c.us", "Contact 1")
    assert engine.whatsapp_timer is not None
    engine.execute_whatsapp_pulse()

    assert len(pulses_emitted) == 1
    # Check that privacy is strictly maintained (no sender or number leaked)
    assert "27111111111" not in pulses_emitted[0][0]
    assert "Contact 1" not in pulses_emitted[0][0]
    assert "New WhatsApp messages" in pulses_emitted[0][0]
    assert pulses_emitted[0][1] == "WhatsApp message received"

    # Extra delay increased by 10 seconds
    assert engine._wa_extra_delay == 10.0
    assert engine._wa_last_pulse_time == 1000.0

    # 2. Fire second pulse 2 minutes later
    current_fake_time = 1120.0
    engine.handle_whatsapp_message("27111111111@c.us", "Contact 1")
    engine.execute_whatsapp_pulse()
    assert engine._wa_extra_delay == 20.0

    # 3. Simulate 30 minutes (1800s) of silence
    current_fake_time = 1120.0 + 1801.0
    engine.handle_whatsapp_message("27111111111@c.us", "Contact 1")
    # Buffer delay should reset back to base (extra_delay = 0.0)
    assert engine._wa_extra_delay == 0.0
    if engine.whatsapp_timer:
        engine.whatsapp_timer.cancel()


def test_coalescing_when_pulse_already_queued(pulse_env):
    orch, sm, engine = pulse_env
    sm.set("core.auto-pulse.pulse-all-dms", True)
    sm.save()

    pulses_emitted = []
    engine.trigger_pulse.connect(lambda p, purpose=None: pulses_emitted.append((p, purpose)))

    # Simulate orchestrator already having a WhatsApp pulse queued
    orch.has_queued_pulse.return_value = True

    engine.handle_whatsapp_message("27111111111@c.us", "Contact 1")
    engine.execute_whatsapp_pulse()

    # Nothing should be emitted because it coalesces
    assert len(pulses_emitted) == 0
    # And extra delay must NOT escalate on coalesced pulses
    assert engine._wa_extra_delay == 0.0


def test_reset_whatsapp_slowdown(pulse_env):
    orch, sm, engine = pulse_env
    engine._wa_extra_delay = 30.0
    engine._wa_last_pulse_time = 500.0

    engine.reset_whatsapp_slowdown()
    assert engine._wa_extra_delay == 0.0
    assert engine._wa_last_pulse_time == 0.0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"msg_type": "gp2"},
        {"msg_type": "group_notification"},
        {"msg_type": "e2e_notification"},
        {"msg_type": "notification"},
        {"msg_type": "notification_template"},
        {"msg_type": "broadcast_notification"},
        {"msg_type": "protocol"},
        {"msg_type": "revoked"},
        {"msg_type": "call_log"},
        {"msg_type": "reaction"},
        {"msg_type": "ciphertext"},
        {"msg_type": "poll_update"},
        {"msg_type": "chat", "subtype": "sender_revoke"},
        {"msg_type": "chat", "is_notification": True},
        {"msg_type": "chat", "is_revoked": True},
        {"msg_type": "chat", "is_visible": False},
        {"msg_type": "chat", "is_unread_type": False},
        {"msg_type": "chat", "raw_chat_id": "status@broadcast"},
    ],
)
def test_non_message_and_invisible_events_ignored(pulse_env, kwargs):
    orch, sm, engine = pulse_env
    sm.set("core.auto-pulse.pulse-all-dms", True)
    sm.set("core.auto-pulse.pulse-all-groups", True)
    sm.set("core.auto-pulse.pulse-on-mentions", True)
    sm.save()

    engine.handle_whatsapp_message(
        "27111111111@c.us",
        "Alice",
        is_group=False,
        mentioned=True,
        msg_id="false_27111111111@c.us_NONMSG01",
        **kwargs,
    )
    assert engine.whatsapp_timer is None
    assert engine._pending_whatsapp_messages == []

    # Newsletter JID rejected regardless of msg_type
    engine.handle_whatsapp_message(
        "120363000000000000@newsletter",
        "Channel",
        is_group=False,
        msg_type="chat",
        msg_id="false_120363000000000000@newsletter_NEWS01",
    )
    assert engine.whatsapp_timer is None
    assert engine._pending_whatsapp_messages == []


def test_revoke_sole_pending_message_cancels_countdown(pulse_env):
    orch, sm, engine = pulse_env
    sm.set("core.auto-pulse.pulse-all-dms", True)
    sm.set("core.auto-pulse.buffer-seconds", 0.5)
    sm.save()

    pulses_emitted = []
    engine.trigger_pulse.connect(lambda p, purpose=None: pulses_emitted.append((p, purpose)))

    engine.handle_whatsapp_message(
        "27111111111@c.us",
        "Alice",
        raw_chat_id="27111111111@c.us",
        msg_id="false_27111111111@c.us_3EB0ABC123",
        msg_stanza_id="3EB0ABC123",
        msg_type="chat",
    )
    assert engine.whatsapp_timer is not None
    assert len(engine._pending_whatsapp_messages) == 1

    engine.handle_whatsapp_message_revoked(
        msg_id="false_27111111111@c.us_3EB0ABC123",
        chat_id="27111111111@c.us",
        revoke_type="sender_revoke",
    )
    assert engine.whatsapp_timer is None
    assert engine._pending_whatsapp_messages == []
    assert engine._pending_whatsapp_chats == set()
    assert pulses_emitted == []
    assert engine._wa_extra_delay == 0.0


def test_revoke_by_stanza_id_with_different_jid_cancels_countdown(pulse_env):
    orch, sm, engine = pulse_env
    sm.set("core.auto-pulse.pulse-all-dms", True)
    sm.set("core.auto-pulse.buffer-seconds", 0.5)
    sm.save()

    pulses_emitted = []
    engine.trigger_pulse.connect(lambda p, purpose=None: pulses_emitted.append((p, purpose)))

    engine.handle_whatsapp_message(
        "27111111111@c.us",
        "Alice",
        raw_chat_id="999888777@lid",
        msg_id="false_999888777@lid_STANZA999",
        msg_stanza_id="STANZA999",
        msg_type="chat",
    )
    assert engine.whatsapp_timer is not None

    # Revoke arrives referencing @c.us instead of @lid, sharing the same stanza ID
    engine.handle_whatsapp_message_revoked(
        msg_id="false_27111111111@c.us_STANZA999",
        msg_stanza_id="STANZA999",
        chat_id="27111111111@c.us",
        revoke_type="revoke",
    )
    assert engine.whatsapp_timer is None
    assert engine._pending_whatsapp_messages == []
    assert pulses_emitted == []


def test_revoke_partial_batch_keeps_countdown_until_last_revoked(pulse_env):
    orch, sm, engine = pulse_env
    sm.set("core.auto-pulse.pulse-all-dms", True)
    sm.set("core.auto-pulse.buffer-seconds", 0.5)
    sm.save()

    engine.handle_whatsapp_message(
        "27111111111@c.us",
        "Alice",
        msg_id="false_27111111111@c.us_MSG_A",
        msg_stanza_id="MSG_A",
        msg_type="chat",
    )
    engine.handle_whatsapp_message(
        "27222222222@c.us",
        "Bob",
        msg_id="false_27222222222@c.us_MSG_B",
        msg_stanza_id="MSG_B",
        msg_type="chat",
    )
    initial_timer = engine.whatsapp_timer
    assert initial_timer is not None
    assert len(engine._pending_whatsapp_messages) == 2

    # Revoking one message keeps the timer running for the remaining message
    engine.handle_whatsapp_message_revoked(msg_id="false_27111111111@c.us_MSG_A")
    assert engine.whatsapp_timer is initial_timer
    assert len(engine._pending_whatsapp_messages) == 1
    assert engine._pending_whatsapp_chats == {"27222222222@c.us"}

    # Revoking an unrelated ID does not affect the timer
    engine.handle_whatsapp_message_revoked(msg_id="false_27999999999@c.us_UNRELATED")
    assert engine.whatsapp_timer is initial_timer
    assert len(engine._pending_whatsapp_messages) == 1

    # Revoking the last remaining message cancels the countdown
    engine.handle_whatsapp_message_revoked(msg_id="false_27222222222@c.us_MSG_B")
    assert engine.whatsapp_timer is None
    assert engine._pending_whatsapp_messages == []


def test_visible_unread_checker_gates_execution_and_delay(pulse_env):
    orch, sm, engine = pulse_env
    sm.set("core.auto-pulse.pulse-all-dms", True)
    sm.set("core.auto-pulse.buffer-seconds", 0.5)
    sm.save()

    pulses_emitted = []
    engine.trigger_pulse.connect(lambda p, purpose=None: pulses_emitted.append((p, purpose)))

    # 1. Zero visible unread messages -> skip pulse and do not escalate delay
    checker_calls = []
    engine.whatsapp_visible_unread_checker = lambda **kw: (checker_calls.append(kw) or 0)
    engine.handle_whatsapp_message(
        "27111111111@c.us",
        "Alice",
        raw_chat_id="27111111111@c.us",
        msg_id="false_27111111111@c.us_MSG1",
        msg_stanza_id="MSG1",
        msg_type="chat",
    )
    engine.execute_whatsapp_pulse()
    assert len(checker_calls) == 1
    assert checker_calls[0]["chat_ids"] == ["27111111111@c.us"]
    assert checker_calls[0]["msg_ids"] == ["false_27111111111@c.us_MSG1"]
    assert checker_calls[0]["stanza_ids"] == ["MSG1"]
    assert checker_calls[0]["has_untracked"] is False
    assert pulses_emitted == []
    assert engine._wa_extra_delay == 0.0

    # 2. Checker exception -> fail closed (skip pulse, do not escalate delay)
    def failing_checker(**kw):
        raise RuntimeError("bridge offline")

    engine.whatsapp_visible_unread_checker = failing_checker
    engine.handle_whatsapp_message(
        "27111111111@c.us",
        "Alice",
        msg_id="false_27111111111@c.us_MSG2",
        msg_stanza_id="MSG2",
        msg_type="chat",
    )
    engine.execute_whatsapp_pulse()
    assert pulses_emitted == []
    assert engine._wa_extra_delay == 0.0

    # 3. Positive visible count -> emit pulse and escalate delay
    engine.whatsapp_visible_unread_checker = lambda **kw: 1
    engine.handle_whatsapp_message(
        "27111111111@c.us",
        "Alice",
        msg_id="false_27111111111@c.us_MSG3",
        msg_stanza_id="MSG3",
        msg_type="chat",
    )
    engine.execute_whatsapp_pulse()
    assert len(pulses_emitted) == 1
    assert engine._wa_extra_delay == 10.0


def test_js_browser_message_visibility_helper_in_node():
    import json
    import os
    import re
    import shutil
    import subprocess

    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node binary not available")

    server_js_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "src", "tools", "whatsapp_node", "server.js")
    )
    with open(server_js_path, "r", encoding="utf-8") as f:
        src = f.read()

    match = re.search(
        r"const browserMessageVisibilityHelper = `([\s\S]*?)`;\s*\n\s*async function ensureListenersAttached",
        src,
    )
    assert match is not None, "Could not locate browserMessageVisibilityHelper in server.js"
    helper_code = match.group(1)

    node_script = """
const assert = require('assert');
const helperCode = process.env.HELPER_CODE;
eval(helperCode);

// 1. Stanza ID extraction
assert.strictEqual(extractStanzaIdInBrowser({ id: '3EB0STANZA', _serialized: 'false_123@c.us_3EB0STANZA' }), '3EB0STANZA');
assert.strictEqual(extractStanzaIdInBrowser('false_120363024851923214@g.us_3EB0ABC_196@lid'), '3EB0ABC');
assert.strictEqual(extractStanzaIdInBrowser('true_123@c.us_XYZ'), 'XYZ');
assert.strictEqual(extractStanzaIdInBrowser(null), '');

// 2. Visible user messages
const visibleMsgs = [
    { id: { _serialized: 'false_1@c.us_M1', fromMe: false }, type: 'chat', body: 'Hello!', from: '1@c.us' },
    { id: { _serialized: 'false_1@c.us_M2', fromMe: false }, type: 'image', isMedia: true, hasMedia: true, from: '1@c.us' },
    { id: { _serialized: 'false_1@c.us_M3', fromMe: false }, type: 'ptt', hasMedia: true, from: '1@c.us' },
    { id: { _serialized: 'false_1@c.us_M4', fromMe: false }, type: 'location', lat: -29.7, lng: 30.8, from: '1@c.us' },
    { id: { _serialized: 'false_1@c.us_M5', fromMe: false }, type: 'vcard', body: 'BEGIN:VCARD', from: '1@c.us' },
    { id: { _serialized: 'false_1@c.us_M6', fromMe: false }, type: 'poll_creation', pollName: 'Lunch?', pollOptions: [{ name: 'Yes' }], from: '1@c.us' },
    { id: { _serialized: 'false_1@c.us_M7', fromMe: false }, type: 'sticker', isMedia: true, from: '1@c.us' },
    { id: { _serialized: 'false_1@c.us_M8', fromMe: false }, type: 'chat', subtype: 'url', body: 'https://example.com', matchedText: 'https://example.com', from: '1@c.us' },
];
for (const m of visibleMsgs) {
    assert.strictEqual(isVisibleUserMessageInBrowser(m), true, 'Expected visible for ' + m.id._serialized);
}

// 3. Non-message / system / invisible / revoked / empty events
const hiddenMsgs = [
    { id: { _serialized: 'false_1@g.us_H1', fromMe: false }, type: 'gp2', subtype: 'add', chatId: '1@g.us' },
    { id: { _serialized: 'false_1@c.us_H2', fromMe: false }, type: 'e2e_notification', subtype: 'identity', from: '1@c.us' },
    { id: { _serialized: 'false_1@c.us_H3', fromMe: false }, type: 'protocol', subtype: 'revoke', from: '1@c.us' },
    { id: { _serialized: 'false_1@c.us_H4', fromMe: false }, type: 'revoked', body: '', from: '1@c.us' },
    { id: { _serialized: 'false_1@c.us_H5', fromMe: false }, type: 'call_log', from: '1@c.us' },
    { id: { _serialized: 'false_1@c.us_H6', fromMe: false }, type: 'reaction', from: '1@c.us' },
    { id: { _serialized: 'false_1@c.us_H7', fromMe: false }, type: 'ciphertext', from: '1@c.us' },
    { id: { _serialized: 'false_1@c.us_H8', fromMe: false }, type: 'chat', body: '   ', from: '1@c.us' },
    { id: { _serialized: 'true_1@c.us_H9', fromMe: true }, type: 'chat', body: 'Sent by me', fromMe: true, from: '1@c.us' },
    { id: { _serialized: 'false_1@c.us_H10', fromMe: false }, type: 'chat', body: 'Hidden notif', isNotification: true, from: '1@c.us' },
    { id: { _serialized: 'false_1@c.us_H11', fromMe: false }, type: 'chat', body: 'Not unread type', isUnreadType: false, from: '1@c.us' },
    { id: { _serialized: 'false_status@broadcast_H12', fromMe: false }, type: 'chat', body: 'Status', chatId: 'status@broadcast' },
    { id: { _serialized: 'false_123@newsletter_H13', fromMe: false }, type: 'chat', body: 'Newsletter', chatId: '123@newsletter' },
];
for (const m of hiddenMsgs) {
    assert.strictEqual(isVisibleUserMessageInBrowser(m), false, 'Expected hidden for ' + m.id._serialized);
}

// 4. getVisibleUnreadMessagesInBrowser filters out chats whose only unread item is revoked/system
global.window = {
    WPP: {
        whatsapp: { enums: { SYSTEM_MESSAGE_TYPES: ['gp2', 'e2e_notification'] } },
        chat: {
            list: async () => [
                { id: '1@c.us', unreadCount: 1 },
                { id: '2@g.us', unreadCount: 2 },
                { id: '999@newsletter', unreadCount: 3 }
            ],
            getMessages: async (cid) => {
                if (cid === '1@c.us') {
                    return [{ id: { _serialized: 'false_1@c.us_REV1', id: 'REV1', fromMe: false }, type: 'revoked', chatId: '1@c.us' }];
                }
                if (cid === '2@g.us') {
                    return [
                        { id: { _serialized: 'false_2@g.us_SYS1', id: 'SYS1', fromMe: false }, type: 'gp2', chatId: '2@g.us' },
                        { id: { _serialized: 'false_2@g.us_REAL1', id: 'REAL1', fromMe: false }, type: 'chat', body: 'Hey team', chatId: '2@g.us' }
                    ];
                }
                return [];
            }
        }
    }
};

(async () => {
    const items = await getVisibleUnreadMessagesInBrowser();
    assert.strictEqual(items.length, 1);
    assert.strictEqual(items[0].msg.id.id, 'REAL1');
    console.log(JSON.stringify({ ok: true, count: items.length }));
})().catch(err => {
    console.error(err);
    process.exit(1);
});
"""
    env = dict(os.environ, HELPER_CODE=helper_code)
    proc = subprocess.run([node_bin, "-e", node_script], capture_output=True, text=True, env=env, timeout=15)
    assert proc.returncode == 0, f"Node helper test failed: {proc.stderr}\n{proc.stdout}"
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    assert data == {"ok": True, "count": 1}


def test_mention_triggers_pulse_after_configured_delay_via_daemon(pulse_env):
    import threading
    from core.whatsapp_daemon import WhatsAppDaemon

    orch, sm, engine = pulse_env
    sm.set("core.auto-pulse.pulse-all-dms", False)
    sm.set("core.auto-pulse.pulse-all-groups", False)
    sm.set("core.auto-pulse.group-whitelist", [])
    sm.set("core.auto-pulse.pulse-on-mentions", True)
    sm.set("core.auto-pulse.buffer-seconds", 0.2)
    sm.save()

    fired_event = threading.Event()
    fired_times = []
    pulses_emitted = []
    checker_calls = []

    engine.whatsapp_visible_unread_checker = lambda **kw: (checker_calls.append(kw) or 1)
    engine.trigger_pulse.connect(
        lambda p, purpose=None: (
            fired_times.append(time.monotonic()),
            pulses_emitted.append((p, purpose)),
            fired_event.set(),
        )
    )

    daemon = WhatsAppDaemon(agent_id=orch.agent_id)
    daemon.message_callback = engine.handle_whatsapp_message

    # 1. Mention in unwhitelisted group triggers after the configured delay
    t0 = time.monotonic()
    daemon._parse_message_line(
        '[MSG_RECEIVED_JSON] {"chatId": "120363024851923214@g.us", "rawChatId": "120363024851923214@g.us", '
        '"senderName": "Alice", "isGroup": true, "groupName": "+1 Intelligence", "mentioned": true, '
        '"mentionReason": "jid", "msgId": "false_120363024851923214@g.us_3EB0MENTION1_196@lid", '
        '"msgStanzaId": "3EB0MENTION1", "msgType": "chat", "subtype": ""}'
    )
    assert engine.whatsapp_timer is not None
    # Must NOT fire immediately before the configured delay
    assert not fired_event.wait(timeout=0.08)
    assert len(pulses_emitted) == 0

    # Must fire once the configured delay elapses
    assert fired_event.wait(timeout=1.5)
    elapsed = fired_times[0] - t0
    assert elapsed >= 0.18, f"Pulse fired too early ({elapsed:.3f}s < 0.18s)"
    assert len(pulses_emitted) == 1
    assert pulses_emitted[0][1] == "WhatsApp message received"
    assert "+1 Intelligence" not in pulses_emitted[0][0]
    assert "Alice" not in pulses_emitted[0][0]
    assert len(checker_calls) == 1
    assert checker_calls[0]["stanza_ids"] == ["3EB0MENTION1"]
    assert checker_calls[0]["chat_ids"] == ["120363024851923214@g.us"]

    # 2. When pulse-on-mentions is disabled, the same @mention does NOT trigger a pulse
    sm.set("core.auto-pulse.pulse-on-mentions", False)
    sm.save()
    daemon._parse_message_line(
        '[MSG_RECEIVED_JSON] {"chatId": "120363024851923214@g.us", "rawChatId": "120363024851923214@g.us", '
        '"senderName": "Alice", "isGroup": true, "groupName": "+1 Intelligence", "mentioned": true, '
        '"mentionReason": "jid", "msgId": "false_120363024851923214@g.us_3EB0MENTION2_196@lid", '
        '"msgStanzaId": "3EB0MENTION2", "msgType": "chat", "subtype": ""}'
    )
    assert engine.whatsapp_timer is None


def test_mention_media_message_and_zero_delay(pulse_env):
    import threading

    orch, sm, engine = pulse_env
    sm.set("core.auto-pulse.pulse-on-mentions", True)
    sm.set("core.auto-pulse.buffer-seconds", 0)
    sm.save()

    fired_event = threading.Event()
    pulses_emitted = []
    engine.whatsapp_visible_unread_checker = lambda **kw: 1
    engine.trigger_pulse.connect(lambda p, purpose=None: (pulses_emitted.append((p, purpose)), fired_event.set()))

    engine.handle_whatsapp_message(
        "120363024851923214@g.us",
        "M",
        is_group=True,
        group_name="+1 Intelligence",
        mentioned=True,
        mention_reason="text_handle",
        msg_id="false_120363024851923214@g.us_VID01_141@lid",
        msg_stanza_id="VID01",
        msg_type="video",
    )
    assert fired_event.wait(timeout=1.0)
    assert len(pulses_emitted) == 1
    assert pulses_emitted[0][1] == "WhatsApp message received"


def test_js_browser_mention_helper_in_node():
    import json
    import os
    import re
    import shutil
    import subprocess

    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node binary not available")

    server_js_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "src", "tools", "whatsapp_node", "server.js")
    )
    with open(server_js_path, "r", encoding="utf-8") as f:
        src = f.read()

    match = re.search(
        r"(const browserMentionHelper = `[\s\S]*?`;)\s*\n\s*const browserMessageVisibilityHelper",
        src,
    )
    assert match is not None, "Could not locate browserMentionHelper in server.js"
    helper_decl = match.group(1)

    node_script = r"""
const assert = require('assert');
const helperStr = (new Function(process.env.HELPER_DECL + '; return browserMentionHelper;'))();
eval(helperStr);

(async () => {
    // 1. JID normalization (device suffixes, @s.whatsapp.net, object without _serialized)
    assert.strictEqual(normalizeJidInBrowser('262320127250573:0@lid'), '262320127250573@lid');
    assert.strictEqual(normalizeJidInBrowser('+27606376925:14@s.whatsapp.net'), '27606376925@c.us');
    assert.strictEqual(normalizeJidInBrowser({ user: '262320127250573', server: 'lid' }), '262320127250573@lid');
    assert.strictEqual(normalizeJidInBrowser({ _serialized: '27606376925@c.us' }), '27606376925@c.us');
    assert.strictEqual(normalizeJidInBrowser(null), '');

    const myPnWid = { _serialized: '27606376925@c.us', user: '27606376925', server: 'c.us' };
    const myLidWid = { _serialized: '262320127250573@lid', user: '262320127250573', server: 'lid' };

    // 2. Simulate broken WPP.conn.getMyUserLid() (WA-JS 4.7.2 getMeLidUserOrThrow removal!)
    //    while UserPrefs.getMaybeMeLidUser() and getPnLidEntry() are available.
    global.window = {
        WPP: {
            conn: {
                getMyUserId: () => myPnWid,
                getMyUserLid: () => { throw new TypeError('n.UserPrefs.getMeLidUserOrThrow is not a function'); }
            },
            whatsapp: {
                UserPrefs: {
                    getMaybeMeUser: () => myPnWid,
                    getMaybeMeLidUser: () => myLidWid
                },
                Conn: { pushname: '@AmityAI' },
                MsgStore: {
                    getModelsArray: () => [
                        {
                            id: { id: 'MY_SENT_STANZA_1', fromMe: true, remote: { _serialized: '120363@g.us' } },
                            chatId: { _serialized: '120363@g.us' },
                            fromMe: true
                        }
                    ]
                }
            },
            contact: {
                getPnLidEntry: async (jid) => {
                    if (String(jid).includes('27606376925') || String(jid).includes('262320127250573')) {
                        return { lid: myLidWid, phoneNumber: myPnWid };
                    }
                    return null;
                }
            }
        }
    };

    // 2a. LID mention detected even when WPP.conn.getMyUserLid() throws
    let r = await detectMessageMentionInBrowser({
        type: 'chat',
        body: '@262320127250573 What do you think?',
        mentionedJidList: [{ _serialized: '262320127250573@lid' }]
    }, 'Amity');
    assert.strictEqual(r.mentioned, true);
    assert.strictEqual(r.reason, 'jid');

    // 2b. LID mention with device suffix and plain {user, server} object
    r = await detectMessageMentionInBrowser({
        type: 'chat',
        body: '@262320127250573 hi',
        mentionedJidList: [{ user: '262320127250573:0', server: 'lid' }]
    }, 'Amity');
    assert.strictEqual(r.mentioned, true);

    // 2c. @s.whatsapp.net phone JID mention
    r = await detectMessageMentionInBrowser({
        type: 'chat',
        body: 'ping',
        mentionedJidList: ['27606376925@s.whatsapp.net']
    }, 'Amity');
    assert.strictEqual(r.mentioned, true);

    // 2d. Mention stored inside _data.mentionedJidList or Set
    r = await detectMessageMentionInBrowser({
        type: 'chat',
        body: 'ping',
        _data: { mentionedJidList: new Set(['262320127250573@lid']) }
    }, 'Amity');
    assert.strictEqual(r.mentioned, true);

    // 2e. Native msg.isMentioned(wid)
    r = await detectMessageMentionInBrowser({
        type: 'chat',
        body: 'ping',
        isMentioned: (wid) => wid === myLidWid
    }, 'Amity');
    assert.strictEqual(r.mentioned, true);
    assert.strictEqual(r.reason, 'native_is_mentioned');

    // 2f. Group-wide @everyone protocol mention (nonJidMentions bit 1)
    r = await detectMessageMentionInBrowser({
        type: 'chat',
        body: '@everyone meeting in 5',
        nonJidMentions: 1
    }, 'Amity');
    assert.strictEqual(r.mentioned, true);
    assert.strictEqual(r.reason, 'mention_all');

    // 2g. Quoted reply in DM without quotedParticipant (_quotedMsgObj.fromMe = true)
    r = await detectMessageMentionInBrowser({
        type: 'chat',
        body: 'Thanks!',
        _quotedMsgObj: { fromMe: true }
    }, 'Amity');
    assert.strictEqual(r.mentioned, true);
    assert.strictEqual(r.reason, 'quoted_from_me');

    // 2h. Quoted reply in LID group when WA-JS failed to hydrate _quotedMsgObj (stanza lookup)
    r = await detectMessageMentionInBrowser({
        type: 'chat',
        chatId: { _serialized: '120363@g.us' },
        body: 'Following up on this',
        quotedStanzaID: 'MY_SENT_STANZA_1'
    }, 'Amity');
    assert.strictEqual(r.mentioned, true);
    assert.strictEqual(r.reason, 'quoted_stanza');

    // 2i. Text @<lid> fallback when mentionedJidList is empty
    r = await detectMessageMentionInBrowser({
        type: 'chat',
        body: 'Hey @262320127250573 check this',
        mentionedJidList: []
    }, 'Amity');
    assert.strictEqual(r.mentioned, true);
    assert.strictEqual(r.reason, 'text_id');

    // 2j. Media caption @Amity and @AmityAI mention
    r = await detectMessageMentionInBrowser({
        type: 'video',
        isMedia: true,
        body: '/9j/4AAQbase64thumbnail',
        caption: 'Look at this @AmityAI!'
    }, 'Amity');
    assert.strictEqual(r.mentioned, true);
    assert.strictEqual(r.reason, 'text_handle');

    r = await detectMessageMentionInBrowser({
        type: 'chat',
        body: 'Hey @amity, thoughts?'
    }, 'Amity');
    assert.strictEqual(r.mentioned, true);
    assert.strictEqual(r.reason, 'text_handle');

    // 2k. Media base64 body MUST NOT trigger false positive when caption has no mention
    r = await detectMessageMentionInBrowser({
        type: 'image',
        isMedia: true,
        body: 'base64 @Amity fake body',
        caption: 'Nice sunset'
    }, 'Amity');
    assert.strictEqual(r.mentioned, false);

    // 3. LID-only mention when UserPrefs has NO LID getter at all -> resolved solely via getPnLidEntry
    global.window.WPP.whatsapp.UserPrefs = { getMaybeMeUser: () => myPnWid };
    r = await detectMessageMentionInBrowser({
        type: 'chat',
        body: '@262320127250573 ping',
        mentionedJidList: ['262320127250573@lid']
    }, 'Amity');
    assert.strictEqual(r.mentioned, true);

    // 4. Negative cases — must NOT false-trigger
    const nonMentions = [
        { type: 'chat', body: 'Hey Amity, how are you?' },
        { type: 'chat', body: 'Contact us at info@amity.com please' },
        { type: 'chat', body: 'Check https://example.com/@amity for details' },
        { type: 'chat', body: 'Ask @AmityBot instead' },
        { type: 'chat', body: '@999999999999 check this', mentionedJidList: ['999999999999@lid'] },
        { type: 'chat', body: 'Replying to Bob', quotedParticipant: '999999999999@lid', quotedMsg: { fromMe: false } },
    ];
    for (const nm of nonMentions) {
        const nr = await detectMessageMentionInBrowser(nm, 'Amity');
        assert.strictEqual(nr.mentioned, false, 'Expected NOT mentioned for: ' + JSON.stringify(nm) + ' got ' + JSON.stringify(nr));
    }

    console.log(JSON.stringify({ ok: true }));
})().catch(err => {
    console.error(err);
    process.exit(1);
});
"""
    env = dict(os.environ, HELPER_DECL=helper_decl)
    proc = subprocess.run([node_bin, "-e", node_script], capture_output=True, text=True, env=env, timeout=15)
    assert proc.returncode == 0, f"Node mention helper test failed: {proc.stderr}\n{proc.stdout}"
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    assert data == {"ok": True}


def test_js_count_visible_unread_tail_scan_in_node():
    import json
    import os
    import re
    import shutil
    import subprocess

    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node binary not available")

    server_js_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "src", "tools", "whatsapp_node", "server.js")
    )
    with open(server_js_path, "r", encoding="utf-8") as f:
        src = f.read()

    match = re.search(
        r"const browserMessageVisibilityHelper = `([\s\S]*?)`;\s*\n\s*async function ensureListenersAttached",
        src,
    )
    assert match is not None, "Could not locate browserMessageVisibilityHelper in server.js"
    helper_code = match.group(1)

    node_script = r"""
const assert = require('assert');
eval(process.env.HELPER_CODE);

(async () => {
    const groupId = '120363024851923214@g.us';
    const msgOldRead = { id: { _serialized: 'false_' + groupId + '_OLD0', id: 'OLD0', fromMe: false }, type: 'chat', body: 'earlier', chatId: groupId };
    const msgMention = { id: { _serialized: 'false_' + groupId + '_MENTION1', id: 'MENTION1', fromMe: false }, type: 'chat', body: '@262320127250573 hi', chatId: groupId };
    const msgRevokedAfter = { id: { _serialized: 'false_' + groupId + '_REV2', id: 'REV2', fromMe: false }, type: 'revoked', chatId: groupId };
    const msgSysAfter = { id: { _serialized: 'false_' + groupId + '_SYS3', id: 'SYS3', fromMe: false }, type: 'gp2', chatId: groupId };
    const msgNewVisible = { id: { _serialized: 'false_' + groupId + '_NEW4', id: 'NEW4', fromMe: false }, type: 'chat', body: 'later msg', chatId: groupId };

    // Case 1: Mention M1 is unread (unreadCount=1), but a later revoked message occupies the
    // raw onlyUnread slice. Tail scan must still confirm M1 is visible & unread!
    global.window = {
        WPP: {
            whatsapp: { enums: { SYSTEM_MESSAGE_TYPES: ['gp2'] } },
            chat: {
                list: async () => [{ id: { _serialized: groupId }, unreadCount: 1 }],
                getMessages: async (cid, opts) => {
                    if (opts && opts.onlyUnread) {
                        return [msgRevokedAfter];
                    }
                    return [msgOldRead, msgMention, msgRevokedAfter, msgSysAfter];
                }
            }
        }
    };
    let res = await countVisibleUnreadInBrowser({
        chatIds: [groupId],
        msgIds: [msgMention.id._serialized],
        stanzaIds: ['MENTION1'],
        hasUntracked: false
    });
    assert.strictEqual(res.visibleCount, 1, 'Expected mention to survive subsequent revoked/gp2 events');

    // Case 2: Mention M1 was already read; a NEW unread message NEW4 arrived (unreadCount=1).
    // M1 is outside the 1 newest visible incoming message -> must return 0!
    global.window.WPP.chat.getMessages = async (cid, opts) => {
        if (opts && opts.onlyUnread) {
            return [msgNewVisible];
        }
        return [msgOldRead, msgMention, msgNewVisible];
    };
    res = await countVisibleUnreadInBrowser({
        chatIds: [groupId],
        msgIds: [msgMention.id._serialized],
        stanzaIds: ['MENTION1'],
        hasUntracked: false
    });
    assert.strictEqual(res.visibleCount, 0, 'Already-read mention must not trigger when only newer non-mention is unread');

    // Case 3: Mention M1 itself was revoked (with OLD0 before it) -> must return 0!
    const msgMentionRevoked = { id: { _serialized: 'false_' + groupId + '_MENTION1', id: 'MENTION1', fromMe: false }, type: 'revoked', chatId: groupId };
    global.window.WPP.chat.getMessages = async (cid, opts) => {
        if (opts && opts.onlyUnread) {
            return [msgMentionRevoked];
        }
        return [msgOldRead, msgMentionRevoked];
    };
    res = await countVisibleUnreadInBrowser({
        chatIds: [groupId],
        msgIds: [msgMention.id._serialized],
        stanzaIds: ['MENTION1'],
        hasUntracked: false
    });
    assert.strictEqual(res.visibleCount, 0, 'Revoked mention must return 0');

    // Case 4: Chat marked read (no unread chats) -> must return 0!
    global.window.WPP.chat.list = async () => [];
    res = await countVisibleUnreadInBrowser({
        chatIds: [groupId],
        msgIds: [msgMention.id._serialized],
        stanzaIds: ['MENTION1'],
        hasUntracked: false
    });
    assert.strictEqual(res.visibleCount, 0, 'Marked-read chat must return 0');

    console.log(JSON.stringify({ ok: true }));
})().catch(err => {
    console.error(err);
    process.exit(1);
});
"""
    env = dict(os.environ, HELPER_CODE=helper_code)
    proc = subprocess.run([node_bin, "-e", node_script], capture_output=True, text=True, env=env, timeout=15)
    assert proc.returncode == 0, f"Node visible unread tail-scan test failed: {proc.stderr}\n{proc.stdout}"
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    assert data == {"ok": True}


def test_get_visible_unread_tail_recovery_parity_and_pause_cancels_wa_timer(pulse_env):
    """Regression: GET /unread recovers displaced tracked unread messages without returning old read messages,
    and pausing cancels any pending WhatsApp debounce timer."""
    import json
    import os
    import re
    import shutil
    import subprocess

    node_bin = shutil.which("node")
    assert node_bin is not None, "node binary required to validate WA-JS unread tail recovery"

    server_js_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "src", "tools", "whatsapp_node", "server.js")
    )
    with open(server_js_path, "r", encoding="utf-8") as f:
        src = f.read()

    match = re.search(
        r"const browserMessageVisibilityHelper = `([\s\S]*?)`;\s*\n\s*async function ensureListenersAttached",
        src,
    )
    assert match is not None
    helper_code = match.group(1)

    node_script = r"""
const assert = require('assert');
eval(process.env.HELPER_CODE);

(async () => {
    const groupId = '120363024851923214@g.us';
    const msgOldRead = { id: { _serialized: 'false_' + groupId + '_OLD0', id: 'OLD0', fromMe: false }, type: 'chat', body: 'earlier', chatId: groupId };
    const msgMention = { id: { _serialized: 'false_' + groupId + '_MENTION1', id: 'MENTION1', fromMe: false }, type: 'chat', body: '@bot hi', chatId: groupId };
    const msgRevokedAfter = { id: { _serialized: 'false_' + groupId + '_REV2', id: 'REV2', fromMe: false }, type: 'revoked', chatId: groupId };
    const msgSysAfter = { id: { _serialized: 'false_' + groupId + '_SYS3', id: 'SYS3', fromMe: false }, type: 'gp2', chatId: groupId };

    global.window = {
        WPP: {
            whatsapp: { enums: { SYSTEM_MESSAGE_TYPES: ['gp2'] } },
            chat: {
                list: async () => [{ id: { _serialized: groupId }, unreadCount: 1 }],
                getMessages: async (cid, opts) => {
                    if (opts && opts.onlyUnread) {
                        return [msgRevokedAfter];
                    }
                    return [msgOldRead, msgMention, msgRevokedAfter, msgSysAfter];
                }
            }
        }
    };

    // Track MENTION1 as a live incoming unread message; REV2 later arrives and displaces it
    recordTrackedVisibleUnreadInBrowser([groupId], msgMention.id._serialized, 'MENTION1');
    let items = await getVisibleUnreadMessagesInBrowser();
    assert.strictEqual(items.length, 1, 'Expected GET /unread to recover displaced tracked unread message');
    assert.strictEqual(items[0].msg.id.id, 'MENTION1');

    // Now revoke MENTION1 itself: must NOT resurrect OLD0 even if unreadCount lags at 1
    removeTrackedVisibleUnreadInBrowser(msgMention.id._serialized, 'MENTION1');
    const msgMentionRevoked = { id: { _serialized: 'false_' + groupId + '_MENTION1', id: 'MENTION1', fromMe: false }, type: 'revoked', chatId: groupId };
    global.window.WPP.chat.getMessages = async (cid, opts) => {
        if (opts && opts.onlyUnread) {
            return [msgMentionRevoked];
        }
        return [msgOldRead, msgMentionRevoked];
    };
    items = await getVisibleUnreadMessagesInBrowser();
    assert.strictEqual(items.length, 0, 'Must not return already-read OLD0 after unread message is revoked');

    console.log(JSON.stringify({ ok: true }));
})().catch(err => {
    console.error(err);
    process.exit(1);
});
"""
    env = dict(os.environ, HELPER_CODE=helper_code)
    proc = subprocess.run([node_bin, "-e", node_script], capture_output=True, text=True, env=env, timeout=15)
    assert proc.returncode == 0, f"Node GET /unread parity test failed: {proc.stderr}\n{proc.stdout}"
    assert json.loads(proc.stdout.strip().splitlines()[-1]) == {"ok": True}

    # Pause symmetry: pausing agent cancels pending WhatsApp pulse timer and clears pending batch
    orch, sm, engine = pulse_env
    sm.set("core.auto-pulse.pulse-all-dms", True)
    sm.set("core.auto-pulse.buffer-seconds", 10)
    sm.save()

    engine.handle_whatsapp_message(
        "27821234567@c.us",
        "Alice",
        is_group=False,
        msg_id="false_27821234567@c.us_PAUSE1",
        msg_stanza_id="PAUSE1",
        msg_type="chat",
    )
    assert engine.whatsapp_timer is not None
    assert len(engine._pending_whatsapp_messages) == 1

    engine.notify_paused()
    assert engine.whatsapp_timer is None
    assert engine._pending_whatsapp_messages == []
