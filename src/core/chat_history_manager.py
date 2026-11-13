import os
import sqlite3
import threading
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
from config import paths


class ChatHistoryManager:
    """Manages persistent one-on-one chat history for agents using SQLite.

    Provides high-performance, thread-safe message persistence, pagination,
    and history inspection, isolated per agent in their respective data directories.
    """

    _instances = {}
    _class_lock = threading.RLock()

    def __new__(cls, agent_id: Optional[str] = None, db_path: Optional[str] = None):
        with cls._class_lock:
            resolved_path = db_path or paths.get_chat_history_db_path(agent_id) if agent_id else (db_path or "")
            norm_path = os.path.abspath(resolved_path) if resolved_path else ""
            if norm_path not in cls._instances:
                instance = super().__new__(cls)
                instance._initialized = False
                cls._instances[norm_path] = instance
            return cls._instances[norm_path]

    @classmethod
    def clear_instances(cls):
        with cls._class_lock:
            cls._instances.clear()

    @classmethod
    def remove_instance(cls, agent_id: Optional[str] = None, db_path: Optional[str] = None):
        with cls._class_lock:
            resolved_path = db_path or paths.get_chat_history_db_path(agent_id) if agent_id else (db_path or "")
            norm_path = os.path.abspath(resolved_path) if resolved_path else ""
            cls._instances.pop(norm_path, None)

    def __init__(self, agent_id: Optional[str] = None, db_path: Optional[str] = None):
        if getattr(self, "_initialized", False):
            return

        self.agent_id = agent_id
        self.db_path = db_path or paths.get_chat_history_db_path(agent_id) if agent_id else db_path
        if not self.db_path:
            raise ValueError("Either agent_id or db_path must be provided to ChatHistoryManager")

        self._lock = threading.RLock()
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
                        sender TEXT NOT NULL,
                        content TEXT NOT NULL
                    )
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_chat_messages_timestamp
                    ON messages(timestamp);
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_chat_messages_id
                    ON messages(id);
                """)
                conn.commit()

    def add_message(self, sender: str, content: str, timestamp: Optional[str] = None) -> Dict[str, Any]:
        """Persists a new message into the agent's chat history.

        Args:
            sender: The sender display name or role (e.g. 'User', 'Agent', 'System')
            content: The message text / markdown content
            timestamp: Optional ISO 8601 timestamp; defaults to current UTC time

        Returns:
            Dictionary representing the stored message record.
        """
        if not timestamp:
            timestamp = datetime.now(timezone.utc).isoformat()

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO messages (timestamp, sender, content) VALUES (?, ?, ?)",
                    (timestamp, sender, content)
                )
                msg_id = cursor.lastrowid
                conn.commit()
                return {
                    "id": msg_id,
                    "timestamp": timestamp,
                    "sender": sender,
                    "content": content
                }

    def update_message(self, message_id: int, content: str, timestamp: Optional[str] = None) -> bool:
        """Updates the content (and optionally timestamp) of an existing message by ID."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if timestamp:
                    cursor.execute(
                        "UPDATE messages SET content = ?, timestamp = ? WHERE id = ?",
                        (content, timestamp, message_id),
                    )
                else:
                    cursor.execute(
                        "UPDATE messages SET content = ? WHERE id = ?",
                        (content, message_id),
                    )
                updated = cursor.rowcount > 0
                conn.commit()
                return updated

    def get_messages(self, limit: int = 50, before_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Fetches messages in chronological order, optionally paginated before a specific ID.

        Args:
            limit: Maximum number of messages to return
            before_id: If provided, fetches messages with id < before_id (older history)

        Returns:
            List of message dicts in chronological (ascending) order.
        """
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if before_id is not None:
                    cursor.execute("""
                        SELECT id, timestamp, sender, content
                        FROM messages
                        WHERE id < ?
                        ORDER BY id DESC
                        LIMIT ?
                    """, (before_id, limit))
                else:
                    cursor.execute("""
                        SELECT id, timestamp, sender, content
                        FROM messages
                        ORDER BY id DESC
                        LIMIT ?
                    """, (limit,))
                rows = cursor.fetchall()
                if not rows:
                    return []

                # Convert to ascending chronological order
                rows = list(reversed(rows))
                return [
                    {
                        "id": r["id"],
                        "timestamp": r["timestamp"],
                        "sender": r["sender"],
                        "content": r["content"]
                    }
                    for r in rows
                ]

    def get_message_count(self) -> int:
        """Returns the total number of stored messages."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM messages")
                return cursor.fetchone()[0]

    def prune_expired(self, retention_days: int) -> int:
        """Prunes messages older than retention_days.

        Args:
            retention_days: Age in days past which messages should be removed.

        Returns:
            Number of rows deleted.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM messages WHERE timestamp < ?", (cutoff,))
                pruned = cursor.rowcount
                conn.commit()
                if pruned > 0:
                    logging.info(f"ChatHistoryManager: Pruned {pruned} messages older than {retention_days} days from {self.db_path}")
                return pruned
