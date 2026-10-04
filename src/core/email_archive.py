import os
import re
import json
import time
import email
import sqlite3
import logging
import threading
from email.header import decode_header
from email import policy
from email.message import EmailMessage
from typing import List, Dict, Any, Optional

from config import paths

logger = logging.getLogger("core.EmailArchive")


def decode_mime_header(header_value: Optional[str]) -> str:
    """Decodes MIME encoded header strings to standard Python unicode string."""
    if not header_value:
        return ""
    decoded_fragments = []
    for fragment, encoding in decode_header(header_value):
        if isinstance(fragment, bytes):
            try:
                decoded_fragments.append(fragment.decode(encoding or "utf-8", errors="replace"))
            except Exception:
                decoded_fragments.append(fragment.decode("utf-8", errors="replace"))
        else:
            decoded_fragments.append(str(fragment))
    return "".join(decoded_fragments)


def html_to_plain_text(html_content: str) -> str:
    """Converts HTML email body into clean, readable plain text."""
    if not html_content:
        return ""
    text = re.sub(r'<head.*?>.*?</head>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style.*?>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<script.*?>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<(?:br|p|div|tr|h[1-6])[\s/>]', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<li[\s>]', '\n* ', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&gt;', '>', text)
    text = re.sub(r'&quot;', '"', text)
    text = re.sub(r'&#39;', "'", text)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(chunk for chunk in lines if chunk)


class EmailArchive:
    """
    Manages local SQLite archiving of agent emails retrieved from IMAP servers.
    Moving emails to a local archive ensures remote webmail and IMAP mailboxes
    do not exceed storage quotas and bounce incoming mail.
    """

    def __init__(self, agent_id: Optional[str] = None, db_path: Optional[str] = None):
        self.agent_id = agent_id
        self._custom_db = db_path is not None
        if db_path:
            self.db_path = db_path
        else:
            self.db_path = paths.get_email_archive_db_path(agent_id)

        self._lock = threading.RLock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Creates a SQLite connection configured with Row factory and busy timeout."""
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Initializes SQLite tables and indexes for the email archive."""
        if self.db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("PRAGMA journal_mode=WAL;")
                cursor.execute("PRAGMA busy_timeout=5000;")
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS archived_emails (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        folder TEXT NOT NULL DEFAULT 'INBOX',
                        message_id TEXT,
                        server_uid TEXT,
                        subject TEXT,
                        from_sender TEXT,
                        to_recipient TEXT,
                        cc TEXT,
                        date_str TEXT,
                        timestamp REAL,
                        is_read INTEGER DEFAULT 0,
                        has_attachments INTEGER DEFAULT 0,
                        attachment_names TEXT DEFAULT '[]',
                        body_plain TEXT,
                        body_html TEXT,
                        raw_msg BLOB,
                        created_at REAL
                    );
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_emails_folder ON archived_emails(folder);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_emails_timestamp ON archived_emails(timestamp DESC);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_emails_message_id ON archived_emails(message_id);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_emails_server_uid ON archived_emails(server_uid);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_emails_is_read ON archived_emails(is_read);")
                conn.commit()

    def save_email(
        self,
        folder: str = "INBOX",
        raw_bytes: bytes = b"",
        server_uid: Optional[str] = None,
        is_read: bool = False,
        msg_obj: Optional[EmailMessage] = None
    ) -> int:
        """
        Parses and archives an email into the local SQLite database.
        Deduplicates by folder and Message-ID (or header fingerprint).
        Returns the unique local archive integer ID.
        """
        if msg_obj is None and raw_bytes:
            msg = email.message_from_bytes(raw_bytes, policy=policy.default)
        elif msg_obj is not None:
            msg = msg_obj
            if not raw_bytes:
                raw_bytes = msg.as_bytes()
        else:
            raise ValueError("Either raw_bytes or msg_obj must be provided to save_email.")

        subject = decode_mime_header(msg.get("Subject", "(No Subject)"))
        from_sender = decode_mime_header(msg.get("From", "Unknown"))
        to_recipient = decode_mime_header(msg.get("To", ""))
        cc = decode_mime_header(msg.get("Cc", ""))
        date_str = msg.get("Date", "")
        message_id = str(msg.get("Message-ID", "")).strip()

        # Parse timestamp
        timestamp = time.time()
        if date_str:
            try:
                parsed_dt = email.utils.parsedate_to_datetime(date_str)
                if parsed_dt:
                    timestamp = parsed_dt.timestamp()
            except Exception:
                pass

        # Parse body and attachments
        plain_body = ""
        html_body = ""
        attachments = []

        if msg.is_multipart():
            part_idx = 1
            for part in msg.walk():
                if part.get_content_maintype() == 'multipart':
                    continue

                fn = part.get_filename()
                content_disposition = str(part.get("Content-Disposition", ""))
                content_type = part.get_content_type()

                if fn or "attachment" in content_disposition or (content_disposition and content_type not in ["text/plain", "text/html"]):
                    fn_decoded = decode_mime_header(fn or f"attachment_{part_idx}")
                    attachments.append(fn_decoded)
                    part_idx += 1
                elif content_type == "text/plain" and not plain_body:
                    payload = part.get_payload(decode=True)
                    charset = part.get_content_charset() or "utf-8"
                    plain_body = payload.decode(charset, errors="replace") if payload else ""
                elif content_type == "text/html" and not html_body:
                    payload = part.get_payload(decode=True)
                    charset = part.get_content_charset() or "utf-8"
                    html_body = payload.decode(charset, errors="replace") if payload else ""
        else:
            content_type = msg.get_content_type()
            payload = msg.get_payload(decode=True)
            charset = msg.get_content_charset() or "utf-8"
            if content_type == "text/plain":
                plain_body = payload.decode(charset, errors="replace") if payload else ""
            elif content_type == "text/html":
                html_body = payload.decode(charset, errors="replace") if payload else ""

        if not plain_body.strip() and html_body.strip():
            plain_body = html_to_plain_text(html_body)

        has_attachments = 1 if attachments else 0
        attachment_names = json.dumps(attachments)
        read_val = 1 if is_read else 0
        now_ts = time.time()

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Deduplication check
                existing_row = None
                if message_id:
                    cursor.execute(
                        "SELECT id, is_read, server_uid FROM archived_emails WHERE folder = ? AND message_id = ?",
                        (folder, message_id)
                    )
                    existing_row = cursor.fetchone()

                if not existing_row and subject and from_sender:
                    cursor.execute(
                        "SELECT id, is_read, server_uid FROM archived_emails WHERE folder = ? AND from_sender = ? AND subject = ? AND ABS(timestamp - ?) < 60",
                        (folder, from_sender, subject, timestamp)
                    )
                    existing_row = cursor.fetchone()

                if existing_row:
                    existing_id = existing_row["id"]
                    # Update read status and server_uid if updated
                    new_read = 1 if (is_read or existing_row["is_read"]) else 0
                    cursor.execute(
                        "UPDATE archived_emails SET is_read = ?, server_uid = COALESCE(?, server_uid) WHERE id = ?",
                        (new_read, server_uid, existing_id)
                    )
                    conn.commit()
                    return existing_id

                cursor.execute("""
                    INSERT INTO archived_emails (
                        folder, message_id, server_uid, subject, from_sender, to_recipient,
                        cc, date_str, timestamp, is_read, has_attachments, attachment_names,
                        body_plain, body_html, raw_msg, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    folder, message_id, server_uid, subject, from_sender, to_recipient,
                    cc, date_str, timestamp, read_val, has_attachments, attachment_names,
                    plain_body, html_body, raw_bytes, now_ts
                ))
                conn.commit()
                return cursor.lastrowid

    def get_emails(
        self,
        folder: str = "INBOX",
        limit: int = 10,
        unread_only: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Retrieves recent emails from the local archive for a specific folder.
        Returns a list of email metadata dictionaries sorted newest first.
        """
        limit = max(1, min(limit, 100))
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if unread_only:
                    cursor.execute("""
                        SELECT id, folder, message_id, server_uid, subject, from_sender, to_recipient, cc, date_str, timestamp, is_read, has_attachments, attachment_names
                        FROM archived_emails
                        WHERE folder = ? AND is_read = 0
                        ORDER BY timestamp DESC, id DESC
                        LIMIT ?
                    """, (folder, limit))
                else:
                    cursor.execute("""
                        SELECT id, folder, message_id, server_uid, subject, from_sender, to_recipient, cc, date_str, timestamp, is_read, has_attachments, attachment_names
                        FROM archived_emails
                        WHERE folder = ?
                        ORDER BY timestamp DESC, id DESC
                        LIMIT ?
                    """, (folder, limit))

                rows = cursor.fetchall()
                return [dict(r) for r in rows]

    def get_email(self, email_id: str, folder: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Finds a single archived email by its local ID, server UID, or Message-ID.
        Returns a dict containing full message contents and raw bytes, or None if not found.
        """
        target = str(email_id).strip()
        if not target:
            return None

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Try numeric ID
                if target.isdigit():
                    if folder:
                        cursor.execute("SELECT * FROM archived_emails WHERE id = ? AND folder = ?", (int(target), folder))
                    else:
                        cursor.execute("SELECT * FROM archived_emails WHERE id = ?", (int(target),))
                    row = cursor.fetchone()
                    if row:
                        return dict(row)

                # Try server_uid
                if folder:
                    cursor.execute("SELECT * FROM archived_emails WHERE server_uid = ? AND folder = ?", (target, folder))
                else:
                    cursor.execute("SELECT * FROM archived_emails WHERE server_uid = ?", (target,))
                row = cursor.fetchone()
                if row:
                    return dict(row)

                # Try message_id
                if folder:
                    cursor.execute("SELECT * FROM archived_emails WHERE message_id = ? AND folder = ?", (target, folder))
                else:
                    cursor.execute("SELECT * FROM archived_emails WHERE message_id = ?", (target,))
                row = cursor.fetchone()
                if row:
                    return dict(row)

                return None

    def search_emails(
        self,
        folder: str = "INBOX",
        query: Optional[str] = None,
        sender: Optional[str] = None,
        recipient: Optional[str] = None,
        subject: Optional[str] = None,
        since_date: Optional[str] = None,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Searches the local email archive with multiple criteria filters.
        """
        limit = max(1, min(limit, 100))
        conditions = ["folder = ?"]
        params: List[Any] = [folder]

        if query:
            conditions.append("(subject LIKE ? OR body_plain LIKE ? OR from_sender LIKE ?)")
            q_like = f"%{query}%"
            params.extend([q_like, q_like, q_like])

        if sender:
            conditions.append("from_sender LIKE ?")
            params.append(f"%{sender}%")

        if recipient:
            conditions.append("(to_recipient LIKE ? OR cc LIKE ?)")
            r_like = f"%{recipient}%"
            params.extend([r_like, r_like])

        if subject:
            conditions.append("subject LIKE ?")
            params.append(f"%{subject}%")

        if since_date:
            parsed_ts = None
            try:
                dt = email.utils.parsedate_to_datetime(since_date)
                if dt:
                    parsed_ts = dt.timestamp()
            except Exception:
                pass

            if parsed_ts is not None:
                conditions.append("timestamp >= ?")
                params.append(parsed_ts)
            else:
                conditions.append("date_str LIKE ?")
                params.append(f"%{since_date}%")

        sql = f"""
            SELECT id, folder, message_id, server_uid, subject, from_sender, to_recipient, cc, date_str, timestamp, is_read, has_attachments, attachment_names
            FROM archived_emails
            WHERE {' AND '.join(conditions)}
            ORDER BY timestamp DESC, id DESC
            LIMIT ?
        """
        params.append(limit)

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(sql, params)
                rows = cursor.fetchall()
                return [dict(r) for r in rows]

    def mark_as_read(self, email_id: str, is_read: bool = True, folder: Optional[str] = None) -> bool:
        """Updates the read status for an archived email."""
        em = self.get_email(email_id, folder=folder)
        if not em:
            return False

        real_id = em["id"]
        val = 1 if is_read else 0
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE archived_emails SET is_read = ? WHERE id = ?", (val, real_id))
                conn.commit()
                return cursor.rowcount > 0

    def delete_email(self, email_id: str, folder: Optional[str] = None) -> bool:
        """Deletes an archived email from the local database."""
        em = self.get_email(email_id, folder=folder)
        if not em:
            return False

        real_id = em["id"]
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM archived_emails WHERE id = ?", (real_id,))
                conn.commit()
                return cursor.rowcount > 0

    def list_folders(self) -> List[str]:
        """Returns distinct mailbox folders available in the local archive."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT DISTINCT folder FROM archived_emails ORDER BY folder ASC")
                rows = cursor.fetchall()
                folders = [r["folder"] for r in rows if r["folder"]]
                if "INBOX" not in folders:
                    folders.insert(0, "INBOX")
                return folders

    def get_stats(self) -> Dict[str, Any]:
        """Returns statistical counts for archived emails."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) AS total, SUM(CASE WHEN is_read = 0 THEN 1 ELSE 0 END) AS unread FROM archived_emails")
                row = cursor.fetchone()
                return {
                    "total": row["total"] or 0,
                    "unread": row["unread"] or 0
                }
