"""
core/morphological_layer.py — Morphological Memory Graph & Setpoint Engine
Part of Open Amity framework.
Based on MEMPALACE_MORPHOLOGICAL_UPGRADE_SPEC.md v1.1.0.
"""

import os
import json
import sqlite3
import logging
import datetime
import contextlib
from typing import List, Dict, Tuple, Optional, Set
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
        self.gamma = gamma
        self.leak = leak
        self.decay_rate = decay_rate
        self.dissipation = dissipation
        self.dt = dt
        self.n_steps = n_steps
        self.top_k = top_k
        self.eta = eta
        self.max_coactivation_set = max_coactivation_set

        os.makedirs(self.palace_path, exist_ok=True)
        self._init_db()

    @contextlib.contextmanager
    def _get_conn(self):
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self):
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
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS morpho_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """)

    def is_empty(self) -> bool:
        """Returns True if morpho_nodes has zero entries (used for backfill detection)."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM morpho_nodes").fetchone()
            return row["cnt"] == 0

    def register_drawer(self, drawer_id: str, wing: str = "default", room: str = "general"):
        """Register a new memory drawer as a topological node."""
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self._get_conn() as conn:
            conn.execute("""
            INSERT OR IGNORE INTO morpho_nodes (drawer_id, wing, room, created_at, last_activated)
            VALUES (?, ?, ?, ?, ?)
            """, (drawer_id, wing, room, now_iso, now_iso))

    def register_drawers_batch(self, drawers: List[Tuple[str, str, str]]):
        """Batch register memory drawers: [(drawer_id, wing, room), ...]"""
        if not drawers:
            return
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        records = [(did, wing, room, now_iso, now_iso) for did, wing, room in drawers]
        with self._get_conn() as conn:
            conn.executemany("""
            INSERT OR IGNORE INTO morpho_nodes (drawer_id, wing, room, created_at, last_activated)
            VALUES (?, ?, ?, ?, ?)
            """, records)

    def remove_drawer(self, drawer_id: str):
        """Remove a drawer and all attached conductances."""
        with self._get_conn() as conn:
            conn.execute("DELETE FROM morpho_edges WHERE source_id = ? OR target_id = ?", (drawer_id, drawer_id))
            conn.execute("DELETE FROM morpho_nodes WHERE drawer_id = ?", (drawer_id,))

    def record_coactivation(self, active_drawer_ids: List[str], eta: Optional[float] = None):
        """
        Hebbian reinforcement: strengthen bidirectional edges between co-activated drawers.
        Capped to max_coactivation_set most recently touched drawers.
        """
        if eta is None:
            eta = self.eta

        # Deduplicate while preserving insertion order
        ids = list(dict.fromkeys(active_drawer_ids))

        # Capping per §9.3: Keep only the max_coactivation_set most recently touched
        if len(ids) > self.max_coactivation_set:
            ids = ids[-self.max_coactivation_set:]

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        if len(ids) < 2:
            if len(ids) == 1:
                with self._get_conn() as conn:
                    conn.execute("UPDATE morpho_nodes SET last_activated = ? WHERE drawer_id = ?", (now_iso, ids[0]))
            return

        with self._get_conn() as conn:
            # Update last_activated for all involved nodes
            for nid in ids:
                conn.execute("UPDATE morpho_nodes SET last_activated = ? WHERE drawer_id = ?", (now_iso, nid))

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
                        """, (u, v, now_iso, now_iso, eta, now_iso))

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

        steps = n_steps if n_steps is not None else self.n_steps
        k_out = top_k if top_k is not None else self.top_k

        with self._get_conn() as conn:
            total_nodes_row = conn.execute("SELECT COUNT(*) AS cnt FROM morpho_nodes").fetchone()
            total_nodes = total_nodes_row["cnt"] if total_nodes_row else 0
            if total_nodes == 0:
                return []

            # If total_nodes > 2000, restrict to 2-hop neighborhood per §9.7
            if total_nodes > 2000:
                placeholders = ",".join("?" for _ in target_drawer_ids)
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
                subgraph_rows = conn.execute(query, tuple(target_drawer_ids)).fetchall()
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
        # Degree d_i = sum_k W_ik (weighted degree per §4.3)
        deg = np.sum(W, axis=1, keepdims=True) + 1e-6
        G = W / np.sqrt(deg @ deg.T)

        # Initialize potentials
        V = np.zeros(n, dtype=float)
        V_target = np.zeros(n, dtype=float)

        for tid in target_drawer_ids:
            if tid in node_to_idx:
                idx = node_to_idx[tid]
                V_target[idx] = 1.0
                V[idx] = 1.0

        # Dynamic Euler step loop
        for _ in range(steps):
            drive = self.leak * (G @ V)
            restore = -self.gamma * (V - V_target)
            dissipation = -self.dissipation * V
            dV = (restore + drive + dissipation) * self.dt
            V = np.clip(V + dV, 0.0, 1.0)

        # Rank results
        ranked_indices = np.argsort(-V)
        results: List[Tuple[str, float]] = []
        target_set = set(target_drawer_ids)

        for idx in ranked_indices:
            if len(results) >= k_out:
                break
            node_id = nodes[idx]
            if node_id in target_set:
                continue  # Targets are inputs, never output discoveries (§9.9 test 6)
            if V[idx] > 0.10:
                results.append((node_id, float(V[idx])))

        return results

    def link_setpoint(self, goal_key: str, drawer_ids: List[str]):
        """Explicitly connects a goal setpoint anchor to target memory drawers."""
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self._get_conn() as conn:
            row = conn.execute("SELECT target_drawers_json, explicit_drawers_json FROM morpho_setpoints WHERE goal_key = ?", (goal_key,)).fetchone()
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

            for did in drawer_ids:
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

    def get_setpoint(self, goal_key: str) -> Optional[Dict]:
        """Retrieves a morpho_setpoints record if present."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT goal_key, text_hash, target_drawers_json, explicit_drawers_json, updated_at FROM morpho_setpoints WHERE goal_key = ?", (goal_key,)).fetchone()
            if not row:
                return None
            return {
                "goal_key": row["goal_key"],
                "text_hash": row["text_hash"],
                "target_drawers": json.loads(row["target_drawers_json"] or "[]"),
                "explicit_drawers": json.loads(row["explicit_drawers_json"] or "[]"),
                "updated_at": row["updated_at"]
            }

    def upsert_setpoint(self, goal_key: str, text_hash: str, target_drawers: List[str]):
        """Upsert target drawers for a goal key with its text hash."""
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self._get_conn() as conn:
            row = conn.execute("SELECT explicit_drawers_json FROM morpho_setpoints WHERE goal_key = ?", (goal_key,)).fetchone()
            explicit = []
            if row and row["explicit_drawers_json"]:
                try:
                    explicit = json.loads(row["explicit_drawers_json"])
                except Exception:
                    explicit = []

            # Union targets with explicit
            combined = list(dict.fromkeys(target_drawers + explicit))

            conn.execute("""
            INSERT INTO morpho_setpoints (goal_key, text_hash, target_drawers_json, explicit_drawers_json, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(goal_key) DO UPDATE SET
                text_hash = ?,
                target_drawers_json = ?,
                updated_at = ?
            """, (goal_key, text_hash, json.dumps(combined), json.dumps(explicit), now_iso, text_hash, json.dumps(combined), now_iso))

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
                # Count edges among neighbors
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
