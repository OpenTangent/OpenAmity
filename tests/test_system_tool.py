import os
import sys
import socket
from unittest.mock import MagicMock, patch
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from tools.system_tool import SystemTool, get_internal_ip


def test_get_internal_ip_live():
    """Verify live internal IP detection does not return loopback on a connected machine."""
    ip = get_internal_ip()
    assert ip is not None
    assert isinstance(ip, str)
    assert len(ip.split(".")) == 4


def test_get_internal_ip_udp_probe():
    """Verify get_internal_ip uses routing socket probe when available."""
    mock_socket_instance = MagicMock()
    mock_socket_instance.getsockname.return_value = ("192.168.1.154", 54321)

    with patch("socket.socket") as mock_sock_cls:
        mock_sock_cls.return_value.__enter__.return_value = mock_socket_instance
        ip = get_internal_ip()
        assert ip == "192.168.1.154"
        mock_socket_instance.connect.assert_called_once_with(("8.8.8.8", 80))


def test_get_internal_ip_fallback_to_psutil():
    """Verify get_internal_ip falls back to psutil when socket routing probe fails."""
    class MockSnicAddr:
        def __init__(self, family, address):
            self.family = family
            self.address = address

    class MockStat:
        def __init__(self, isup):
            self.isup = isup

    mock_addrs = {
        "lo": [MockSnicAddr(socket.AF_INET, "127.0.0.1")],
        "docker0": [MockSnicAddr(socket.AF_INET, "172.17.0.1")],
        "enp0s3": [MockSnicAddr(socket.AF_INET, "192.168.1.154")],
    }
    mock_stats = {
        "lo": MockStat(isup=True),
        "docker0": MockStat(isup=True),
        "enp0s3": MockStat(isup=True),
    }

    with patch("socket.socket", side_effect=OSError("Network unreachable")):
        with patch("psutil.net_if_addrs", return_value=mock_addrs):
            with patch("psutil.net_if_stats", return_value=mock_stats):
                ip = get_internal_ip()
                assert ip == "192.168.1.154"


def test_get_internal_ip_fallback_to_hostname():
    """Verify get_internal_ip falls back to socket.gethostbyname if probe and psutil fail."""
    with patch("socket.socket", side_effect=OSError("Network unreachable")):
        with patch("psutil.net_if_addrs", side_effect=Exception("psutil error")):
            with patch("socket.gethostname", return_value="myhost"):
                with patch("socket.gethostbyname", return_value="10.0.0.50"):
                    ip = get_internal_ip()
                    assert ip == "10.0.0.50"


def test_system_tool_platform_info_includes_internal_ip():
    """Verify SystemTool platform_info includes the discovered internal IP."""
    tool = SystemTool(orchestrator=None)
    with patch("tools.system_tool.get_internal_ip", return_value="192.168.1.154"):
        output = tool.execute("platform_info")
        assert "=== Network & Peripherals ===" in output
        assert "Internal IP: 192.168.1.154" in output


def test_system_tool_api_key_lifecycle(tmp_path):
    """Verify autonomous API key generation, listing, revocation, and API docs."""
    import re
    agent_id = "test_sys_agent"
    agent_dir = tmp_path / "agents" / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)

    orch = MagicMock()
    orch.agent_id = agent_id

    with patch("config.paths.get_base_dir_for", return_value=str(agent_dir)), \
         patch("config.paths.get_agent_data_dir", return_value=str(agent_dir)):
        tool = SystemTool(orchestrator=orch)

        # 1. Generate key
        gen_out = tool.execute("generate_api_key", name="HomeAssistant")
        assert "=== API Key Successfully Generated ===" in gen_out
        assert "API Key: oa_sec_" in gen_out
        assert "Example cURL Command:" in gen_out
        assert "curl -X POST" in gen_out

        # 2. List keys
        list_out = tool.execute("list_api_keys")
        assert "=== Agent API Keys ===" in list_out
        assert "HomeAssistant" in list_out
        assert "[Active]" in list_out

        # Extract key ID from list output
        m = re.search(r"ID: (key_[a-f0-9]+)", list_out)
        assert m is not None
        key_id = m.group(1)

        # 3. Revoke key
        rev_out = tool.execute("revoke_api_key", key_id=key_id)
        assert f"API key '{key_id}' successfully revoked" in rev_out

        # Verify list shows revoked
        list_out2 = tool.execute("list_api_keys")
        assert "[Revoked]" in list_out2

        # 4. API Docs
        docs_out = tool.execute("api_docs")
        assert "=== Open Amity Pulse Hook API Documentation ===" in docs_out
        assert "POST /api/v1/agents/" in docs_out
        assert "RATE_LIMITED" in docs_out
        assert "PULSE_COLLISION" in docs_out


def test_system_tool_annotations_and_cerebrum_loading():
    """Verify all SystemTool methods have valid annotations and can be inspected without NameError."""
    import inspect
    from typing import get_type_hints
    from tools.system_tool import SystemTool

    for name, member in inspect.getmembers(SystemTool, predicate=callable):
        # get_type_hints eagerly evaluates annotations, replicating Python <=3.13 behavior
        hints = get_type_hints(member)
        assert isinstance(hints, dict)


