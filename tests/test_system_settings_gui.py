import os
import sys
import json
import tempfile
import pytest
from unittest.mock import MagicMock

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PySide6.QtWidgets import QApplication
from gui.settings_panel import SettingsPanelWidget
from core.config_manager import ConfigManager
from core.settings_manager import SettingsManager


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def temp_env(monkeypatch):
    temp_dir = tempfile.mkdtemp(prefix="openamity_sys_settings_test_")
    config_file = os.path.join(temp_dir, "config.json")
    agent_dir = os.path.join(temp_dir, "agents", "test_agent")
    os.makedirs(agent_dir, exist_ok=True)
    settings_file = os.path.join(agent_dir, "settings.json")
    env_file = os.path.join(agent_dir, ".env")

    monkeypatch.setattr("config.paths.get_app_data_dir", lambda: temp_dir)
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: os.path.join(temp_dir, "agents", aid) if aid else os.path.join(temp_dir, "agents", "default"))
    monkeypatch.setattr("config.paths.get_config_file", lambda: config_file)

    yield {
        "temp_dir": temp_dir,
        "config_file": config_file,
        "agent_dir": agent_dir,
        "settings_file": settings_file,
        "env_file": env_file
    }

    import shutil
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_settings_panel_populates_system_config_on_init(qapp, temp_env):
    # Pre-write custom config to disk
    with open(temp_env["config_file"], "w") as f:
        json.dump({
            "user-full-name": "Dr. Amity User",
            "user-phone-number": "+27831234567",
            "user-email": "amity.user@example.com",
            "backup-location": "/custom/backup/path"
        }, f)

    # Initialize ConfigManager with custom path
    cm = ConfigManager(config_file=temp_env["config_file"])

    # Instantiate SettingsPanelWidget and attach our config
    panel = SettingsPanelWidget()
    panel.config = cm
    panel.load_system_config()

    # Verify GUI is populated immediately
    assert panel.ui_user_full_name.text() == "Dr. Amity User"
    assert panel.ui_user_phone_number.text() == "+27831234567"
    assert panel.ui_user_email.text() == "amity.user@example.com"
    assert panel.ui_backup_location.text() == "/custom/backup/path"


def test_settings_panel_save_system_config(qapp, temp_env):
    cm = ConfigManager(config_file=temp_env["config_file"])
    panel = SettingsPanelWidget()
    panel.config = cm
    panel.load_system_config()

    # User modifies values in the GUI
    panel.ui_user_full_name.setText("Updated Full Name")
    panel.ui_user_phone_number.setText("+15559876543")
    panel.ui_user_email.setText("updated@domain.com")
    panel.ui_backup_location.setText("/new/backup/dir")

    panel.save_system_config()

    # Re-read config from disk using new ConfigManager instance
    cm2 = ConfigManager(config_file=temp_env["config_file"])
    assert cm2.get("user-full-name") == "Updated Full Name"
    assert cm2.get("user-phone-number") == "+15559876543"
    assert cm2.get("user-email") == "updated@domain.com"
    assert cm2.get("backup-location") == "/new/backup/dir"


def test_agent_settings_save_preserves_system_config(qapp, temp_env, monkeypatch):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: temp_env["agent_dir"])

    # Pre-write custom config
    with open(temp_env["config_file"], "w") as f:
        json.dump({
            "user-full-name": "Protected Name",
            "user-phone-number": "+27820000000",
            "user-email": "protected@example.com",
            "backup-location": "/protected/backups"
        }, f)

    cm = ConfigManager(config_file=temp_env["config_file"])
    sm = SettingsManager(agent_id="test_agent")

    panel = SettingsPanelWidget()
    panel.config = cm
    panel.load_settings(settings_manager=sm)

    # Edit agent name
    panel.ui_agent_name.setText("Nova Prime")
    panel.save_settings()

    # Verify agent settings saved
    assert sm.get("core.agent.name") == "Nova Prime"

    # Verify system config was preserved and not blanked out
    cm_check = ConfigManager(config_file=temp_env["config_file"])
    assert cm_check.get("user-full-name") == "Protected Name"
    assert cm_check.get("user-phone-number") == "+27820000000"
    assert cm_check.get("user-email") == "protected@example.com"
    assert cm_check.get("backup-location") == "/protected/backups"


def test_load_settings_none_guard(qapp, temp_env):
    # Ensure load_settings(None) does not crash and still loads system config
    cm = ConfigManager(config_file=temp_env["config_file"])
    cm.set("user-full-name", "Admin Guard Test")
    cm.save()

    panel = SettingsPanelWidget()
    panel.config = cm
    panel.load_settings(settings_manager=None)

    assert panel.ui_user_full_name.text() == "Admin Guard Test"
