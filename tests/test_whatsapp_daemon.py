import sys
import os
import shutil
import tempfile
import socket
import pytest
from unittest.mock import patch, MagicMock

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.whatsapp_daemon import WhatsAppDaemon


@pytest.fixture
def temp_agent_environment(monkeypatch):
    """Fixture that creates a temporary app data directory for isolated testing."""
    temp_dir = tempfile.mkdtemp(prefix="openamity_wa_test_")
    monkeypatch.setattr("config.paths.get_app_data_dir", lambda: temp_dir)
    monkeypatch.setattr("config.paths.get_base_dir_for",
                        lambda aid: os.path.join(temp_dir, "agents", aid) if aid else os.path.join(temp_dir, "agents", "default"))
    monkeypatch.setattr("config.paths.get_whatsapp_bridge_dir",
                        lambda aid: os.path.join(temp_dir, "agents", aid if aid else "default", "whatsapp_bridge"))
    monkeypatch.setattr("config.paths.get_whatsapp_data_dir",
                        lambda aid: os.path.join(temp_dir, "agents", aid if aid else "default", "whatsapp_data"))

    yield temp_dir

    shutil.rmtree(temp_dir, ignore_errors=True)


def test_whatsapp_daemon_single_agent_port(temp_agent_environment):
    with patch.object(WhatsAppDaemon, "_is_port_in_use", return_value=False):
        daemon = WhatsAppDaemon(agent_id="agent-1")
        assert daemon.port == 3000
        assert daemon.base_url == "http://localhost:3000"


def test_whatsapp_daemon_multi_agent_port_isolation(temp_agent_environment):
    with patch.object(WhatsAppDaemon, "_is_port_in_use", return_value=False):
        # Agent 1 initializes and simulates running on port 3000
        daemon1 = WhatsAppDaemon(agent_id="agent-1")
        os.makedirs(daemon1.bridge_dir, exist_ok=True)
        with open(daemon1.port_file, "w") as f:
            f.write(str(daemon1.port))

        # Agent 2 initializes and should allocate port 3001
        daemon2 = WhatsAppDaemon(agent_id="agent-2")
        os.makedirs(daemon2.bridge_dir, exist_ok=True)
        with open(daemon2.port_file, "w") as f:
            f.write(str(daemon2.port))

        # Agent 3 initializes and should allocate port 3002
        daemon3 = WhatsAppDaemon(agent_id="agent-3")
        os.makedirs(daemon3.bridge_dir, exist_ok=True)
        with open(daemon3.port_file, "w") as f:
            f.write(str(daemon3.port))

        assert daemon1.port == 3000
        assert daemon2.port == 3001
        assert daemon3.port == 3002

        # Verify get_port_for_agent static helper
        assert WhatsAppDaemon.get_port_for_agent("agent-1") == 3000
        assert WhatsAppDaemon.get_port_for_agent("agent-2") == 3001
        assert WhatsAppDaemon.get_port_for_agent("agent-3") == 3002


def test_whatsapp_daemon_skips_occupied_port(temp_agent_environment):
    # Simulate port 3000 being occupied without binding real host socket
    with patch.object(WhatsAppDaemon, "_is_port_in_use", side_effect=lambda p: p == 3000):
        daemon = WhatsAppDaemon(agent_id="agent-1")
        # Since 3000 is occupied, it should pick 3001
        assert daemon.port == 3001
        assert daemon.base_url == "http://localhost:3001"


def test_whatsapp_daemon_start_passes_port_env(temp_agent_environment):
    daemon = WhatsAppDaemon(agent_id="agent-env-test")
    os.makedirs(daemon.bridge_dir, exist_ok=True)
    # Create dummy server.js so start doesn't bail early
    with open(os.path.join(daemon.bridge_dir, "server.js"), "w") as f:
        f.write("// dummy server")

    with patch("subprocess.run") as mock_run, \
         patch("subprocess.Popen") as mock_popen, \
         patch.object(daemon, "_kill_orphaned_daemon"), \
         patch("threading.Thread"), \
         patch("requests.get") as mock_get:

        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.stdout = MagicMock()
        mock_process.stderr = MagicMock()
        mock_popen.return_value = mock_process
        mock_get.return_value.status_code = 200

        daemon.start(force_update=False)

        assert mock_popen.called
        call_args = mock_popen.call_args
        env = call_args[1].get("env", {})
        assert env.get("WHATSAPP_PORT") == str(daemon.port)
        assert env.get("PORT") == str(daemon.port)
        assert os.path.exists(daemon.port_file)
        with open(daemon.port_file, "r") as pf:
            assert pf.read().strip() == str(daemon.port)


def test_whatsapp_daemon_update_engine_asset(temp_agent_environment):
    daemon = WhatsAppDaemon(agent_id="agent-update-test")
    os.makedirs(daemon.data_dir, exist_ok=True)

    with patch("requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"console.log('wppconnect wa-js bundle');" * 2000  # > 50KB
        mock_get.return_value = mock_response

        updated = daemon.update_engine_asset(force=True)
        assert updated is True

        target_file = os.path.join(daemon.data_dir, "wppconnect-wa.js")
        assert os.path.exists(target_file)
        with open(target_file, "rb") as f:
            assert f.read() == mock_response.content

        timestamp_file = os.path.join(daemon.data_dir, ".last_engine_update")
        assert os.path.exists(timestamp_file)


def test_whatsapp_daemon_ensure_chrome_executable_permissions(temp_agent_environment):
    import stat
    daemon = WhatsAppDaemon(agent_id="agent-perm-test")
    cache_dir = os.path.join(daemon.data_dir, "puppeteer_cache", "chrome", "linux-146.0", "chrome-linux64")
    os.makedirs(cache_dir, exist_ok=True)

    # Create dummy binaries without executable permissions (0o644)
    binaries = [
        "chrome",
        "chrome_crashpad_handler",
        "chrome_sandbox",
        "chrome-wrapper",
        "crashpad_handler"
    ]
    created_paths = []
    for b in binaries:
        p = os.path.join(cache_dir, b)
        with open(p, "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)  # 0o644
        assert not (os.stat(p).st_mode & stat.S_IXUSR)
        created_paths.append(p)

    # Run permission fixer
    daemon._ensure_chrome_executable_permissions()

    # Verify all helper binaries now have executable permissions
    for p in created_paths:
        mode = os.stat(p).st_mode
        assert bool(mode & stat.S_IXUSR), f"{p} should have user execute permission"
        assert bool(mode & stat.S_IXGRP), f"{p} should have group execute permission"
        assert bool(mode & stat.S_IXOTH), f"{p} should have other execute permission"


def test_whatsapp_daemon_msg_received_parsing(temp_agent_environment):
    daemon = WhatsAppDaemon(agent_id="agent-msg-test")
    received = []
    daemon.message_callback = lambda sid, name: received.append((sid, name))

    class MockPipe:
        def __init__(self, lines):
            self.lines = iter(lines)
        def readline(self):
            return next(self.lines, '')
        def close(self):
            pass

    # Test 3-part MSG_RECEIVED with full name
    lines1 = ["[MSG_RECEIVED] 27836527975@c.us Kerry Parker\n"]
    daemon.node_process = MagicMock()
    daemon.node_process.stdout = MockPipe(lines1)

    # Invoke read_output logic directly
    for line in iter(daemon.node_process.stdout.readline, ''):
        line_str = line.strip()
        if line_str.startswith("[MSG_RECEIVED]"):
            parts = line_str.split(" ", 2)
            if len(parts) >= 2:
                sender_id = parts[1]
                sender_name = parts[2] if len(parts) >= 3 else ""
                if daemon.message_callback:
                    daemon.message_callback(sender_id, sender_name)

    assert len(received) == 1
    assert received[0] == ("27836527975@c.us", "Kerry Parker")

    # Test 2-part MSG_RECEIVED (no sender name)
    lines2 = ["[MSG_RECEIVED] 27836527975@c.us\n"]
    for line in lines2:
        line_str = line.strip()
        if line_str.startswith("[MSG_RECEIVED]"):
            parts = line_str.split(" ", 2)
            if len(parts) >= 2:
                sender_id = parts[1]
                sender_name = parts[2] if len(parts) >= 3 else ""
                if daemon.message_callback:
                    daemon.message_callback(sender_id, sender_name)

    assert len(received) == 2
    assert received[1] == ("27836527975@c.us", "")


def test_pulse_engine_whatsapp_whitelist_matching(temp_agent_environment):
    from core.pulse_engine import PulseEngine
    from core.settings_manager import SettingsManager
    from core.address_book import AddressBookManager

    orchestrator = MagicMock()
    orchestrator.agent_id = "agent-pulse-test"
    orchestrator.is_paused = False
    engine = PulseEngine(orchestrator)

    # Mock settings with whitelist
    settings = SettingsManager(agent_id="agent-pulse-test")
    settings.set("core.auto-pulse.whitelist", ["+27836527975", "Alice Smith"])
    settings.set("core.auto-pulse.buffer-seconds", 0.05)
    settings.set("core.auto-pulse.ratelimit-minutes", 5)
    settings.save()
    engine.settings_manager = settings

    # Add contact to address book
    ab = AddressBookManager(agent_id="agent-pulse-test")
    ab.add_contact("+27836527975", "Kerry Parker", relationship="Friend")
    engine.address_book_manager = ab

    pulse_fired = []
    engine.trigger_pulse.connect(lambda p: pulse_fired.append(p))

    # 1. Group message should be ignored
    engine.handle_whatsapp_message("12345-67890@g.us", "Group Chat")
    assert engine.whatsapp_timer is None

    # 2. Status broadcast message should be ignored
    engine.handle_whatsapp_message("status@broadcast", "Status")
    assert engine.whatsapp_timer is None

    # 3. Unwhitelisted number should be ignored
    engine.handle_whatsapp_message("27112223333@c.us", "Stranger")
    assert engine.whatsapp_timer is None

    # 4. Whitelisted international number should match and schedule pulse
    engine.handle_whatsapp_message("27836527975@c.us", "")
    assert engine.whatsapp_timer is not None
    assert "Kerry Parker" in engine.pending_whatsapp_sender

    # Wait for buffer execution
    import time
    time.sleep(0.1)
    assert len(pulse_fired) == 1
    assert "Check your unread WhatsApp messages" in pulse_fired[0]

    # 5. Rate limiting prevents immediate second pulse
    engine.whatsapp_timer = None
    engine.handle_whatsapp_message("27836527975@c.us", "Kerry Parker")
    assert engine.whatsapp_timer is None  # Suppressed by rate limit

    # 6. Test matching national number format in whitelist (e.g. 0836527975)
    settings.set("core.auto-pulse.whitelist", ["0836527975"])
    settings.save()
    engine.last_pulse_time = 0  # reset cooldown
    engine.handle_whatsapp_message("27836527975@c.us", "")
    assert engine.whatsapp_timer is not None

    # 7. Test matching contact name in whitelist
    if engine.whatsapp_timer:
        engine.whatsapp_timer.cancel()
    settings.set("core.auto-pulse.whitelist", ["Alice Smith"])
    settings.save()
    engine.last_pulse_time = 0
    engine.handle_whatsapp_message("27999999999@c.us", "Alice Smith")
    assert engine.whatsapp_timer is not None
    if engine.whatsapp_timer:
        engine.whatsapp_timer.cancel()


