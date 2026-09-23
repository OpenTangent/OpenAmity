import os
import sys
import tempfile
import shutil
import pytest
from unittest.mock import MagicMock, patch

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PySide6.QtWidgets import QApplication
from core.agent_manager import AgentManager
from gui.main_window import MainWindow


@pytest.fixture(scope="session")
def qapp():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication.instance()
    if app is None:
        app = QApplication(["-platform", "offscreen"])
    return app


@pytest.fixture
def temp_environment(monkeypatch):
    from core.chatroom_manager import ChatroomManager
    ChatroomManager._instance = None

    temp_dir = tempfile.mkdtemp(prefix="openamity_menu_test_")
    monkeypatch.setattr("config.paths.get_app_data_dir", lambda: temp_dir)
    monkeypatch.setattr("config.paths.get_base_dir_for",
                        lambda aid: os.path.join(temp_dir, "agents", aid) if aid else os.path.join(temp_dir, "agents", "default"))
    monkeypatch.setattr("config.paths.get_mempalace_dir",
                        lambda aid: os.path.join(temp_dir, "agents", aid, "mempalace") if aid else os.path.join(temp_dir, "agents", "default", "mempalace"))
    monkeypatch.setattr("config.paths.get_chatroom_dir", lambda: os.path.join(temp_dir, "chatroom"))

    yield temp_dir

    ChatroomManager._instance = None
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_hamburger_menu_partitioning_and_chatroom_visibility(qapp, temp_environment):
    agent_manager = AgentManager()
    aid1 = agent_manager.create_new_agent()
    aid2 = agent_manager.create_new_agent()

    with patch("gui.main_window.SpellCheckHighlighter"):
        window = MainWindow(agent_manager)

    actions = window.hamburger_menu.actions()

    # Verify action sequence
    # Total items: 6 agent sections + 1 Delete Agent + 1 Separator + 1 System Settings + 1 Exit = 10 items
    assert len(actions) == 10

    expected_agent_titles = [
        "Basic Agent Settings",
        "Agent Values and Goals",
        "Agent Voice",
        "Provider Setup",
        "Social Accounts",
        "Other Agent Settings",
        "Delete Agent"
    ]

    for idx, title in enumerate(expected_agent_titles):
        assert actions[idx].text() == title
        assert not actions[idx].isSeparator()

    # Verify partition separator is at index 7
    assert actions[7].isSeparator()

    # Verify system sections below the partition line
    assert actions[8].text() == "System Settings"
    assert not actions[8].isSeparator()
    assert actions[9].text() == "Exit"
    assert not actions[9].isSeparator()

    # Test Agent View State
    window.switch_to_agent(aid1)
    assert not window.hamburger_btn.isHidden()
    for action in window.agent_menu_actions:
        assert action.isVisible()
    assert window.menu_separator.isVisible()
    assert actions[8].isVisible()  # System Settings
    assert actions[9].isVisible()  # Exit

    visible_in_agent_view = [a.text() for a in actions if not a.isSeparator() and a.isVisible()]
    assert visible_in_agent_view == [
        "Basic Agent Settings",
        "Agent Values and Goals",
        "Agent Voice",
        "Provider Setup",
        "Social Accounts",
        "Other Agent Settings",
        "Delete Agent",
        "System Settings",
        "Exit"
    ]

    # Test Chatroom View State
    window.switch_to_chatroom()
    assert not window.hamburger_btn.isHidden()
    for action in window.agent_menu_actions:
        assert not action.isVisible()
    assert not window.menu_separator.isVisible()
    assert actions[8].isVisible()  # System Settings
    assert actions[9].isVisible()  # Exit

    visible_in_chatroom = [a.text() for a in actions if not a.isSeparator() and a.isVisible()]
    assert visible_in_chatroom == ["System Settings", "Exit"]

    # Test switching back to Agent View
    window.switch_to_agent(aid2)
    assert not window.hamburger_btn.isHidden()
    for action in window.agent_menu_actions:
        assert action.isVisible()
    assert window.menu_separator.isVisible()
    assert actions[8].isVisible()
    assert actions[9].isVisible()

    window.close()


def test_tab_change_hides_settings_panel_except_wizard(qapp, temp_environment):
    agent_manager = AgentManager()
    aid1 = agent_manager.create_new_agent()
    aid2 = agent_manager.create_new_agent()

    with patch("gui.main_window.SpellCheckHighlighter"):
        window = MainWindow(agent_manager)

    # 1. Open normal settings panel (section 0)
    window.show_settings_section(0)
    assert window.stacked_layout.currentIndex() == 1
    assert not window.settings_panel.wizard_mode

    # Switch tabs to another agent -> settings should hide (stacked_layout index 0)
    window.switch_to_agent(aid2)
    assert window.stacked_layout.currentIndex() == 0

    # Open system settings (section 6)
    window.show_settings_section(6)
    assert window.stacked_layout.currentIndex() == 1

    # Switch tabs to chatroom -> settings should hide
    window.switch_to_chatroom()
    assert window.stacked_layout.currentIndex() == 0

    # 2. Wizard mode (first-run)
    window.settings_panel.set_wizard_mode(True)
    window.stacked_layout.setCurrentIndex(1)

    # Switch tab while in wizard mode -> settings should remain visible
    window.switch_to_agent(aid1)
    assert window.stacked_layout.currentIndex() == 1

    window.close()


def test_tab_widths_remain_constant_on_selection(qapp, temp_environment):
    agent_manager = AgentManager()
    aid1 = agent_manager.create_new_agent()
    aid2 = agent_manager.create_new_agent()

    with patch("gui.main_window.SpellCheckHighlighter"):
        window = MainWindow(agent_manager)

    # 1. Check Chatroom Tab sizeHint when unselected vs selected
    window.switch_to_agent(aid1)
    chatroom_unselected_size = window.btn_chatroom.sizeHint()

    window.switch_to_chatroom()
    chatroom_selected_size = window.btn_chatroom.sizeHint()

    assert chatroom_unselected_size.width() == chatroom_selected_size.width()

    # 2. Check Agent Tab sizeHint when unselected vs selected vs different states
    agent_btn = window.tab_buttons[aid1]

    agent_btn.set_selected(False)
    agent_unselected_width = agent_btn.sizeHint().width()

    agent_btn.set_selected(True)
    agent_selected_width = agent_btn.sizeHint().width()

    assert agent_unselected_width == agent_selected_width

    # 3. Check Agent Tab sizeHint across activity states (thinking, speaking, idle)
    agent_btn.set_activity_state("thinking")
    assert agent_btn.sizeHint().width() == agent_unselected_width

    agent_btn.set_activity_state("speaking")
    assert agent_btn.sizeHint().width() == agent_unselected_width

    agent_btn.set_activity_state("idle")
    assert agent_btn.sizeHint().width() == agent_unselected_width

    window.close()


def test_switching_away_from_open_settings_commits_settings(qapp, temp_environment):
    agent_manager = AgentManager()
    aid1 = agent_manager.create_new_agent()
    aid2 = agent_manager.create_new_agent()

    with patch("gui.main_window.SpellCheckHighlighter"):
        window = MainWindow(agent_manager)

    window.switch_to_agent(aid1)
    # Open settings section 1 (Values and Goals)
    window.show_settings_section(1)
    assert window.stacked_layout.currentIndex() == 1

    # Modify values in the panel
    window.settings_panel.ui_core_values.add_item("Courage")

    # Settings should not be committed yet
    sm1 = window.agent_views[aid1].settings_manager
    assert "Courage" not in sm1.get("core.agent.core-values", [])

    # Switch to aid2
    window.switch_to_agent(aid2)

    # Panel should be closed and aid1's settings committed
    assert window.stacked_layout.currentIndex() == 0
    assert "Courage" in sm1.get("core.agent.core-values", [])

    window.close()


