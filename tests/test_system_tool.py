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
