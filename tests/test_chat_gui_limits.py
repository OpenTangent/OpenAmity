import os
import sys
import tempfile
import shutil
import pytest
from unittest.mock import MagicMock

# Add src to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PySide6.QtWidgets import QApplication
from core.chat_history_manager import ChatHistoryManager
from core.chatroom_manager import ChatroomManager
from core.backup_manager import EXCLUDED_FILE_NAMES
from gui.agent_view import AgentView
from gui.chatroom_view import ChatroomView


@pytest.fixture(scope="session")
def qapp():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication.instance()
    if app is None:
        app = QApplication(["-platform", "offscreen"])
    return app


@pytest.fixture
def temp_gui_env(monkeypatch):
    ChatHistoryManager.clear_instances()
    ChatroomManager._instance = None
    temp_dir = tempfile.mkdtemp(prefix="openamity_test_gui_limits_")

    monkeypatch.setattr("config.paths.get_app_data_dir", lambda: temp_dir)
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: os.path.join(temp_dir, "agents", aid))
    monkeypatch.setattr("config.paths.get_chat_history_db_path", lambda aid: os.path.join(temp_dir, "agents", aid, "chat_history.db"))
    monkeypatch.setattr("config.paths.get_chatroom_dir", lambda: os.path.join(temp_dir, "chatroom"))
    monkeypatch.setattr("config.paths.get_chatroom_db_path", lambda: os.path.join(temp_dir, "chatroom", "chatroom.db"))

    yield temp_dir

    ChatHistoryManager.clear_instances()
    ChatroomManager._instance = None
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_backup_manager_excludes_chat_history():
    """Verify that chat_history.db is explicitly excluded from .oaa backups."""
    assert "chat_history.db" in EXCLUDED_FILE_NAMES


def create_mock_orchestrator(agent_id, history_mgr=None):
    mock_orchestrator = MagicMock()
    mock_orchestrator.agent_id = agent_id
    mock_orchestrator.is_paused = False
    mock_orchestrator.is_busy = False
    mock_orchestrator.is_thinking = False
    mock_orchestrator.settings_manager.get.side_effect = lambda k, default=None: "TestAgent" if k == "core.agent.name" else default
    mock_orchestrator.chat_history_manager = history_mgr or ChatHistoryManager(agent_id=agent_id)
    return mock_orchestrator


def test_agent_view_console_log_block_count(qapp, temp_gui_env):
    """Verify AgentView console log enforces memory limit of 1000 blocks."""
    mock_orchestrator = create_mock_orchestrator("+OA-TEST-CONSOLE")

    view = AgentView(mock_orchestrator)
    assert view.console_log.document().maximumBlockCount() == 1000

    # Append 1500 lines and verify block count is capped
    for i in range(1500):
        view.append_to_console(f"Console line {i}")

    assert view.console_log.document().blockCount() <= 1000
    view.cleanup()
    view.close()


def test_agent_view_history_load_and_pagination(qapp, temp_gui_env):
    """Verify AgentView loads existing chat history from SQLite and supports scrollback pagination."""
    agent_id = "+OA-TEST-HIST"
    history_mgr = ChatHistoryManager(agent_id=agent_id)

    # Populate 75 historical messages
    for i in range(1, 76):
        history_mgr.add_message("User" if i % 2 == 1 else "Agent", f"Message {i}")

    mock_orchestrator = create_mock_orchestrator(agent_id, history_mgr)
    view = AgentView(mock_orchestrator)

    # On init, it should have loaded BATCH_SIZE (50) messages: messages 26 to 75
    assert len(view._loaded_messages) == 50
    assert view._loaded_messages[0]["content"] == "Message 26"
    assert view._loaded_messages[-1]["content"] == "Message 75"
    assert view._has_more_history is True

    # Trigger load_older_messages (simulating scroll up to top)
    view.load_older_messages()

    # Now it should have loaded the remaining 25 older messages (messages 1 to 75)
    assert len(view._loaded_messages) == 75
    assert view._loaded_messages[0]["content"] == "Message 1"
    assert view._loaded_messages[-1]["content"] == "Message 75"
    assert view._has_more_history is False

    view.cleanup()
    view.close()


def test_agent_view_max_gui_messages_limit(qapp, temp_gui_env):
    """Verify AgentView limits in-GUI loaded messages to MAX_GUI_MESSAGES for memory conservation."""
    agent_id = "+OA-TEST-LIMIT"
    mock_orchestrator = create_mock_orchestrator(agent_id)

    view = AgentView(mock_orchestrator)
    assert view.MAX_GUI_MESSAGES == 150

    # Stream 200 messages in real-time
    for i in range(1, 201):
        view.append_to_conversation("User", f"Streamed message {i}")

    # Loaded messages in GUI memory must be strictly capped at MAX_GUI_MESSAGES
    assert len(view._loaded_messages) == 150
    assert view._loaded_messages[0]["content"] == "Streamed message 51"
    assert view._loaded_messages[-1]["content"] == "Streamed message 200"
    assert view._has_more_history is True

    view.cleanup()
    view.close()


def test_chatroom_view_limits(qapp, temp_gui_env):
    """Verify ChatroomView console log and in-GUI loaded messages limits."""
    mock_agent_manager = MagicMock()
    cm = ChatroomManager()
    cm.create_chatroom("General Room")

    view = ChatroomView(mock_agent_manager, chatroom_id=1)
    assert view.console_log.document().maximumBlockCount() == 1000
    assert view.MAX_GUI_MESSAGES == 150

    # Append 1500 console lines
    for i in range(1500):
        view.append_to_console(f"Chatroom console line {i}")
    assert view.console_log.document().blockCount() <= 1000

    # Simulate 200 incoming chatroom messages
    for i in range(1, 201):
        event_dict = {
            "event": "new_message",
            "chatroom_id": 1,
            "message": {
                "id": i,
                "chatroom_id": 1,
                "sender_id": "+OA-MEMBER-0001",
                "sender_name": "Member",
                "sender_type": "agent",
                "content": f"Chatroom msg {i}",
                "timestamp": "2026-11-08T09:00:00Z"
            }
        }
        view.on_chatroom_event(event_dict)

    # In-GUI loaded messages must be capped at MAX_GUI_MESSAGES (150)
    assert len(view._loaded_messages) == 150
    assert view._loaded_messages[0]["content"] == "Chatroom msg 51"
    assert view._loaded_messages[-1]["content"] == "Chatroom msg 200"

    view.close()
