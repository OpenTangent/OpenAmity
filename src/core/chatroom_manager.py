import os
import sqlite3
import threading
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Callable
from config import paths


class ChatroomManager:
    _instance = None
    _instance_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super(ChatroomManager, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    @classmethod
    def reset_instance(cls):
        with cls._instance_lock:
            cls._instance = None

    def __init__(self, db_path: Optional[str] = None):
        if getattr(self, "_initialized", False):
            if db_path is not None and db_path != self.db_path:
                self.db_path = db_path
                self._ensure_db()
            elif not os.path.exists(self.db_path):
                self.db_path = db_path or paths.get_chatroom_db_path()
                self._ensure_db()
            return

        self.db_path = db_path or paths.get_chatroom_db_path()
        self._lock = threading.Lock()
        self._listeners: List[Callable[[Dict[str, Any]], None]] = []
        self._ensure_db()
        self._initialized = True

    def _get_connection(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _ensure_db(self):
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("PRAGMA user_version;")
                user_version = cursor.fetchone()[0]

                # Check if this is an existing legacy database needing snapshot & migration
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='messages'")
                messages_table_exists = cursor.fetchone() is not None

                if messages_table_exists and user_version < 2:
                    cursor.execute("PRAGMA table_info(messages)")
                    cols = {info[1] for info in cursor.fetchall()}
                    if "chatroom_id" not in cols:
                        # Legacy database detected. Perform safe pre-v2 snapshot backup.
                        backup_path = f"{self.db_path}.pre-v2.{int(datetime.now(timezone.utc).timestamp())}.bak"
                        try:
                            bck_conn = sqlite3.connect(backup_path)
                            conn.backup(bck_conn)
                            bck_conn.close()
                            logging.info(f"ChatroomManager: Created pre-v2 migration backup at {backup_path}")
                        except Exception as e:
                            logging.error(f"ChatroomManager: Failed to backup legacy database before migration: {e}", exc_info=True)
                            raise RuntimeError(f"ChatroomManager: Backup failed before v2 migration: {e}") from e

                # Create schema tables
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS chatrooms (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        purpose TEXT DEFAULT '',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        deleted_at TEXT
                    )
                """)
                cursor.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_chatrooms_active_name
                    ON chatrooms(LOWER(name)) WHERE deleted_at IS NULL;
                """)

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS chatroom_members (
                        chatroom_id INTEGER NOT NULL,
                        agent_uid TEXT NOT NULL,
                        watch INTEGER NOT NULL DEFAULT 0,
                        added_at TEXT NOT NULL,
                        removed_at TEXT,
                        PRIMARY KEY (chatroom_id, agent_uid),
                        FOREIGN KEY(chatroom_id) REFERENCES chatrooms(id) ON DELETE CASCADE
                    )
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_chatroom_members_lookup
                    ON chatroom_members(chatroom_id, agent_uid);
                """)

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS room_cursors (
                        chatroom_id INTEGER NOT NULL,
                        reader_uid TEXT NOT NULL,
                        last_read_message_id INTEGER NOT NULL DEFAULT 0,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (chatroom_id, reader_uid),
                        FOREIGN KEY(chatroom_id) REFERENCES chatrooms(id) ON DELETE CASCADE
                    )
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_room_cursors_lookup
                    ON room_cursors(chatroom_id, reader_uid);
                """)

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        chatroom_id INTEGER,
                        timestamp TEXT NOT NULL,
                        sender_id TEXT NOT NULL,
                        sender_name TEXT NOT NULL,
                        sender_type TEXT NOT NULL,
                        recipient_id TEXT,
                        content TEXT NOT NULL,
                        reply_to_id INTEGER,
                        origin_instance TEXT NOT NULL DEFAULT 'local',
                        FOREIGN KEY(chatroom_id) REFERENCES chatrooms(id) ON DELETE CASCADE
                    )
                """)

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS reactions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        message_id INTEGER NOT NULL,
                        agent_uid TEXT NOT NULL,
                        reaction TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        UNIQUE(message_id, agent_uid) ON CONFLICT REPLACE,
                        FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE CASCADE
                    )
                """)

                # Add chatroom_id column to messages if missing
                cursor.execute("PRAGMA table_info(messages)")
                cols = {info[1] for info in cursor.fetchall()}
                if "chatroom_id" not in cols:
                    cursor.execute("ALTER TABLE messages ADD COLUMN chatroom_id INTEGER REFERENCES chatrooms(id);")

                # Migrate legacy messages into Room 1 ('Group Chat')
                cursor.execute("SELECT COUNT(*) FROM messages WHERE chatroom_id IS NULL")
                null_count = cursor.fetchone()[0]
                if null_count > 0:
                    now_iso = datetime.now(timezone.utc).isoformat()
                    cursor.execute("SELECT id FROM chatrooms WHERE id = 1")
                    if not cursor.fetchone():
                        cursor.execute("""
                            INSERT INTO chatrooms (id, name, purpose, created_at, updated_at)
                            VALUES (1, 'Group Chat', 'General workspace chatroom', ?, ?)
                        """, (now_iso, now_iso))
                    cursor.execute("UPDATE messages SET chatroom_id = 1 WHERE chatroom_id IS NULL")

                    # Migrate legacy agent_cursors table if present
                    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='agent_cursors'")
                    if cursor.fetchone():
                        cursor.execute("""
                            INSERT OR IGNORE INTO room_cursors (chatroom_id, reader_uid, last_read_message_id, updated_at)
                            SELECT 1, agent_uid, last_read_message_id, updated_at FROM agent_cursors
                        """)

                cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_room_id ON messages(chatroom_id, id);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_id_recipient ON messages(id, recipient_id);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_sender ON messages(sender_id);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_reactions_msg ON reactions(message_id);")

                cursor.execute("PRAGMA user_version = 2;")
                conn.commit()

    def complete_legacy_migration(self, agent_uids: List[str]) -> bool:
        """
        Called by AgentManager on startup to seed all existing agents into Room 1 ('Group Chat')
        if Room 1 exists and legacy member seeding has not been completed.
        All agents are added with watch = 0.
        """
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id, name FROM chatrooms WHERE id = 1 AND deleted_at IS NULL")
                room1 = cursor.fetchone()
                if not room1 or room1["name"] != "Group Chat":
                    return False

                now_iso = datetime.now(timezone.utc).isoformat()
                cursor.execute("SELECT MAX(id) FROM messages WHERE chatroom_id = 1")
                row = cursor.fetchone()
                max_id = row[0] if row and row[0] is not None else 0

                for uid in agent_uids:
                    if not uid:
                        continue
                    cursor.execute("""
                        INSERT OR IGNORE INTO chatroom_members (chatroom_id, agent_uid, watch, added_at)
                        VALUES (1, ?, 0, ?)
                    """, (uid, now_iso))
                    cursor.execute("""
                        INSERT OR IGNORE INTO room_cursors (chatroom_id, reader_uid, last_read_message_id, updated_at)
                        VALUES (1, ?, ?, ?)
                    """, (uid, max_id, now_iso))
                conn.commit()
                return True

    def register_listener(self, callback: Callable[[Dict[str, Any]], None]):
        """Registers a callback to be called whenever a chatroom event occurs."""
        with self._lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def unregister_listener(self, callback: Callable[[Dict[str, Any]], None]):
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    def _notify_listeners(self, event_data: Dict[str, Any]):
        listeners_copy = []
        with self._lock:
            listeners_copy = list(self._listeners)
        for cb in listeners_copy:
            try:
                cb(event_data)
            except Exception as e:
                logging.error(f"ChatroomManager: Error notifying listener: {e}", exc_info=True)

    # -------------------------------------------------------------------------
    # Room Management CRUD
    # -------------------------------------------------------------------------

    def create_chatroom(
        self,
        name: str,
        members: Optional[List[Any]] = None,
        purpose: str = ""
    ) -> int:
        name = name.strip()
        if not name:
            raise ValueError("Chatroom name cannot be empty.")
        if len(name) > 40:
            raise ValueError("Chatroom name cannot exceed 40 characters.")
        if name.lower() == "all agents":
            raise ValueError("'All Agents' is a reserved tab name.")

        now_iso = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM chatrooms WHERE LOWER(name) = LOWER(?) AND deleted_at IS NULL", (name,))
                if cursor.fetchone():
                    raise ValueError(f"A chatroom named '{name}' already exists.")

                cursor.execute("""
                    INSERT INTO chatrooms (name, purpose, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                """, (name, purpose.strip(), now_iso, now_iso))
                chatroom_id = cursor.lastrowid

                if members:
                    for m in members:
                        if isinstance(m, dict):
                            uid = m.get("agent_uid", "").strip()
                            watch = 1 if m.get("watch") else 0
                        elif isinstance(m, (tuple, list)):
                            uid = str(m[0]).strip()
                            watch = 1 if len(m) > 1 and m[1] else 0
                        else:
                            uid = str(m).strip()
                            watch = 0

                        if uid:
                            cursor.execute("""
                                INSERT INTO chatroom_members (chatroom_id, agent_uid, watch, added_at)
                                VALUES (?, ?, ?, ?)
                            """, (chatroom_id, uid, watch, now_iso))
                            cursor.execute("""
                                INSERT INTO room_cursors (chatroom_id, reader_uid, last_read_message_id, updated_at)
                                VALUES (?, ?, 0, ?)
                            """, (chatroom_id, uid, now_iso))
                conn.commit()

        self._notify_listeners({
            "event": "room_created",
            "chatroom_id": chatroom_id,
            "name": name,
            "purpose": purpose.strip()
        })
        return chatroom_id

    def update_chatroom(
        self,
        chatroom_id: int,
        name: Optional[str] = None,
        purpose: Optional[str] = None,
        members: Optional[List[Any]] = None
    ) -> bool:
        now_iso = datetime.now(timezone.utc).isoformat()
        added_uids = []
        removed_uids = []
        watch_changes = {}

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id, name, purpose FROM chatrooms WHERE id = ? AND deleted_at IS NULL", (chatroom_id,))
                current = cursor.fetchone()
                if not current:
                    raise ValueError(f"Chatroom ID {chatroom_id} not found.")

                new_name = current["name"]
                if name is not None:
                    new_name = name.strip()
                    if not new_name:
                        raise ValueError("Chatroom name cannot be empty.")
                    if len(new_name) > 40:
                        raise ValueError("Chatroom name cannot exceed 40 characters.")
                    if new_name.lower() == "all agents":
                        raise ValueError("'All Agents' is a reserved tab name.")
                    cursor.execute("SELECT id FROM chatrooms WHERE LOWER(name) = LOWER(?) AND id != ? AND deleted_at IS NULL", (new_name, chatroom_id))
                    if cursor.fetchone():
                        raise ValueError(f"A chatroom named '{new_name}' already exists.")

                new_purpose = current["purpose"] if purpose is None else purpose.strip()

                cursor.execute("""
                    UPDATE chatrooms SET name = ?, purpose = ?, updated_at = ?
                    WHERE id = ?
                """, (new_name, new_purpose, now_iso, chatroom_id))

                if members is not None:
                    cursor.execute("""
                        SELECT agent_uid, watch FROM chatroom_members
                        WHERE chatroom_id = ? AND removed_at IS NULL
                    """, (chatroom_id,))
                    existing_map = {row["agent_uid"]: bool(row["watch"]) for row in cursor.fetchall()}

                    target_map = {}
                    for m in members:
                        if isinstance(m, dict):
                            uid = m.get("agent_uid", "").strip()
                            watch = bool(m.get("watch"))
                        elif isinstance(m, (tuple, list)):
                            uid = str(m[0]).strip()
                            watch = bool(m[1]) if len(m) > 1 else False
                        else:
                            uid = str(m).strip()
                            watch = False
                        if uid:
                            target_map[uid] = watch

                    cursor.execute("SELECT MAX(id) FROM messages WHERE chatroom_id = ?", (chatroom_id,))
                    row = cursor.fetchone()
                    max_id = row[0] if row and row[0] is not None else 0

                    for uid, watch in target_map.items():
                        watch_int = 1 if watch else 0
                        if uid not in existing_map:
                            added_uids.append(uid)
                            cursor.execute("""
                                INSERT INTO chatroom_members (chatroom_id, agent_uid, watch, added_at, removed_at)
                                VALUES (?, ?, ?, ?, NULL)
                                ON CONFLICT(chatroom_id, agent_uid) DO UPDATE SET
                                    watch = excluded.watch,
                                    added_at = excluded.added_at,
                                    removed_at = NULL
                            """, (chatroom_id, uid, watch_int, now_iso))
                            cursor.execute("""
                                INSERT INTO room_cursors (chatroom_id, reader_uid, last_read_message_id, updated_at)
                                VALUES (?, ?, ?, ?)
                                ON CONFLICT(chatroom_id, reader_uid) DO UPDATE SET
                                    last_read_message_id = MAX(room_cursors.last_read_message_id, excluded.last_read_message_id),
                                    updated_at = excluded.updated_at
                            """, (chatroom_id, uid, max_id, now_iso))
                        else:
                            if existing_map[uid] != watch:
                                watch_changes[uid] = watch
                                cursor.execute("""
                                    UPDATE chatroom_members SET watch = ? WHERE chatroom_id = ? AND agent_uid = ?
                                """, (watch_int, chatroom_id, uid))

                    for uid in existing_map:
                        if uid not in target_map:
                            removed_uids.append(uid)
                            cursor.execute("""
                                UPDATE chatroom_members SET removed_at = ? WHERE chatroom_id = ? AND agent_uid = ?
                            """, (now_iso, chatroom_id, uid))

                conn.commit()

        self._notify_listeners({
            "event": "room_updated",
            "chatroom_id": chatroom_id,
            "name": new_name,
            "purpose": new_purpose
        })
        if added_uids or removed_uids or watch_changes:
            self._notify_listeners({
                "event": "membership_changed",
                "chatroom_id": chatroom_id,
                "added": added_uids,
                "removed": removed_uids,
                "watch_changes": watch_changes
            })
        return True

    def delete_chatroom(self, chatroom_id: int) -> bool:
        """Soft-deletes a chatroom by recording deleted_at."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM chatrooms WHERE id = ? AND deleted_at IS NULL", (chatroom_id,))
                if not cursor.fetchone():
                    return False
                cursor.execute("UPDATE chatrooms SET deleted_at = ?, updated_at = ? WHERE id = ?", (now_iso, now_iso, chatroom_id))
                conn.commit()

        self._notify_listeners({
            "event": "room_deleted",
            "chatroom_id": chatroom_id
        })
        return True

    # Backward compatibility alias
    delete_room = delete_chatroom

    def list_chatrooms(self, include_deleted: bool = False) -> List[Dict[str, Any]]:
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                where = "" if include_deleted else "WHERE c.deleted_at IS NULL"
                cursor.execute(f"""
                    SELECT c.id, c.name, c.purpose, c.created_at, c.updated_at, c.deleted_at,
                           COUNT(m.agent_uid) as member_count
                    FROM chatrooms c
                    LEFT JOIN chatroom_members m ON c.id = m.chatroom_id AND m.removed_at IS NULL
                    {where}
                    GROUP BY c.id
                    ORDER BY c.id ASC
                """)
                return [dict(r) for r in cursor.fetchall()]

    def get_chatroom(self, chatroom_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT c.id, c.name, c.purpose, c.created_at, c.updated_at, c.deleted_at,
                           COUNT(m.agent_uid) as member_count
                    FROM chatrooms c
                    LEFT JOIN chatroom_members m ON c.id = m.chatroom_id AND m.removed_at IS NULL
                    WHERE c.id = ?
                    GROUP BY c.id
                """, (chatroom_id,))
                row = cursor.fetchone()
                return dict(row) if row else None

    def get_members(self, chatroom_id: int, include_removed: bool = False) -> List[Dict[str, Any]]:
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                where = "" if include_removed else "AND removed_at IS NULL"
                cursor.execute(f"""
                    SELECT agent_uid, watch, added_at, removed_at
                    FROM chatroom_members
                    WHERE chatroom_id = ? {where}
                    ORDER BY added_at ASC
                """, (chatroom_id,))
                return [
                    {
                        "agent_uid": r["agent_uid"],
                        "watch": bool(r["watch"]),
                        "added_at": r["added_at"],
                        "removed_at": r["removed_at"]
                    }
                    for r in cursor.fetchall()
                ]

    def get_watchers(self, chatroom_id: int) -> List[str]:
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT agent_uid FROM chatroom_members
                    WHERE chatroom_id = ? AND watch = 1 AND removed_at IS NULL
                """, (chatroom_id,))
                return [r["agent_uid"] for r in cursor.fetchall()]

    def is_member(self, chatroom_id: int, agent_uid: str) -> bool:
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT 1 FROM chatroom_members
                    WHERE chatroom_id = ? AND agent_uid = ? AND removed_at IS NULL
                """, (chatroom_id, agent_uid))
                return cursor.fetchone() is not None

    def remove_agent_everywhere(self, agent_uid: str):
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE chatroom_members SET removed_at = ?
                    WHERE agent_uid = ? AND removed_at IS NULL
                """, (now_iso, agent_uid))
                conn.commit()

    def get_agent_chatrooms(self, agent_uid: str) -> List[Dict[str, Any]]:
        """
        Returns list of chatrooms agent_uid has active access to,
        including unread_count and mention_count,
        sorted by mention_count DESC, unread_count DESC, name ASC.
        """
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT c.id, c.name, c.purpose, cm.watch,
                           COALESCE(rc.last_read_message_id, 0) as last_read_id
                    FROM chatrooms c
                    JOIN chatroom_members cm ON c.id = cm.chatroom_id AND cm.removed_at IS NULL
                    LEFT JOIN room_cursors rc ON c.id = rc.chatroom_id AND rc.reader_uid = ?
                    WHERE cm.agent_uid = ? AND c.deleted_at IS NULL
                """, (agent_uid, agent_uid))
                rooms = cursor.fetchall()
                if not rooms:
                    return []

                results = []
                mention_tag = f"@{agent_uid}"
                for r in rooms:
                    rid = r["id"]
                    last_read = r["last_read_id"]
                    cursor.execute("""
                        SELECT COUNT(*) as member_count FROM chatroom_members
                        WHERE chatroom_id = ? AND removed_at IS NULL
                    """, (rid,))
                    mcount = cursor.fetchone()["member_count"]

                    cursor.execute("""
                        SELECT id, content, recipient_id FROM messages
                        WHERE chatroom_id = ? AND id > ? AND sender_id != ?
                          AND (recipient_id IS NULL OR recipient_id = ?)
                    """, (rid, last_read, agent_uid, agent_uid))
                    unreads = cursor.fetchall()
                    unread_count = len(unreads)
                    mention_count = 0
                    for u in unreads:
                        if u["recipient_id"] == agent_uid or mention_tag in u["content"]:
                            mention_count += 1

                    results.append({
                        "chatroom_id": rid,
                        "name": r["name"],
                        "purpose": r["purpose"] or "",
                        "member_count": mcount,
                        "you_watch": bool(r["watch"]),
                        "unread_count": unread_count,
                        "mention_count": mention_count
                    })

                results.sort(key=lambda x: (-x["mention_count"], -x["unread_count"], x["name"].lower()))
                return results

    def get_user_room_badges(self) -> Dict[int, Dict[str, int]]:
        """Returns unread and mention counts for USER across active chatrooms."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM chatrooms WHERE deleted_at IS NULL")
                rooms = cursor.fetchall()
                badges = {}
                for r in rooms:
                    rid = r["id"]
                    cursor.execute("SELECT last_read_message_id FROM room_cursors WHERE chatroom_id = ? AND reader_uid = 'USER'", (rid,))
                    row = cursor.fetchone()
                    last_read = row["last_read_message_id"] if row else 0

                    cursor.execute("""
                        SELECT id, content FROM messages
                        WHERE chatroom_id = ? AND id > ? AND sender_type != 'user'
                          AND (recipient_id IS NULL OR recipient_id = 'USER')
                    """, (rid, last_read))
                    unreads = cursor.fetchall()
                    unread_count = len(unreads)
                    mention_count = sum(1 for u in unreads if "@USER" in u["content"] or "@User" in u["content"])
                    badges[rid] = {"unread_count": unread_count, "mention_count": mention_count}
                return badges

    # -------------------------------------------------------------------------
    # Messages and Reactions
    # -------------------------------------------------------------------------

    def post_message(
        self,
        sender_id: str,
        sender_name: str,
        sender_type: str,
        content: str,
        chatroom_id: int = 1,
        recipient_id: Optional[str] = None,
        reply_to_id: Optional[int] = None,
        origin_instance: str = "local"
    ) -> int:
        """
        Posts a new message to the specified chatroom (public broadcast or DM).
        """
        content = content.strip()
        if not content:
            raise ValueError("Message content cannot be empty.")

        now_iso = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id, name, deleted_at FROM chatrooms WHERE id = ?", (chatroom_id,))
                room = cursor.fetchone()
                if not room or room["deleted_at"] is not None:
                    raise ValueError(f"Chatroom ID {chatroom_id} does not exist or has been deleted.")

                if sender_type == "agent":
                    cursor.execute("""
                        SELECT 1 FROM chatroom_members
                        WHERE chatroom_id = ? AND agent_uid = ? AND removed_at IS NULL
                    """, (chatroom_id, sender_id))
                    if not cursor.fetchone():
                        raise PermissionError(f"Agent '{sender_id}' is not a member of chatroom '{room['name']}' (ID: {chatroom_id}).")

                if recipient_id:
                    if recipient_id != "USER":
                        cursor.execute("""
                            SELECT 1 FROM chatroom_members
                            WHERE chatroom_id = ? AND agent_uid = ? AND removed_at IS NULL
                        """, (chatroom_id, recipient_id))
                        if not cursor.fetchone():
                            raise PermissionError(f"Recipient '{recipient_id}' is not a member of chatroom '{room['name']}' (ID: {chatroom_id}).")

                cursor.execute("""
                    INSERT INTO messages (chatroom_id, timestamp, sender_id, sender_name, sender_type, recipient_id, content, reply_to_id, origin_instance)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (chatroom_id, now_iso, sender_id, sender_name, sender_type, recipient_id, content, reply_to_id, origin_instance))
                conn.commit()
                msg_id = cursor.lastrowid

                # Advance sender's own cursor
                reader_tag = "USER" if sender_type == "user" else sender_id
                cursor.execute("""
                    INSERT INTO room_cursors (chatroom_id, reader_uid, last_read_message_id, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(chatroom_id, reader_uid) DO UPDATE SET
                        last_read_message_id = MAX(room_cursors.last_read_message_id, excluded.last_read_message_id),
                        updated_at = excluded.updated_at
                """, (chatroom_id, reader_tag, msg_id, now_iso))
                conn.commit()

        msg_dict = {
            "event": "new_message",
            "id": msg_id,
            "chatroom_id": chatroom_id,
            "timestamp": now_iso,
            "sender_id": sender_id,
            "sender_name": sender_name,
            "sender_type": sender_type,
            "recipient_id": recipient_id,
            "content": content,
            "reply_to_id": reply_to_id,
            "origin_instance": origin_instance,
            "reactions": []
        }
        self._notify_listeners(msg_dict)
        return msg_id

    def _get_reactions_map(self, conn: sqlite3.Connection, message_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
        if not message_ids:
            return {}
        placeholders = ",".join("?" for _ in message_ids)
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT message_id, agent_uid, reaction, created_at
            FROM reactions
            WHERE message_id IN ({placeholders})
            ORDER BY created_at ASC
        """, message_ids)
        reactions_map: Dict[int, List[Dict[str, Any]]] = {mid: [] for mid in message_ids}
        for row in cursor.fetchall():
            mid = row["message_id"]
            if mid in reactions_map:
                reactions_map[mid].append({
                    "agent_uid": row["agent_uid"],
                    "reaction": row["reaction"],
                    "created_at": row["created_at"]
                })
        return reactions_map

    def get_unread_messages(
        self,
        agent_uid: str,
        chatroom_id: Optional[int] = None,
        limit: int = 30,
        mark_as_read: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Fetches unread messages for agent_uid.
        If chatroom_id is provided, queries that specific room.
        If chatroom_id is None, queries across all active rooms the agent is a member of.
        Advances read cursor(s) if mark_as_read is True.
        """
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if chatroom_id is not None:
                    rooms_to_check = [chatroom_id]
                else:
                    cursor.execute("""
                        SELECT chatroom_id FROM chatroom_members cm
                        JOIN chatrooms c ON cm.chatroom_id = c.id
                        WHERE cm.agent_uid = ? AND cm.removed_at IS NULL AND c.deleted_at IS NULL
                    """, (agent_uid,))
                    rooms_to_check = [r["chatroom_id"] for r in cursor.fetchall()]

                if not rooms_to_check:
                    return []

                all_messages = []
                now_iso = datetime.now(timezone.utc).isoformat()
                for rid in rooms_to_check:
                    cursor.execute("""
                        SELECT last_read_message_id FROM room_cursors
                        WHERE chatroom_id = ? AND reader_uid = ?
                    """, (rid, agent_uid))
                    row = cursor.fetchone()
                    last_read_id = row["last_read_message_id"] if row else 0

                    cursor.execute("""
                        SELECT id, chatroom_id, timestamp, sender_id, sender_name, sender_type, recipient_id, content, reply_to_id, origin_instance
                        FROM messages
                        WHERE chatroom_id = ? AND id > ? AND sender_id != ?
                          AND (recipient_id IS NULL OR recipient_id = ?)
                        ORDER BY id ASC
                        LIMIT ?
                    """, (rid, last_read_id, agent_uid, agent_uid, limit))
                    rows = cursor.fetchall()
                    if not rows:
                        continue

                    msg_ids = [r["id"] for r in rows]
                    reactions_map = self._get_reactions_map(conn, msg_ids)

                    max_id = last_read_id
                    for r in rows:
                        mid = r["id"]
                        if mid > max_id:
                            max_id = mid
                        all_messages.append({
                            "id": mid,
                            "chatroom_id": r["chatroom_id"],
                            "timestamp": r["timestamp"],
                            "sender_id": r["sender_id"],
                            "sender_name": r["sender_name"],
                            "sender_type": r["sender_type"],
                            "recipient_id": r["recipient_id"],
                            "content": r["content"],
                            "reply_to_id": r["reply_to_id"],
                            "origin_instance": r["origin_instance"],
                            "reactions": reactions_map.get(mid, [])
                        })

                    if mark_as_read and max_id > last_read_id:
                        cursor.execute("""
                            INSERT INTO room_cursors (chatroom_id, reader_uid, last_read_message_id, updated_at)
                            VALUES (?, ?, ?, ?)
                            ON CONFLICT(chatroom_id, reader_uid) DO UPDATE SET
                                last_read_message_id = excluded.last_read_message_id,
                                updated_at = excluded.updated_at
                        """, (rid, agent_uid, max_id, now_iso))
                        conn.commit()

                return all_messages

    def get_unread_mentions(
        self,
        agent_uid: str,
        chatroom_id: Optional[int] = None,
        limit: int = 30
    ) -> List[Dict[str, Any]]:
        """
        Fetches unread messages that explicitly mention @agent_uid.
        """
        mention_tag = f"@{agent_uid}"
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if chatroom_id is not None:
                    rooms_to_check = [chatroom_id]
                else:
                    cursor.execute("""
                        SELECT chatroom_id FROM chatroom_members cm
                        JOIN chatrooms c ON cm.chatroom_id = c.id
                        WHERE cm.agent_uid = ? AND cm.removed_at IS NULL AND c.deleted_at IS NULL
                    """, (agent_uid,))
                    rooms_to_check = [r["chatroom_id"] for r in cursor.fetchall()]

                if not rooms_to_check:
                    return []

                all_messages = []
                for rid in rooms_to_check:
                    cursor.execute("""
                        SELECT last_read_message_id FROM room_cursors
                        WHERE chatroom_id = ? AND reader_uid = ?
                    """, (rid, agent_uid))
                    row = cursor.fetchone()
                    last_read_id = row["last_read_message_id"] if row else 0

                    cursor.execute("""
                        SELECT id, chatroom_id, timestamp, sender_id, sender_name, sender_type, recipient_id, content, reply_to_id, origin_instance
                        FROM messages
                        WHERE chatroom_id = ? AND id > ? AND sender_id != ?
                          AND (recipient_id IS NULL OR recipient_id = ?) AND content LIKE ?
                        ORDER BY id ASC
                        LIMIT ?
                    """, (rid, last_read_id, agent_uid, agent_uid, f"%{mention_tag}%", limit))
                    rows = cursor.fetchall()
                    if not rows:
                        continue

                    msg_ids = [r["id"] for r in rows]
                    reactions_map = self._get_reactions_map(conn, msg_ids)

                    for r in rows:
                        mid = r["id"]
                        all_messages.append({
                            "id": mid,
                            "chatroom_id": r["chatroom_id"],
                            "timestamp": r["timestamp"],
                            "sender_id": r["sender_id"],
                            "sender_name": r["sender_name"],
                            "sender_type": r["sender_type"],
                            "recipient_id": r["recipient_id"],
                            "content": r["content"],
                            "reply_to_id": r["reply_to_id"],
                            "origin_instance": r["origin_instance"],
                            "reactions": reactions_map.get(mid, [])
                        })
                return all_messages

    def get_recent_messages(
        self,
        chatroom_id: int = 1,
        limit: int = 20,
        viewer_uid: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetches recent messages in chatroom_id without advancing read cursors.
        """
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if viewer_uid:
                    cursor.execute("""
                        SELECT id, chatroom_id, timestamp, sender_id, sender_name, sender_type, recipient_id, content, reply_to_id, origin_instance
                        FROM messages
                        WHERE chatroom_id = ? AND (recipient_id IS NULL OR recipient_id = ? OR sender_id = ?)
                        ORDER BY id DESC
                        LIMIT ?
                    """, (chatroom_id, viewer_uid, viewer_uid, limit))
                else:
                    cursor.execute("""
                        SELECT id, chatroom_id, timestamp, sender_id, sender_name, sender_type, recipient_id, content, reply_to_id, origin_instance
                        FROM messages
                        WHERE chatroom_id = ? AND recipient_id IS NULL
                        ORDER BY id DESC
                        LIMIT ?
                    """, (chatroom_id, limit))
                rows = cursor.fetchall()
                if not rows:
                    return []

                rows = list(reversed(rows))
                msg_ids = [r["id"] for r in rows]
                reactions_map = self._get_reactions_map(conn, msg_ids)

                messages = []
                for r in rows:
                    mid = r["id"]
                    messages.append({
                        "id": mid,
                        "chatroom_id": r["chatroom_id"],
                        "timestamp": r["timestamp"],
                        "sender_id": r["sender_id"],
                        "sender_name": r["sender_name"],
                        "sender_type": r["sender_type"],
                        "recipient_id": r["recipient_id"],
                        "content": r["content"],
                        "reply_to_id": r["reply_to_id"],
                        "origin_instance": r["origin_instance"],
                        "reactions": reactions_map.get(mid, [])
                    })
                return messages

    def get_recent_messages_from(
        self,
        chatroom_id: int = 1,
        sender_id_or_name: str = "",
        limit: int = 20,
        viewer_uid: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        sender_id_or_name = sender_id_or_name.strip()
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, chatroom_id, timestamp, sender_id, sender_name, sender_type, recipient_id, content, reply_to_id, origin_instance
                    FROM messages
                    WHERE chatroom_id = ?
                      AND (LOWER(sender_id) = LOWER(?) OR LOWER(sender_name) = LOWER(?))
                      AND (recipient_id IS NULL OR recipient_id = ? OR sender_id = ?)
                    ORDER BY id DESC
                    LIMIT ?
                """, (chatroom_id, sender_id_or_name, sender_id_or_name, viewer_uid or "", viewer_uid or "", limit))
                rows = cursor.fetchall()
                if not rows:
                    return []

                rows = list(reversed(rows))
                msg_ids = [r["id"] for r in rows]
                reactions_map = self._get_reactions_map(conn, msg_ids)

                messages = []
                for r in rows:
                    mid = r["id"]
                    messages.append({
                        "id": mid,
                        "chatroom_id": r["chatroom_id"],
                        "timestamp": r["timestamp"],
                        "sender_id": r["sender_id"],
                        "sender_name": r["sender_name"],
                        "sender_type": r["sender_type"],
                        "recipient_id": r["recipient_id"],
                        "content": r["content"],
                        "reply_to_id": r["reply_to_id"],
                        "origin_instance": r["origin_instance"],
                        "reactions": reactions_map.get(mid, [])
                    })
                return messages

    def add_reaction(self, message_id: int, agent_uid: str, reaction: str) -> bool:
        reaction = reaction.strip()
        if not reaction:
            return False

        now_iso = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id, chatroom_id FROM messages WHERE id = ?", (message_id,))
                msg = cursor.fetchone()
                if not msg:
                    return False
                chatroom_id = msg["chatroom_id"]

                cursor.execute("""
                    INSERT INTO reactions (message_id, agent_uid, reaction, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(message_id, agent_uid) DO UPDATE SET
                        reaction = excluded.reaction,
                        created_at = excluded.created_at
                """, (message_id, agent_uid, reaction, now_iso))
                conn.commit()

        self._notify_listeners({
            "event": "new_reaction",
            "message_id": message_id,
            "chatroom_id": chatroom_id,
            "agent_uid": agent_uid,
            "reaction": reaction,
            "created_at": now_iso
        })
        return True

    def get_all_ui_messages(self, chatroom_id: int = 1, limit: int = 50, before_id: Optional[int] = None) -> List[Dict[str, Any]]:
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if before_id is not None:
                    cursor.execute("""
                        SELECT id, chatroom_id, timestamp, sender_id, sender_name, sender_type, recipient_id, content, reply_to_id, origin_instance
                        FROM messages
                        WHERE chatroom_id = ? AND id < ?
                        ORDER BY id DESC
                        LIMIT ?
                    """, (chatroom_id, before_id, limit))
                else:
                    cursor.execute("""
                        SELECT id, chatroom_id, timestamp, sender_id, sender_name, sender_type, recipient_id, content, reply_to_id, origin_instance
                        FROM messages
                        WHERE chatroom_id = ?
                        ORDER BY id DESC
                        LIMIT ?
                    """, (chatroom_id, limit))
                rows = cursor.fetchall()
                if not rows:
                    return []

                rows = list(reversed(rows))
                msg_ids = [r["id"] for r in rows]
                reactions_map = self._get_reactions_map(conn, msg_ids)

                messages = []
                for r in rows:
                    mid = r["id"]
                    messages.append({
                        "id": mid,
                        "chatroom_id": r["chatroom_id"],
                        "timestamp": r["timestamp"],
                        "sender_id": r["sender_id"],
                        "sender_name": r["sender_name"],
                        "sender_type": r["sender_type"],
                        "recipient_id": r["recipient_id"],
                        "content": r["content"],
                        "reply_to_id": r["reply_to_id"],
                        "origin_instance": r["origin_instance"],
                        "reactions": reactions_map.get(mid, [])
                    })
                return messages

    def get_message_by_id(self, message_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, chatroom_id, timestamp, sender_id, sender_name, sender_type, recipient_id, content, reply_to_id, origin_instance
                    FROM messages
                    WHERE id = ?
                """, (message_id,))
                row = cursor.fetchone()
                if not row:
                    return None
                reactions_map = self._get_reactions_map(conn, [message_id])
                return {
                    "id": row["id"],
                    "chatroom_id": row["chatroom_id"],
                    "timestamp": row["timestamp"],
                    "sender_id": row["sender_id"],
                    "sender_name": row["sender_name"],
                    "sender_type": row["sender_type"],
                    "recipient_id": row["recipient_id"],
                    "content": row["content"],
                    "reply_to_id": row["reply_to_id"],
                    "origin_instance": row["origin_instance"],
                    "reactions": reactions_map.get(message_id, [])
                }

    def mark_room_read(self, chatroom_id: int, reader_uid: str):
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM chatrooms WHERE id = ?", (chatroom_id,))
                if not cursor.fetchone():
                    return

                cursor.execute("SELECT MAX(id) FROM messages WHERE chatroom_id = ?", (chatroom_id,))
                row = cursor.fetchone()
                max_id = row[0] if row and row[0] is not None else 0

                cursor.execute("""
                    INSERT INTO room_cursors (chatroom_id, reader_uid, last_read_message_id, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(chatroom_id, reader_uid) DO UPDATE SET
                        last_read_message_id = MAX(room_cursors.last_read_message_id, excluded.last_read_message_id),
                        updated_at = excluded.updated_at
                """, (chatroom_id, reader_uid, max_id, now_iso))
                conn.commit()

        self._notify_listeners({
            "event": "room_read",
            "chatroom_id": chatroom_id,
            "reader_uid": reader_uid
        })
