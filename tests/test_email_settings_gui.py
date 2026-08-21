import os
import sys
import tempfile
import shutil
import pytest
from unittest.mock import MagicMock, patch

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QWheelEvent
from PySide6.QtCore import QPointF, Qt
from gui.settings_panel import (
    EMAIL_PRESETS,
    detect_provider_from_email,
    EmailConnectionTestWorker,
    SettingsPanelWidget
)
from core.settings_manager import SettingsManager


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_detect_provider_from_email():
    assert detect_provider_from_email("agent@gmail.com") == "Gmail / Google Workspace"
    assert detect_provider_from_email("agent@googlemail.com") == "Gmail / Google Workspace"
    assert detect_provider_from_email("agent@outlook.com") == "Outlook / Microsoft 365"
    assert detect_provider_from_email("agent@hotmail.com") == "Outlook / Microsoft 365"
    assert detect_provider_from_email("agent@yahoo.com") == "Yahoo Mail"
    assert detect_provider_from_email("agent@icloud.com") == "iCloud Mail"
    assert detect_provider_from_email("agent@me.com") == "iCloud Mail"
    assert detect_provider_from_email("agent@fastmail.com") == "Fastmail"
    assert detect_provider_from_email("agent@customdomain.org") == "Custom (IMAP / SMTP)"


def test_email_presets_structure():
    expected_providers = [
        "Gmail / Google Workspace",
        "Outlook / Microsoft 365",
        "Yahoo Mail",
        "iCloud Mail",
        "Fastmail",
        "Custom (IMAP / SMTP)"
    ]
    for p in expected_providers:
        assert p in EMAIL_PRESETS
        cfg = EMAIL_PRESETS[p]
        assert "imap_host" in cfg
        assert "imap_port" in cfg
        assert "imap_sec" in cfg
        assert "smtp_host" in cfg
        assert "smtp_port" in cfg
        assert "smtp_sec" in cfg


def test_settings_panel_email_widget_initialization(qapp):
    panel = SettingsPanelWidget()
    assert hasattr(panel, "ui_use_email")
    assert hasattr(panel, "ui_email_provider")
    assert hasattr(panel, "ui_email_address")
    assert hasattr(panel, "ui_email_display_name")
    assert hasattr(panel, "ui_email_auth_password_radio")
    assert hasattr(panel, "ui_email_auth_oauth_radio")
    assert hasattr(panel, "ui_email_password")
    assert hasattr(panel, "ui_email_pass_toggle_btn")
    assert hasattr(panel, "ui_email_adv_toggle_btn")
    assert hasattr(panel, "ui_email_adv_container")
    assert hasattr(panel, "ui_email_test_btn")
    assert hasattr(panel, "ui_email_test_status")

    # Wording check
    assert panel.ui_email_auth_password_radio.text() == "Password"


def test_social_cards_toggle_all_and_no_html_tags(qapp):
    panel = SettingsPanelWidget()
    # Check that all 4 social cards have integrated toggle checkboxes without <b> tags
    for cb_name in ["ui_use_email", "ui_use_whatsapp", "ui_use_moltbook", "ui_use_mastodon"]:
        cb = getattr(panel, cb_name)
        assert cb is not None
        assert "<b>" not in cb.text()
        assert "</b>" not in cb.text()
        cb.setChecked(True)
        assert cb.isChecked()
        cb.setChecked(False)
        assert not cb.isChecked()


def test_no_scroll_wheel_on_input_controls(qapp):
    from PySide6.QtWidgets import QComboBox, QSpinBox, QSlider
    from PySide6.QtCore import QPoint
    panel = SettingsPanelWidget()

    # Find combo boxes and spin boxes
    combos = panel.findChildren(QComboBox)
    assert len(combos) > 0
    for cb in combos:
        initial_idx = cb.currentIndex()
        event = QWheelEvent(
            QPointF(10, 10), QPointF(10, 10),
            QPoint(0, 0), QPoint(0, 120),
            Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False
        )
        cb.wheelEvent(event)
        # Value must not change
        assert cb.currentIndex() == initial_idx


def test_settings_panel_preset_selection(qapp):
    panel = SettingsPanelWidget()
    panel._is_loading = False

    # Switch to Outlook first
    idx_out = panel.ui_email_provider.findText("Outlook / Microsoft 365")
    assert idx_out >= 0
    panel.ui_email_provider.setCurrentIndex(idx_out)
    assert panel.ui_email_imap_host.text() == "outlook.office365.com"
    assert panel.ui_email_smtp_port.text() == "587"
    assert panel.ui_email_smtp_security.currentText() == "STARTTLS"

    # Switch to Gmail
    idx = panel.ui_email_provider.findText("Gmail / Google Workspace")
    assert idx >= 0
    panel.ui_email_provider.setCurrentIndex(idx)
    assert panel.ui_email_imap_host.text() == "imap.gmail.com"
    assert panel.ui_email_imap_port.text() == "993"
    assert panel.ui_email_imap_security.currentText() == "SSL/TLS"
    assert panel.ui_email_smtp_host.text() == "smtp.gmail.com"
    assert panel.ui_email_smtp_port.text() == "465"
    assert panel.ui_email_smtp_security.currentText() == "SSL/TLS"


def test_settings_panel_email_address_auto_detect(qapp):
    panel = SettingsPanelWidget()
    panel._is_loading = False

    # Setting email address triggers auto-detect
    panel.ui_email_address.setText("bot@yahoo.com")
    panel._on_email_address_changed()
    assert panel.ui_email_provider.currentText() == "Yahoo Mail"
    assert panel.ui_email_imap_host.text() == "imap.mail.yahoo.com"


def test_settings_panel_advanced_accordion_toggle(qapp):
    panel = SettingsPanelWidget()
    assert panel.ui_email_adv_container.isHidden()
    assert "▶" in panel.ui_email_adv_toggle_btn.text()

    panel._toggle_advanced_email_settings()
    assert not panel.ui_email_adv_container.isHidden()
    assert "▼" in panel.ui_email_adv_toggle_btn.text()

    panel._toggle_advanced_email_settings()
    assert panel.ui_email_adv_container.isHidden()
    assert "▶" in panel.ui_email_adv_toggle_btn.text()


def test_settings_panel_password_reveal_toggle(qapp):
    from PySide6.QtWidgets import QLineEdit
    panel = SettingsPanelWidget()
    assert panel.ui_email_password.echoMode() == QLineEdit.Password

    panel._toggle_email_password_visibility()
    assert panel.ui_email_password.echoMode() == QLineEdit.Normal
    assert panel.ui_email_pass_toggle_btn.text() == "🔒"

    panel._toggle_email_password_visibility()
    assert panel.ui_email_password.echoMode() == QLineEdit.Password
    assert panel.ui_email_pass_toggle_btn.text() == "👁"


def test_settings_panel_auth_type_radio_switch(qapp):
    panel = SettingsPanelWidget()
    panel.ui_email_auth_oauth_radio.setChecked(True)
    assert not panel.ui_email_oauth_container.isHidden()
    assert panel.ui_email_password_container.isHidden()

    panel.ui_email_auth_password_radio.setChecked(True)
    assert panel.ui_email_oauth_container.isHidden()
    assert not panel.ui_email_password_container.isHidden()


def test_settings_load_and_save_email_roundtrip(qapp):
    mock_settings = MagicMock()
    stored_env = {
        "EMAIL_ADDRESS": "amy@fastmail.com",
        "EMAIL_PROVIDER_PRESET": "Fastmail",
        "EMAIL_AUTH_TYPE": "password",
        "EMAIL_PASSWORD": "fastmail-app-pass",
        "EMAIL_IMAP_HOST": "imap.fastmail.com",
        "EMAIL_IMAP_PORT": "993",
        "EMAIL_IMAP_SECURITY": "SSL/TLS",
        "EMAIL_SMTP_HOST": "smtp.fastmail.com",
        "EMAIL_SMTP_PORT": "465",
        "EMAIL_SMTP_SECURITY": "SSL/TLS",
        "EMAIL_DISPLAY_NAME": "Amy Fastmail",
        "EMAIL_USERNAME": "amy@fastmail.com"
    }

    def fake_get_env(key, default=""):
        return stored_env.get(key, default)

    def fake_set_env(key, val):
        stored_env[key] = str(val)

    mock_settings.get_env.side_effect = fake_get_env
    mock_settings.set_env.side_effect = fake_set_env
    mock_settings.get.side_effect = lambda key, default=None: default

    panel = SettingsPanelWidget()
    panel.load_settings(mock_settings)

    assert panel.ui_email_address.text() == "amy@fastmail.com"
    assert panel.ui_email_provider.currentText() == "Fastmail"
    assert panel.ui_email_password.text() == "fastmail-app-pass"
    assert panel.ui_email_display_name.text() == "Amy Fastmail"

    # Modify and save
    panel.ui_email_display_name.setText("Amy Modified")
    panel.save_settings()
    assert stored_env["EMAIL_DISPLAY_NAME"] == "Amy Modified"


def test_settings_manager_per_agent_env_isolation():
    temp_dir = tempfile.mkdtemp(prefix="openamity_isolation_test_")
    try:
        with patch("config.paths.get_base_dir_for") as mock_base_dir:
            dir_a = os.path.join(temp_dir, "agent_a")
            dir_b = os.path.join(temp_dir, "agent_b")

            mock_base_dir.side_effect = lambda aid: dir_a if aid == "agent_a" else dir_b

            sm_a = SettingsManager(agent_id="agent_a")
            sm_b = SettingsManager(agent_id="agent_b")

            sm_a.set_env("EMAIL_ADDRESS", "amity@example.com")
            sm_a.set_env("EMAIL_DISPLAY_NAME", "Amity")

            # Agent B should NOT see Agent A's values
            assert sm_b.get_env("EMAIL_ADDRESS") == ""
            assert sm_b.get_env("EMAIL_DISPLAY_NAME") == ""

            # os.environ should NOT have been polluted
            assert os.environ.get("EMAIL_ADDRESS") != "amity@example.com"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_email_connection_test_worker_validation(qapp):
    results = []
    worker = EmailConnectionTestWorker(
        host_imap="",
        port_imap="993",
        sec_imap="SSL/TLS",
        host_smtp="",
        port_smtp="465",
        sec_smtp="SSL/TLS",
        username="",
        password="",
        auth_type="password"
    )
    worker.finished_signal.connect(lambda ok, msg: results.append((ok, msg)))
    worker.run()
    assert len(results) == 1
    assert results[0][0] is False
    assert "Email Address" in results[0][1]


def test_email_connection_test_worker_mock_success(qapp):
    results = []
    worker = EmailConnectionTestWorker(
        host_imap="imap.example.com",
        port_imap="993",
        sec_imap="SSL/TLS",
        host_smtp="smtp.example.com",
        port_smtp="465",
        sec_smtp="SSL/TLS",
        username="user@example.com",
        password="secretpassword",
        auth_type="password"
    )
    worker.finished_signal.connect(lambda ok, msg: results.append((ok, msg)))

    with patch("imaplib.IMAP4_SSL") as mock_imap, patch("smtplib.SMTP_SSL") as mock_smtp:
        mock_imap_instance = MagicMock()
        mock_imap.return_value = mock_imap_instance
        mock_smtp_instance = MagicMock()
        mock_smtp.return_value = mock_smtp_instance

        worker.run()

        assert len(results) == 1
        assert results[0][0] is True
        assert "connected and authenticated successfully" in results[0][1]
        mock_imap_instance.login.assert_called_once_with("user@example.com", "secretpassword")
        mock_smtp_instance.login.assert_called_once_with("user@example.com", "secretpassword")
