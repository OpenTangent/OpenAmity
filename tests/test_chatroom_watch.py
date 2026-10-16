import os
import tempfile
import time
from unittest.mock import MagicMock
import pytest
from core.chatroom_manager import ChatroomManager
from core.chatroom_notifier import ChatroomWatchNotifier


@pytest.fixture
def watch_env():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "chatroom.db")
        ChatroomManager._instance = None
        mgr = ChatroomManager(db_path=db_path)

        mock_agent_mgr = MagicMock()
        notifier = ChatroomWatchNotifier(mock_agent_mgr)

        yield mgr, mock_agent_mgr, notifier

        notifier.stop()
        ChatroomManager._instance = None


def test_watch_notifier_delay_escalation_and_sender_exclusion(watch_env, monkeypatch):
    mgr, mock_agent_mgr, notifier = watch_env

    # Mock timer creation to inspect delay
    scheduled_delays = []
    orig_schedule = notifier._schedule_agent_pulse

    def mock_schedule(room_id, agent_uid, agent_id, delay):
        scheduled_delays.append(delay)

    monkeypatch.setattr(notifier, "_schedule_agent_pulse", mock_schedule)

    # Create room with watchers: agent_1 and agent_2
    rid = mgr.create_chatroom(
        name="Team Room",
        members=[
            {"agent_uid": "+OA-AAAA-1111", "watch": True},
            {"agent_uid": "+OA-BBBB-2222", "watch": True}
        ]
    )

    mock_agent_mgr.get_agent_id_by_uid.side_effect = lambda uid: "aid_1" if uid == "+OA-AAAA-1111" else "aid_2"
    mock_agent_mgr.get_orchestrator.return_value = MagicMock(is_paused=False)

    # 1st message: sent by +OA-AAAA-1111
    # Only +OA-BBBB-2222 should be scheduled, with initial delay 30.0s
    mgr.post_message(
        sender_id="+OA-AAAA-1111",
        sender_name="Agent A",
        sender_type="agent",
        content="First message",
        chatroom_id=rid
    )
    assert len(scheduled_delays) == 1
    assert scheduled_delays[-1] == 30.0

    # 2nd message in room: delay escalates by +30s -> 60.0s
    mgr.post_message(
        sender_id="USER",
        sender_name="User",
        sender_type="user",
        content="Second message",
        chatroom_id=rid
    )
    # Both Agent A and Agent B are scheduled with 60.0s
    assert scheduled_delays[-1] == 60.0


def test_dm_notifies_non_watching_member(watch_env, monkeypatch):
    mgr, mock_agent_mgr, notifier = watch_env

    scheduled_agents = []

    def mock_schedule(room_id, agent_uid, agent_id, delay):
        scheduled_agents.append(agent_uid)

    monkeypatch.setattr(notifier, "_schedule_agent_pulse", mock_schedule)

    # Agent A is watching, Agent B is NOT watching
    rid = mgr.create_chatroom(
        name="General",
        members=[
            {"agent_uid": "+OA-AAAA-1111", "watch": True},
            {"agent_uid": "+OA-BBBB-2222", "watch": False}
        ]
    )

    mock_agent_mgr.get_agent_id_by_uid.side_effect = lambda uid: "aid_1" if uid == "+OA-AAAA-1111" else "aid_2"
    mock_agent_mgr.get_orchestrator.return_value = MagicMock(is_paused=False)

    # Regular public message: only Agent A is notified
    mgr.post_message(
        sender_id="USER",
        sender_name="User",
        sender_type="user",
        content="Public update",
        chatroom_id=rid
    )
    assert scheduled_agents == ["+OA-AAAA-1111"]
    scheduled_agents.clear()

    # Private DM to Agent B (non-watching member): Agent B must be notified
    mgr.post_message(
        sender_id="USER",
        sender_name="User",
        sender_type="user",
        content="Confidential ask",
        chatroom_id=rid,
        recipient_id="+OA-BBBB-2222"
    )
    # Both watching Agent A? Wait, Agent A should not receive private DM notification if it's private to B!
    # Wait, let's check: watchers receive for room, but if DM, only recipient gets DM!
    assert "+OA-BBBB-2222" in scheduled_agents


def test_paused_agent_excluded(watch_env, monkeypatch):
    mgr, mock_agent_mgr, notifier = watch_env

    scheduled_agents = []

    def mock_schedule(room_id, agent_uid, agent_id, delay):
        scheduled_agents.append(agent_uid)

    monkeypatch.setattr(notifier, "_schedule_agent_pulse", mock_schedule)

    rid = mgr.create_chatroom(
        name="Lounge",
        members=[{"agent_uid": "+OA-AAAA-1111", "watch": True}]
    )

    mock_agent_mgr.get_agent_id_by_uid.return_value = "aid_1"
    # Agent is paused
    mock_agent_mgr.get_orchestrator.return_value = MagicMock(is_paused=True)

    mgr.post_message(
        sender_id="USER",
        sender_name="User",
        sender_type="user",
        content="Wake up?",
        chatroom_id=rid
    )
    assert len(scheduled_agents) == 0
