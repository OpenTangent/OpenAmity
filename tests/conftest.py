import os
import socket
import tempfile
import pytest


_original_socket_connect = socket.socket.connect


@pytest.fixture(autouse=True, scope="session")
def block_external_network():
    """Strict safety guard: prevents tests from making external network or API calls.
    Only loopback connections (127.0.0.1, localhost, ::1) and local IPC are allowed.
    Any attempt to reach external services (e.g. Google, Anthropic, OpenAI, OpenRouter,
    or external hosts) raises RuntimeError immediately, guaranteeing zero test token cost.
    """
    def guarded_connect(self, address):
        if isinstance(address, tuple) and len(address) >= 2:
            host = str(address[0])
            allowed_hosts = ("127.0.0.1", "localhost", "::1", "0.0.0.0")
            if host not in allowed_hosts and not host.startswith("127."):
                raise RuntimeError(
                    f"External network call forbidden during test execution: connection to {address} blocked. "
                    "Ensure all API and external network calls are mocked."
                )
        return _original_socket_connect(self, address)

    socket.socket.connect = guarded_connect
    yield
    socket.socket.connect = _original_socket_connect


@pytest.fixture(autouse=True, scope="session")
def isolate_xdg_data_for_tests():
    """Safety guard: isolates XDG_DATA_HOME for pytest sessions to ensure tests never
    accidentally create or modify files in the real application data directory (~/.var/app/com.openamity.OpenAmity/data).
    """
    temp_xdg = tempfile.mkdtemp(prefix="openamity_pytest_data_")
    old_xdg = os.environ.get("XDG_DATA_HOME")
    os.environ["XDG_DATA_HOME"] = temp_xdg

    yield temp_xdg

    if old_xdg is not None:
        os.environ["XDG_DATA_HOME"] = old_xdg
    else:
        os.environ.pop("XDG_DATA_HOME", None)

    import shutil
    shutil.rmtree(temp_xdg, ignore_errors=True)


@pytest.fixture(autouse=True)
def cleanup_qt_state():
    """Ensures Qt widgets and active timers are closed after each test to prevent thread and memory leaks."""
    yield
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.closeAllWindows()
            app.processEvents()
    except (ImportError, RuntimeError):
        pass


@pytest.fixture(scope="session")
def qapp():
    """Shared session-scoped QApplication to avoid costly repeated theme and font setups."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtWidgets import QApplication
    from gui.theme import setup_app_theme
    app = QApplication.instance()
    if app is None:
        app = QApplication(["-platform", "offscreen"])
        setup_app_theme(app)
    # Existing QApplication may already own widgets from module fixtures.
    # Replacing its proxy style here can hang Qt during global repolish.
    return app



@pytest.fixture(autouse=True, scope="session")
def initialise_shared_qt_theme():
    """Establish style before module qapp fixtures can populate the app.

    Replacing a live application's proxy style mid-suite can hang Qt. A single
    early theme application preserves the style contract without repolish.
    """
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtWidgets import QApplication
    from gui.theme import setup_app_theme
    app = QApplication.instance()
    if app is None:
        app = QApplication(["-platform", "offscreen"])
    setup_app_theme(app)
    yield app
