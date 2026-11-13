"""
core/trajectory_manager.py — Trajectory & Teleological Horizon Manager
Manages agent aspirations (with teleological 'why' and concentric 'sphere_level')
and tasks across both trajectory.json and trajectory.db with idempotent SQLite
schema migration and state_barrier participation.
"""

import os
import json
import uuid
import sqlite3
import logging
import datetime
import threading
import contextlib
from typing import Dict, List, Optional, Any

from config import paths
from core.file_utils import atomic_json_write
from core.state_barrier import state_barrier, state_root

logger = logging.getLogger(__name__)


def _clamp_sphere_level(val: Any, default: int = 1) -> int:
    if val is None or isinstance(val, bool):
        return default
    if isinstance(val, float) and not val.is_integer():
        raise ValueError(f"sphere_level must be an integer in [1, 10], got {val!r}")
    try:
        ival = int(val)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"sphere_level must be an integer in [1, 10], got {val!r}") from exc
    return max(1, min(10, ival))


class TrajectoryManager:
    """
    Manages the agent's Trajectory (aspirations and tasks) with synchronized
    persistence in both trajectory.json and trajectory.db.
    """

    VALID_TIERS = ("short_term", "medium_term", "long_term")

    def __init__(self, agent_id: Optional[str] = None, data_dir: Optional[str] = None):
        self.agent_id = agent_id
        self.base_dir = data_dir or paths.get_base_dir_for(agent_id)
        os.makedirs(self.base_dir, exist_ok=True)
        self.file_path = os.path.join(self.base_dir, "trajectory.json")
        self.db_path = os.path.join(self.base_dir, "trajectory.db")
        self._lock = threading.RLock()
        self._init_and_migrate_db()

    @contextlib.contextmanager
    def _get_conn(self):
        with state_barrier(state_root(self.base_dir)), self._lock:
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            conn = sqlite3.connect(self.db_path, timeout=15.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            try:
                with conn:
                    yield conn
            finally:
                conn.close()

    def _init_and_migrate_db(self):
        """
        Initializes trajectory.db and runs idempotent ALTER TABLE migrations
        for legacy aspirations tables missing 'why' or 'sphere_level'.
        """
        try:
            with self._get_conn() as conn:
                conn.execute("""
                CREATE TABLE IF NOT EXISTS aspirations (
                    id TEXT PRIMARY KEY,
                    tier TEXT NOT NULL DEFAULT 'short_term',
                    description TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT '',
                    why TEXT DEFAULT '',
                    sphere_level INTEGER DEFAULT 1
                )
                """)
                conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    description TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    priority TEXT NOT NULL DEFAULT 'medium',
                    created_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT ''
                )
                """)
                # Idempotent column check for pre-existing aspirations table
                cols = {
                    row["name"]
                    for row in conn.execute("PRAGMA table_info(aspirations)").fetchall()
                }
                if "tier" not in cols:
                    conn.execute("ALTER TABLE aspirations ADD COLUMN tier TEXT NOT NULL DEFAULT 'short_term'")
                if "status" not in cols:
                    conn.execute("ALTER TABLE aspirations ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
                if "updated_at" not in cols:
                    conn.execute("ALTER TABLE aspirations ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''")
                if "why" not in cols:
                    conn.execute("ALTER TABLE aspirations ADD COLUMN why TEXT DEFAULT ''")
                if "sphere_level" not in cols:
                    conn.execute("ALTER TABLE aspirations ADD COLUMN sphere_level INTEGER DEFAULT 1")
        except Exception as e:
            logger.warning(f"TrajectoryManager: Could not initialize/migrate trajectory.db: {e}")

    @staticmethod
    def _default_data() -> Dict[str, Any]:
        return {
            "aspirations": {
                "short_term": [],
                "medium_term": [],
                "long_term": [],
            },
            "tasks": [],
        }

    @staticmethod
    def _normalize_aspiration(asp: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(asp)
        out.setdefault("id", str(uuid.uuid4())[:8])
        out.setdefault("description", "")
        out.setdefault("status", "active")
        now_iso = datetime.datetime.now().isoformat()
        out.setdefault("created_at", now_iso)
        out.setdefault("updated_at", out["created_at"])
        why_val = out.get("why")
        out["why"] = str(why_val).strip() if why_val is not None else ""
        try:
            out["sphere_level"] = _clamp_sphere_level(out.get("sphere_level", 1), default=1)
        except ValueError:
            out["sphere_level"] = 1
        return out

    def load_data(self) -> Dict[str, Any]:
        """
        Loads trajectory data from trajectory.json and merges/synchronizes with trajectory.db.
        """
        with state_barrier(state_root(self.base_dir)), self._lock:
            self._init_and_migrate_db()
            data = self._default_data()
            json_exists = os.path.exists(self.file_path)

            if json_exists:
                try:
                    with open(self.file_path, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                    if isinstance(loaded, dict):
                        raw_asps = loaded.get("aspirations", {})
                        if isinstance(raw_asps, dict):
                            for tier in self.VALID_TIERS:
                                tier_list = raw_asps.get(tier, [])
                                if isinstance(tier_list, list):
                                    data["aspirations"][tier] = [
                                        self._normalize_aspiration(a)
                                        for a in tier_list
                                        if isinstance(a, dict)
                                    ]
                        raw_tasks = loaded.get("tasks", [])
                        if isinstance(raw_tasks, list):
                            data["tasks"] = [dict(t) for t in raw_tasks if isinstance(t, dict)]
                except Exception as e:
                    logger.error(f"TrajectoryManager: Failed to read {self.file_path}: {e}")

            # Check trajectory.db for any aspirations added/migrated directly in SQLite
            try:
                with self._get_conn() as conn:
                    db_rows = conn.execute(
                        "SELECT id, tier, description, status, created_at, updated_at, why, sphere_level FROM aspirations"
                    ).fetchall()
                    known_ids = {
                        a["id"]
                        for tier in self.VALID_TIERS
                        for a in data["aspirations"].get(tier, [])
                        if "id" in a
                    }
                    if not json_exists and db_rows:
                        for row in db_rows:
                            tier = row["tier"] if row["tier"] in self.VALID_TIERS else "short_term"
                            data["aspirations"][tier].append(
                                self._normalize_aspiration({
                                    "id": row["id"],
                                    "description": row["description"] or "",
                                    "status": row["status"] or "active",
                                    "created_at": row["created_at"] or datetime.datetime.now().isoformat(),
                                    "updated_at": row["updated_at"] or datetime.datetime.now().isoformat(),
                                    "why": row["why"] or "",
                                    "sphere_level": row["sphere_level"] if row["sphere_level"] is not None else 1,
                                })
                            )
                    elif db_rows:
                        # Sync any DB-only rows into data
                        for row in db_rows:
                            if row["id"] not in known_ids and not json_exists:
                                tier = row["tier"] if row["tier"] in self.VALID_TIERS else "short_term"
                                data["aspirations"][tier].append(
                                    self._normalize_aspiration(dict(row))
                                )
            except Exception as e:
                logger.debug(f"TrajectoryManager: Could not read aspirations from trajectory.db: {e}")

            if not json_exists:
                try:
                    self.save_data(data)
                except Exception as e:
                    logger.warning(f"TrajectoryManager: Could not write initial trajectory.json: {e}")
            else:
                self._sync_to_db(data)

            return data

    def _sync_to_db(self, data: Dict[str, Any]):
        try:
            with self._get_conn() as conn:
                cols = {
                    row["name"]
                    for row in conn.execute("PRAGMA table_info(aspirations)").fetchall()
                }
                has_horizon = "horizon" in cols
                conn.execute("DELETE FROM aspirations")
                for tier in self.VALID_TIERS:
                    for asp in data.get("aspirations", {}).get(tier, []):
                        norm = self._normalize_aspiration(asp)
                        if has_horizon:
                            conn.execute("""
                            INSERT OR REPLACE INTO aspirations
                            (id, horizon, tier, description, status, created_at, updated_at, why, sphere_level)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (
                                norm["id"],
                                tier,
                                tier,
                                norm["description"],
                                norm["status"],
                                norm["created_at"],
                                norm["updated_at"],
                                norm["why"],
                                norm["sphere_level"],
                            ))
                        else:
                            conn.execute("""
                            INSERT OR REPLACE INTO aspirations
                            (id, tier, description, status, created_at, updated_at, why, sphere_level)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """, (
                                norm["id"],
                                tier,
                                norm["description"],
                                norm["status"],
                                norm["created_at"],
                                norm["updated_at"],
                                norm["why"],
                                norm["sphere_level"],
                            ))
                conn.execute("DELETE FROM tasks")
                for t in data.get("tasks", []):
                    if isinstance(t, dict) and t.get("id"):
                        conn.execute("""
                        INSERT OR REPLACE INTO tasks
                        (id, description, status, priority, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """, (
                            str(t["id"]),
                            str(t.get("description", "")),
                            str(t.get("status", "pending")),
                            str(t.get("priority", "medium")),
                            str(t.get("created_at", "")),
                            str(t.get("updated_at", "")),
                        ))
        except Exception as e:
            logger.warning(f"TrajectoryManager: Failed syncing to trajectory.db: {e}")

    def save_data(self, data: Dict[str, Any]):
        """
        Atomically persists trajectory state to trajectory.json and synchronizes trajectory.db.
        """
        with state_barrier(state_root(self.base_dir)), self._lock:
            for tier in self.VALID_TIERS:
                if tier in data.get("aspirations", {}):
                    data["aspirations"][tier] = [
                        self._normalize_aspiration(a)
                        for a in data["aspirations"][tier]
                        if isinstance(a, dict)
                    ]
            atomic_json_write(self.file_path, data)
            self._sync_to_db(data)
