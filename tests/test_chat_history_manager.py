import os
import sys
import tempfile
import shutil
import pytest
from datetime import datetime, timezone, timedelta

# Add src to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.chat_history_manager import ChatHistoryManager


@pytest.fixture
def temp_chat_dir(monkeypatch):
    ChatHistoryManager.clear_instances()
    temp_dir = tempfile.mkdtemp(prefix="openamity_test_chat_hist_")
    monkeypatch.setattr("config.paths.get_app_data_dir", lambda: temp_dir)
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: os.path.join(temp_dir, "agents", aid))
    monkeypatch.setattr("config.paths.get_chat_history_db_path", lambda aid: os.path.join(temp_dir, "agents", aid, "chat_history.db"))

    yield temp_dir

    ChatHistoryManager.clear_instances()
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_chat_history_manager_crud(temp_chat_dir):
    agent_id = "+OA-TEST-0001"
    manager = ChatHistoryManager(agent_id=agent_id)

    assert manager.get_message_count() == 0

    # Add messages
    m1 = manager.add_message("User", "Hello agent!")
    assert m1["id"] == 1
    assert m1["sender"] == "User"
    assert m1["content"] == "Hello agent!"
    assert "timestamp" in m1

    m2 = manager.add_message("Agent", "Hello user, how can I assist you?")
    assert m2["id"] == 2
    assert m2["sender"] == "Agent"

    assert manager.get_message_count() == 2

    # Fetch messages
    messages = manager.get_messages(limit=10)
    assert len(messages) == 2
    assert messages[0]["id"] == 1
    assert messages[1]["id"] == 2


def test_chat_history_pagination(temp_chat_dir):
    agent_id = "+OA-TEST-PAGE"
    manager = ChatHistoryManager(agent_id=agent_id)

    # Insert 15 messages
    for i in range(1, 16):
        manager.add_message("User" if i % 2 == 1 else "Agent", f"Message {i}")

    assert manager.get_message_count() == 15

    # Fetch latest 5 messages
    latest_5 = manager.get_messages(limit=5)
    assert len(latest_5) == 5
    assert [m["id"] for m in latest_5] == [11, 12, 13, 14, 15]

    # Fetch 5 messages before id=11
    older_5 = manager.get_messages(limit=5, before_id=11)
    assert len(older_5) == 5
    assert [m["id"] for m in older_5] == [6, 7, 8, 9, 10]

    # Fetch 5 messages before id=6
    oldest_5 = manager.get_messages(limit=5, before_id=6)
    assert len(oldest_5) == 5
    assert [m["id"] for m in oldest_5] == [1, 2, 3, 4, 5]

    # Fetch before id=1 (should be empty)
    none_left = manager.get_messages(limit=5, before_id=1)
    assert len(none_left) == 0


def test_chat_history_agent_isolation(temp_chat_dir):
    agent_a = "+OA-AGENT-AAAA"
    agent_b = "+OA-AGENT-BBBB"

    mgr_a = ChatHistoryManager(agent_id=agent_a)
    mgr_b = ChatHistoryManager(agent_id=agent_b)

    mgr_a.add_message("User", "Msg for A")
    mgr_b.add_message("User", "Msg for B")

    msgs_a = mgr_a.get_messages()
    msgs_b = mgr_b.get_messages()

    assert len(msgs_a) == 1
    assert msgs_a[0]["content"] == "Msg for A"

    assert len(msgs_b) == 1
    assert msgs_b[0]["content"] == "Msg for B"


def test_chat_history_prune(temp_chat_dir):
    agent_id = "+OA-TEST-PRUN"
    manager = ChatHistoryManager(agent_id=agent_id)

    # Add an old message (10 days ago) and a recent message
    old_time = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    recent_time = datetime.now(timezone.utc).isoformat()

    manager.add_message("User", "Old message", timestamp=old_time)
    manager.add_message("User", "Recent message", timestamp=recent_time)

    assert manager.get_message_count() == 2

    pruned = manager.prune_expired(retention_days=7)
    assert pruned == 1
    assert manager.get_message_count() == 1

    remaining = manager.get_messages()
    assert remaining[0]["content"] == "Recent message"
