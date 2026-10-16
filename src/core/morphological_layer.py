"""
core/morphological_layer.py — Morphological Memory Graph & Setpoint Engine
Part of Open Amity framework.
Based on MEMPALACE_MORPHOLOGICAL_UPGRADE_SPEC.md v1.1.0 and MemoryAudit2026-10-05.
"""

import os
import re
import json
import time
import uuid
import math
import sqlite3
import logging
import datetime
import threading
import contextlib
from typing import List, Dict, Tuple, Optional, Set, Any
import numpy as np

logger = logging.getLogger(__name__)


class MorphologicalMemoryLayer:
    def __init__(
        self,
        palace_path: str,
        gamma: float = 0.6,
        leak: float = 0.8,
        decay_rate: float = 0.002,
        dissipation: float = 0.05,
        dt: float = 0.1,
        n_steps: int = 25,
        top_k: int = 5,
        eta: float = 1.0,
        max_coactivation_set: int = 16,
    ):
        self.palace_path = palace_path
        self.db_path = os.path.join(palace_path, "morpho_graph.db")
        self._param_lock = threading.RLock()

        # Validate initial numerical parameters
        d = float(decay_rate)
        if not math.isfinite(d) or d < 0.0 or d > 1.0:
            raise ValueError(f"Invalid decay_rate: {decay_rate}. Must be finite and between 0.0 and 1.0.")
        g = float(gamma)
        if not math.isfinite(g) or g < 0.0:
            raise ValueError(f"Invalid gamma: {gamma}. Must be finite and >= 0.0.")
        l = float(leak)
        if not math.isfinite(l) or l < 0.0:
            raise ValueError(f"Invalid leak: {leak}. Must be finite and >= 0.0.")
        ds = float(dissipation)
        if not math.isfinite(ds) or ds < 0.0:
            raise ValueError(f"Invalid dissipation: {dissipation}. Must be finite and >= 0.0.")
        dt_val = float(dt)
        if not math.isfinite(dt_val) or dt_val <= 0.0:
            raise ValueError(f"Invalid dt: {dt}. Must be finite and > 0.0.")
        if isinstance(n_steps, float) and not n_steps.is_integer():
            raise ValueError(f"Invalid n_steps: {n_steps}. Must be positive integer.")
        ns = int(n_steps)
        if ns <= 0:
            raise ValueError(f"Invalid n_steps: {n_steps}. Must be positive integer.")
        if isinstance(top_k, float) and not top_k.is_integer():
            raise ValueError(f"Invalid top_k: {top_k}. Must be positive integer.")
        tk = int(top_k)
        if tk <= 0:
            raise ValueError(f"Invalid top_k: {top_k}. Must be positive integer.")
        e = float(eta)
        if not math.isfinite(e) or e < 0.0:
            raise ValueError(f"Invalid eta: {eta}. Must be finite and >= 0.0.")
        if isinstance(max_coactivation_set, float) and not max_coactivation_set.is_integer():
            raise ValueError(f"Invalid max_coactivation_set: {max_coactivation_set}. Must be positive integer.")
        mcs = int(max_coactivation_set)
        if mcs <= 0:
            raise ValueError(f"Invalid max_coactivation_set: {max_coactivation_set}. Must be positive integer.")

        self.gamma = g
        self.leak = l
        self.decay_rate = d
        self.dissipation = ds
        self.dt = dt_val
        self.n_steps = ns
        self.top_k = tk
        self.eta = e
        self.max_coactivation_set = mcs

        os.makedirs(self.palace_path, exist_ok=True)
        self._init_db()

    @contextlib.contextmanager
    def _get_conn(self):
        from core.state_barrier import state_barrier, state_root
        with state_barrier(state_root(self.palace_path)):
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            try:
                with conn:
                    yield conn
            finally:
                conn.close()

    def _canonical_id(self, raw_id: str) -> str:
        """Resolves any physical chunk ID (e.g. drawer_x_chunk_000001) to canonical parent logical ID."""
        if not raw_id:
            return ""
        s = str(raw_id)
        m = re.match(r"^(.*)_chunk_\d+$", s)
        return m.group(1) if m else s

    def _execute_init_db(self):
        with self._get_conn() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS morpho_nodes (
                drawer_id TEXT PRIMARY KEY,
                wing TEXT NOT NULL DEFAULT 'default',
                room TEXT NOT NULL DEFAULT 'general',
                baseline_potential REAL DEFAULT 0.0,
                created_at TEXT NOT NULL,
                last_activated TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS morpho_edges (
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                weight REAL NOT NULL DEFAULT 1.0,
                created_at TEXT NOT NULL,
                last_reinforced TEXT NOT NULL,
                PRIMARY KEY (source_id, target_id),
                FOREIGN KEY (source_id) REFERENCES morpho_nodes(drawer_id) ON DELETE CASCADE,
                FOREIGN KEY (target_id) REFERENCES morpho_nodes(drawer_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_edges_source ON morpho_edges(source_id);
            CREATE INDEX IF NOT EXISTS idx_edges_target ON morpho_edges(target_id);

            CREATE TABLE IF NOT EXISTS morpho_setpoints (
                goal_key TEXT PRIMARY KEY,
                text_hash TEXT,
                target_drawers_json TEXT NOT NULL,
                explicit_drawers_json TEXT DEFAULT '[]',
                corpus_revision INTEGER DEFAULT 0,
                status TEXT DEFAULT 'ok',
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS morpho_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """)

            # Ensure columns exist on legacy tables
            existing_cols = {r["name"] for r in conn.execute("PRAGMA table_info(morpho_setpoints)").fetchall()}
            if "corpus_revision" not in existing_cols:
                conn.execute("ALTER TABLE morpho_setpoints ADD COLUMN corpus_revision INTEGER DEFAULT 0")
            if "status" not in existing_cols:
                conn.execute("ALTER TABLE morpho_setpoints ADD COLUMN status TEXT DEFAULT 'ok'")

    def _init_db(self, max_retries: int = 1):
        for attempt in range(max_retries + 1):
            try:
                self._execute_init_db()
                return
            except (sqlite3.DatabaseError, sqlite3.OperationalError) as e:
                err_msg = str(e).lower()
                is_corrupt = "file is not a database" in err_msg or "malformed" in err_msg
                if is_corrupt:
                    import shutil
                    corrupt_backup = f"{self.db_path}.corrupt.{int(time.time())}"
                    logging.critical(
                        f"MorphologicalMemoryLayer: Database corrupted ({e}). Quarantining to {corrupt_backup} and re-initializing."
                    )
                    if os.path.exists(self.db_path):
                        try:
                            shutil.move(self.db_path, corrupt_backup)
                        except Exception as move_err:
                            logging.error(f"MorphologicalMemoryLayer: Failed to quarantine corrupted database: {move_err}")
                            raise RuntimeError(f"Failed to quarantine corrupted database: {move_err}") from move_err
                    for ext in ["-wal", "-shm"]:
                        sidecar = f"{self.db_path}{ext}"
                        if os.path.exists(sidecar):
                            try:
                                shutil.move(sidecar, f"{corrupt_backup}{ext}")
                            except Exception as sidecar_err:
                                logging.warning(f"Could not preserve sidecar {sidecar}: {sidecar_err}")
                    self._execute_init_db()
                    return
                else:
                    if attempt < max_retries:
                        time.sleep(0.05)
                        continue
                    logging.error(f"MorphologicalMemoryLayer: Transient database error during init: {e}")
                    raise

    def migrate_v2(self, dry_run: bool = False, pre_snapshot: bool = True) -> Dict[str, Any]:
        """
        M2: Versioned graph migration and reconciliation.
        Converts legacy physical chunk nodes and edges to canonical logical drawer IDs.
        - Uses exact regex pattern matching (never broad SQL LIKE).
        - Enforces dry-run inventory and optional pre-migration database snapshot.
        - Uses conservative rule (MAX weight) when collapsing multiple chunk edges.
        - Removes resulting self-edges.
        - Aborts on malformed setpoint JSON rather than wiping to empty lists.
        - Deletes ONLY the enumerated migrated chunk IDs.
        - Idempotent.
        """
        chunk_re = re.compile(r"^(.+)_chunk_(\d+)$")
        with self._get_conn() as conn:
            meta = conn.execute("SELECT value FROM morpho_meta WHERE key = 'schema_version'").fetchone()
            if meta and str(meta["value"]) == "2":
                return {"status": "already_migrated", "schema_version": 2}

            all_nodes = conn.execute(
                "SELECT drawer_id, wing, room, created_at, last_activated FROM morpho_nodes"
            ).fetchall()

            # Filter strictly by exact regex, avoiding false matches on names containing '_chunk_'
            chunk_nodes = [row for row in all_nodes if chunk_re.match(row["drawer_id"])]

            # Preflight validate setpoints JSON shapes
            sp_rows_preflight = conn.execute(
                "SELECT goal_key, target_drawers_json, explicit_drawers_json FROM morpho_setpoints"
            ).fetchall()
            for sp in sp_rows_preflight:
                gk = sp["goal_key"]
                try:
                    targets = json.loads(sp["target_drawers_json"] or "[]")
                except Exception as je:
                    raise ValueError(f"Malformed target_drawers_json for goal '{gk}': {je}") from je
                if not isinstance(targets, list):
                    raise ValueError(f"Invalid target_drawers_json shape for goal '{gk}': expected list, got {type(targets).__name__}")
                try:
                    explicits = json.loads(sp["explicit_drawers_json"] or "[]")
                except Exception as je:
                    raise ValueError(f"Malformed explicit_drawers_json for goal '{gk}': {je}") from je
                if not isinstance(explicits, list):
                    raise ValueError(f"Invalid explicit_drawers_json shape for goal '{gk}': expected list, got {type(explicits).__name__}")

            if not chunk_nodes:
                if dry_run:
                    return {"status": "dry_run", "chunks_identified": 0, "schema_version": None}
                conn.execute("""
                INSERT INTO morpho_meta (key, value) VALUES ('schema_version', '2')
                ON CONFLICT(key) DO UPDATE SET value = '2'
                """)
                return {"status": "noop", "chunks_identified": 0, "schema_version": 2}

            physical_chunk_ids = [r["drawer_id"] for r in chunk_nodes]
            parent_nodes = {}
            for row in chunk_nodes:
                cid = row["drawer_id"]
                m = chunk_re.match(cid)
                pid = m.group(1)
                if pid not in parent_nodes:
                    parent_nodes[pid] = {
                        "wing": row["wing"],
                        "room": row["room"],
                        "created_at": row["created_at"],
                        "last_activated": row["last_activated"]
                    }

            if dry_run:
                return {
                    "status": "dry_run",
                    "chunks_identified": len(chunk_nodes),
                    "chunks": physical_chunk_ids,
                    "parents_to_register": list(parent_nodes.keys())
                }

        # Pre-migration snapshot before altering any data
        backup_path = None
        if pre_snapshot and os.path.exists(self.db_path):
            try:
                backup_path = f"{self.db_path}.bak.{int(time.time())}_{uuid.uuid4().hex[:6]}"
                src_c = sqlite3.connect(f"file:{os.path.abspath(self.db_path)}?mode=ro", uri=True)
                dst_c = sqlite3.connect(backup_path)
                with dst_c:
                    src_c.backup(dst_c)
                src_c.close()
                dst_c.close()
            except Exception as snap_err:
                logger.error(f"MorphologicalMemoryLayer: Could not create pre-migration snapshot: {snap_err}")
                return {
                    "status": "snapshot_failed",
                    "error": f"Pre-migration database snapshot failed: {snap_err}",
                    "chunks_identified": len(chunk_nodes),
                    "backup_path": None,
                    "schema_version": None
                }

        with self._get_conn() as conn:
            meta = conn.execute("SELECT value FROM morpho_meta WHERE key = 'schema_version'").fetchone()
            if meta and str(meta["value"]) == "2":
                return {"status": "already_migrated", "schema_version": 2}

            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

            # 1. Register parent logical nodes (preserving original wing, room, timestamps)
            for pid, pdata in parent_nodes.items():
                conn.execute("""
                INSERT OR IGNORE INTO morpho_nodes (drawer_id, wing, room, created_at, last_activated)
                VALUES (?, ?, ?, ?, ?)
                """, (pid, pdata["wing"], pdata["room"], pdata["created_at"] or now_iso, pdata["last_activated"] or now_iso))

            # 2. Map edges: read all edges, map physical chunk endpoints to canonical parents
            edges = conn.execute(
                "SELECT source_id, target_id, weight, created_at, last_reinforced FROM morpho_edges"
            ).fetchall()

            aggregated_edges = {}
            chunk_id_set = set(physical_chunk_ids)
            dropped_self_count = 0

            for e in edges:
                src, dst, w = e["source_id"], e["target_id"], float(e["weight"])
                c_at, r_at = e["created_at"], e["last_reinforced"]

                new_src = chunk_re.match(src).group(1) if src in chunk_id_set and chunk_re.match(src) else src
                new_dst = chunk_re.match(dst).group(1) if dst in chunk_id_set and chunk_re.match(dst) else dst

                if new_src == new_dst:
                    dropped_self_count += 1
                    continue

                key = (new_src, new_dst)
                if key not in aggregated_edges or w > aggregated_edges[key]["weight"]:
                    aggregated_edges[key] = {
                        "weight": w,
                        "created_at": c_at,
                        "last_reinforced": r_at
                    }

            # 3. Update setpoints: validate JSON strictly, abort on malformed JSON
            sp_rows = conn.execute(
                "SELECT goal_key, target_drawers_json, explicit_drawers_json FROM morpho_setpoints"
            ).fetchall()

            for sp in sp_rows:
                gk = sp["goal_key"]
                try:
                    targets = json.loads(sp["target_drawers_json"] or "[]")
                except Exception as je:
                    raise ValueError(f"Malformed target_drawers_json for goal '{gk}': {je}") from je
                try:
                    explicits = json.loads(sp["explicit_drawers_json"] or "[]")
                except Exception as je:
                    raise ValueError(f"Malformed explicit_drawers_json for goal '{gk}': {je}") from je

                new_targets = list(dict.fromkeys(
                    chunk_re.sub(r"\1", tid) if tid in chunk_id_set and chunk_re.match(tid) else tid
                    for tid in targets
                ))
                new_explicits = list(dict.fromkeys(
                    chunk_re.sub(r"\1", tid) if tid in chunk_id_set and chunk_re.match(tid) else tid
                    for tid in explicits
                ))

                conn.execute("""
                UPDATE morpho_setpoints
                SET target_drawers_json = ?, explicit_drawers_json = ?
                WHERE goal_key = ?
                """, (json.dumps(new_targets), json.dumps(new_explicits), gk))

            # 4. Upsert aggregated edges
            for (s, d), data in aggregated_edges.items():
                conn.execute("""
                INSERT INTO morpho_edges (source_id, target_id, weight, created_at, last_reinforced)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_id, target_id) DO UPDATE SET
                    weight = MAX(morpho_edges.weight, excluded.weight),
                    last_reinforced = excluded.last_reinforced
                """, (s, d, data["weight"], data["created_at"], data["last_reinforced"]))

            # 5. Delete ONLY the enumerated chunk IDs from edges and nodes (NEVER broad LIKE!)
            for cid in physical_chunk_ids:
                conn.execute("DELETE FROM morpho_edges WHERE source_id = ? OR target_id = ?", (cid, cid))
                conn.execute("DELETE FROM morpho_nodes WHERE drawer_id = ?", (cid,))

            # Record schema version 2
            conn.execute("""
            INSERT INTO morpho_meta (key, value) VALUES ('schema_version', '2')
            ON CONFLICT(key) DO UPDATE SET value = '2'
            """)

        report = {
            "status": "migrated",
            "chunks_identified": len(physical_chunk_ids),
            "parents_registered": len(parent_nodes),
            "edges_remapped": len(aggregated_edges),
            "edges_dropped_self": dropped_self_count,
            "backup_path": backup_path,
            "schema_version": 2
        }
        logger.info(f"MorphologicalMemoryLayer: v2 migration report: {report}")
        return report

    _migrate_v2 = migrate_v2

    def is_empty(self) -> bool:
        """Returns True if morpho_nodes has zero entries (used for backfill detection)."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM morpho_nodes").fetchone()
            return row["cnt"] == 0

    def get_corpus_revision(self) -> int:
        """Returns the persistent monotonic corpus revision from morpho_meta."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT value FROM morpho_meta WHERE key = 'corpus_revision'").fetchone()
            if row and row["value"]:
                try:
                    return int(row["value"])
                except (ValueError, TypeError):
                    return 1
            return 1

    def increment_corpus_revision(self) -> int:
        """Increments and persists the monotonic corpus revision in morpho_meta."""
        with self._get_conn() as conn:
            current = self.get_corpus_revision()
            new_rev = current + 1
            conn.execute("""
            INSERT INTO morpho_meta (key, value) VALUES ('corpus_revision', ?)
            ON CONFLICT(key) DO UPDATE SET value = ?
            """, (str(new_rev), str(new_rev)))
            return new_rev

    def get_meta(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Reads a metadata value from morpho_meta."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT value FROM morpho_meta WHERE key = ?", (key,)).fetchone()
            if row and row["value"] is not None:
                return str(row["value"])
            return default

    def set_meta(self, key: str, value: str):
        """Persists a metadata key-value pair in morpho_meta."""
        with self._get_conn() as conn:
            conn.execute("""
            INSERT INTO morpho_meta (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = ?
            """, (key, str(value), str(value)))

    def register_drawer(self, drawer_id: str, wing: str = "default", room: str = "general"):
        """Register a new memory drawer as a topological node using its canonical logical ID."""
        canonical_id = self._canonical_id(drawer_id)
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self._get_conn() as conn:
            conn.execute("""
            INSERT OR IGNORE INTO morpho_nodes (drawer_id, wing, room, created_at, last_activated)
            VALUES (?, ?, ?, ?, ?)
            """, (canonical_id, wing, room, now_iso, now_iso))
        self.increment_corpus_revision()

    def register_drawers_batch(self, drawers: List[Tuple[str, str, str]]):
        """Batch register memory drawers: [(drawer_id, wing, room), ...] using canonical IDs."""
        if not drawers:
            return
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        records_dict = {}
        for did, wing, room in drawers:
            cid = self._canonical_id(did)
            if cid not in records_dict:
                records_dict[cid] = (cid, wing, room, now_iso, now_iso)
        records = list(records_dict.values())
        with self._get_conn() as conn:
            conn.executemany("""
            INSERT OR IGNORE INTO morpho_nodes (drawer_id, wing, room, created_at, last_activated)
            VALUES (?, ?, ?, ?, ?)
            """, records)
        self.increment_corpus_revision()

    def remove_drawer(self, drawer_id: str):
        """Remove a drawer, attached conductances, and clean up references from goal setpoints."""
        cid = self._canonical_id(drawer_id)
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self._get_conn() as conn:
            conn.execute("DELETE FROM morpho_edges WHERE source_id = ? OR target_id = ?", (cid, cid))
            conn.execute("DELETE FROM morpho_nodes WHERE drawer_id = ?", (cid,))

            # Clean references from morpho_setpoints
            sp_rows = conn.execute("SELECT goal_key, target_drawers_json, explicit_drawers_json FROM morpho_setpoints").fetchall()
            for sp in sp_rows:
                gk = sp["goal_key"]
                try:
                    targets = json.loads(sp["target_drawers_json"] or "[]")
                    explicits = json.loads(sp["explicit_drawers_json"] or "[]")
                except Exception:
                    targets, explicits = [], []
                new_targets = [d for d in targets if d != cid]
                new_explicits = [d for d in explicits if d != cid]
                if len(new_targets) != len(targets) or len(new_explicits) != len(explicits):
                    conn.execute("""
                    UPDATE morpho_setpoints
                    SET target_drawers_json = ?, explicit_drawers_json = ?, updated_at = ?
                    WHERE goal_key = ?
                    """, (json.dumps(new_targets), json.dumps(new_explicits), now_iso, gk))
        self.increment_corpus_revision()

    def update_parameters(
        self,
        gamma: Optional[float] = None,
        leak: Optional[float] = None,
        decay_rate: Optional[float] = None,
        dissipation: Optional[float] = None,
        dt: Optional[float] = None,
        n_steps: Optional[int] = None,
        top_k: Optional[int] = None,
        eta: Optional[float] = None,
        max_coactivation_set: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Atomically validates and updates numerical engine parameters.
        Rejects non-finite (NaN, inf) or invalid parameters without mutating state.
        """
        with self._param_lock:
            proposed = {}
            if gamma is not None:
                g = float(gamma)
                if not math.isfinite(g) or g < 0.0:
                    raise ValueError(f"Invalid gamma: {gamma}. Must be finite and >= 0.0.")
                proposed["gamma"] = g
            if leak is not None:
                l = float(leak)
                if not math.isfinite(l) or l < 0.0:
                    raise ValueError(f"Invalid leak: {leak}. Must be finite and >= 0.0.")
                proposed["leak"] = l
            if decay_rate is not None:
                d = float(decay_rate)
                if not math.isfinite(d) or d < 0.0 or d > 1.0:
                    raise ValueError(f"Invalid decay_rate: {decay_rate}. Must be finite and between 0.0 and 1.0.")
                proposed["decay_rate"] = d
            if dissipation is not None:
                ds = float(dissipation)
                if not math.isfinite(ds) or ds < 0.0:
                    raise ValueError(f"Invalid dissipation: {dissipation}. Must be finite and >= 0.0.")
                proposed["dissipation"] = ds
            if dt is not None:
                dt_val = float(dt)
                if not math.isfinite(dt_val) or dt_val <= 0.0:
                    raise ValueError(f"Invalid dt: {dt}. Must be finite and > 0.0.")
                proposed["dt"] = dt_val
            if n_steps is not None:
                if isinstance(n_steps, float) and not n_steps.is_integer():
                    raise ValueError(f"Invalid n_steps: {n_steps}. Must be positive integer.")
                ns = int(n_steps)
                if ns <= 0:
                    raise ValueError(f"Invalid n_steps: {n_steps}. Must be positive integer.")
                proposed["n_steps"] = ns
            if top_k is not None:
                if isinstance(top_k, float) and not top_k.is_integer():
                    raise ValueError(f"Invalid top_k: {top_k}. Must be positive integer.")
                tk = int(top_k)
                if tk <= 0:
                    raise ValueError(f"Invalid top_k: {top_k}. Must be positive integer.")
                proposed["top_k"] = tk
            if eta is not None:
                e = float(eta)
                if not math.isfinite(e) or e < 0.0:
                    raise ValueError(f"Invalid eta: {eta}. Must be finite and >= 0.0.")
                proposed["eta"] = e
            if max_coactivation_set is not None:
                if isinstance(max_coactivation_set, float) and not max_coactivation_set.is_integer():
                    raise ValueError(f"Invalid max_coactivation_set: {max_coactivation_set}. Must be positive integer.")
                mcs = int(max_coactivation_set)
                if mcs <= 0:
                    raise ValueError(f"Invalid max_coactivation_set: {max_coactivation_set}. Must be positive integer.")
                proposed["max_coactivation_set"] = mcs

            for k, v in proposed.items():
                setattr(self, k, v)

            return self.get_effective_parameters()

    def get_effective_parameters(self) -> Dict[str, Any]:
        """Returns the active numerical configuration."""
        with self._param_lock:
            return {
                "gamma": self.gamma,
                "leak": self.leak,
                "decay_rate": self.decay_rate,
                "dissipation": self.dissipation,
                "dt": self.dt,
                "n_steps": self.n_steps,
                "top_k": self.top_k,
                "eta": self.eta,
                "max_coactivation_set": self.max_coactivation_set,
            }

    def record_coactivation(self, active_drawer_ids: List[str], eta: Optional[float] = None):
        """
        Hebbian reinforcement: strengthen bidirectional edges between co-activated drawers.
        Capped to max_coactivation_set most recently touched drawers.
        Resolves physical chunk IDs to canonical logical IDs and ensures nodes are registered.
        Zero eta disables learning without creating new learned edges.
        """
        effective_eta = self.eta if eta is None else float(eta)

        # Canonicalize IDs: map chunk IDs to logical drawer IDs
        canonical_ids = [self._canonical_id(did) for did in active_drawer_ids if did]
        # Deduplicate while preserving insertion order
        ids = list(dict.fromkeys(canonical_ids))

        # Capping per §9.3: Keep only the max_coactivation_set most recently touched
        if len(ids) > self.max_coactivation_set:
            ids = ids[-self.max_coactivation_set:]

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        if not ids:
            return

        with self._get_conn() as conn:
            # Ensure all nodes exist in morpho_nodes to guarantee zero foreign key failures
            for nid in ids:
                conn.execute("""
                INSERT OR IGNORE INTO morpho_nodes (drawer_id, wing, room, created_at, last_activated)
                VALUES (?, 'default', 'general', ?, ?)
                """, (nid, now_iso, now_iso))
                conn.execute("UPDATE morpho_nodes SET last_activated = ? WHERE drawer_id = ?", (now_iso, nid))

            if len(ids) < 2:
                return

            # Zero eta disables Hebbian learning (§M4 task 6)
            if effective_eta == 0.0:
                return

            # Reinforce / create bidirectional edges
            for i in range(len(ids)):
                for j in range(len(ids)):
                    if i != j:
                        u, v = ids[i], ids[j]
                        conn.execute("""
                        INSERT INTO morpho_edges (source_id, target_id, weight, created_at, last_reinforced)
                        VALUES (?, ?, 1.0, ?, ?)
                        ON CONFLICT(source_id, target_id) DO UPDATE SET
                            weight = MIN(10.0, weight + ?),
                            last_reinforced = ?
                        """, (u, v, now_iso, now_iso, effective_eta, now_iso))

    def apply_decay(self, elapsed_minutes: Optional[float] = None):
        """
        Intrinsic forgetting: decay all edge conductances and prune dead edges (< 0.05).
        Per §9.5, computes elapsed time using morpho_meta('last_decay_utc') unless explicitly overridden.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        now_iso = now.isoformat()

        with self._get_conn() as conn:
            if elapsed_minutes is not None:
                elapsed = max(0.0, min(7.0 * 24.0 * 60.0, float(elapsed_minutes)))
            else:
                meta_row = conn.execute("SELECT value FROM morpho_meta WHERE key = 'last_decay_utc'").fetchone()
                if not meta_row or not meta_row["value"]:
                    conn.execute("""
                    INSERT INTO morpho_meta (key, value) VALUES ('last_decay_utc', ?)
                    ON CONFLICT(key) DO UPDATE SET value = ?
                    """, (now_iso, now_iso))
                    return

                try:
                    last_time = datetime.datetime.fromisoformat(meta_row["value"])
                    if last_time.tzinfo is None:
                        last_time = last_time.replace(tzinfo=datetime.timezone.utc)
                    raw_elapsed = (now - last_time).total_seconds() / 60.0
                    elapsed = max(0.0, min(7.0 * 24.0 * 60.0, raw_elapsed))
                except Exception as e:
                    logger.warning(f"Error parsing last_decay_utc: {e}")
                    elapsed = 0.0

            if elapsed <= 0.0:
                return

            factor = (1.0 - self.decay_rate) ** elapsed
            conn.execute("UPDATE morpho_edges SET weight = weight * ?", (factor,))
            conn.execute("DELETE FROM morpho_edges WHERE weight < 0.05")

            conn.execute("""
            INSERT INTO morpho_meta (key, value) VALUES ('last_decay_utc', ?)
            ON CONFLICT(key) DO UPDATE SET value = ?
            """, (now_iso, now_iso))

    def relax_setpoint(
        self,
        target_drawer_ids: List[str],
        n_steps: Optional[int] = None,
        top_k: Optional[int] = None,
    ) -> List[Tuple[str, float]]:
        """
        Runs dynamic perturbation relaxation loop conditioned on target setpoints.
        Returns top-k activated (drawer_id, potential) pairs, excluding target_drawer_ids.
        """
        if not target_drawer_ids:
            return []

        # Canonicalize target IDs
        canonical_targets = list(dict.fromkeys(self._canonical_id(tid) for tid in target_drawer_ids if tid))
        if not canonical_targets:
            return []

        with self._param_lock:
            p_leak = self.leak
            p_gamma = self.gamma
            p_dissipation = self.dissipation
            p_dt = self.dt
            steps = n_steps if n_steps is not None else self.n_steps
            k_out = top_k if top_k is not None else self.top_k

        with self._get_conn() as conn:
            total_nodes_row = conn.execute("SELECT COUNT(*) AS cnt FROM morpho_nodes").fetchone()
            total_nodes = total_nodes_row["cnt"] if total_nodes_row else 0
            if total_nodes == 0:
                return []

            # If total_nodes > 2000, restrict to 2-hop neighborhood per §9.7
            if total_nodes > 2000:
                placeholders = ",".join("?" for _ in canonical_targets)
                query = f"""
                WITH RECURSIVE neighborhood(node_id, hop) AS (
                    SELECT drawer_id, 0 FROM morpho_nodes WHERE drawer_id IN ({placeholders})
                    UNION
                    SELECT e.target_id, n.hop + 1
                    FROM neighborhood n
                    JOIN morpho_edges e ON e.source_id = n.node_id
                    WHERE n.hop < 2
                    UNION
                    SELECT e.source_id, n.hop + 1
                    FROM neighborhood n
                    JOIN morpho_edges e ON e.target_id = n.node_id
                    WHERE n.hop < 2
                )
                SELECT DISTINCT node_id FROM neighborhood
                """
                subgraph_rows = conn.execute(query, tuple(canonical_targets)).fetchall()
                nodes = [r[0] for r in subgraph_rows]
            else:
                nodes = [r["drawer_id"] for r in conn.execute("SELECT drawer_id FROM morpho_nodes").fetchall()]

            if not nodes:
                return []

            node_to_idx = {nid: i for i, nid in enumerate(nodes)}
            n = len(nodes)
            W = np.zeros((n, n), dtype=float)

            # Retrieve edges within this subset
            if total_nodes > 2000:
                placeholders = ",".join("?" for _ in nodes)
                edge_rows = conn.execute(
                    f"SELECT source_id, target_id, weight FROM morpho_edges WHERE source_id IN ({placeholders}) AND target_id IN ({placeholders})",
                    tuple(nodes) + tuple(nodes),
                ).fetchall()
            else:
                edge_rows = conn.execute("SELECT source_id, target_id, weight FROM morpho_edges").fetchall()

            for e in edge_rows:
                u, v, w = e["source_id"], e["target_id"], e["weight"]
                if u in node_to_idx and v in node_to_idx:
                    W[node_to_idx[u], node_to_idx[v]] = w

        # Symmetric normalized coupling matrix:
        deg = np.sum(W, axis=1, keepdims=True) + 1e-6
        G = W / np.sqrt(deg @ deg.T)

        # Initialize potentials
        V = np.zeros(n, dtype=float)
        V_target = np.zeros(n, dtype=float)

        for tid in canonical_targets:
            if tid in node_to_idx:
                idx = node_to_idx[tid]
                V_target[idx] = 1.0
                V[idx] = 1.0

        # Dynamic Euler step loop using thread-safe parameter snapshot
        for _ in range(steps):
            drive = p_leak * (G @ V)
            restore = -p_gamma * (V - V_target)
            dissipation = -p_dissipation * V
            dV = (restore + drive + dissipation) * p_dt
            V = np.clip(V + dV, 0.0, 1.0)

        # Rank results
        ranked_indices = np.argsort(-V)
        results: List[Tuple[str, float]] = []
        target_set = set(canonical_targets)

        for idx in ranked_indices:
            if len(results) >= k_out:
                break
            node_id = nodes[idx]
            if node_id in target_set:
                continue
            if V[idx] > 0.10:
                results.append((node_id, float(V[idx])))

        return results

    def link_setpoint(self, goal_key: str, drawer_ids: List[str]) -> Dict[str, Any]:
        """Explicitly connects a goal setpoint anchor to target memory drawers."""
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        canonical_ids = [self._canonical_id(did) for did in drawer_ids if did]
        if not canonical_ids:
            raise ValueError(f"Cannot link setpoint '{goal_key}': no drawer IDs provided.")

        with self._get_conn() as conn:
            # Validate that drawer_ids exist in morpho_nodes
            placeholders = ",".join("?" for _ in canonical_ids)
            existing_rows = conn.execute(
                f"SELECT drawer_id FROM morpho_nodes WHERE drawer_id IN ({placeholders})",
                tuple(canonical_ids),
            ).fetchall()
            existing_set = {r["drawer_id"] for r in existing_rows}
            valid_ids = [did for did in canonical_ids if did in existing_set]
            missing = [did for did in canonical_ids if did not in existing_set]
            if missing:
                logger.warning(
                    f"MorphologicalMemoryLayer: Linking setpoint '{goal_key}' with unregistered drawer IDs: {missing}"
                )
            if not valid_ids:
                raise ValueError(
                    f"Cannot link setpoint '{goal_key}': requested drawer IDs are not registered in the graph: {missing}"
                )

            row = conn.execute(
                "SELECT target_drawers_json, explicit_drawers_json FROM morpho_setpoints WHERE goal_key = ?",
                (goal_key,),
            ).fetchone()
            explicit = []
            target = []
            if row:
                try:
                    explicit = json.loads(row["explicit_drawers_json"] or "[]")
                except Exception:
                    explicit = []
                try:
                    target = json.loads(row["target_drawers_json"] or "[]")
                except Exception:
                    target = []

            for did in valid_ids:
                if did not in explicit:
                    explicit.append(did)
                if did not in target:
                    target.append(did)

            conn.execute("""
            INSERT INTO morpho_setpoints (goal_key, target_drawers_json, explicit_drawers_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(goal_key) DO UPDATE SET
                target_drawers_json = ?,
                explicit_drawers_json = ?,
                updated_at = ?
            """, (goal_key, json.dumps(target), json.dumps(explicit), now_iso, json.dumps(target), json.dumps(explicit), now_iso))

            return {"linked": valid_ids, "rejected": missing}

    def unlink_setpoint(self, goal_key: str, drawer_ids: List[str]) -> bool:
        """Removes explicit drawer links from a goal setpoint."""
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        canonical_remove = set(self._canonical_id(did) for did in drawer_ids if did)
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT target_drawers_json, explicit_drawers_json FROM morpho_setpoints WHERE goal_key = ?",
                (goal_key,),
            ).fetchone()
            if not row:
                return False
            try:
                explicit = json.loads(row["explicit_drawers_json"] or "[]")
                target = json.loads(row["target_drawers_json"] or "[]")
            except Exception:
                explicit, target = [], []

            new_explicit = [d for d in explicit if d not in canonical_remove]
            new_target = [d for d in target if d not in canonical_remove]

            conn.execute("""
            UPDATE morpho_setpoints
            SET target_drawers_json = ?, explicit_drawers_json = ?, updated_at = ?
            WHERE goal_key = ?
            """, (json.dumps(new_target), json.dumps(new_explicit), now_iso, goal_key))
            return True

    def clear_setpoint(self, goal_key: str) -> bool:
        """Removes a goal setpoint entirely."""
        with self._get_conn() as conn:
            conn.execute("DELETE FROM morpho_setpoints WHERE goal_key = ?", (goal_key,))
            return True

    def clean_retired_goals(self, active_goal_keys: List[str]):
        """Clean up retired or deleted goal rows from morpho_setpoints."""
        with self._get_conn() as conn:
            if not active_goal_keys:
                conn.execute("DELETE FROM morpho_setpoints")
                return
            placeholders = ",".join("?" for _ in active_goal_keys)
            conn.execute(
                f"DELETE FROM morpho_setpoints WHERE goal_key NOT IN ({placeholders})",
                tuple(active_goal_keys),
            )

    def get_setpoint(self, goal_key: str) -> Optional[Dict]:
        """Retrieves a morpho_setpoints record if present."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM morpho_setpoints WHERE goal_key = ?", (goal_key,)
            ).fetchone()
            if not row:
                return None
            keys = row.keys()
            return {
                "goal_key": row["goal_key"],
                "text_hash": row["text_hash"],
                "target_drawers": json.loads(row["target_drawers_json"] or "[]"),
                "explicit_drawers": json.loads(row["explicit_drawers_json"] or "[]"),
                "corpus_revision": row["corpus_revision"] if "corpus_revision" in keys else 0,
                "status": row["status"] if "status" in keys else "ok",
                "updated_at": row["updated_at"],
            }

    def upsert_setpoint(
        self,
        goal_key: str,
        text_hash: str,
        target_drawers: List[str],
        corpus_revision: int = 0,
        status: str = "ok",
    ):
        """Upsert target drawers for a goal key with its text hash, corpus revision, and status."""
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        canonical_targets = list(dict.fromkeys(self._canonical_id(did) for did in target_drawers if did))
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT explicit_drawers_json FROM morpho_setpoints WHERE goal_key = ?",
                (goal_key,),
            ).fetchone()
            explicit = []
            if row and row["explicit_drawers_json"]:
                try:
                    explicit = json.loads(row["explicit_drawers_json"])
                except Exception:
                    explicit = []

            # Union targets with explicit
            combined = list(dict.fromkeys(canonical_targets + explicit))

            conn.execute("""
            INSERT INTO morpho_setpoints (goal_key, text_hash, target_drawers_json, explicit_drawers_json, corpus_revision, status, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(goal_key) DO UPDATE SET
                text_hash = excluded.text_hash,
                target_drawers_json = excluded.target_drawers_json,
                corpus_revision = excluded.corpus_revision,
                status = excluded.status,
                updated_at = excluded.updated_at
            """, (goal_key, text_hash, json.dumps(combined), json.dumps(explicit), int(corpus_revision), str(status), now_iso))

    def get_graph_status(self) -> Dict:
        """
        Returns topological status: node count, edge count,
        average clustering coefficient, and top 5 most conductive highways.
        """
        with self._get_conn() as conn:
            node_rows = conn.execute("SELECT drawer_id FROM morpho_nodes").fetchall()
            nodes = [r["drawer_id"] for r in node_rows]
            node_count = len(nodes)

            edge_rows = conn.execute("SELECT source_id, target_id, weight FROM morpho_edges").fetchall()
            edge_count = len(edge_rows)

            # Top 5 most conductive highways
            highways_rows = conn.execute(
                "SELECT source_id, target_id, weight FROM morpho_edges ORDER BY weight DESC, last_reinforced DESC LIMIT 5"
            ).fetchall()
            top_highways = [
                {"source": r["source_id"], "target": r["target_id"], "weight": round(float(r["weight"]), 3)}
                for r in highways_rows
            ]

        # Calculate average clustering coefficient
        if node_count < 3 or edge_count == 0:
            avg_cc = 0.0
        else:
            adj: Dict[str, Set[str]] = {nid: set() for nid in nodes}
            for e in edge_rows:
                u, v = e["source_id"], e["target_id"]
                if u in adj and v in adj:
                    adj[u].add(v)
                    adj[v].add(u)

            local_coeffs = []
            for nid, neighbors in adj.items():
                k = len(neighbors)
                if k < 2:
                    local_coeffs.append(0.0)
                    continue
                triangles = sum(
                    1 for v in neighbors for w in neighbors if v < w and w in adj[v]
                )
                possible = k * (k - 1) / 2.0
                local_coeffs.append(triangles / possible)

            avg_cc = float(sum(local_coeffs) / len(local_coeffs)) if local_coeffs else 0.0

        return {
            "node_count": node_count,
            "edge_count": edge_count,
            "avg_clustering_coefficient": round(avg_cc, 4),
            "top_highways": top_highways,
        }
