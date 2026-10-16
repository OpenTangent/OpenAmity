import os
import sqlite3
import tempfile
import pytest
from core.chatroom_manager import ChatroomManager


@pytest.fixture
def temp_db_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


def test_fresh_database_has_no_rooms_and_v2_schema(temp_db_dir):
    db_path = os.path.join(temp_db_dir, "chatroom.db")
    ChatroomManager._instance = None
    mgr = ChatroomManager(db_path=db_path)

    rooms = mgr.list_chatrooms()
    assert len(rooms) == 0

    with mgr._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA user_version;")
        assert cursor.fetchone()[0] == 2
    ChatroomManager._instance = None


def test_legacy_database_migration_to_group_chat(temp_db_dir):
    db_path = os.path.join(temp_db_dir, "chatroom.db")

    # Seed legacy v1 database
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            sender_id TEXT NOT NULL,
            sender_name TEXT NOT NULL,
            sender_type TEXT NOT NULL,
            recipient_id TEXT,
            content TEXT NOT NULL,
            reply_to_id INTEGER,
            origin_instance TEXT NOT NULL DEFAULT 'local'
        )
    """)
    cursor.execute("""
        CREATE TABLE agent_cursors (
            agent_uid TEXT PRIMARY KEY,
            last_read_message_id INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )
    """)
    cursor.execute("""
        INSERT INTO messages (timestamp, sender_id, sender_name, sender_type, content)
        VALUES ('2026-10-09T08:00:00Z', 'USER', 'User', 'user', 'Welcome to Open Amity!')
    """)
    cursor.execute("""
        INSERT INTO agent_cursors (agent_uid, last_read_message_id, updated_at)
        VALUES ('+OA-AAAA-1111', 1, '2026-10-09T08:01:00Z')
    """)
    conn.commit()
    conn.close()

    ChatroomManager._instance = None
    mgr = ChatroomManager(db_path=db_path)

    # Verify backup was created
    bak_files = [f for f in os.listdir(temp_db_dir) if f.startswith("chatroom.db.pre-v2.")]
    assert len(bak_files) == 1

    # Verify Room 1 'Group Chat' was created
    rooms = mgr.list_chatrooms()
    assert len(rooms) == 1
    assert rooms[0]["id"] == 1
    assert rooms[0]["name"] == "Group Chat"

    # Verify legacy message has chatroom_id 1
    recent = mgr.get_recent_messages(chatroom_id=1)
    assert len(recent) == 1
    assert recent[0]["chatroom_id"] == 1
    assert recent[0]["content"] == "Welcome to Open Amity!"

    # Complete legacy migration: seed all existing agents with watch = 0
    all_uids = ["+OA-AAAA-1111", "+OA-BBBB-2222"]
    res = mgr.complete_legacy_migration(all_uids)
    assert res is True

    members = mgr.get_members(chatroom_id=1)
    member_uids = {m["agent_uid"]: m["watch"] for m in members}
    assert member_uids["+OA-AAAA-1111"] is False  # watch = 0
    assert member_uids["+OA-BBBB-2222"] is False

    # Check that +OA-AAAA-1111 has cursor preserved from legacy table
    with mgr._get_connection() as c:
        cur = c.cursor()
        cur.execute("SELECT last_read_message_id FROM room_cursors WHERE chatroom_id = 1 AND reader_uid = '+OA-AAAA-1111'")
        assert cur.fetchone()[0] == 1

    ChatroomManager._instance = None


def test_room_crud_and_membership_guards(temp_db_dir):
    db_path = os.path.join(temp_db_dir, "chatroom.db")
    ChatroomManager._instance = None
    mgr = ChatroomManager(db_path=db_path)

    # Create room
    rid = mgr.create_chatroom(
        name="Dev Team",
        purpose="Engineering discussions",
        members=[{"agent_uid": "+OA-AAAA-1111", "watch": True}]
    )
    assert rid == 1
    room = mgr.get_chatroom(rid)
    assert room["name"] == "Dev Team"
    assert room["purpose"] == "Engineering discussions"

    # Reserved name check
    with pytest.raises(ValueError, match="reserved"):
        mgr.create_chatroom(name="All Agents")

    # Duplicate name check
    with pytest.raises(ValueError, match="already exists"):
        mgr.create_chatroom(name="dev team")

    # Non-member agent cannot post
    with pytest.raises(PermissionError, match="not a member"):
        mgr.post_message(
            sender_id="+OA-NOTM-0000",
            sender_name="Outsider",
            sender_type="agent",
            content="Hello?",
            chatroom_id=rid
        )

    # Member agent can post
    mid = mgr.post_message(
        sender_id="+OA-AAAA-1111",
        sender_name="DevBot",
        sender_type="agent",
        content="Deploying now.",
        chatroom_id=rid
    )
    assert mid > 0

    # DM recipient must be a member
    with pytest.raises(PermissionError, match="Recipient.*not a member"):
        mgr.post_message(
            sender_id="+OA-AAAA-1111",
            sender_name="DevBot",
            sender_type="agent",
            content="Secret info",
            chatroom_id=rid,
            recipient_id="+OA-NOTM-0000"
        )

    # Update room: add member
    mgr.update_chatroom(
        chatroom_id=rid,
        members=[
            {"agent_uid": "+OA-AAAA-1111", "watch": False},
            {"agent_uid": "+OA-BBBB-2222", "watch": True}
        ]
    )
    watchers = mgr.get_watchers(rid)
    assert watchers == ["+OA-BBBB-2222"]

    # Delete room (soft delete)
    assert mgr.delete_chatroom(rid) is True
    assert len(mgr.list_chatrooms()) == 0  # not in active list
    assert len(mgr.list_chatrooms(include_deleted=True)) == 1

    # Posting in deleted room fails
    with pytest.raises(ValueError, match="deleted"):
        mgr.post_message(
            sender_id="USER",
            sender_name="User",
            sender_type="user",
            content="Anyone here?",
            chatroom_id=rid
        )

    ChatroomManager._instance = None
