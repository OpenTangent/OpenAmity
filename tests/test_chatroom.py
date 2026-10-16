import sys
import os
import tempfile
import pytest

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.chatroom_manager import ChatroomManager
from tools.chatroom_tool import ChatroomSkill


class MockOrchestrator:
    def __init__(self, agent_id="agent_1", agent_uid="+OA-AAAA-1111", agent_name="Amity"):
        self.agent_id = agent_id
        self.agent_uid = agent_uid
        self._agent_name = agent_name

        class MockSettings:
            def __init__(self, name):
                self.name = name
            def get(self, key, default=None):
                if key == "core.agent.name":
                    return self.name
                return default

        self.settings_manager = MockSettings(agent_name)


@pytest.fixture
def temp_chatroom(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "chatroom.db")
        ChatroomManager._instance = None
        manager = ChatroomManager(db_path=db_path)
        # Create default room with test agents
        manager.create_chatroom(
            name="General",
            purpose="General workspace discussion",
            members=[
                {"agent_uid": "+OA-AAAA-1111", "watch": True},
                {"agent_uid": "+OA-BBBB-2222", "watch": True},
                {"agent_uid": "+OA-CCCC-3333", "watch": False}
            ]
        )
        yield manager
        ChatroomManager._instance = None


def test_post_and_unread_flow(temp_chatroom):
    mgr = temp_chatroom
    agent_a = "+OA-AAAA-1111"
    agent_b = "+OA-BBBB-2222"

    # User posts message
    msg1_id = mgr.post_message(
        sender_id="USER",
        sender_name="User",
        sender_type="user",
        content="Welcome to the workspace team!",
        chatroom_id=1
    )
    assert msg1_id == 1

    # Agent A checks unread
    unread_a = mgr.get_unread_messages(agent_a, chatroom_id=1, limit=30, mark_as_read=True)
    assert len(unread_a) == 1
    assert unread_a[0]["sender_type"] == "user"
    assert unread_a[0]["content"] == "Welcome to the workspace team!"

    # Agent A checks unread again -> should be empty since cursor advanced
    assert len(mgr.get_unread_messages(agent_a, chatroom_id=1)) == 0

    # Agent B checks unread -> should receive msg 1
    unread_b = mgr.get_unread_messages(agent_b, chatroom_id=1, limit=30, mark_as_read=True)
    assert len(unread_b) == 1
    assert unread_b[0]["id"] == 1

    # Agent A broadcasts public message
    msg2_id = mgr.post_message(
        sender_id=agent_a,
        sender_name="Amity",
        sender_type="agent",
        content="Thanks! Ready to work.",
        chatroom_id=1
    )
    assert msg2_id == 2

    # Agent B fetches unread -> gets message 2
    unread_b2 = mgr.get_unread_messages(agent_b, chatroom_id=1)
    assert len(unread_b2) == 1
    assert unread_b2[0]["id"] == 2


def test_direct_messages_and_privacy(temp_chatroom):
    mgr = temp_chatroom
    agent_a = "+OA-AAAA-1111"
    agent_b = "+OA-BBBB-2222"
    agent_c = "+OA-CCCC-3333"

    # Agent A sends private DM to Agent B bound to room 1
    dm_id = mgr.post_message(
        sender_id=agent_a,
        sender_name="Amity",
        sender_type="agent",
        content="Private plans for project Alpha.",
        chatroom_id=1,
        recipient_id=agent_b
    )

    # Agent B checks unread -> sees DM
    unread_b = mgr.get_unread_messages(agent_b, chatroom_id=1)
    assert len(unread_b) == 1
    assert unread_b[0]["id"] == dm_id
    assert unread_b[0]["recipient_id"] == agent_b

    # Agent C checks unread -> should NOT see the DM
    unread_c = mgr.get_unread_messages(agent_c, chatroom_id=1)
    assert len(unread_c) == 0

    # Public recent without viewer_uid -> DMs hidden
    public_recent = mgr.get_recent_messages(chatroom_id=1, limit=10)
    assert len(public_recent) == 0

    # Viewer Agent B sees the DM in recent
    b_recent = mgr.get_recent_messages(chatroom_id=1, limit=10, viewer_uid=agent_b)
    assert len(b_recent) == 1
    assert b_recent[0]["id"] == dm_id

    # UI gets all messages including DMs
    ui_msgs = mgr.get_all_ui_messages(chatroom_id=1)
    assert len(ui_msgs) == 1
    assert ui_msgs[0]["recipient_id"] == agent_b


def test_mentions_and_reactions(temp_chatroom):
    mgr = temp_chatroom
    agent_a = "+OA-AAAA-1111"
    agent_b = "+OA-BBBB-2222"

    msg_id = mgr.post_message(
        sender_id=agent_a,
        sender_name="Amity",
        sender_type="agent",
        content=f"Hello @{agent_b} can you check this?",
        chatroom_id=1
    )

    # Mentions check for Agent B
    mentions_b = mgr.get_unread_mentions(agent_b, chatroom_id=1)
    assert len(mentions_b) == 1
    assert mentions_b[0]["id"] == msg_id

    # Mentions check for Agent A
    mentions_a = mgr.get_unread_mentions(agent_a, chatroom_id=1)
    assert len(mentions_a) == 0

    # Reaction test
    assert mgr.add_reaction(msg_id, agent_b, "👍") is True
    recent = mgr.get_recent_messages(chatroom_id=1, viewer_uid=agent_b)
    assert len(recent[0]["reactions"]) == 1
    assert recent[0]["reactions"][0]["reaction"] == "👍"
    assert recent[0]["reactions"][0]["agent_uid"] == agent_b


def test_chatroom_tool_execution(temp_chatroom):
    mock_orch = MockOrchestrator(agent_id="agent_1", agent_uid="+OA-AAAA-1111", agent_name="Amity")
    tool = ChatroomSkill(orchestrator=mock_orch)

    # List rooms
    res_list = tool.execute("list_rooms")
    assert "Your Accessible Chatrooms" in res_list
    assert "General" in res_list

    # Broadcast message
    res_send = tool.execute("send", chatroom_id=1, message="Hello world!")
    assert "Message broadcast to chatroom #1 successfully" in res_send

    # Check recent
    res_recent = tool.execute("recent", chatroom_id=1)
    assert "Hello world!" in res_recent

    # React
    res_react = tool.execute("react", message_id=1, reaction="🚀")
    assert "Reacted with '🚀'" in res_react

    # Send room-bound DM
    res_dm = tool.execute("send_dm", chatroom_id=1, recipient_id="+OA-BBBB-2222", message="Private test")
    assert "Direct message sent privately to +OA-BBBB-2222 in chatroom #1" in res_dm

    # Whoami
    res_whoami = tool.execute("whoami")
    assert "+OA-AAAA-1111" in res_whoami
    assert "Amity" in res_whoami


def test_get_message_by_id(temp_chatroom):
    mgr = temp_chatroom
    msg_id = mgr.post_message(
        sender_id="+OA-AAAA-1111",
        sender_name="Amity",
        sender_type="agent",
        content="Test message for lookup",
        chatroom_id=1
    )
    msg = mgr.get_message_by_id(msg_id)
    assert msg is not None
    assert msg["id"] == msg_id
    assert msg["content"] == "Test message for lookup"
    assert mgr.get_message_by_id(99999) is None


def test_chatroom_view_anchor_click():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QUrl
    from gui.chatroom_view import ChatroomView

    app = QApplication.instance() or QApplication([])

    class MockAgentManager:
        def get_agent_id_by_uid(self, uid):
            if uid == "+OA-AAAA-1111":
                return "agent_1"
            return None
        def get_agent_name(self, aid):
            return "Amity"

    view = ChatroomView(MockAgentManager(), chatroom_id=1)
    requested_agents = []
    view.agent_tab_requested.connect(lambda t: requested_agents.append(t))

    view.on_anchor_clicked(QUrl("agent:+OA-AAAA-1111"))
    assert requested_agents == ["+OA-AAAA-1111"]


def test_user_full_name_in_chatroom_tool(temp_chatroom):
    mgr = temp_chatroom
    agent_a = "+OA-AAAA-1111"

    # User "Jane Doe" posts a message
    msg_id = mgr.post_message(
        sender_id="USER",
        sender_name="Jane Doe",
        sender_type="user",
        content="Good morning everyone!",
        chatroom_id=1
    )

    mock_orch = MockOrchestrator(agent_id="agent_1", agent_uid=agent_a, agent_name="Amity")
    tool = ChatroomSkill(orchestrator=mock_orch)

    # Check unread formatting
    unread_res = tool.execute("unread", chatroom_id=1)
    assert "[TYPE: USER] Jane Doe (USER): Good morning everyone!" in unread_res

    # Check recent_from with "USER"
    recent_user = tool.execute("recent_from", chatroom_id=1, sender_id="USER")
    assert "[TYPE: USER] Jane Doe (USER): Good morning everyone!" in recent_user

    # Check recent_from with "Jane Doe"
    recent_name = tool.execute("recent_from", chatroom_id=1, sender_id="Jane Doe")
    assert "[TYPE: USER] Jane Doe (USER): Good morning everyone!" in recent_name


def test_chatroom_view_send_user_message_uses_config(temp_chatroom, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QApplication
    from gui.chatroom_view import ChatroomView
    import json

    app = QApplication.instance() or QApplication([])

    # Setup temporary config with custom user full name
    cfg_file = str(tmp_path / "config.json")
    with open(cfg_file, "w") as f:
        json.dump({"user-full-name": "Alice Wonderland"}, f)

    monkeypatch.setattr("config.paths.get_config_file", lambda: cfg_file)
    monkeypatch.setattr("config.paths.get_app_data_dir", lambda: str(tmp_path))

    class MockAgentManager:
        def get_agent_id_by_uid(self, uid):
            return None
        def get_agent_name(self, aid):
            return "Agent"

    view = ChatroomView(MockAgentManager(), chatroom_id=1)
    view.text_input.setPlainText("Hello from Alice!")
    view.send_user_message()

    # Verify posted message in chatroom manager
    messages = temp_chatroom.get_recent_messages(chatroom_id=1, limit=10)
    assert len(messages) == 1
    assert messages[0]["sender_id"] == "USER"
    assert messages[0]["sender_name"] == "Alice Wonderland"
    assert messages[0]["sender_type"] == "user"
    assert messages[0]["content"] == "Hello from Alice!"


def test_pagination_with_before_id(temp_chatroom):
    mgr = temp_chatroom
    # Post 75 messages
    for i in range(1, 76):
        mgr.post_message(
            sender_id="USER",
            sender_name="User",
            sender_type="user",
            content=f"Message {i}",
            chatroom_id=1
        )

    # Initial batch: most recent 50 messages (messages 26 to 75)
    batch1 = mgr.get_all_ui_messages(chatroom_id=1, limit=50)
    assert len(batch1) == 50
    assert batch1[0]["content"] == "Message 26"
    assert batch1[-1]["content"] == "Message 75"

    # Query older messages before oldest id (batch1[0]["id"] is 26)
    batch2 = mgr.get_all_ui_messages(chatroom_id=1, limit=50, before_id=batch1[0]["id"])
    assert len(batch2) == 25
    assert batch2[0]["content"] == "Message 1"
    assert batch2[-1]["content"] == "Message 25"

    # Query before first message
    batch3 = mgr.get_all_ui_messages(chatroom_id=1, limit=50, before_id=batch2[0]["id"])
    assert len(batch3) == 0


def test_chatroom_view_pagination(temp_chatroom):
    from PySide6.QtWidgets import QApplication
    from gui.chatroom_view import ChatroomView

    app = QApplication.instance() or QApplication([])

    for i in range(1, 80):
        temp_chatroom.post_message(
            sender_id="USER",
            sender_name="User",
            sender_type="user",
            content=f"Message {i}",
            chatroom_id=1
        )

    class MockAgentManager:
        def get_agent_id_by_uid(self, uid):
            return None
        def get_agent_name(self, aid):
            return "Agent"

    view = ChatroomView(MockAgentManager(), chatroom_id=1)
    # Initially 50 messages loaded
    assert len(view._loaded_messages) == 50
    assert view._loaded_messages[0]["content"] == "Message 30"
    assert view._has_more_history is True

    # Call load_older_messages()
    view.load_older_messages()
    assert len(view._loaded_messages) == 79
    assert view._loaded_messages[0]["content"] == "Message 1"
    assert view._has_more_history is False


def test_delete_room_alias(temp_chatroom):
    mgr = temp_chatroom
    rid = mgr.create_chatroom("Temporary Room")
    assert rid is not None
    # Test delete_room alias
    assert mgr.delete_room(rid) is True
    assert mgr.get_chatroom(rid)["deleted_at"] is not None
    assert rid not in [r["id"] for r in mgr.list_chatrooms()]

