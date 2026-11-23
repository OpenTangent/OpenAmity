"""
core/palace_fts.py — Zero-dependency SQLite FTS5 sidecar lexical index for Open Amity MemPalace.
Supports:
  - Porter-stemmed unicode61 BM25 lexical search (1 row per canonical logical drawer)
  - Safe FTS5 query sanitization (immune to syntax errors from punctuation/operators)
  - Equal/Weighted Reciprocal Rank Fusion (RRF) with ChromaDB dense search
  - Query-centred snippet preview extraction preserving [Session ...]/metadata headers
  - Corruption quarantine & automatic rebuild signal, plus graceful degradation if FTS5 is absent
"""
from __future__ import annotations

import contextlib
import logging
import os
import re
import shutil
import sqlite3
import threading
import time
import uuid
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for", "of",
    "with", "by", "from", "as", "is", "was", "are", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could", "should",
    "may", "might", "must", "shall", "can", "i", "me", "my", "we", "our", "you",
    "your", "he", "him", "his", "she", "her", "it", "its", "they", "them", "their",
    "this", "that", "these", "those", "what", "which", "who", "whom", "whose",
    "when", "where", "why", "how", "about", "into", "through", "during", "before",
    "after", "above", "below", "up", "down", "out", "off", "over", "under", "again",
    "then", "once", "here", "there", "all", "any", "both", "each", "few", "more",
    "most", "other", "some", "such", "no", "nor", "not", "only", "own", "same",
    "so", "than", "too", "very", "just", "now", "tell", "many", "much", "last",
    "previously", "mentioned", "talked", "discussed", "asked", "said", "remember",
    "get", "got", "go", "went", "make", "made", "take", "took",
})


def _extract_clean_tokens(query: str) -> List[str]:
    """Extract lowercase alphanumeric tokens of length >= 2."""
    raw = re.findall(r"[A-Za-z0-9']+", (query or "").lower())
    return [t for t in (tok.strip("'") for tok in raw) if len(t) >= 2]


def tokenize_non_stopwords(query: str, max_terms: int = 16) -> List[str]:
    """Extract deduplicated non-stopword alphanumeric tokens without falling back to stopwords."""
    cleaned = _extract_clean_tokens(query)
    non_stop = [t for t in cleaned if t not in _STOPWORDS]
    return list(dict.fromkeys(non_stop))[:max_terms]


def tokenize_query(query: str, max_terms: int = 16) -> List[str]:
    """Extract clean, deduplicated alphanumeric tokens for lexical search & snippet centering."""
    cleaned = _extract_clean_tokens(query)
    non_stop = [t for t in cleaned if t not in _STOPWORDS]
    tokens = non_stop if non_stop else cleaned
    return list(dict.fromkeys(tokens))[:max_terms]


def build_safe_fts5_query(query: str, max_terms: int = 16) -> str:
    """Convert arbitrary user text into a safe FTS5 MATCH expression."""
    tokens = tokenize_query(query, max_terms=max_terms)
    if not tokens:
        return ""
    # Double-quoting each alphanumeric token prevents FTS5 operator/syntax injection
    escaped = [t.replace('"', "") for t in tokens if t.replace('"', "")]
    return " OR ".join(f'"{t}"' for t in escaped)


def _clamp_plain(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    if max_len < 4:
        return text[:max_len]
    return text[: max_len - 3] + "..."


def extract_query_centered_snippet(text: str, query: str, max_len: int = 450) -> str:
    """
    Build a concise preview centred on the highest-density query-term window,
    preserving leading bracketed metadata headers like '[Session 034 | Date: ...]'.
    """
    max_len = max(1, int(max_len))
    clean = re.sub(r"\s+", " ", (text or "")).strip()
    if len(clean) <= max_len:
        return clean

    toks = tokenize_query(query)
    if not toks:
        return _clamp_plain(clean, max_len)

    lower = clean.lower()
    positions: List[Tuple[int, str]] = []
    for t in toks:
        start = 0
        while True:
            pos = lower.find(t, start)
            if pos == -1:
                break
            positions.append((pos, t))
            start = pos + len(t)
            if len(positions) >= 200:
                break

    if not positions:
        return _clamp_plain(clean, max_len)

    positions.sort()
    hdr_end = clean.find("]")
    include_header = clean.startswith("[") and 0 < hdr_end < 80 and (hdr_end + 20) < max_len
    header = clean[: hdr_end + 1] + " " if include_header else ""
    avail = max(20, max_len - len(header))

    best_score = (-1, -1)
    best_start = len(header)
    for pos, _ in positions:
        w_start = max(len(header), pos - avail // 3)
        w_end = min(len(clean), w_start + avail)
        window = lower[w_start:w_end]
        distinct = sum(1 for t in toks if t in window)
        total = sum(window.count(t) for t in toks)
        sc = (distinct, total)
        if sc > best_score:
            best_score = sc
            best_start = w_start

    if best_start <= len(header) + 15:
        return _clamp_plain(clean, max_len)

    # Snap start slightly left to a word boundary if nearby
    space_pos = clean.rfind(" ", len(header), best_start + 1)
    if space_pos != -1 and (best_start - space_pos) <= 20:
        best_start = space_pos + 1

    prefix = "..." if best_start > len(header) else ""
    body_budget = max(1, max_len - len(header) - len(prefix))
    end = min(len(clean), best_start + body_budget)
    if end < len(clean):
        if body_budget >= 4:
            end = min(len(clean), best_start + body_budget - 3)
            suffix = "..."
        else:
            suffix = ""
    else:
        suffix = ""

    snippet = f"{header}{prefix}{clean[best_start:end]}{suffix}"
    return snippet[:max_len]


def fuse_rrf(
    dense_ids: Sequence[str],
    bm25_ids: Sequence[str],
    w_dense: float = 1.0,
    w_bm25: float = 1.0,
    k: float = 60.0,
) -> List[Tuple[str, float]]:
    """Weighted Reciprocal Rank Fusion over two ranked lists of canonical logical drawer IDs."""
    scores: Dict[str, float] = defaultdict(float)
    for rank, did in enumerate(dense_ids, start=1):
        scores[did] += w_dense / (k + rank)
    for rank, did in enumerate(bm25_ids, start=1):
        scores[did] += w_bm25 / (k + rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


def _is_corrupt_sqlite_error(exc: Exception) -> bool:
    if not isinstance(exc, sqlite3.DatabaseError):
        return False
    msg = str(exc).lower()
    return any(tok in msg for tok in ("file is not a database", "malformed", "corrupt", "not a database"))


class PalaceFTSIndex:
    """Self-healing SQLite FTS5 lexical sidecar index stored at <palace_path>/palace_fts.db."""

    def __init__(self, palace_path: str):
        self.palace_path = str(palace_path)
        self.db_path = os.path.join(self.palace_path, "palace_fts.db")
        self._fts5_ok: Optional[bool] = None
        self._needs_backfill: bool = False
        self._quarantine_lock = threading.RLock()
        os.makedirs(self.palace_path, exist_ok=True)
        self._init_db()

    @contextlib.contextmanager
    def _conn(self):
        from core.state_barrier import state_barrier, state_root
        with state_barrier(state_root(self.palace_path)):
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = WAL")
            try:
                with conn:
                    yield conn
            finally:
                conn.close()

    def _execute_init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS palace_fts USING fts5(
                    drawer_id UNINDEXED,
                    wing UNINDEXED,
                    room UNINDEXED,
                    source_file UNINDEXED,
                    authored_at UNINDEXED,
                    content,
                    tokenize = 'porter unicode61 remove_diacritics 2'
                );
                CREATE TABLE IF NOT EXISTS fts_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
                """
            )

    def _quarantine_and_reinit(self, exc: Exception) -> None:
        from core.state_barrier import state_barrier, state_root
        with self._quarantine_lock, state_barrier(state_root(self.palace_path)):
            corrupt_backup = f"{self.db_path}.corrupt.{int(time.time())}"
            if os.path.exists(corrupt_backup):
                corrupt_backup = f"{corrupt_backup}_{uuid.uuid4().hex[:6]}"
            logger.critical(
                f"PalaceFTSIndex: Database corrupted ({exc}). Quarantining to {corrupt_backup} and re-initializing."
            )
            if os.path.exists(self.db_path):
                shutil.move(self.db_path, corrupt_backup)
            for ext in ("-wal", "-shm"):
                sidecar = f"{self.db_path}{ext}"
                if os.path.exists(sidecar):
                    try:
                        shutil.move(sidecar, f"{corrupt_backup}{ext}")
                    except Exception as sidecar_err:
                        logger.warning(f"Could not preserve sidecar {sidecar}: {sidecar_err}")
            self._execute_init_db()
            self._fts5_ok = True
            self._needs_backfill = True

    def _init_db(self, max_retries: int = 1) -> None:
        for attempt in range(max_retries + 1):
            try:
                self._execute_init_db()
                self._fts5_ok = True
                return
            except sqlite3.DatabaseError as e:
                if _is_corrupt_sqlite_error(e):
                    try:
                        self._quarantine_and_reinit(e)
                    except Exception as q_err:
                        logger.error(f"PalaceFTSIndex: Failed quarantine/reinit (falling back to dense-only): {q_err}")
                        self._fts5_ok = False
                    return
                err_msg = str(e).lower()
                if isinstance(e, sqlite3.OperationalError) and ("locked" in err_msg or "busy" in err_msg) and attempt < max_retries:
                    time.sleep(0.05)
                    continue
                logger.warning(f"PalaceFTSIndex unavailable (falling back to dense-only): {e}")
                self._fts5_ok = False
                return

    @property
    def available(self) -> bool:
        return bool(self._fts5_ok)

    def consume_needs_backfill(self) -> bool:
        """Returns and clears the mid-session quarantine backfill signal."""
        flag = self._needs_backfill
        self._needs_backfill = False
        return flag

    def count(self) -> int:
        if not self.available:
            return 0
        try:
            with self._conn() as conn:
                row = conn.execute("SELECT COUNT(*) AS cnt FROM palace_fts").fetchone()
                return int(row["cnt"]) if row else 0
        except sqlite3.DatabaseError as e:
            if _is_corrupt_sqlite_error(e):
                self._quarantine_and_reinit(e)
            else:
                logger.warning(f"PalaceFTSIndex count error: {e}")
            return 0

    def upsert_drawer(
        self,
        drawer_id: str,
        content: str,
        wing: str = "default",
        room: str = "general",
        source_file: str = "",
        authored_at: str = "",
    ) -> bool:
        if not self.available or not drawer_id:
            return False
        try:
            with self._conn() as conn:
                conn.execute("DELETE FROM palace_fts WHERE drawer_id = ?", (drawer_id,))
                conn.execute(
                    "INSERT INTO palace_fts (drawer_id, wing, room, source_file, authored_at, content) VALUES (?, ?, ?, ?, ?, ?)",
                    (drawer_id, wing or "default", room or "general", source_file or "", authored_at or "", content or ""),
                )
            return True
        except sqlite3.DatabaseError as e:
            if _is_corrupt_sqlite_error(e):
                self._quarantine_and_reinit(e)
            else:
                logger.warning(f"PalaceFTSIndex upsert error for {drawer_id}: {e}")
            return False

    def rebuild_from_records(self, records: Iterable[Dict[str, str]]) -> int:
        """Atomically rebuild the entire FTS5 table from canonical logical drawer dicts."""
        if not self.available:
            return 0
        rows = [
            (
                r["drawer_id"],
                r.get("wing", "default"),
                r.get("room", "general"),
                r.get("source_file", ""),
                r.get("authored_at", ""),
                r.get("content", ""),
            )
            for r in records
            if r.get("drawer_id")
        ]
        try:
            with self._conn() as conn:
                conn.execute("DELETE FROM palace_fts")
                conn.executemany(
                    "INSERT INTO palace_fts (drawer_id, wing, room, source_file, authored_at, content) VALUES (?, ?, ?, ?, ?, ?)",
                    rows,
                )
                conn.execute(
                    "INSERT INTO fts_meta (key, value) VALUES ('indexed_drawers', ?) ON CONFLICT(key) DO UPDATE SET value = ?",
                    (str(len(rows)), str(len(rows))),
                )
            self._needs_backfill = False
            return len(rows)
        except sqlite3.DatabaseError as e:
            if _is_corrupt_sqlite_error(e):
                self._quarantine_and_reinit(e)
            else:
                logger.warning(f"PalaceFTSIndex rebuild error: {e}")
            return 0

    def delete_drawer(self, drawer_id: str) -> bool:
        if not self.available or not drawer_id:
            return False
        try:
            with self._conn() as conn:
                conn.execute("DELETE FROM palace_fts WHERE drawer_id = ?", (drawer_id,))
            return True
        except sqlite3.DatabaseError as e:
            if _is_corrupt_sqlite_error(e):
                self._quarantine_and_reinit(e)
            else:
                logger.warning(f"PalaceFTSIndex delete error for {drawer_id}: {e}")
            return False

    def get_drawer(self, drawer_id: str) -> Optional[Dict[str, Any]]:
        if not self.available or not drawer_id:
            return None
        try:
            with self._conn() as conn:
                r = conn.execute(
                    "SELECT drawer_id, wing, room, source_file, authored_at, content FROM palace_fts WHERE drawer_id = ? LIMIT 1",
                    (drawer_id,),
                ).fetchone()
            if not r:
                return None
            return {
                "drawer_id": r["drawer_id"],
                "wing": r["wing"],
                "room": r["room"],
                "source_file": r["source_file"],
                "authored_at": r["authored_at"],
                "content": r["content"],
            }
        except sqlite3.DatabaseError as e:
            if _is_corrupt_sqlite_error(e):
                self._quarantine_and_reinit(e)
            else:
                logger.warning(f"PalaceFTSIndex get_drawer error for {drawer_id}: {e}")
            return None

    def get_drawers(self, drawer_ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        if not self.available or not drawer_ids:
            return {}
        unique_ids = list(dict.fromkeys(str(d) for d in drawer_ids if d))
        if not unique_ids:
            return {}
        out: Dict[str, Dict[str, Any]] = {}
        try:
            with self._conn() as conn:
                for start in range(0, len(unique_ids), 500):
                    batch = unique_ids[start:start + 500]
                    placeholders = ",".join("?" for _ in batch)
                    rows = conn.execute(
                        f"SELECT drawer_id, wing, room, source_file, authored_at, content "
                        f"FROM palace_fts WHERE drawer_id IN ({placeholders})",
                        tuple(batch),
                    ).fetchall()
                    for r in rows:
                        out[r["drawer_id"]] = {
                            "drawer_id": r["drawer_id"],
                            "wing": r["wing"],
                            "room": r["room"],
                            "source_file": r["source_file"],
                            "authored_at": r["authored_at"],
                            "content": r["content"],
                        }
            return out
        except sqlite3.DatabaseError as e:
            if _is_corrupt_sqlite_error(e):
                self._quarantine_and_reinit(e)
            else:
                logger.warning(f"PalaceFTSIndex get_drawers error: {e}")
            return {}

    def search(
        self,
        query: str,
        wing: Optional[str] = None,
        room: Optional[str] = None,
        limit: int = 30,
    ) -> List[Dict[str, Any]]:
        if not self.available:
            return []
        fts_q = build_safe_fts5_query(query)
        if not fts_q:
            return []

        clauses = ["palace_fts MATCH ?"]
        params: List[Any] = [fts_q]
        if wing:
            clauses.append("wing = ?")
            params.append(wing)
        if room:
            clauses.append("room = ?")
            params.append(room)
        params.append(max(1, int(limit)))

        sql = (
            "SELECT drawer_id, wing, room, source_file, authored_at, content, bm25(palace_fts) AS bm25_score "
            f"FROM palace_fts WHERE {' AND '.join(clauses)} "
            "ORDER BY bm25_score ASC LIMIT ?"
        )
        try:
            with self._conn() as conn:
                rows = conn.execute(sql, tuple(params)).fetchall()
        except sqlite3.DatabaseError as e:
            if _is_corrupt_sqlite_error(e):
                self._quarantine_and_reinit(e)
            else:
                logger.warning(f"PalaceFTSIndex search error for query '{query}': {e}")
            return []

        return [
            {
                "drawer_id": r["drawer_id"],
                "wing": r["wing"],
                "room": r["room"],
                "source_file": r["source_file"],
                "authored_at": r["authored_at"],
                "content": r["content"],
                "bm25_score": float(r["bm25_score"]),
            }
            for r in rows
        ]
