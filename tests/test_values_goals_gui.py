import os
import sys
import json
import tempfile
import pytest
from unittest.mock import MagicMock

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QKeyEvent
from gui.editable_list_widget import EditableItemListWidget, EditableItemRow, ItemTextEdit
from gui.settings_panel import SettingsPanelWidget
from core.settings_manager import SettingsManager
from core.config_manager import ConfigManager


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def temp_env(monkeypatch):
    temp_dir = tempfile.mkdtemp(prefix="openamity_val_goals_test_")
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


def test_editable_item_row_basics(qapp):
    row = EditableItemRow(text="Initial Value", placeholder="Enter value")
    assert row.get_text() == "Initial Value"

    row.set_text("Updated Value")
    assert row.get_text() == "Updated Value"

    # Up/Down button states
    row.set_up_enabled(False)
    assert not row.btn_up.isEnabled()
    row.set_up_enabled(True)
    assert row.btn_up.isEnabled()

    row.set_down_enabled(False)
    assert not row.btn_down.isEnabled()
    row.set_down_enabled(True)
    assert row.btn_down.isEnabled()


def test_editable_list_widget_add_remove(qapp):
    widget = EditableItemListWidget(
        add_button_text="+ Add Value",
        placeholder_text="e.g. Empathy",
        empty_message="No values defined"
    )

    assert widget.count() == 0
    assert not widget.empty_label.isHidden()

    # Add items
    widget.add_item("Value 1")
    widget.add_item("Value 2")
    assert widget.count() == 2
    assert widget.empty_label.isHidden()
    assert widget.get_items() == ["Value 1", "Value 2"]

    # Verify button states
    # Row 0: first item -> up disabled, down enabled
    assert not widget.rows[0].btn_up.isEnabled()
    assert widget.rows[0].btn_down.isEnabled()

    # Row 1: last item -> up enabled, down disabled
    assert widget.rows[1].btn_up.isEnabled()
    assert not widget.rows[1].btn_down.isEnabled()

    # Add item via button click
    widget.btn_add.click()
    assert widget.count() == 3
    widget.rows[2].set_text("Value 3")
    assert widget.get_items() == ["Value 1", "Value 2", "Value 3"]

    # Delete row 1 (middle row)
    widget.rows[1].btn_delete.click()
    assert widget.count() == 2
    assert widget.get_items() == ["Value 1", "Value 3"]

    # Now row 1 is last item -> up enabled, down disabled
    assert widget.rows[1].btn_up.isEnabled()
    assert not widget.rows[1].btn_down.isEnabled()


def test_editable_list_widget_reordering(qapp):
    widget = EditableItemListWidget()
    widget.set_items(["Item A", "Item B", "Item C"])
    assert widget.get_items() == ["Item A", "Item B", "Item C"]

    # Move Item B up (row index 1 -> 0)
    widget.rows[1].btn_up.click()
    assert widget.get_items() == ["Item B", "Item A", "Item C"]

    # Move Item B down (row index 0 -> 1)
    widget.rows[0].btn_down.click()
    assert widget.get_items() == ["Item A", "Item B", "Item C"]


def test_editable_list_widget_return_key_behavior(qapp):
    widget = EditableItemListWidget()
    widget.set_items(["Item 1"])
    assert widget.count() == 1

    # Simulate pressing Return in the last row
    row = widget.rows[0]
    key_event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key_Return, Qt.NoModifier)
    row.text_edit.keyPressEvent(key_event)

    # A new row should have been appended
    assert widget.count() == 2
    assert widget.rows[1].get_text() == ""


def test_settings_panel_values_goals_integration(qapp, temp_env):
    # Pre-write custom settings to disk
    initial_settings = {
        "core": {
            "agent": {
                "name": "TestBot",
                "core-values": [
                    "Kindness (Be thoughtful and considerate)",
                    "Clarity (Explain concepts simply)"
                ],
                "overarching-goals": [
                    "Empower Users (Help users succeed with their tools)"
                ]
            }
        }
    }
    with open(temp_env["settings_file"], "w") as f:
        json.dump(initial_settings, f)

    sm = SettingsManager(agent_id="test_agent")
    sm.settings_file = temp_env["settings_file"]

    panel = SettingsPanelWidget()
    panel.load_settings(sm)

    # Check that UI loaded the items
    assert panel.ui_core_values.get_items() == [
        "Kindness (Be thoughtful and considerate)",
        "Clarity (Explain concepts simply)"
    ]
    assert panel.ui_overarching_goals.get_items() == [
        "Empower Users (Help users succeed with their tools)"
    ]
    assert "<b>Core Values</b>" in panel.lbl_values_header.text()
    assert "(2)" in panel.lbl_values_header.text()
    assert "<b>Overarching Goals</b>" in panel.lbl_goals_header.text()
    assert "(1)" in panel.lbl_goals_header.text()

    # Add a new value inline
    panel.ui_core_values.btn_add.click()
    panel.ui_core_values.rows[2].set_text("Curiosity (Ask deep questions)")

    # Save settings
    panel.save_settings()

    # Verify saved settings in manager
    saved_cv = sm.get("core.agent.core-values")
    assert saved_cv == [
        "Kindness (Be thoughtful and considerate)",
        "Clarity (Explain concepts simply)",
        "Curiosity (Ask deep questions)"
    ]

    # Delete first goal
    panel.ui_overarching_goals.rows[0].btn_delete.click()
    panel.save_settings()
    assert sm.get("core.agent.overarching-goals") == []
    assert panel.ui_overarching_goals.count() == 0
    assert not panel.ui_overarching_goals.empty_label.isHidden()


def test_typing_in_values_goals_does_not_save_until_close(qapp, temp_env):
    initial_settings = {
        "core": {
            "agent": {
                "name": "DeferredBot",
                "core-values": ["Integrity"],
                "overarching-goals": ["Help humans"]
            }
        }
    }
    with open(temp_env["settings_file"], "w") as f:
        json.dump(initial_settings, f)

    sm = SettingsManager(agent_id="test_agent")
    sm.settings_file = temp_env["settings_file"]

    panel = SettingsPanelWidget()
    panel.load_settings(sm)

    # Track settings_saved emissions
    settings_saved_count = 0
    def on_saved():
        nonlocal settings_saved_count
        settings_saved_count += 1
    panel.settings_saved.connect(on_saved)

    # Track close_requested emissions
    close_requested_called = False
    def on_close():
        nonlocal close_requested_called
        close_requested_called = True
    panel.close_requested.connect(on_close)

    # Add a new row to values and type character by character
    row = panel.ui_core_values.add_item("")
    for char in "Empirical Rigor":
        row.text_edit.insertPlainText(char)

    # Add a new row to goals and type character by character
    goal_row = panel.ui_overarching_goals.add_item("")
    for char in "Advance Research":
        goal_row.text_edit.insertPlainText(char)

    # Verify that typing characters did NOT emit settings_saved
    assert settings_saved_count == 0
    # Settings on disk/manager should NOT be committed yet
    assert sm.get("core.agent.core-values") == ["Integrity"]
    assert sm.get("core.agent.overarching-goals") == ["Help humans"]

    # Now close the settings panel via the close button (request_close)
    panel.request_close()

    # Settings should now be committed and settings_saved emitted exactly once
    assert close_requested_called is True
    assert settings_saved_count == 1
    assert "Empirical Rigor" in sm.get("core.agent.core-values")
    assert "Advance Research" in sm.get("core.agent.overarching-goals")


def test_on_save_clicked_commits_and_closes(qapp, temp_env):
    initial_settings = {
        "core": {
            "agent": {
                "name": "SaveBot",
                "core-values": ["Patience"],
                "overarching-goals": ["Listen carefully"]
            }
        }
    }
    with open(temp_env["settings_file"], "w") as f:
        json.dump(initial_settings, f)

    sm = SettingsManager(agent_id="test_agent")
    sm.settings_file = temp_env["settings_file"]

    panel = SettingsPanelWidget()
    panel.load_settings(sm)

    settings_saved_count = 0
    panel.settings_saved.connect(lambda: globals().update(saved=True))
    saved = False
    def on_saved():
        nonlocal saved
        saved = True
    panel.settings_saved.connect(on_saved)

    closed = False
    panel.close_requested.connect(lambda: globals().update(closed=True))
    def on_closed():
        nonlocal closed
        closed = True
    panel.close_requested.connect(on_closed)

    # Edit value
    panel.ui_core_values.rows[0].text_edit.setPlainText("Deep Patience")
    assert not saved

    # Click Save button
    panel.on_save_clicked()

    assert saved is True
    assert closed is True
    assert sm.get("core.agent.core-values") == ["Deep Patience"]

