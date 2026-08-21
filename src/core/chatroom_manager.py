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

    def __init__(self, db_path: Optional[str] = None):
        if getattr(self, "_initialized", False):
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
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS messages (
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
                    CREATE TABLE IF NOT EXISTS agent_cursors (
                        agent_uid TEXT PRIMARY KEY,
                        last_read_message_id INTEGER NOT NULL DEFAULT 0,
                        updated_at TEXT NOT NULL
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

                cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_id_recipient ON messages(id, recipient_id);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_sender ON messages(sender_id);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_reactions_msg ON reactions(message_id);")
                conn.commit()

    def register_listener(self, callback: Callable[[Dict[str, Any]], None]):
        """Registers a callback to be called whenever a new message or reaction occurs."""
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

    def post_message(
        self,
        sender_id: str,
        sender_name: str,
        sender_type: str,
        content: str,
        recipient_id: Optional[str] = None,
        reply_to_id: Optional[int] = None,
        origin_instance: str = "local"
    ) -> int:
        """
        Posts a new message to the chatroom (public broadcast or DM).
        """
        content = content.strip()
        if not content:
            raise ValueError("Message content cannot be empty.")

        now_iso = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO messages (timestamp, sender_id, sender_name, sender_type, recipient_id, content, reply_to_id, origin_instance)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (now_iso, sender_id, sender_name, sender_type, recipient_id, content, reply_to_id, origin_instance))
                conn.commit()
                msg_id = cursor.lastrowid

        msg_dict = {
            "event": "new_message",
            "id": msg_id,
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

    def get_unread_messages(self, agent_uid: str, limit: int = 30, mark_as_read: bool = True) -> List[Dict[str, Any]]:
        """
        Fetches unread messages for agent_uid (public messages and DMs sent to this agent).
        Advances the agent's unread cursor if mark_as_read is True.
        """
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT last_read_message_id FROM agent_cursors WHERE agent_uid = ?", (agent_uid,))
                row = cursor.fetchone()
                last_read_id = row["last_read_message_id"] if row else 0

                cursor.execute("""
                    SELECT id, timestamp, sender_id, sender_name, sender_type, recipient_id, content, reply_to_id, origin_instance
                    FROM messages
                    WHERE id > ? AND (recipient_id IS NULL OR recipient_id = ?)
                    ORDER BY id ASC
                    LIMIT ?
                """, (last_read_id, agent_uid, limit))
                rows = cursor.fetchall()
                if not rows:
                    return []

                msg_ids = [r["id"] for r in rows]
                reactions_map = self._get_reactions_map(conn, msg_ids)

                messages = []
                max_id = last_read_id
                for r in rows:
                    mid = r["id"]
                    if mid > max_id:
                        max_id = mid
                    messages.append({
                        "id": mid,
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
                    now_iso = datetime.now(timezone.utc).isoformat()
                    cursor.execute("""
                        INSERT INTO agent_cursors (agent_uid, last_read_message_id, updated_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(agent_uid) DO UPDATE SET
                            last_read_message_id = excluded.last_read_message_id,
                            updated_at = excluded.updated_at
                    """, (agent_uid, max_id, now_iso))
                    conn.commit()

                return messages

    def get_unread_mentions(self, agent_uid: str, limit: int = 30) -> List[Dict[str, Any]]:
        """
        Fetches unread messages that explicitly mention @agent_uid.
        """
        mention_tag = f"@{agent_uid}"
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT last_read_message_id FROM agent_cursors WHERE agent_uid = ?", (agent_uid,))
                row = cursor.fetchone()
                last_read_id = row["last_read_message_id"] if row else 0

                cursor.execute("""
                    SELECT id, timestamp, sender_id, sender_name, sender_type, recipient_id, content, reply_to_id, origin_instance
                    FROM messages
                    WHERE id > ? AND (recipient_id IS NULL OR recipient_id = ?) AND content LIKE ?
                    ORDER BY id ASC
                    LIMIT ?
                """, (last_read_id, agent_uid, f"%{mention_tag}%", limit))
                rows = cursor.fetchall()
                if not rows:
                    return []

                msg_ids = [r["id"] for r in rows]
                reactions_map = self._get_reactions_map(conn, msg_ids)

                messages = []
                for r in rows:
                    mid = r["id"]
                    messages.append({
                        "id": mid,
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

    def get_recent_messages(self, limit: int = 20, viewer_uid: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Fetches recent public messages (and DMs if viewer_uid matches sender or recipient).
        Does NOT advance the unread cursor.
        """
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if viewer_uid:
                    cursor.execute("""
                        SELECT id, timestamp, sender_id, sender_name, sender_type, recipient_id, content, reply_to_id, origin_instance
                        FROM messages
                        WHERE recipient_id IS NULL OR recipient_id = ? OR sender_id = ?
                        ORDER BY id DESC
                        LIMIT ?
                    """, (viewer_uid, viewer_uid, limit))
                else:
                    cursor.execute("""
                        SELECT id, timestamp, sender_id, sender_name, sender_type, recipient_id, content, reply_to_id, origin_instance
                        FROM messages
                        WHERE recipient_id IS NULL
                        ORDER BY id DESC
                        LIMIT ?
                    """, (limit,))
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

    def get_recent_messages_from(self, sender_id_or_name: str, limit: int = 20, viewer_uid: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Fetches recent messages sent by a specific sender ID or sender Name.
        """
        sender_id_or_name = sender_id_or_name.strip()
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, timestamp, sender_id, sender_name, sender_type, recipient_id, content, reply_to_id, origin_instance
                    FROM messages
                    WHERE (LOWER(sender_id) = LOWER(?) OR LOWER(sender_name) = LOWER(?))
                      AND (recipient_id IS NULL OR recipient_id = ? OR sender_id = ?)
                    ORDER BY id DESC
                    LIMIT ?
                """, (sender_id_or_name, sender_id_or_name, viewer_uid or "", viewer_uid or "", limit))
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
        """
        Adds an emoji reaction to a specific message.
        """
        reaction = reaction.strip()
        if not reaction:
            return False

        now_iso = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM messages WHERE id = ?", (message_id,))
                if not cursor.fetchone():
                    return False
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
            "agent_uid": agent_uid,
            "reaction": reaction,
            "created_at": now_iso
        })
        return True

    def get_all_ui_messages(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Returns all messages (public broadcasts, user messages, and DMs) for the UI chatroom view.
        """
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, timestamp, sender_id, sender_name, sender_type, recipient_id, content, reply_to_id, origin_instance
                    FROM messages
                    ORDER BY id DESC
                    LIMIT ?
                """, (limit,))
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
        """
        Fetches a single message by ID with its reactions.
        """
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, timestamp, sender_id, sender_name, sender_type, recipient_id, content, reply_to_id, origin_instance
                    FROM messages
                    WHERE id = ?
                """, (message_id,))
                row = cursor.fetchone()
                if not row:
                    return None
                reactions_map = self._get_reactions_map(conn, [message_id])
                return {
                    "id": row["id"],
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

