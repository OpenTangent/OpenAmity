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
