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

    sidebar = ChatroomSidebar()

    all_agents_called = []
    room_selected_called = []
    new_room_called = []

    sidebar.all_agents_selected.connect(lambda: all_agents_called.append(True))
    sidebar.chatroom_selected.connect(lambda rid: room_selected_called.append(rid))
    sidebar.new_chatroom_requested.connect(lambda: new_room_called.append(True))

    # Test initial selection and labels
    assert sidebar.btn_all_agents.is_selected is True
    assert sidebar.btn_all_agents.icon_prefix == "✦"
    assert sidebar.btn_all_agents.display_title == "Agents"
    assert sidebar.lbl_section.text() == "🗫 Chatrooms"
    assert r1 in sidebar.room_buttons

    # Click room button
    sidebar.room_buttons[r1].click()
    assert len(room_selected_called) == 1
    assert room_selected_called[0] == r1
    assert sidebar.room_buttons[r1].is_selected is True
    assert sidebar.btn_all_agents.is_selected is False

    # Click All Agents
    sidebar.btn_all_agents.click()
    assert len(all_agents_called) == 1
    assert sidebar.btn_all_agents.is_selected is True
    assert sidebar.room_buttons[r1].is_selected is False

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
    assert sidebar.btn_all_agents.icon_prefix == "✦"
    assert sidebar.btn_all_agents.display_title == "Agents"
    assert sidebar.lbl_section.text() == "🗫 Chatrooms"
    sidebar.close()


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


