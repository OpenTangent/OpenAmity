import os
import sys
import tempfile
import shutil
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PySide6.QtWidgets import QApplication
from core.agent_manager import AgentManager
from core.chatroom_manager import ChatroomManager
from gui.chatroom_sidebar import ChatroomSidebar
from gui.chatroom_editor_panel import ChatroomEditorPanel


@pytest.fixture(scope="session")
def qapp():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication.instance()
    if app is None:
        app = QApplication(["-platform", "offscreen"])
    return app


@pytest.fixture
def temp_environment(monkeypatch):
    ChatroomManager._instance = None
    temp_dir = tempfile.mkdtemp(prefix="openamity_gui_comp_test_")
    monkeypatch.setattr("config.paths.get_app_data_dir", lambda: temp_dir)
    monkeypatch.setattr("config.paths.get_base_dir_for",
                        lambda aid: os.path.join(temp_dir, "agents", aid) if aid else os.path.join(temp_dir, "agents", "default"))
    monkeypatch.setattr("config.paths.get_mempalace_dir",
                        lambda aid: os.path.join(temp_dir, "agents", aid, "mempalace") if aid else os.path.join(temp_dir, "agents", "default", "mempalace"))
    monkeypatch.setattr("config.paths.get_chatroom_dir", lambda: os.path.join(temp_dir, "chatroom"))

    yield temp_dir

    ChatroomManager._instance = None
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_chatroom_sidebar_interactions(qapp, temp_environment):
    agent_manager = AgentManager()
    aid1 = agent_manager.create_new_agent()
    uid1 = agent_manager.get_agent_uid(aid1)

    cm = ChatroomManager()
    r1 = cm.create_chatroom("General", members=[{"agent_uid": uid1, "watch": True}])

    sidebar = ChatroomSidebar(agent_manager)

    agent_selected_called = []
    new_agent_called = []
    room_selected_called = []
    new_room_called = []
    edit_room_called = []

    sidebar.agent_selected.connect(lambda aid: agent_selected_called.append(aid))
    sidebar.new_agent_requested.connect(lambda: new_agent_called.append(True))
    sidebar.chatroom_selected.connect(lambda rid: room_selected_called.append(rid))
    sidebar.new_chatroom_requested.connect(lambda: new_room_called.append(True))
    sidebar.edit_chatroom_requested.connect(lambda rid: edit_room_called.append(rid))

    # Test initial headers and buttons
    assert sidebar.btn_agents_header.text() == "✦ Agents"
    assert sidebar.lbl_section.text() == "🗫 Chatrooms"
    assert aid1 in sidebar.agent_buttons
    assert r1 in sidebar.room_buttons

    # Select agent
    sidebar.agent_buttons[aid1].click()
    assert agent_selected_called == [aid1]
    assert sidebar.agent_buttons[aid1].is_selected is True

    # Click + New Agent
    sidebar.btn_add_agent.click()
    assert len(new_agent_called) == 1

    # Click room button -> selects room and clears agent selection
    sidebar.room_buttons[r1].click()
    assert room_selected_called == [r1]
    assert sidebar.room_buttons[r1].is_selected is True
    assert sidebar.agent_buttons[aid1].is_selected is False

    # Click + Edit Room inside selected room sub-list
    sidebar.room_edit_buttons[r1].click()
    assert edit_room_called == [r1]

    # Click + New Chatroom
    sidebar.btn_add_room.click()
    assert len(new_room_called) == 1

    sidebar.close()


def test_chatroom_editor_checkbox_linkage_and_close_semantics(qapp, temp_environment):
    agent_manager = AgentManager()
    aid1 = agent_manager.create_new_agent()
    uid1 = agent_manager.get_agent_uid(aid1)

    cm = ChatroomManager()
    panel = ChatroomEditorPanel(agent_manager)

    close_requested = []
    saved_called = []
    panel.close_requested.connect(lambda: close_requested.append(True))
    panel.saved.connect(lambda rid: saved_called.append(rid))

    # 1. New Chatroom mode
    panel.load_new_chatroom()
    assert not panel.is_edit_mode
    assert panel.lbl_title.text() == "New Chatroom"
    assert uid1 in panel.agent_rows

    row = panel.agent_rows[uid1]
    assert not row["access_cb"].isChecked()
    assert not row["watch_cb"].isChecked()
    assert not row["watch_cb"].isEnabled()

    # Toggling Access to True enables Watch
    row["access_cb"].setChecked(True)
    assert row["watch_cb"].isEnabled()

    # Turn on Watch
    row["watch_cb"].setChecked(True)
    assert row["watch_cb"].isChecked()

    # Toggling Access to False should uncheck and disable Watch
    row["access_cb"].setChecked(False)
    assert not row["watch_cb"].isChecked()
    assert not row["watch_cb"].isEnabled()

    # In New mode, [x] close discards changes
    panel.txt_name.setText("Unsaved Room")
    panel._on_close_clicked()
    assert len(close_requested) == 1
    assert len(saved_called) == 0

    # 2. Create actual room
    panel.load_new_chatroom()
    panel.txt_name.setText("Beta Team")
    row = panel.agent_rows[uid1]
    row["access_cb"].setChecked(True)
    row["watch_cb"].setChecked(True)

    with patch("PySide6.QtWidgets.QMessageBox.warning"):
        panel._save_changes()

    assert len(saved_called) == 1
    new_rid = saved_called[0]
    room = cm.get_chatroom(new_rid)
    assert room["name"] == "Beta Team"
    members = cm.get_members(new_rid)
    assert len(members) == 1
    assert members[0]["agent_uid"] == uid1
    assert members[0]["watch"] is True

    # 3. Edit Chatroom mode
    panel.load_edit_chatroom(new_rid)
    assert panel.is_edit_mode
    assert panel.lbl_title.text() == "Edit Chatroom"
    assert panel.txt_name.text() == "Beta Team"
    assert panel.agent_rows[uid1]["access_cb"].isChecked()
    assert panel.agent_rows[uid1]["watch_cb"].isChecked()

    # In Edit mode, [x] close automatically saves changes without asking
    panel.txt_name.setText("Beta Team Updated")
    panel._on_close_clicked()
    assert len(saved_called) == 2
    assert saved_called[1] == new_rid
    updated_room = cm.get_chatroom(new_rid)
    assert updated_room["name"] == "Beta Team Updated"

    panel.close()


def test_chatroom_editor_new_agent_button(qapp, temp_environment):
    agent_manager = AgentManager()
    panel = ChatroomEditorPanel(agent_manager)

    new_agent_signal_emitted = []
    panel.new_agent_requested.connect(lambda: new_agent_signal_emitted.append(True))

    panel.load_new_chatroom()
    assert panel.btn_new_agent is not None
    assert panel.lbl_sec2.text() == "Participating Agents"

    # Clicking + New Agent should close panel and emit new_agent_requested
    panel.btn_new_agent.click()
    assert len(new_agent_signal_emitted) == 1
    panel.close()


def test_sidebar_all_agents_icon(qapp, temp_environment):
    sidebar = ChatroomSidebar()
    assert sidebar.btn_agents_header.text() == "✦ Agents"
    assert sidebar.btn_chatrooms_header.text() == "🗫 Chatrooms"
    assert sidebar.lbl_section.text() == "🗫 Chatrooms"
    assert sidebar.btn_add_agent.text() == "+ New Agent"
    sidebar.close()


def test_sidebar_section_collapse_and_persistence(qapp, temp_environment):
    from core.config_manager import ConfigManager

    agent_manager = AgentManager()
    aid1 = agent_manager.create_new_agent()

    sidebar = ChatroomSidebar(agent_manager)
    assert not sidebar.btn_agents_header.is_collapsed
    assert not sidebar.agents_container.isHidden()
    assert not sidebar.btn_chatrooms_header.is_collapsed
    assert not sidebar.rooms_container.isHidden()

    # Collapse Agents header
    sidebar.btn_agents_header.click()
    assert sidebar.btn_agents_header.is_collapsed is True
    assert sidebar.agents_container.isHidden() is True
    assert ConfigManager().get("ui.agents-collapsed") is True

    # Collapse Chatrooms header
    sidebar.btn_chatrooms_header.click()
    assert sidebar.btn_chatrooms_header.is_collapsed is True
    assert sidebar.rooms_container.isHidden() is True
    assert ConfigManager().get("ui.chatrooms-collapsed") is True
    sidebar.close()

    # Fresh instance restores persisted section collapse states
    sidebar2 = ChatroomSidebar(agent_manager)
    assert aid1 in sidebar2.agent_buttons
    assert sidebar2.btn_agents_header.is_collapsed is True
    assert sidebar2.agents_container.isHidden() is True
    assert sidebar2.btn_chatrooms_header.is_collapsed is True
    assert sidebar2.rooms_container.isHidden() is True

    # Expand back
    sidebar2.btn_agents_header.click()
    sidebar2.btn_chatrooms_header.click()
    assert not sidebar2.agents_container.isHidden()
    assert not sidebar2.rooms_container.isHidden()
    assert ConfigManager().get("ui.agents-collapsed") is False
    assert ConfigManager().get("ui.chatrooms-collapsed") is False
    sidebar2.close()


def test_sidebar_selected_chatroom_inline_members_and_watch_indicators(qapp, temp_environment):
    from core.settings_manager import SettingsManager

    agent_manager = AgentManager()
    aid1 = agent_manager.create_new_agent()
    s1 = SettingsManager(agent_id=aid1)
    s1.set("core.agent.name", "Amity")
    s1.set("core.agent.creation-date", "2026-01-01")
    s1.save()

    aid2 = agent_manager.create_new_agent()
    s2 = SettingsManager(agent_id=aid2)
    s2.set("core.agent.name", "Nova")
    s2.set("core.agent.creation-date", "2026-01-02")
    s2.save()

    uid1 = agent_manager.get_agent_uid(aid1)
    uid2 = agent_manager.get_agent_uid(aid2)

    cm = ChatroomManager()
    r1 = cm.create_chatroom("Room One", members=[
        {"agent_uid": uid1, "watch": True},
        {"agent_uid": uid2, "watch": False},
    ])
    r2 = cm.create_chatroom("Room Two", members=[{"agent_uid": uid2, "watch": True}])
    r3 = cm.create_chatroom("Empty Room", members=[])

    sidebar = ChatroomSidebar(agent_manager)

    # Verify + New Agent is the last widget in agents_layout
    last_idx = sidebar.agents_layout.count() - 1
    assert sidebar.agents_layout.itemAt(last_idx).widget() is sidebar.btn_add_agent

    # In DM mode, all room member sub-lists are collapsed
    sidebar.set_selected_agent(aid1)
    assert sidebar.room_member_containers[r1].isHidden()
    assert sidebar.room_member_containers[r2].isHidden()
    assert sidebar.room_member_containers[r3].isHidden()
    assert len(sidebar.room_member_buttons) == 0

    # Select Room One -> only Room One expands inline with Amity (watching) and Nova (not watching) + Edit Room
    sidebar.room_buttons[r1].click()
    assert not sidebar.room_member_containers[r1].isHidden()
    assert sidebar.room_member_containers[r2].isHidden()
    assert sidebar.room_member_containers[r3].isHidden()
    assert list(sidebar.room_member_buttons.keys()) == [aid1, aid2]
    assert sidebar.room_member_buttons[aid1].is_watching is True
    assert "Watching this room" in sidebar.room_member_buttons[aid1].toolTip()
    assert sidebar.room_member_buttons[aid2].is_watching is False
    assert "Room access only" in sidebar.room_member_buttons[aid2].toolTip()
    assert r1 in sidebar.room_edit_buttons
    r1_layout = sidebar.room_member_containers[r1].layout()
    assert r1_layout.itemAt(r1_layout.count() - 1).widget() is sidebar.room_edit_buttons[r1]

    # Select Empty Room -> Room One collapses, Empty Room still shows + Edit Room
    sidebar.room_buttons[r3].click()
    assert sidebar.room_member_containers[r1].isHidden()
    assert not sidebar.room_member_containers[r3].isHidden()
    assert len(sidebar.room_member_buttons) == 0
    assert r3 in sidebar.room_edit_buttons

    # Select Room Two, collapse Agents section, then click Room Two member -> auto-expands Agents!
    sidebar.room_buttons[r2].click()
    assert list(sidebar.room_member_buttons.keys()) == [aid2]
    sidebar.set_agents_collapsed(True)
    assert sidebar.agents_container.isHidden()

    selected_agents = []
    sidebar.agent_selected.connect(lambda aid: selected_agents.append(aid))
    sidebar.room_member_buttons[aid2].click()

    assert selected_agents == [aid2]
    assert sidebar.selected_context == ("all", None)
    assert sidebar.selected_agent_id == aid2
    assert not sidebar.agents_container.isHidden()
    assert not sidebar.btn_agents_header.is_collapsed
    assert sidebar.room_member_containers[r2].isHidden()

    sidebar.close()


def test_sidebar_membership_changed_and_activity_propagation(qapp, temp_environment):
    agent_manager = AgentManager()
    aid1 = agent_manager.create_new_agent()
    aid2 = agent_manager.create_new_agent()
    uid1 = agent_manager.get_agent_uid(aid1)
    uid2 = agent_manager.get_agent_uid(aid2)

    cm = ChatroomManager()
    r1 = cm.create_chatroom("Ops Room", members=[{"agent_uid": uid1, "watch": False}])

    sidebar = ChatroomSidebar(agent_manager)
    sidebar.set_selected_chatroom(r1)
    assert list(sidebar.room_member_buttons.keys()) == [aid1]

    # Activity & pause propagate to both primary and sub-list buttons
    sidebar.set_agent_activity_state(aid1, "thinking")
    assert sidebar.agent_buttons[aid1].activity_state == "thinking"
    assert sidebar.room_member_buttons[aid1].activity_state == "thinking"

    sidebar.set_agent_activity_state(aid1, "speaking")
    assert sidebar.agent_buttons[aid1].activity_state == "speaking"
    assert sidebar.room_member_buttons[aid1].activity_state == "speaking"

    sidebar.set_agent_paused(aid1, True)
    assert sidebar.agent_buttons[aid1].is_paused is True
    assert sidebar.agent_buttons[aid1].activity_state == "idle"
    assert sidebar.room_member_buttons[aid1].is_paused is True
    assert sidebar.room_member_buttons[aid1].activity_state == "idle"

    # Membership change event refreshes active room sub-list
    cm.update_chatroom(r1, members=[
        {"agent_uid": uid1, "watch": True},
        {"agent_uid": uid2, "watch": False},
    ])
    qapp.processEvents()
    assert aid1 in sidebar.room_member_buttons
    assert aid2 in sidebar.room_member_buttons
    assert sidebar.room_member_buttons[aid1].is_watching is True

    sidebar.close()


def test_sidebar_collapse_vertical_rail_settings_bar_and_activity_accent(qapp, temp_environment):
    from core.config_manager import ConfigManager
    from core.settings_manager import SettingsManager

    agent_manager = AgentManager()
    aid1 = agent_manager.create_new_agent()
    s1 = SettingsManager(agent_id=aid1)
    s1.set("core.agent.name", "Amity")
    s1.save()

    aid2 = agent_manager.create_new_agent()
    s2 = SettingsManager(agent_id=aid2)
    s2.set("core.agent.name", "Dex")
    s2.save()

    cm = ChatroomManager()
    r1 = cm.create_chatroom("Design Room", members=[])

    sidebar = ChatroomSidebar(agent_manager)
    sidebar.set_selected_agent(aid1)

    assert sidebar.width() == ChatroomSidebar.EXPANDED_WIDTH
    assert not sidebar.settings_bar.isHidden()
    assert not sidebar.hamburger_btn.isHidden()
    assert sidebar.collapsed_strip.isHidden()

    # Collapse via bottom settings bar button
    sidebar.btn_collapse_sidebar.click()
    assert sidebar.is_collapsed() is True
    assert sidebar.width() == ChatroomSidebar.COLLAPSED_WIDTH
    assert sidebar.expanded_container.isHidden()
    assert sidebar.settings_bar.isHidden()
    assert not sidebar.collapsed_strip.isHidden()
    assert sidebar.collapsed_strip.display_text == "Amity"
    assert ConfigManager().get("ui.sidebar-collapsed") is True

    # Switching to chatroom updates vertical rail label
    sidebar.set_selected_chatroom(r1)
    assert sidebar.collapsed_strip.display_text == "#Design Room"

    # Activity accent prioritizes speaking over thinking across all agents
    sidebar.set_agent_activity_state(aid1, "thinking")
    assert sidebar.collapsed_strip.activity_state == "thinking"

    sidebar.set_agent_activity_state(aid2, "speaking")
    assert sidebar.collapsed_strip.activity_state == "speaking"

    sidebar.set_agent_activity_state(aid2, "idle")
    assert sidebar.collapsed_strip.activity_state == "thinking"

    sidebar.set_agent_paused(aid1, True)
    assert sidebar.collapsed_strip.activity_state == "idle"
    sidebar.close()

    # Fresh instance restores persisted sidebar-collapsed state
    sidebar2 = ChatroomSidebar(agent_manager)
    assert sidebar2.is_collapsed() is True
    assert sidebar2.width() == ChatroomSidebar.COLLAPSED_WIDTH
    assert sidebar2.settings_bar.isHidden()

    # Clicking the vertical strip reopens the sidebar
    sidebar2.collapsed_strip.click()
    assert sidebar2.is_collapsed() is False
    assert sidebar2.width() == ChatroomSidebar.EXPANDED_WIDTH
    assert not sidebar2.settings_bar.isHidden()
    assert not sidebar2.hamburger_btn.isHidden()
    assert ConfigManager().get("ui.sidebar-collapsed") is False
    sidebar2.close()


def test_sidebar_muted_action_buttons_header_spacing_and_resizable_width(qapp, temp_environment):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from core.config_manager import ConfigManager

    agent_manager = AgentManager()
    aid1 = agent_manager.create_new_agent()
    uid1 = agent_manager.get_agent_uid(aid1)

    cm = ChatroomManager()
    r1 = cm.create_chatroom("Resizable Room", members=[{"agent_uid": uid1, "watch": True}])

    sidebar = ChatroomSidebar(agent_manager)
    sidebar.set_selected_chatroom(r1)

    # 1. Chrome surface coloring & muted dashed action buttons (+ New Agent, + New Chatroom, + Edit Room)
    from gui.chatroom_sidebar import SIDEBAR_BG_COLOR
    assert SIDEBAR_BG_COLOR == "#222222"
    assert "#222222" in sidebar.styleSheet()
    assert "#222222" in sidebar.settings_bar.styleSheet()
    assert "background-color: transparent" in sidebar.btn_add_agent.styleSheet()
    assert "background-color: transparent" in sidebar.btn_add_room.styleSheet()
    assert "background-color: transparent" in sidebar.room_edit_buttons[r1].styleSheet()

    # Collapsed rail renders with the #222222 chrome background
    sidebar.set_collapsed(True)
    sidebar.collapsed_strip.resize(28, 120)
    rail_img = sidebar.collapsed_strip.grab().toImage()
    assert rail_img.pixelColor(10, 4).name() == "#222222"
    sidebar.set_collapsed(False)

    # 2. Larger padded Agents & Chatrooms headers
    assert sidebar.btn_agents_header.font().pixelSize() == 13
    assert sidebar.btn_chatrooms_header.font().pixelSize() == 13
    assert sidebar.btn_agents_header.height() >= 36
    assert sidebar.btn_chatrooms_header.height() >= 36

    # 3. Programmatic width clamping & persistence
    sidebar.set_expanded_width(50)
    assert sidebar.expanded_width() == ChatroomSidebar.MIN_EXPANDED_WIDTH
    assert sidebar.width() == ChatroomSidebar.MIN_EXPANDED_WIDTH

    sidebar.set_expanded_width(999)
    assert sidebar.expanded_width() == ChatroomSidebar.MAX_EXPANDED_WIDTH
    assert sidebar.width() == ChatroomSidebar.MAX_EXPANDED_WIDTH

    sidebar.set_expanded_width(275)
    assert sidebar.expanded_width() == 275
    assert sidebar.width() == 275
    assert ConfigManager().get("ui.sidebar-width") == 275

    # Collapsing preserves custom width when reopened
    sidebar.set_collapsed(True)
    assert sidebar.width() == ChatroomSidebar.COLLAPSED_WIDTH
    sidebar.set_collapsed(False)
    assert sidebar.width() == 275

    # 4. Interactive mouse drag resize on right-edge handle and double-click reset
    handle = sidebar.resize_handle
    press_ev = QMouseEvent(
        QEvent.MouseButtonPress, QPointF(2, 50), QPointF(275, 50),
        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier
    )
    move_ev = QMouseEvent(
        QEvent.MouseMove, QPointF(37, 50), QPointF(310, 50),
        Qt.NoButton, Qt.LeftButton, Qt.NoModifier
    )
    release_ev = QMouseEvent(
        QEvent.MouseButtonRelease, QPointF(37, 50), QPointF(310, 50),
        Qt.LeftButton, Qt.NoButton, Qt.NoModifier
    )
    handle.mousePressEvent(press_ev)
    handle.mouseMoveEvent(move_ev)
    assert sidebar.width() == 310
    handle.mouseReleaseEvent(release_ev)
    assert ConfigManager().get("ui.sidebar-width") == 310
    sidebar.close()

    # Fresh instance restores persisted width
    sidebar2 = ChatroomSidebar(agent_manager)
    assert sidebar2.expanded_width() == 310
    assert sidebar2.width() == 310

    # Double-clicking resize handle resets to default width
    dbl_ev = QMouseEvent(
        QEvent.MouseButtonDblClick, QPointF(2, 50), QPointF(310, 50),
        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier
    )
    sidebar2.resize_handle.mouseDoubleClickEvent(dbl_ev)
    assert sidebar2.width() == ChatroomSidebar.EXPANDED_WIDTH
    assert ConfigManager().get("ui.sidebar-width") == ChatroomSidebar.EXPANDED_WIDTH
    sidebar2.close()


def test_agent_roster_item_encapsulation_and_watch_styling(qapp, temp_environment):
    agent_manager = AgentManager()
    aid1 = agent_manager.create_new_agent()
    uid1 = agent_manager.get_agent_uid(aid1)

    panel = ChatroomEditorPanel(agent_manager)
    panel.load_new_chatroom()
    panel.show()

    row = panel.agent_rows[uid1]
    item = row["item"]
    watch_btn = row["watch_btn"]

    # Initial state: unselected
    assert not row["access_cb"].isChecked()
    assert not item.badge_selected.isVisible()
    assert not watch_btn.isEnabled()
    assert watch_btn.text() == "Watch"

    # 1. Click item to select agent
    item.click()
    assert row["access_cb"].isChecked()
    assert item.badge_selected.isVisible()
    assert watch_btn.isEnabled()
    assert watch_btn.text() == "Watch"

    # 2. Click Watch button to enable watch
    watch_btn.click()
    assert row["watch_cb"].isChecked()
    assert watch_btn.text() == "✓ Watch"
    assert "✓ Watch" in watch_btn.text()

    # 3. Click Watch button again to disable watch
    watch_btn.click()
    assert not row["watch_cb"].isChecked()
    assert watch_btn.text() == "Watch"
    # Agent remains selected
    assert row["access_cb"].isChecked()
    assert item.badge_selected.isVisible()

    # 4. Turn Watch back on then deselect agent
    watch_btn.click()
    assert row["watch_cb"].isChecked()

    item.click()
    assert not row["access_cb"].isChecked()
    assert not item.badge_selected.isVisible()
    # Watch automatically disables and unchecks
    assert not row["watch_cb"].isChecked()
    assert not watch_btn.isEnabled()

    panel.close()


def test_cross_thread_chatroom_message_sidebar_and_view(qapp, temp_environment):
    import threading
    from gui.chatroom_view import ChatroomView

    agent_manager = AgentManager()
    aid1 = agent_manager.create_new_agent()
    uid1 = agent_manager.get_agent_uid(aid1)
    aid2 = agent_manager.create_new_agent()
    uid2 = agent_manager.get_agent_uid(aid2)

    cm = ChatroomManager()
    r2 = cm.create_chatroom("Support - Security", members=[
        {"agent_uid": uid1, "watch": True},
        {"agent_uid": uid2, "watch": True}
    ])

    sidebar = ChatroomSidebar()
    view = ChatroomView(agent_manager, chatroom_id=r2)
    qapp.processEvents()

    assert r2 in sidebar.room_buttons
    room_btn = sidebar.room_buttons[r2]
    assert room_btn.unread_count == 0

    # 1. Agent 1 sends message from a background worker thread
    def worker1():
        cm.post_message(
            sender_id=uid1,
            sender_name="Amity",
            sender_type="agent",
            content="Hello Delta! Welcome to Support - Security.",
            chatroom_id=r2
        )

    t1 = threading.Thread(target=worker1)
    t1.start()
    t1.join()

    # Process Qt event queue
    qapp.processEvents()

    # Verify tab button still exists and is not destroyed/disappeared
    assert r2 in sidebar.room_buttons
    assert sidebar.room_buttons[r2] == room_btn
    assert any("Hello Delta" in m.get("content", "") for m in view._loaded_messages)

    # 2. Agent 2 replies from another background thread
    def worker2():
        cm.post_message(
            sender_id=uid2,
            sender_name="Delta",
            sender_type="agent",
            content="Thanks Amity. Saved you to contacts.",
            chatroom_id=r2,
            reply_to_id=1
        )

    t2 = threading.Thread(target=worker2)
    t2.start()
    t2.join()

    qapp.processEvents()

    # Verify both messages are in view and sidebar remained intact
    assert r2 in sidebar.room_buttons
    assert sidebar.room_buttons[r2] == room_btn
    assert len(view._loaded_messages) == 2
    assert any("Thanks Amity" in m.get("content", "") for m in view._loaded_messages)

    sidebar.close()
    view.close()
    qapp.processEvents()


def test_sidebar_agent_button_pauses_pulse_timer_when_hidden(qapp):
    """Regression: SidebarAgentButton must stop its 30ms QTimer when hidden and resume when shown."""
    from gui.chatroom_sidebar import SidebarAgentButton

    btn = SidebarAgentButton("agent-timer", "Amity")
    try:
        # While hidden, setting activity state should NOT start the animation timer
        btn.set_activity_state("thinking")
        assert btn.activity_state == "thinking"
        assert btn.pulse_timer.isActive() is False

        # Showing resumes the pulse timer
        btn.show()
        qapp.processEvents()
        assert btn.pulse_timer.isActive() is True

        # Hiding stops the pulse timer immediately
        btn.hide()
        qapp.processEvents()
        assert btn.pulse_timer.isActive() is False

        # Showing again resumes while active; setting idle stops it
        btn.show()
        qapp.processEvents()
        assert btn.pulse_timer.isActive() is True
        btn.set_activity_state("idle")
        assert btn.pulse_timer.isActive() is False
    finally:
        btn.close()
        qapp.processEvents()


