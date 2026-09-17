import os
import sys
import json
import tempfile
import shutil
import pytest
from unittest.mock import MagicMock

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PySide6.QtWidgets import QApplication, QLabel, QCheckBox
from gui.settings_panel import SettingsPanelWidget
from core.settings_manager import SettingsManager


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def temp_agent_env():
    temp_dir = tempfile.mkdtemp(prefix="openamity_other_settings_test_")
    agent_dir = os.path.join(temp_dir, "test_agent")
    os.makedirs(agent_dir, exist_ok=True)
    settings_file = os.path.join(agent_dir, "settings.json")

    yield {
        "temp_dir": temp_dir,
        "agent_dir": agent_dir,
        "settings_file": settings_file,
    }

    shutil.rmtree(temp_dir, ignore_errors=True)


def test_tools_section_removed_from_other_agent_settings(qapp):
    panel = SettingsPanelWidget()
    # Panel at index 5 is "Other Agent Settings"
    other_panel = panel.stack.widget(5)
    assert other_panel is not None

    # Check that ui_tool_checkboxes does not exist on the panel
    assert not hasattr(panel, "ui_tool_checkboxes")

    # Check that no label in the Other Agent Settings panel has "Tools" text
    labels = other_panel.findChildren(QLabel)
    label_texts = [lbl.text() for lbl in labels]
    assert not any("Tools" in t for t in label_texts)
    assert not any("<b>Tools</b>" in t for t in label_texts)

    # Check that tool checkboxes (e.g., Terminal) are not present in other_panel
    checkboxes = other_panel.findChildren(QCheckBox)
    cb_texts = [cb.text() for cb in checkboxes]
    assert "Terminal" not in cb_texts
    assert "Contacts" not in cb_texts
    assert "Email" not in cb_texts
    assert "WhatsApp" not in cb_texts

    # But Other Agent Settings still has its Agent Setup controls:
    assert hasattr(panel, "ui_low_token_mode")
    assert hasattr(panel, "ui_max_memories")
    assert hasattr(panel, "ui_agency_limit")
    assert hasattr(panel, "ui_wa_buffer")
    assert hasattr(panel, "ui_whatsapp_whitelist")


def test_tool_settings_load_and_save_intact(qapp):
    mock_settings = MagicMock()
    stored_settings = {
        "core.tools.email": True,
        "core.tools.whatsapp": False,
        "core.tools.moltbook": True,
        "core.tools.mastodon": False,
        "core.tools.terminal": True,
        "core.tools.datetime": True,
    }

    def fake_get(key, default=None):
        return stored_settings.get(key, default)

    def fake_set(key, val):
        stored_settings[key] = val

    mock_settings.get.side_effect = fake_get
    mock_settings.set.side_effect = fake_set
    mock_settings.get_env.side_effect = lambda key, default="": default

    panel = SettingsPanelWidget()
    panel.load_settings(mock_settings)

    # Verify social checkboxes loaded from core.tools.*
    assert panel.ui_use_email.isChecked() is True
    assert panel.ui_use_whatsapp.isChecked() is False
    assert panel.ui_use_moltbook.isChecked() is True
    assert panel.ui_use_mastodon.isChecked() is False

    # Modify social checkboxes and save
    panel.ui_use_email.setChecked(False)
    panel.ui_use_whatsapp.setChecked(True)
    panel.save_settings()

    # Verify changes saved back to settings
    assert stored_settings["core.tools.email"] is False
    assert stored_settings["core.tools.whatsapp"] is True
    assert stored_settings["core.tools.moltbook"] is True
    assert stored_settings["core.tools.mastodon"] is False

    # Verify other tool settings (terminal, datetime) were not removed or disrupted
    assert stored_settings["core.tools.terminal"] is True
    assert stored_settings["core.tools.datetime"] is True
