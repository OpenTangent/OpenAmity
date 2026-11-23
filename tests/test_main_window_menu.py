import os
import sys
import tempfile
import shutil
import pytest
from unittest.mock import MagicMock, patch

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PySide6.QtWidgets import QApplication, QMessageBox
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

    # Verify action sequence:
    # 7 agent sections + 1 Delete Agent + 2 Chatroom sections + 1 Separator + 1 System Settings + 1 Exit = 13 items
    assert len(actions) == 13

    expected_agent_titles = [
        "Cognitive Provider",
        "Basic Agent Settings",
        "Agent Values and Goals",
        "Agent Voice",
        "Media Provider",
        "Social Accounts",
        "Other Agent Settings",
        "Delete Agent"
    ]

    for idx, title in enumerate(expected_agent_titles):
        assert actions[idx].text() == title
        assert not actions[idx].isSeparator()

    # Verify chatroom actions
    assert actions[8].text() == "Edit Chatroom"
    assert actions[9].text() == "Delete Chatroom"

    # Verify partition separator is at index 10
    assert actions[10].isSeparator()

    # Verify system sections below the partition line
    assert actions[11].text() == "System Settings"
    assert not actions[11].isSeparator()
    assert actions[12].text() == "Exit"
    assert not actions[12].isSeparator()

    # Test Agent View State
    window.switch_to_agent(aid1)
    assert window.hamburger_btn.parentWidget() is window.chatroom_sidebar.settings_bar
    assert not window.chatroom_sidebar.settings_bar.isHidden()
    assert not window.hamburger_btn.isHidden()
    for action in window.agent_menu_actions:
        assert action.isVisible()
    for action in window.chatroom_menu_actions:
        assert not action.isVisible()
    assert window.menu_separator.isVisible()
    assert actions[11].isVisible()  # System Settings
    assert actions[12].isVisible()  # Exit

    visible_in_agent_view = [a.text() for a in actions if not a.isSeparator() and a.isVisible()]
    assert visible_in_agent_view == [
        "Cognitive Provider",
        "Basic Agent Settings",
        "Agent Values and Goals",
        "Agent Voice",
        "Media Provider",
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
    for action in window.chatroom_menu_actions:
        assert action.isVisible()
    assert window.menu_separator.isVisible()
    assert actions[11].isVisible()  # System Settings
    assert actions[12].isVisible()  # Exit

    visible_in_chatroom = [a.text() for a in actions if not a.isSeparator() and a.isVisible()]
    assert visible_in_chatroom == ["Edit Chatroom", "Delete Chatroom", "System Settings", "Exit"]

    # Collapsing sidebar hides settings bar and hamburger button completely
    window.chatroom_sidebar.set_collapsed(True)
    assert window.chatroom_sidebar.settings_bar.isHidden()
    window.chatroom_sidebar.set_collapsed(False)
    assert not window.chatroom_sidebar.settings_bar.isHidden()

    # Test switching back to Agent View
    window.switch_to_agent(aid2)
    assert not window.hamburger_btn.isHidden()
    for action in window.agent_menu_actions:
        assert action.isVisible()
    for action in window.chatroom_menu_actions:
        assert not action.isVisible()
    assert window.menu_separator.isVisible()
    assert actions[11].isVisible()
    assert actions[12].isVisible()

    window.close()


def test_dynamic_agent_tabs_and_context_switching(qapp, temp_environment):
    from core.chatroom_manager import ChatroomManager
    agent_manager = AgentManager()
    aid1 = agent_manager.create_new_agent()
    aid2 = agent_manager.create_new_agent()
    uid1 = agent_manager.get_agent_uid(aid1)

    cm = ChatroomManager()
    # Create room with only aid1 access
    room_id = cm.create_chatroom("Alpha Room", members=[{"agent_uid": uid1, "watch": False}])

    with patch("gui.main_window.SpellCheckHighlighter"):
        window = MainWindow(agent_manager)

    # Top agent tab bar has been removed
    assert not hasattr(window, "header_widget")
    assert not hasattr(window, "tabs_layout")

    # Initially in Agent DM view with all agents in sidebar and + New Agent last
    assert window.current_context == ("all", None)
    sidebar = window.chatroom_sidebar
    agents_layout = sidebar.agents_layout
    sidebar_agent_ids = [
        agents_layout.itemAt(i).widget().agent_id
        for i in range(agents_layout.count() - 1)
    ]
    assert aid1 in sidebar_agent_ids
    assert aid2 in sidebar_agent_ids
    assert agents_layout.itemAt(agents_layout.count() - 1).widget() is sidebar.btn_add_agent

    # Switch to room
    window.switch_to_chatroom(room_id)
    assert window.current_context == ("room", room_id)
    assert list(sidebar.room_member_buttons.keys()) == [aid1]
    assert room_id in sidebar.room_edit_buttons

    # Clicking + Edit Room opens ChatroomEditorPanel
    sidebar.room_edit_buttons[room_id].click()
    assert window.stacked_layout.currentIndex() == 2
    window.hide_chatroom_editor()
    assert window.stacked_layout.currentIndex() == 0

    # Collapse Agents header, then click sub-listed agent -> opens DM and auto-expands Agents
    sidebar.set_agents_collapsed(True)
    assert sidebar.agents_container.isHidden()
    sidebar.room_member_buttons[aid1].click()
    assert window.current_context == ("all", None)
    assert window.current_agent_id == aid1
    assert not sidebar.agents_container.isHidden()
    assert len(sidebar.room_member_buttons) == 0

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
    from core.chatroom_manager import ChatroomManager

    agent_manager = AgentManager()
    aid1 = agent_manager.create_new_agent()
    aid2 = agent_manager.create_new_agent()
    uid1 = agent_manager.get_agent_uid(aid1)

    cm = ChatroomManager()
    rid = cm.create_chatroom("Width Room", members=[{"agent_uid": uid1, "watch": False}])

    with patch("gui.main_window.SpellCheckHighlighter"):
        window = MainWindow(agent_manager)

    # 1. Check Chatroom SidebarTabButton sizeHint when unselected vs selected
    room_btn = window.chatroom_sidebar.room_buttons[rid]
    window.switch_to_agent(aid1)
    chatroom_unselected_size = room_btn.sizeHint()

    window.switch_to_chatroom(rid)
    chatroom_selected_size = room_btn.sizeHint()

    assert chatroom_unselected_size.width() == chatroom_selected_size.width()

    # 2. Check SidebarAgentButton sizeHint when unselected vs selected vs different states
    agent_btn = window.tab_buttons[aid1]

    agent_btn.set_selected(False)
    agent_unselected_width = agent_btn.sizeHint().width()

    agent_btn.set_selected(True)
    agent_selected_width = agent_btn.sizeHint().width()

    assert agent_unselected_width == agent_selected_width

    # 3. Check SidebarAgentButton sizeHint across activity states (thinking, speaking, idle)
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


def test_delete_current_chatroom(qapp, temp_environment):
    from core.chatroom_manager import ChatroomManager
    agent_manager = AgentManager()
    aid1 = agent_manager.create_new_agent()
    uid1 = agent_manager.get_agent_uid(aid1)

    cm = ChatroomManager()
    rid = cm.create_chatroom("Project Delta", members=[{"agent_uid": uid1, "watch": False}])

    with patch("gui.main_window.SpellCheckHighlighter"):
        window = MainWindow(agent_manager)

    # Switch to the created room
    window.switch_to_chatroom(rid)
    assert window.current_context == ("room", rid)
    assert window.current_chatroom_id == rid

    # Test cancel deletion (QMessageBox.No)
    with patch.object(QMessageBox, "question", return_value=QMessageBox.No):
        window.delete_current_chatroom()
        # Room should still exist and context unchanged
        assert cm.get_chatroom(rid)["deleted_at"] is None
        assert window.current_context == ("room", rid)

    # Test confirm deletion (QMessageBox.Yes)
    with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
        window.delete_current_chatroom()
        # Room should be deleted and window switched to All Agents
        assert cm.get_chatroom(rid)["deleted_at"] is not None
        assert window.current_context == ("all", None)
        assert rid not in [r["id"] for r in cm.list_chatrooms()]

    window.close()


def test_background_speaking_agent_and_shortcut_collapse_and_menu_popup(qapp, temp_environment):
    agent_manager = AgentManager()
    aid1 = agent_manager.create_new_agent()
    aid2 = agent_manager.create_new_agent()

    with patch("gui.main_window.SpellCheckHighlighter"):
        window = MainWindow(agent_manager)

    window.switch_to_agent(aid1)

    # Upward hamburger menu popup opens cleanly when expanded
    window.chatroom_sidebar._show_hamburger_menu()
    qapp.processEvents()
    window.hamburger_menu.close()

    # Toggle collapse via Ctrl+B shortcut signal
    assert window.chatroom_sidebar.is_collapsed() is False
    window.sidebar_toggle_shortcut.activated.emit()
    assert window.chatroom_sidebar.is_collapsed() is True

    # Background agent aid2 starts speaking while selected agent aid1 is thinking
    window.ui_agent_busy_state_changed.emit(aid1, True, False)
    window.ui_agent_busy_state_changed.emit(aid2, True, True)
    qapp.processEvents()

    # Selected agent's visualizer stays hidden; background agent view is not shown; collapsed rail prioritizes speaking
    assert window.agent_views[aid1].visualizer.isHidden() is True
    assert window.agent_views[aid2].isVisible() is False
    assert window.chatroom_sidebar.collapsed_strip.activity_state == "speaking"

    # Toggle back via shortcut
    window.sidebar_toggle_shortcut.activated.emit()
    assert window.chatroom_sidebar.is_collapsed() is False

    window.close()


