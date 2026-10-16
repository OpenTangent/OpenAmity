from core.state_barrier import memory_state_operation
import os
import re
import json
import time
import copy
import logging
import datetime
import uuid
import threading
import hashlib
import fcntl
import contextlib
from typing import Optional, List, Dict, Any, Tuple
from core.settings_manager import SettingsManager
from core.file_utils import atomic_json_write
from core.memory_records import MemoryHit, MemoryRecord, MemoryResult

from mempalace.layers import MemoryStack

_mempalace_lock = threading.Lock()
_palace_inprocess_locks = {}
_palace_inprocess_locks_lock = threading.Lock()


def _get_palace_inprocess_lock(palace_path: str) -> threading.RLock:
    norm = os.path.abspath(palace_path)
    with _palace_inprocess_locks_lock:
        if norm not in _palace_inprocess_locks:
            _palace_inprocess_locks[norm] = threading.RLock()
        return _palace_inprocess_locks[norm]


@contextlib.contextmanager
def _palace_cross_instance_lock(palace_path: str):
    """
    Palace-scoped transactional lock covering multi-threading, multi-instance,
    and multi-process operations on agent identity approvals and ledgers.
    """
    if not palace_path:
        yield
        return
    norm = os.path.abspath(palace_path)
    os.makedirs(norm, exist_ok=True)
    rlock = _get_palace_inprocess_lock(norm)
    lock_file_path = os.path.join(norm, ".palace_identity.lock")
    with rlock:
        with open(lock_file_path, "a+") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


class MemPalaceManager:
    def __init__(self, agent_id: str = None, palace_path: str = None, soul_jar_path: str = None):
        self.agent_id = agent_id
        if not palace_path:
            from config import paths
            palace_path = paths.get_mempalace_dir(agent_id)
        if not soul_jar_path:
            soul_jar_path = os.path.abspath(os.path.join(
                os.path.dirname(__file__), "..", "memory", "soul_jar.json"))

        self.palace_path = palace_path
        self.soul_jar_path = soul_jar_path
        self._cache_lock = threading.RLock()
        self._mirrors_cache = None
        self._short_term_cache = None
        self._corpus_revision = 1
        self._degraded = False

        # Ensure directory exists
        os.makedirs(self.palace_path, exist_ok=True)

        # Record embedder identity to prevent MemPalace warnings after a factory reset
        try:
            from mempalace.palace import set_palace_embedder_identity
            set_palace_embedder_identity(self.palace_path, model="minilm")
        except Exception as e:
            logging.debug(f"Could not set embedder identity: {e}")

        # Sync soul_jar.json to MemPalace identity.txt format
        self.identity_path = os.path.join(self.palace_path, "identity.txt")
        self._sync_identity()

        # Morphological Memory (MemPalace v2) configuration
        self._turn_touched = []
        self.morpho_layer = None
        self._load_morpho_settings()

        # Initialize the stack with narrow diagnostics (M3 task 4)
        try:
            self.stack = MemoryStack(
                palace_path=self.palace_path, identity_path=self.identity_path)
            self._degraded = False
        except Exception as e:
            import sqlite3
            is_corrupt_db = isinstance(e, (sqlite3.DatabaseError, sqlite3.OperationalError)) and (
                "file is not a database" in str(e).lower() or "malformed" in str(e).lower()
            )
            if is_corrupt_db:
                import shutil
                timestamp = int(time.time())
                backup_dir = f"{self.palace_path}_quarantined_{timestamp}"
                logging.critical(
                    f"MemPalaceManager: Authoritative storage is corrupted ({e}). Quarantining to {backup_dir} and re-initializing.")
                shutil.move(self.palace_path, backup_dir)
                os.makedirs(self.palace_path, exist_ok=True)

                for safe_file in [
                    "mirrors.json",
                    "short_term_mem.json",
                    "short_term_archive.json",
                    "identity_approvals.json",
                    "identity.txt",
                    "corpus_meta.json",
                    ".sanctuary_initialized",
                ]:
                    src = os.path.join(backup_dir, safe_file)
                    if os.path.exists(src):
                        shutil.copy2(src, os.path.join(self.palace_path, safe_file))

                try:
                    self.stack = MemoryStack(
                        palace_path=self.palace_path, identity_path=self.identity_path)
                    self._degraded = False
                except Exception as fatal_e:
                    logging.error(f"MemPalaceManager: Failed re-initializing MemoryStack after quarantine: {fatal_e}")
                    self.stack = None
                    self._degraded = True
            else:
                # Transient error: do NOT quarantine or wipe palace! Mark degraded state.
                logging.error(f"MemPalaceManager: Transient error initializing MemoryStack: {e}")
                self.stack = None
                self._degraded = True

        # Initialize/reconcile morphology AFTER authoritative storage is healthy (M3 task 4)
        if not self._degraded and self._morpho_enabled:
            self._init_morphological_layer()

        # Ensure sanctuary is initialized (Layer 2 data)
        self.initialize_sanctuary()

        # Ensure default short-term memory is seeded if not present
        self.initialize_short_term_memory()

        # Initialize corpus revision from durable storage if available
        self._corpus_revision = self.get_corpus_revision()

        # Recover any uncommitted identity delta from journal
        self._recover_identity_delta_journal()

    def get_corpus_revision(self) -> int:
        """Returns monotonic corpus revision tracking added/deleted memories."""
        if hasattr(self, 'morpho_layer') and self.morpho_layer:
            rev = self.morpho_layer.get_corpus_revision()
            self._corpus_revision = max(getattr(self, "_corpus_revision", 1), rev)
            return rev
        meta_path = os.path.join(self.palace_path, "corpus_meta.json") if hasattr(self, "palace_path") and self.palace_path else None
        if meta_path and os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    val = data.get("corpus_revision")
                    if val is not None:
                        rev = int(val)
                        self._corpus_revision = max(getattr(self, "_corpus_revision", 1), rev)
                        return rev
            except Exception:
                pass
        return getattr(self, "_corpus_revision", 1)

    @memory_state_operation
    def _increment_corpus_revision(self) -> int:
        """Increments corpus revision to invalidate cached goal anchors across changes."""
        # Invalidate in-memory recall catalog cache on mutation
        if hasattr(self, "_recall_catalog_cache"):
            self._recall_catalog_cache = None

        if hasattr(self, 'morpho_layer') and self.morpho_layer:
            self._corpus_revision = self.morpho_layer.increment_corpus_revision()
            return self._corpus_revision

        # Non-morphological fallback revision tracking with transactional locking and durable persistence
        if hasattr(self, "palace_path") and self.palace_path:
            lock_path = os.path.join(self.palace_path, ".corpus_meta.lock")
            meta_path = os.path.join(self.palace_path, "corpus_meta.json")
            try:
                with open(lock_path, "a+") as lock_file:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                    try:
                        cur_rev = 1
                        data = {}
                        if os.path.exists(meta_path):
                            with open(meta_path, "r", encoding="utf-8") as f:
                                data = json.load(f)
                                if "corpus_revision" in data:
                                    cur_rev = int(data["corpus_revision"])
                        cur_rev = max(cur_rev, getattr(self, "_corpus_revision", 1))
                        new_rev = cur_rev + 1
                        data["corpus_revision"] = new_rev
                        atomic_json_write(meta_path, data)
                        self._corpus_revision = new_rev
                        return new_rev
                    finally:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except Exception as e:
                logging.error(f"Failed to persist corpus revision: {e}")
                raise RuntimeError(f"Failed to persist corpus revision: {e}") from e
        else:
            self._corpus_revision = getattr(self, "_corpus_revision", 1) + 1
            return self._corpus_revision

    def _load_morpho_settings(self):
        settings = SettingsManager(agent_id=self.agent_id)
        self._morpho_settings = settings.get("core.morphological_memory")
        if self._morpho_settings is None:
            self._morpho_settings = settings.get("core.morphological-memory")
        if not isinstance(self._morpho_settings, dict):
            self._morpho_settings = {}
        self._morpho_enabled = self._morpho_settings.get("enabled", True)

    def _validate_budget(self, val: Any, name: str, default: int, min_val: int = 100, max_val: int = 50000) -> int:
        if val is None:
            return default
        if isinstance(val, bool):
            raise ValueError(f"Invalid {name}: boolean {val} is not a valid budget integer.")
        if isinstance(val, float) and not val.is_integer():
            raise ValueError(f"Invalid {name}: fractional value {val} is not a valid budget integer.")
        try:
            int_val = int(val)
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid {name}: {val} cannot be converted to integer.") from e
        if int_val <= 0:
            raise ValueError(f"Invalid {name}: {int_val}. Must be positive integer.")
        return max(min_val, min(max_val, int_val))

    def _load_memory_budgets(self) -> Dict[str, int]:
        settings = SettingsManager(agent_id=self.agent_id)
        raw_budgets = settings.get("core.memory_budgets")
        if raw_budgets is None:
            raw_budgets = settings.get("memory_budgets")
        if not isinstance(raw_budgets, dict):
            raw_budgets = {}

        defaults = {
            "sanctuary_max_chars": 4800,
            "short_term_max_chars": 2400,
            "prospective_max_chars": 2400,
            "short_term_context_max_chars": 3200
        }

        budgets = {}
        for key, def_val in defaults.items():
            budgets[key] = self._validate_budget(raw_budgets.get(key), key, def_val)

        # Provider awareness: if low-token mode is active, scale budgets down
        is_low_token = settings.get("core.low_token_mode", False) or settings.get("core.low-token-mode", False)
        if is_low_token:
            for key in budgets:
                budgets[key] = max(100, budgets[key] // 2)

        return budgets

    def _get_budget(self, budget_name: str, fallback: int) -> int:
        try:
            budgets = self._load_memory_budgets()
            return budgets.get(budget_name, fallback)
        except Exception as e:
            logging.warning(f"Error loading budget {budget_name}: {e}")
            return fallback

    def _get_collection(self, create: bool = False):
        try:
            from mempalace.palace import get_collection
            return get_collection(self.palace_path, create=create)
        except Exception as e:
            logging.warning(f"Could not get palace collection: {e}")
            return None

    def _init_morphological_layer(self):
        try:
            from core.morphological_layer import MorphologicalMemoryLayer
            self.morpho_layer = MorphologicalMemoryLayer(
                palace_path=self.palace_path,
                gamma=float(self._morpho_settings.get("gamma", 0.6)),
                leak=float(self._morpho_settings.get("leak", 0.8)),
                decay_rate=float(self._morpho_settings.get("decay_per_minute", 0.002)),
                dissipation=float(self._morpho_settings.get("dissipation", 0.05)),
                dt=float(self._morpho_settings.get("dt", 0.1)),
                n_steps=int(self._morpho_settings.get("n_steps", 25)),
                top_k=int(self._morpho_settings.get("top_k", 5)),
                eta=float(self._morpho_settings.get("eta", 1.0)),
                max_coactivation_set=int(self._morpho_settings.get("max_coactivation_set", 16)),
            )
            # Backfill migration (§9.2)
            if self.morpho_layer.is_empty():
                self._backfill_morphological_nodes()
        except Exception as e:
            logging.warning(f"Failed to initialize MorphologicalMemoryLayer: {e}", exc_info=True)
            self.morpho_layer = None

    def _backfill_morphological_nodes(self):
        """Backfill existing ChromaDB drawers into morpho_nodes using canonical logical IDs only."""
        if not self.morpho_layer:
            return
        try:
            col = self._get_collection(create=False)
            if col:
                data = col.get(include=["metadatas"])
                ids = data.get("ids") or []
                metas = data.get("metadatas") or []
                chunk_re = re.compile(r"^(.*)_chunk_\d+$")
                drawers_dict = {}
                for did, meta in zip(ids, metas):
                    meta = meta or {}
                    lid = meta.get("parent_drawer_id") or (chunk_re.match(did).group(1) if chunk_re.match(did) else did)
                    w = meta.get("wing", "default")
                    r = meta.get("room", "general")
                    if lid not in drawers_dict:
                        drawers_dict[lid] = (lid, w, r)
                drawers = list(drawers_dict.values())
                if drawers:
                    self.morpho_layer.register_drawers_batch(drawers)
                logging.info(f"Backfill migration: registered {len(drawers)} canonical logical drawers into morpho_nodes.")
        except Exception as e:
            logging.warning(f"Error during morphological backfill migration: {e}")

    @memory_state_operation
    def reconcile_graph(self) -> Dict[str, Any]:
        """
        M2: Idempotent graph reconciliation.
        Verifies all morpho_nodes against authoritative ChromaDB storage.
        Repairs missing authoritative nodes and prunes dangling nodes.
        Aborts if ChromaDB is unavailable to avoid destructive pruning.
        """
        if not self._morpho_enabled or not self.morpho_layer:
            return {"status": "disabled"}

        col = self._get_collection(create=False)
        if not col:
            return {"status": "aborted", "reason": "ChromaDB storage unavailable"}

        try:
            data = col.get(include=["metadatas"])
            col_ids = data.get("ids") or []
            col_metas = data.get("metadatas") or []
        except Exception as e:
            logging.error(f"reconcile_graph aborted due to storage error: {e}")
            return {"status": "aborted", "error": str(e)}

        if col_metas and len(col_ids) != len(col_metas):
            return {"status": "aborted", "error": "Mismatched collection IDs and metadatas length"}

        chunk_re = re.compile(r"^(.*)_chunk_\d+$")
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        authoritative_nodes = {}
        for idx, did in enumerate(col_ids):
            meta = (col_metas[idx] if col_metas and idx < len(col_metas) else {}) or {}
            lid = meta.get("parent_drawer_id") or (chunk_re.match(did).group(1) if chunk_re.match(did) else did)
            if lid not in authoritative_nodes:
                authoritative_nodes[lid] = {
                    "wing": meta.get("wing", "default"),
                    "room": meta.get("room", "general")
                }
        valid_logical_ids = set(authoritative_nodes.keys())

        with self.morpho_layer._get_conn() as conn:
            existing_graph_nodes = {r[0] for r in conn.execute("SELECT drawer_id FROM morpho_nodes").fetchall()}
            dangling = existing_graph_nodes - valid_logical_ids
            missing_nodes = valid_logical_ids - existing_graph_nodes

            # 1. Repair missing authoritative nodes
            for mid in missing_nodes:
                mdata = authoritative_nodes[mid]
                conn.execute("""
                INSERT OR IGNORE INTO morpho_nodes (drawer_id, wing, room, created_at, last_activated)
                VALUES (?, ?, ?, ?, ?)
                """, (mid, mdata["wing"], mdata["room"], now_iso, now_iso))

            # 2. Prune only confirmed dangling nodes
            for did in dangling:
                conn.execute("DELETE FROM morpho_edges WHERE source_id = ? OR target_id = ?", (did, did))
                conn.execute("DELETE FROM morpho_nodes WHERE drawer_id = ?", (did,))

        if missing_nodes or dangling:
            self._increment_corpus_revision()

        return {
            "status": "reconciled",
            "authoritative_drawers": len(valid_logical_ids),
            "retained_graph_nodes": len(valid_logical_ids & existing_graph_nodes),
            "repaired_missing_nodes": len(missing_nodes),
            "pruned_dangling_nodes": len(dangling)
        }

    def touch_drawers(self, drawer_ids: list):
        """Record touched drawer IDs in the current turn using canonical IDs."""
        with self._cache_lock:
            for did in drawer_ids:
                if did:
                    cid = self.morpho_layer._canonical_id(did) if self.morpho_layer else str(did)
                    if cid and cid not in self._turn_touched:
                        self._turn_touched.append(cid)

    @memory_state_operation
    def reload_settings(self):
        """Reload settings, sync identity, and atomically apply updated engine parameters."""
        self._sync_identity()
        self._mirrors_cache = None
        self._short_term_cache = None
        if hasattr(self, 'stack') and hasattr(self.stack, 'l0'):
            self.stack.l0._text = None
        self._load_morpho_settings()

        if self._morpho_enabled:
            if not self.morpho_layer:
                self._init_morphological_layer()
            else:
                try:
                    self.morpho_layer.update_parameters(
                        gamma=self._morpho_settings.get("gamma"),
                        leak=self._morpho_settings.get("leak"),
                        decay_rate=self._morpho_settings.get("decay_per_minute"),
                        dissipation=self._morpho_settings.get("dissipation"),
                        dt=self._morpho_settings.get("dt"),
                        n_steps=self._morpho_settings.get("n_steps"),
                        top_k=self._morpho_settings.get("top_k"),
                        eta=self._morpho_settings.get("eta"),
                        max_coactivation_set=self._morpho_settings.get("max_coactivation_set"),
                    )
                except Exception as e:
                    logging.warning(f"Error updating morphological layer parameters: {e}")
        else:
            self.morpho_layer = None

    @memory_state_operation
    def _sync_identity(self):
        """Convert soul_jar.json to a plain text identity.txt for MemPalace Layer 0"""
        if not os.path.exists(self.soul_jar_path):
            return

        try:
            with open(self.soul_jar_path, 'r', encoding="utf-8") as f:
                soul_jar = json.load(f)

            core_id = soul_jar.get("core_identity", {})
            settings = SettingsManager(agent_id=self.agent_id)
            soul_jar_settings = settings.get("core.agent", {})

            name = soul_jar_settings.get(
                "name", core_id.get("name", "The Agent"))
            created_date = soul_jar_settings.get("creation-date", "Unknown")
            if created_date == "Unknown":
                created_date = datetime.datetime.now().strftime("%Y-%m-%d")
                settings.set("core.agent.creation-date", created_date)
                settings.save()

            gender = settings.get("core.agent.gender", "Unknown")

            lines = []

            meta_header = soul_jar.get("meta_header", {})
            system_role = meta_header.get(
                'system_role_instruction', '').replace('{name}', name)
            lines.append(f"System Role: {system_role}")
            lines.append(f"Created Date: {created_date}")
            try:
                from core.version import __version__ as amity_version
                lines.append(f"Open Amity Framework Version: {amity_version}")
            except ImportError:
                pass
            lines.append("")

            archetype = soul_jar_settings.get(
                "archetype", core_id.get("archetype", ""))
            base_personality = soul_jar_settings.get(
                "base-personality", core_id.get("base_personality", ""))

            lines.append(f"Name: {name}")
            lines.append(f"Gender: {gender}")
            lines.append(f"Archetype: {archetype}")
            lines.append(f"Base Personality: {base_personality}")
            lines.append("")

            lines.append("Core Values:")
            immutable_cv = core_id.get("core_values", [])
            mutable_cv = soul_jar_settings.get("core-values", [])
            for val in immutable_cv + mutable_cv:
                lines.append(f" - {val}")
            lines.append("")

            lines.append("Overarching Goals:")
            immutable_og = core_id.get("overarching_goals", [])
            mutable_og = soul_jar_settings.get("overarching-goals", [])
            for goal in immutable_og + mutable_og:
                lines.append(f" - {goal}")
            lines.append("")

            lines.append("Anti-Patterns:")
            immutable_ap = core_id.get("anti_patterns", [])
            mutable_ap = soul_jar_settings.get("anti-patterns", [])
            for ap in immutable_ap + mutable_ap:
                lines.append(f" - {ap}")
            lines.append("")

            lines.append("Operational Protocols:")
            protocols = soul_jar.get("operational_protocols", {})
            for key, val in protocols.items():
                if isinstance(val, dict):
                    trigger = val.get("trigger", "")
                    action = val.get("action", "")
                    lines.append(f" - {key.replace('_', ' ').title()}:")
                    if trigger:
                        lines.append(f"   - Trigger: {trigger}")
                    if action:
                        lines.append(f"   - Action: {action}")
                else:
                    lines.append(f" - {key}: {val}")

            temp_path = f"{self.identity_path}.tmp.{uuid.uuid4().hex}"
            with open(temp_path, 'w', encoding="utf-8") as f:
                f.write("\n".join(lines))
            os.replace(temp_path, self.identity_path)

            if hasattr(self, 'stack') and hasattr(self.stack, 'l0'):
                self.stack.l0._text = None
        except Exception as e:
            logging.error(f"Error syncing identity: {e}", exc_info=True)

    @memory_state_operation
    def wake_up(self, wing: str = None) -> str:
        """Returns L0 + Self-Perception + Short-Term Context + Morphological Prospective Context with token budgets."""
        def _bound_section(text: str, max_chars: int, label: str) -> str:
            if not text or len(text) <= max_chars:
                return text
            notice = f"\n[Notice: {label} truncated to fit token budget ({max_chars} chars)]"
            avail = max(0, max_chars - len(notice))
            return text[:avail] + notice

        l0_raw = self.stack.l0.render() if self.stack and hasattr(self.stack, 'l0') else ""
        # R1: Layer 0 Identity is protected policy and is NEVER truncated!
        from core.prompt_context import MEMORY_BEGIN, MEMORY_END
        base_context = l0_raw + "\n\n" + MEMORY_BEGIN

        now = datetime.datetime.now().astimezone()
        tz_str = f" ({now.tzname()})" if now.tzname() else ""
        current_time_str = f"{now.strftime('%A, %Y-%m-%d %H:%M:%S')}{tz_str}"
        base_context += f"\n\n[Wake Context: Current Date & Time is {current_time_str}]"

        self_perception = self.get_self_perception()
        if self_perception:
            mirrors_limit = self._get_budget("sanctuary_max_chars", 4800)
            base_context += f"\n\n{_bound_section(self_perception, mirrors_limit, 'Layer 2 Mirrors')}"

        memories = self._load_short_term_memories()
        num_items = len(memories)
        num_words = sum(len(m.get('content', '').split()) for m in memories)
        try:
            num_drawers = self.stack.status().get('total_drawers', 0) if self.stack else 0
        except Exception:
            num_drawers = 0
        base_context += f"\n\n[Memory Status: Short-Term ({num_items} items / {num_words} words), Palace ({num_drawers} drawers)]"

        st_limit = self._get_budget("short_term_max_chars", 2400)
        short_term = self.get_short_term_context(max_chars=st_limit)
        if short_term:
            base_context += f"\n\n--- Short-Term Memory (Continuity) ---\n{short_term}\n"

        # Morphological Memory (Prospective Attractors)
        if self._morpho_enabled and self.morpho_layer:
            try:
                self.step_temporal_decay()
                prospective_limit = self._get_budget("prospective_max_chars", 2400)
                prospective_section = self._get_prospective_memory_context(max_chars=prospective_limit)
                if prospective_section:
                    base_context += f"\n\n{_bound_section(prospective_section, prospective_limit, 'Prospective Attractors')}"
            except Exception as e:
                logging.warning(f"Error generating morphological prospective memories: {e}")

        # Enforce global context budget on non-L0 dynamic context if necessary
        return base_context + "\n" + MEMORY_END

    @memory_state_operation
    def _get_prospective_memory_context(self, max_chars: Optional[int] = None) -> str:
        if not self._morpho_enabled or not self.morpho_layer:
            return ""

        try:
            from config import paths
            traj_path = os.path.join(paths.get_base_dir_for(self.agent_id), "trajectory.json")
            if not os.path.exists(traj_path):
                return ""

            with open(traj_path, "r", encoding="utf-8") as f:
                traj_data = json.load(f)

            goals = []
            aspirations = traj_data.get("aspirations", {})
            for tier in ["short_term", "medium_term", "long_term"]:
                for asp in aspirations.get(tier, []):
                    if asp.get("status") == "active" and asp.get("description"):
                        goals.append((asp.get("id", f"asp_{uuid.uuid4().hex[:6]}"), asp["description"]))

            for task in traj_data.get("tasks", []):
                if task.get("status") == "in_progress" and task.get("description"):
                    goals.append((task.get("id", f"tsk_{uuid.uuid4().hex[:6]}"), task["description"]))

            if not goals:
                return ""

            col = self._get_collection(create=False)
            all_target_drawers = []
            current_rev = self.get_corpus_revision()
            chunk_re = re.compile(r"^(.*)_chunk_\d+$")

            # Detect external collection content, ID, count, or underlying index changes
            if col is not None:
                hasher = hashlib.sha256()

                # 1. Count tracking
                c_count = None
                if hasattr(col, "count") and callable(col.count):
                    try:
                        c_count = col.count()
                        hasher.update(f"count:{c_count}".encode("utf-8"))
                    except Exception:
                        pass

                # Content contract: digest ALL physical documents and metadata.
                # Deterministic ID ordering avoids backend iteration-order drift;
                # SQLite mtimes/mock attributes are not authoritative content.
                if hasattr(col, "get") and callable(col.get):
                    rows = []
                    offset = 0
                    while True:
                        batch = col.get(limit=250, offset=offset, include=["documents", "metadatas"])
                        ids = batch.get("ids", [])
                        docs = batch.get("documents") or [None] * len(ids)
                        metas = batch.get("metadatas") or [{}] * len(ids)
                        rows.extend(zip(ids, docs, metas))
                        offset += len(ids)
                        if not ids or len(ids) < 250:
                            break
                    for did, document, metadata in sorted(rows, key=lambda row: row[0]):
                        hasher.update(json.dumps([did, document, metadata], sort_keys=True,
                                                 ensure_ascii=False, default=str).encode("utf-8"))
                col_meta = getattr(col, "metadata", None)
                hasher.update(json.dumps(col_meta, sort_keys=True, default=str).encode("utf-8"))
                # Backend/embedder swaps invalidate even with the same content.
                embedding_fn = getattr(col, "_embedding_function", None)
                if embedding_fn is not None:
                    config = embedding_fn.get_config() if hasattr(embedding_fn, "get_config") else type(embedding_fn).__qualname__
                    hasher.update(json.dumps(config, sort_keys=True, default=str).encode("utf-8"))

                current_fp = hasher.hexdigest()

                # Baseline handling (restart persistence + first-observation baseline)
                persisted_fp = getattr(self, "_last_col_fp", None)
                if persisted_fp is None:
                    if hasattr(self, "morpho_layer") and self.morpho_layer:
                        persisted_fp = self.morpho_layer.get_meta("col_fingerprint")
                    if persisted_fp is None and hasattr(self, "palace_path") and self.palace_path:
                        meta_file = os.path.join(self.palace_path, "corpus_meta.json")
                        if os.path.exists(meta_file):
                            try:
                                with open(meta_file, "r", encoding="utf-8") as f:
                                    persisted_fp = json.load(f).get("col_fingerprint")
                            except Exception:
                                pass

                if persisted_fp is None:
                    # First-observation baseline: establish baseline without false invalidation storm
                    self._last_col_fp = current_fp
                    if hasattr(self, "morpho_layer") and self.morpho_layer:
                        self.morpho_layer.set_meta("col_fingerprint", current_fp)
                    if hasattr(self, "palace_path") and self.palace_path:
                        try:
                            meta_file = os.path.join(self.palace_path, "corpus_meta.json")
                            data = {}
                            if os.path.exists(meta_file):
                                with open(meta_file, "r", encoding="utf-8") as f:
                                    data = json.load(f)
                            data["col_fingerprint"] = current_fp
                            atomic_json_write(meta_file, data)
                        except Exception:
                            pass
                elif current_fp != persisted_fp:
                    # Content, ID, count, or out-of-band edit detected!
                    self._increment_corpus_revision()
                    current_rev = self.get_corpus_revision()
                    self._last_col_fp = current_fp
                    if hasattr(self, "morpho_layer") and self.morpho_layer:
                        self.morpho_layer.set_meta("col_fingerprint", current_fp)
                    if hasattr(self, "palace_path") and self.palace_path:
                        try:
                            meta_file = os.path.join(self.palace_path, "corpus_meta.json")
                            data = {}
                            if os.path.exists(meta_file):
                                with open(meta_file, "r", encoding="utf-8") as f:
                                    data = json.load(f)
                            data["col_fingerprint"] = current_fp
                            data["corpus_revision"] = current_rev
                            atomic_json_write(meta_file, data)
                        except Exception:
                            pass
                else:
                    self._last_col_fp = current_fp

            for goal_key, goal_text in goals:
                text_hash = hashlib.sha1(goal_text.encode("utf-8")).hexdigest()
                setpoint = self.morpho_layer.get_setpoint(goal_key)

                # M4 & R4: Anchor freshness requires matching text_hash, matching corpus_revision, and 'ok' status.
                # An 'empty' status (no matching memories found previously) is NEVER treated as permanently fresh!
                is_fresh = (
                    setpoint is not None
                    and setpoint.get("text_hash") == text_hash
                    and int(setpoint.get("corpus_revision", 0)) == current_rev
                    and setpoint.get("status") == "ok"
                )

                if is_fresh:
                    target_drawers = setpoint.get("target_drawers", [])
                else:
                    matching_ids = []
                    query_failed = False
                    if col is None:
                        query_failed = True
                    else:
                        try:
                            q = col.query(query_texts=[goal_text], n_results=3, include=["metadatas", "distances"])
                            ids_list = q.get("ids", [[]])
                            dists_list = q.get("distances", [[]])
                            metas_list = q.get("metadatas", [[]])
                            if ids_list and ids_list[0] and dists_list and dists_list[0]:
                                metas = metas_list[0] if metas_list and metas_list[0] else [{}] * len(ids_list[0])
                                for did, dist, meta in zip(ids_list[0], dists_list[0], metas):
                                    meta = meta or {}
                                    if dist is not None and float(dist) < 0.45:
                                        # Map physical chunk to canonical logical ID
                                        lid = meta.get("parent_drawer_id") or (chunk_re.match(did).group(1) if chunk_re.match(did) else did)
                                        matching_ids.append(lid)
                        except Exception as qe:
                            logging.warning(f"Error querying ChromaDB for goal setpoint: {qe}")
                            query_failed = True

                    if not query_failed:
                        status = "ok" if matching_ids else "empty"
                        self.morpho_layer.upsert_setpoint(
                            goal_key, text_hash, matching_ids, corpus_revision=current_rev, status=status
                        )
                        updated_sp = self.morpho_layer.get_setpoint(goal_key)
                        target_drawers = updated_sp.get("target_drawers", []) if updated_sp else matching_ids
                    else:
                        # Do NOT cache transient errors as valid empty setpoints!
                        target_drawers = setpoint.get("explicit_drawers", []) if setpoint else []

                all_target_drawers.extend(target_drawers)

            all_target_drawers = list(dict.fromkeys(all_target_drawers))
            if not all_target_drawers:
                return ""

            top_k = int(self._morpho_settings.get("top_k", 5))
            results = self.morpho_layer.relax_setpoint(all_target_drawers, top_k=top_k)
            if not results or not col:
                return ""

            header = f"--- Morphological Memory (Prospective Attractors [Rev: {current_rev}]) ---"
            max_total_chars = max_chars if max_chars is not None else self._get_budget("prospective_max_chars", 2400)
            if max_total_chars <= 0:
                return ""
            if len(header) >= max_total_chars:
                return header[:max_total_chars]

            lines = [header]
            total_chars = len(header)

            for did, potential in results:
                doc_text = ""
                try:
                    res = col.get(ids=[did], include=["documents"])
                    docs = res.get("documents", [])
                    if docs and docs[0]:
                        doc_text = str(docs[0]).strip().replace("\n", " ")
                    else:
                        # Fallback for chunked memory lookup
                        c_res = col.get(where={"parent_drawer_id": did}, include=["documents", "metadatas"])
                        c_docs = c_res.get("documents", [])
                        if c_docs and c_docs[0]:
                            doc_text = str(c_docs[0]).strip().replace("\n", " ")
                except Exception as de:
                    logging.warning(f"Error fetching document for prospective drawer {did}: {de}")
                    continue

                if not doc_text:
                    continue

                if len(doc_text) > 300:
                    doc_text = doc_text[:297] + "..."

                entry = f"[Drawer #{did}] {doc_text}"
                needed = len(entry) + 1  # 1 for newline
                if total_chars + needed <= max_total_chars:
                    lines.append(entry)
                    total_chars += needed
                else:
                    # Truncate entry if space allows
                    avail = max_total_chars - (total_chars + 1)
                    prefix = f"[Drawer #{did}] "
                    if avail > len(prefix) + 8:
                        clamped = doc_text[:avail - len(prefix) - 3] + "..."
                        lines.append(f"{prefix}{clamped}")
                    break

            if len(lines) <= 1:
                return ""
            res = "\n".join(lines)
            if len(res) > max_total_chars:
                res = res[:max_total_chars - 3] + "..." if max_total_chars >= 3 else res[:max_total_chars]
            return res
        except Exception as e:
            logging.warning(f"Error preparing prospective memory context: {e}", exc_info=True)
            return ""

    @memory_state_operation
    def invalidate_prospective_anchors(self) -> int:
        """
        Explicitly invalidates all cached prospective goal anchors by advancing the corpus revision.
        Returns the new monotonic revision integer.
        """
        with self._cache_lock:
            self._increment_corpus_revision()
            return self.get_corpus_revision()

    @memory_state_operation
    def refresh_prospective_anchors(self, max_chars: Optional[int] = None) -> str:
        """
        Invalidates cached prospective goal anchors, forces re-querying against authoritative
        storage and dynamical graph relaxation, and delivers bounded, revision-tagged prospective
        memory context. Any prior prospective memory block with a lower revision is superseded.
        """
        with self._cache_lock:
            self._increment_corpus_revision()
            rev = self.get_corpus_revision()
            budget = max_chars if max_chars is not None else self._get_budget("prospective_max_chars", 2400)
            if budget <= 0:
                return ""

            supersession_note = f"[Notice: Supersedes all prospective memory contexts prior to Rev: {rev}]\n"
            if len(supersession_note) >= budget:
                return supersession_note[:budget]

            remaining = budget - len(supersession_note)
            context = self._get_prospective_memory_context(max_chars=remaining)
            if not context:
                empty_msg = f"[Notice: Supersedes all prospective memory contexts prior to Rev: {rev}]\n[Rev: {rev}] Prospective memory refreshed: no active aspirations or matching attractors found."
                if len(empty_msg) > budget:
                    empty_msg = empty_msg[:budget - 3] + "..." if budget >= 3 else empty_msg[:budget]
                return empty_msg

            out = f"{supersession_note}{context}"
            if len(out) > budget:
                out = out[:budget - 3] + "..." if budget >= 3 else out[:budget]
            return out

    @memory_state_operation
    def recall_records(self, wing: str = None, room: str = None, limit: int = 10, offset: int = 0) -> MemoryResult:
        """Structured L2 on-demand recall returning a typed MemoryResult containing MemoryHit objects."""
        col = self._get_collection(create=False)
        if not col:
            return MemoryResult(success=False, status="store_failure", error="No palace collection available.")

        current_rev = self.get_corpus_revision()
        cache_key = (current_rev, wing, room)

        # Stable catalog snapshotting for bounded per-call work across paginated offsets
        if hasattr(self, "_recall_catalog_cache") and self._recall_catalog_cache and self._recall_catalog_cache.get("key") == cache_key:
            sorted_hits = self._recall_catalog_cache["hits"]
        else:
            from mempalace.layers import build_where_filter
            where = build_where_filter(wing, room)

            batch_size = 250
            current_offset = 0
            all_ids = []
            all_docs = []
            all_metas = []

            total_col_items = None
            if hasattr(col, "count") and callable(col.count):
                try:
                    total_col_items = col.count()
                except Exception:
                    pass

            supports_offset = True
            while True:
                kwargs = {
                    "include": ["documents", "metadatas"],
                    "limit": batch_size,
                }
                if where:
                    kwargs["where"] = where
                if supports_offset and current_offset > 0:
                    kwargs["offset"] = current_offset

                try:
                    batch = col.get(**kwargs)
                except TypeError as te:
                    if "offset" in str(te):
                        supports_offset = False
                        kwargs.pop("offset", None)
                        kwargs.pop("limit", None)
                        batch = col.get(**kwargs)
                        all_ids = batch.get("ids", [])
                        all_docs = batch.get("documents", [])
                        all_metas = batch.get("metadatas", [])
                        break
                    else:
                        logging.error(f"MemPalaceManager.recall_records retrieval error: {te}")
                        return MemoryResult(success=False, status="error", error=f"Retrieval error: {te}")
                except Exception as e:
                    logging.error(f"MemPalaceManager.recall_records retrieval error: {e}")
                    return MemoryResult(success=False, status="error", error=f"Retrieval error: {e}")

                b_ids = batch.get("ids", [])
                if not b_ids:
                    break
                all_ids.extend(b_ids)
                all_docs.extend(batch.get("documents", []))
                all_metas.extend(batch.get("metadatas", []))

                if not supports_offset or len(b_ids) < batch_size:
                    break
                current_offset += len(b_ids)
                if total_col_items is not None and current_offset >= total_col_items:
                    break

            ids = all_ids
            docs = all_docs
            metas = all_metas

            if not ids:
                return MemoryResult(success=True, status="no_results", hits=[], total_count=0, offset=offset, limit=limit, has_more=False)

            chunk_re = re.compile(r"^(.*)_chunk_\d+$")
            logical_groups: Dict[str, MemoryHit] = {}

            for pid, doc, meta in zip(ids, docs, metas):
                meta = meta or {}
                doc = doc or ""
                lid = meta.get("parent_drawer_id") or (chunk_re.match(pid).group(1) if chunk_re.match(pid) else pid)

                clean_text = doc.strip().replace("\n", " ")
                is_trunc = len(clean_text) > 300 or bool(meta.get("parent_drawer_id"))
                preview = clean_text[:297] + "..." if len(clean_text) > 300 else clean_text
                full_ts = str(meta.get("authored_at") or meta.get("filed_at") or meta.get("timestamp") or meta.get("created_at") or "")

                if lid not in logical_groups:
                    logical_groups[lid] = MemoryHit(
                        logical_id=lid,
                        chunk_ids=[pid],
                        preview=preview,
                        is_truncated=is_trunc,
                        wing=meta.get("wing", "default"),
                        room=meta.get("room", "general"),
                        source_file=os.path.basename(meta.get("source_file", "")) if meta.get("source_file") else "",
                        authored_at=full_ts[:10],
                        metadata=meta
                    )
                else:
                    logical_groups[lid].chunk_ids.append(pid)
                    existing_ts = str((logical_groups[lid].metadata or {}).get("authored_at") or (logical_groups[lid].metadata or {}).get("filed_at") or (logical_groups[lid].metadata or {}).get("timestamp") or (logical_groups[lid].metadata or {}).get("created_at") or "")
                    if full_ts > existing_ts:
                        logical_groups[lid].metadata = meta
                        logical_groups[lid].authored_at = full_ts[:10]

            def _sort_key(h: MemoryHit):
                m = h.metadata or {}
                ts = str(m.get("authored_at") or m.get("filed_at") or m.get("timestamp") or m.get("created_at") or "")
                return (ts, h.logical_id)

            sorted_hits = sorted(logical_groups.values(), key=_sort_key, reverse=True)
            self._recall_catalog_cache = {"key": cache_key, "hits": sorted_hits}

        total_count = len(sorted_hits)
        hits = sorted_hits[offset:offset + limit]
        has_more = (offset + limit) < total_count

        # Touch drawers with CANONICAL LOGICAL IDs only
        if self._morpho_enabled and self.morpho_layer:
            self.touch_drawers([h.logical_id for h in hits])

        return MemoryResult(
            success=True,
            status="ok",
            hits=hits,
            offset=offset,
            limit=limit,
            total_count=total_count,
            has_more=has_more
        )

    def recall(self, wing: str = None, room: str = None, n_results: int = 10, offset: int = 0) -> str:
        """Single-pass L2 on-demand recall returning stable logical IDs and structured previews."""
        res = self.recall_records(wing=wing, room=room, limit=n_results, offset=offset)
        if not res.success:
            return res.error or "Retrieval error."
        if not res.hits:
            label = f"wing={wing}" if wing else ""
            if room:
                label += f" room={room}" if label else f"room={room}"
            return f"No drawers found for {label}." if label else "No drawers found."

        lines = [f"## L2 — ON-DEMAND ({len(res.hits)} drawers)"]
        for hit in res.hits:
            trunc_marker = " [truncated]" if hit.is_truncated else ""
            entry = f"  [Drawer #{hit.logical_id}] [{hit.room}] {hit.preview}{trunc_marker}"
            if hit.source_file:
                entry += f"  ({hit.source_file})"
            lines.append(entry)

        return "\n".join(lines)

    @memory_state_operation
    def search_records(self, query: str, wing: str = None, room: str = None, limit: int = 5, offset: int = 0) -> MemoryResult:
        """Structured L3 semantic search returning a typed MemoryResult containing MemoryHit objects."""
        col = self._get_collection(create=False)
        if not col:
            return MemoryResult(success=False, status="store_failure", error="No palace collection available.")

        if not query or not str(query).strip():
            return MemoryResult(success=False, status="error", error="Query string is required.")

        from mempalace.layers import build_where_filter
        where = build_where_filter(wing, room)

        total_items = col.count() if hasattr(col, "count") and callable(col.count) else None
        target_logical_needed = offset + limit + 1
        chunk_re = re.compile(r"^(.*)_chunk_\d+$")

        from mempalace.layers import _distance_to_similarity, _metric_for_collection
        try:
            metric = _metric_for_collection(col)
        except Exception:
            metric = "cosine"

        fetch_limit = max((offset + limit) * 3, 20)
        collection_exhausted = False
        logical_groups: Dict[str, MemoryHit] = {}

        while True:
            actual_n = min(total_items, fetch_limit) if total_items is not None else fetch_limit
            kwargs = {
                "query_texts": [query],
                "n_results": actual_n,
                "include": ["documents", "metadatas", "distances"]
            }
            if where:
                kwargs["where"] = where

            try:
                results = col.query(**kwargs)
            except Exception as e:
                logging.error(f"MemPalaceManager.search_records query error: {e}")
                return MemoryResult(success=False, status="error", error=f"Search error: {e}")

            ids_list = results.get("ids", [[]])
            docs_list = results.get("documents", [[]])
            metas_list = results.get("metadatas", [[]])
            dists_list = results.get("distances", [[]])

            raw_ids = ids_list[0] if ids_list else []
            raw_docs = docs_list[0] if docs_list else []
            raw_metas = metas_list[0] if metas_list else []
            raw_dists = dists_list[0] if dists_list else []

            if not raw_ids:
                return MemoryResult(success=True, status="no_results", hits=[], total_count=0, offset=offset, limit=limit, has_more=False)

            logical_groups.clear()
            for pid, doc, meta, dist in zip(raw_ids, raw_docs, raw_metas, raw_dists):
                meta = meta or {}
                doc = doc or ""
                lid = meta.get("parent_drawer_id") or (chunk_re.match(pid).group(1) if chunk_re.match(pid) else pid)

                try:
                    sim = round(_distance_to_similarity(dist, metric), 3)
                except Exception:
                    sim = round(max(0.0, min(1.0, 1.0 - float(dist))), 3) if dist is not None else 1.0

                clean_text = doc.strip().replace("\n", " ")
                is_trunc = len(clean_text) > 300 or bool(meta.get("parent_drawer_id"))
                preview = clean_text[:297] + "..." if len(clean_text) > 300 else clean_text
                full_ts = str(meta.get("authored_at") or meta.get("filed_at") or meta.get("timestamp") or meta.get("created_at") or "")

                if lid not in logical_groups:
                    logical_groups[lid] = MemoryHit(
                        logical_id=lid,
                        chunk_ids=[pid],
                        preview=preview,
                        is_truncated=is_trunc,
                        score=sim,
                        metric=metric,
                        wing=meta.get("wing", "default"),
                        room=meta.get("room", "general"),
                        source_file=os.path.basename(meta.get("source_file", "")) if meta.get("source_file") else "",
                        authored_at=full_ts[:10],
                        metadata=meta
                    )
                else:
                    existing = logical_groups[lid]
                    existing.chunk_ids.append(pid)
                    if sim > (existing.score or 0.0):
                        existing.score = sim

            if len(raw_ids) < actual_n or (total_items is not None and actual_n >= total_items):
                collection_exhausted = True
                break

            if len(logical_groups) >= target_logical_needed:
                collection_exhausted = False
                break

            if total_items is not None and fetch_limit >= total_items:
                collection_exhausted = True
                break

            if fetch_limit >= 2000:
                collection_exhausted = False
                break

            fetch_limit = fetch_limit * 2

        sorted_hits = sorted(logical_groups.values(), key=lambda h: (-(h.score or 0.0), h.logical_id))
        if collection_exhausted:
            total_count = len(sorted_hits)
            has_more = (offset + limit) < total_count
        else:
            total_count = None
            has_more = len(sorted_hits) > (offset + limit)

        hits = sorted_hits[offset:offset + limit]

        # Touch drawers with CANONICAL LOGICAL IDs only
        if self._morpho_enabled and self.morpho_layer:
            self.touch_drawers([h.logical_id for h in hits])

        return MemoryResult(
            success=True,
            status="ok",
            hits=hits,
            offset=offset,
            limit=limit,
            total_count=total_count,
            has_more=has_more
        )

    def search(self, query: str, wing: str = None, room: str = None, n_results: int = 5) -> str:
        """Single-pass L3 semantic search returning stable logical IDs and structured previews."""
        res = self.search_records(query, wing=wing, room=room, limit=n_results)
        if not res.success:
            return res.error or "Search error."
        if not res.hits:
            return "No results found."

        lines = [f'## L3 — SEARCH RESULTS for "{query}"']
        for i, hit in enumerate(res.hits, 1):
            sim_str = f"{hit.score:.3f}" if hit.score is not None else "N/A"
            trunc_marker = " [truncated]" if hit.is_truncated else ""
            lines.append(f"  [{i}] [Drawer #{hit.logical_id}] {hit.wing}/{hit.room} (sim={sim_str})")
            lines.append(f"      {hit.preview}{trunc_marker}")
            if hit.source_file:
                lines.append(f"      src: {hit.source_file}")
            if hit.authored_at:
                lines.append(f"      authored: {hit.authored_at}")

        return "\n".join(lines)

    @memory_state_operation
    def get_memory_record(self, drawer_id: str, offset: int = 0, limit: int = 2000) -> MemoryResult:
        """Structured logical-group hydration returning a typed MemoryResult with MemoryRecord."""
        col = self._get_collection(create=False)
        if not col:
            return MemoryResult(success=False, status="store_failure", error="MemPalace collection is not available.")

        if not drawer_id:
            return MemoryResult(success=False, status="error", error="Drawer ID is required.")

        try:
            offset = max(0, int(offset))
            limit = max(1, int(limit))
        except (ValueError, TypeError):
            offset, limit = 0, 2000

        canon_id = self.morpho_layer._canonical_id(drawer_id) if self.morpho_layer else drawer_id

        # 1. Try fetching directly by ID
        try:
            direct_res = col.get(ids=[canon_id], include=["documents", "metadatas"])
        except Exception as e:
            return MemoryResult(success=False, status="error", error=f"Storage error reading drawer #{canon_id}: {e}")

        direct_docs = direct_res.get("documents") or []
        direct_metas = direct_res.get("metadatas") or []

        full_content = ""
        metadata = {}
        chunk_ids = [canon_id]
        chunks_count = 1

        if direct_docs and direct_docs[0]:
            full_content = str(direct_docs[0])
            metadata = direct_metas[0] or {}
        else:
            # 2. Check for chunk group with parent_drawer_id == canon_id
            try:
                c_res = col.get(where={"parent_drawer_id": canon_id}, include=["documents", "metadatas"])
            except Exception as e:
                return MemoryResult(success=False, status="error", error=f"Storage error reading chunks for #{canon_id}: {e}")

            c_ids = c_res.get("ids") or []
            if not c_ids:
                return MemoryResult(success=False, status="missing_id", error=f"Drawer #{canon_id} not found.")

            c_docs = c_res.get("documents") or []
            c_metas = c_res.get("metadatas") or []
            chunks_list = []
            for cid, cdoc, cm in zip(c_ids, c_docs, c_metas):
                cm = cm or {}
                idx = cm.get("chunk_index", 0)
                chunks_list.append((idx, cid, str(cdoc or ""), cm))
            chunks_list.sort(key=lambda x: (x[0], x[1]))

            full_content = "".join(x[2] for x in chunks_list)
            metadata = chunks_list[0][3] if chunks_list else {}
            chunk_ids = [x[1] for x in chunks_list]
            chunks_count = len(chunks_list)

        total_chars = len(full_content)
        slice_end = min(offset + limit, total_chars)
        content_slice = full_content[offset:slice_end]
        has_more = slice_end < total_chars
        revision = hashlib.sha256(full_content.encode("utf-8")).hexdigest()[:16]

        record = MemoryRecord(
            drawer_id=canon_id,
            content=content_slice,
            wing=metadata.get("wing", "default"),
            room=metadata.get("room", "general"),
            metadata=metadata,
            total_chars=total_chars,
            chunks=chunks_count,
            chunk_ids=chunk_ids,
            revision=revision
        )

        return MemoryResult(
            success=True,
            status="ok",
            record=record,
            offset=offset,
            limit=limit,
            total_count=total_chars,
            has_more=has_more
        )

    def get_memory(self, drawer_id: str, offset: int = 0, limit: int = 2000) -> str:
        """
        M1 task 3: MemPalace_get_memory(drawer_id, offset, limit) with paginated logical-group hydration.
        Returns total length, revision, slice content, and continuation state.
        """
        res = self.get_memory_record(drawer_id=drawer_id, offset=offset, limit=limit)
        if not res.success:
            return f"Error: {res.error}"

        record = res.record
        slice_end = min(res.offset + res.limit, record.total_chars)

        header = (
            f"[Drawer #{record.drawer_id}] Wing: {record.wing} | Room: {record.room} | "
            f"Total Chars: {record.total_chars} | Showing: {res.offset} to {slice_end} | "
            f"Has More: {res.has_more} | Revision: {record.revision}"
        )
        body = f"--- Content ---\n{record.content}"
        if res.has_more:
            continuation = f"\n[... truncated. Call MemPalace_get_memory with drawer_id='{record.drawer_id}' and offset={slice_end} to continue reading.]"
        else:
            continuation = ""

        return f"{header}\n{body}{continuation}"

    def get_entity_context(self, entities: list) -> str:
        if not entities:
            return ""
        context = []
        from concurrent.futures import ThreadPoolExecutor

        def fetch(entity):
            res = self.search(entity, wing="sanctuary", room="people", n_results=3)
            if res and "No results" not in res and "No palace" not in res:
                return f"Context for {entity}:\n{res}"
            return None

        with ThreadPoolExecutor(max_workers=min(5, len(entities))) as executor:
            results = executor.map(fetch, entities)

        for res in results:
            if res:
                context.append(res)
        return "\n\n".join(context)

    def get_topic_context(self, topics: list) -> str:
        if not topics:
            return ""
        context = []
        from concurrent.futures import ThreadPoolExecutor

        def fetch(topic):
            res = self.search(topic, n_results=2)
            if res and "No results" not in res and "No palace" not in res:
                return f"Context for topic '{topic}':\n{res}"
            return None

        with ThreadPoolExecutor(max_workers=min(5, len(topics))) as executor:
            results = executor.map(fetch, topics)

        for res in results:
            if res:
                context.append(res)
        return "\n\n".join(context)

    @memory_state_operation
    def _load_mirrors(self) -> dict:
        """Loads and returns deepcopy of mirrors state; treats corrupt JSON distinctly without overwriting."""
        with self._cache_lock:
            if getattr(self, '_mirrors_cache', None) is not None:
                return copy.deepcopy(self._mirrors_cache)

            path = os.path.join(self.palace_path, "mirrors.json")
            if os.path.exists(path):
                try:
                    with open(path, 'r', encoding="utf-8") as f:
                        mirrors = json.load(f)
                except json.JSONDecodeError as jde:
                    logging.critical(f"MemPalaceManager: mirrors.json is corrupt: {jde}")
                    corrupt_backup = f"{path}.corrupt.{int(time.time())}"
                    try:
                        import shutil
                        shutil.copy2(path, corrupt_backup)
                        logging.warning(f"Preserved corrupt mirrors.json to {corrupt_backup}")
                    except Exception:
                        pass
                    raise RuntimeError(f"Corrupt mirrors state file at {path}; preserved as {corrupt_backup}") from jde
                except Exception as e:
                    logging.error(f"Error reading mirrors: {e}")
                    raise

                modified = False
                for perspective, data in list(mirrors.items()):
                    if not isinstance(data, dict):
                        mirrors[perspective] = {"current_view": str(data), "history": [], "provenance": "unknown"}
                        modified = True
                        continue

                    if "current_view" not in data:
                        data["current_view"] = ""
                        modified = True
                    if "history" not in data:
                        data["history"] = []
                        modified = True
                    if "provenance" not in data:
                        data["provenance"] = "unknown"
                        modified = True

                if modified:
                    self._save_mirrors(mirrors)
                else:
                    self._mirrors_cache = copy.deepcopy(mirrors)

                return copy.deepcopy(mirrors)
            return {}

    @memory_state_operation
    def _save_mirrors(self, mirrors: dict):
        """Atomically persists mirrors state BEFORE updating in-memory cache, propagating typed errors."""
        with self._cache_lock:
            path = os.path.join(self.palace_path, "mirrors.json")
            to_save = copy.deepcopy(mirrors)
            atomic_json_write(path, to_save)
            self._mirrors_cache = to_save

    @memory_state_operation
    def update_mirror(self, perspective: str, subjective_view: str, provenance: str = "inferred",
                      confidence: float = 1.0, evidence_refs: list = None, contradicted_by: list = None) -> dict:
        """Surgically updates a mirror record and persists atomically, returning revision metadata."""
        if not perspective or not subjective_view:
            raise ValueError("perspective and subjective_view are required")

        with self._cache_lock:
            # Bound confidence to [0.0, 1.0]
            if confidence is not None:
                try:
                    conf = max(0.0, min(1.0, float(confidence)))
                except (ValueError, TypeError):
                    conf = 1.0
            else:
                conf = 1.0

            mirrors = self._load_mirrors()
            now_str = datetime.datetime.now().isoformat()

            if perspective not in mirrors:
                mirrors[perspective] = {
                    "current_view": subjective_view,
                    "history": []
                }
            else:
                old_view = mirrors[perspective].get("current_view", "")
                old_entry = {
                    "view": old_view,
                    "date": mirrors[perspective].get("last_updated") or now_str,
                    "provenance": mirrors[perspective].get("provenance", "unknown"),
                    "confidence": mirrors[perspective].get("confidence", 1.0),
                    "evidence_refs": list(mirrors[perspective].get("evidence_refs", [])),
                    "contradicted_by": list(mirrors[perspective].get("contradicted_by", []))
                }
                mirrors[perspective].setdefault("history", []).append(old_entry)
                mirrors[perspective]["current_view"] = subjective_view

            mirrors[perspective]["provenance"] = provenance
            mirrors[perspective]["confidence"] = conf
            mirrors[perspective]["evidence_refs"] = list(evidence_refs) if evidence_refs else []
            mirrors[perspective]["contradicted_by"] = list(contradicted_by) if contradicted_by else []
            mirrors[perspective]["last_updated"] = now_str

            self._save_mirrors(mirrors)
            self._increment_corpus_revision()
            return {"success": True, "perspective": perspective, "revision": now_str}

    @memory_state_operation
    def get_self_perception(self, limit: int = 24) -> str:
        """Retrieves core self-perception entries to inject during wake up or bearings."""
        mirrors = self._load_mirrors()
        if not mirrors:
            return ""

        sorted_mirrors = sorted(
            mirrors.items(),
            key=lambda item: (
                item[1].get("last_updated") or
                (item[1].get("history", [])[-1].get("date") if isinstance(item[1].get("history"), list) and item[1].get("history") and isinstance(item[1]["history"][-1], dict) else "") or
                ""
            ) if isinstance(item[1], dict) else "",
            reverse=True
        )

        active_mirrors = sorted_mirrors
        if limit is not None and limit > 0:
            active_mirrors = sorted_mirrors[:limit]

        lines = ["\n--- Core Self Perception & Theory of Mind ---"]
        tensions = []

        for perspective, data in active_mirrors:
            if isinstance(data, dict):
                lines.append(f"Perspective: {perspective}")
                lines.append(f"Subjective View: {data.get('current_view', '')}")
                prov_str = f"Provenance: {data.get('provenance', 'unknown')}"
                conf = data.get("confidence")
                if conf is not None and conf != 1.0:
                    prov_str += f" | Confidence: {conf:.2f}"
                lines.append(f"{prov_str}\n")

                contradictions = data.get("contradicted_by", [])
                if contradictions:
                    for target in contradictions:
                        target_view = mirrors.get(target, {}).get("current_view", "(no record)")
                        tensions.append(
                            f"- Dissonance between '{perspective}' and '{target}':\n"
                            f"  * {perspective}'s view: \"{data.get('current_view', '')}\"\n"
                            f"  * {target}'s view: \"{target_view}\""
                        )
            else:
                lines.append(f"Perspective: {perspective}")
                lines.append(f"Subjective View: {str(data)}")
                lines.append("Provenance: unknown\n")

        if tensions:
            lines.append("--- Open Epistemic Tensions & Dissonance ---")
            lines.append("The following unresolved dialectics exist between perspectives. Sit with these tensions:")
            lines.extend(tensions)
            lines.append("")

        return "\n".join(lines)

    @memory_state_operation
    def initialize_short_term_memory(self):
        """Seed a proactive short-term memory if the file doesn't exist."""
        path = os.path.join(self.palace_path, "short_term_mem.json")
        if not os.path.exists(path):
            seed_memory = [{
                "id": str(uuid.uuid4())[:8],
                "date": datetime.datetime.now().isoformat(),
                "content": "System initialized. My immediate goal is to understand who the user is, what their goals are, and how I play a role. I should populate my trajectory data with tasks and aspirations that move us towards our shared goals."
            }]
            try:
                temp_path = f"{path}.tmp.{uuid.uuid4().hex}"
                with open(temp_path, 'w', encoding="utf-8") as f:
                    json.dump(seed_memory, f, indent=2)
                os.replace(temp_path, path)
            except Exception as e:
                logging.error(f"Error seeding short term memories: {e}")

    @memory_state_operation
    def _load_short_term_memories(self) -> list:
        """Loads and returns deepcopy of short-term memories; treats corrupt JSON distinctly."""
        with self._cache_lock:
            if getattr(self, '_short_term_cache', None) is not None:
                return copy.deepcopy(self._short_term_cache)

            path = os.path.join(self.palace_path, "short_term_mem.json")
            if os.path.exists(path):
                try:
                    with open(path, 'r', encoding="utf-8") as f:
                        memories = json.load(f)
                except json.JSONDecodeError as jde:
                    logging.critical(f"MemPalaceManager: short_term_mem.json is corrupt: {jde}")
                    corrupt_backup = f"{path}.corrupt.{int(time.time())}"
                    try:
                        import shutil
                        shutil.copy2(path, corrupt_backup)
                        logging.warning(f"Preserved corrupt short_term_mem.json to {corrupt_backup}")
                    except Exception:
                        pass
                    raise RuntimeError(f"Corrupt short-term memory file at {path}; preserved as {corrupt_backup}") from jde
                except Exception as e:
                    logging.error(f"Error reading short term memories: {e}")
                    raise

                modified = False
                for m in memories:
                    if "id" not in m:
                        m["id"] = str(uuid.uuid4())[:8]
                        modified = True
                    if "date" not in m:
                        m["date"] = datetime.datetime.now().isoformat()
                        modified = True
                    if "content" not in m:
                        m["content"] = ""
                        modified = True

                if modified:
                    self._save_short_term_memories(memories)
                else:
                    self._short_term_cache = copy.deepcopy(memories)

                return copy.deepcopy(memories)
            return []

    @memory_state_operation
    def _save_short_term_memories(self, memories: list):
        """Atomically persists STM state BEFORE updating in-memory cache, propagating typed errors."""
        with self._cache_lock:
            path = os.path.join(self.palace_path, "short_term_mem.json")
            to_save = copy.deepcopy(memories)
            atomic_json_write(path, to_save)
            self._short_term_cache = to_save

    @memory_state_operation
    def add_short_term_memory(self, content: str, supersedes: list = None, priority: str = "normal", status: str = "active", expiry: Optional[str] = None) -> dict:
        """Appends a new short-term memory with metadata and prunes prioritizing active/unresolved records."""
        if not content:
            raise ValueError("content is required")
        with self._cache_lock:
            memories = self._load_short_term_memories()

            if supersedes:
                memories = [m for m in memories if m.get("id") not in supersedes]

            mem_id = str(uuid.uuid4())[:8]
            now_iso = datetime.datetime.now().isoformat()
            new_memory = {
                "id": mem_id,
                "date": now_iso,
                "content": content,
                "priority": priority,
                "status": status,
            }
            if expiry:
                new_memory["expiry"] = expiry

            memories.append(new_memory)

            settings = SettingsManager(agent_id=self.agent_id)
            max_memories = settings.get("core.agent.max-short-term-memories", 80)
            try:
                max_memories = max(1, int(max_memories))
            except (ValueError, TypeError):
                max_memories = 80

            # R2: Timezone-aware expiry check
            now_dt = datetime.datetime.now(datetime.timezone.utc)
            def is_expired(m):
                exp = m.get("expiry")
                if not exp:
                    return False
                try:
                    exp_dt = datetime.datetime.fromisoformat(str(exp).replace("Z", "+00:00"))
                    if exp_dt.tzinfo is None:
                        exp_dt = exp_dt.replace(tzinfo=datetime.timezone.utc)
                    return exp_dt < now_dt
                except Exception:
                    return str(exp) < now_iso

            # Identify candidates for eviction: NEVER evict unresolved commitments!
            remaining_memories = list(memories)
            evicted_items = []
            if len(remaining_memories) > max_memories:
                prio_map = {"low": 1, "normal": 2, "high": 3, "critical": 4}
                def evict_score(m):
                    if is_expired(m):
                        s = 0
                    elif m.get("status") in ("completed", "resolved", "cancelled", "done"):
                        s = 1
                    else:
                        s = 2
                    p_val = prio_map.get(str(m.get("priority", "normal")).lower(), 2)
                    return (s, p_val)

                while len(remaining_memories) > max_memories:
                    evictable_indices = [
                        i for i, m in enumerate(remaining_memories)
                        if m.get("status") != "unresolved"
                    ]
                    if not evictable_indices:
                        # ALL remaining items are unresolved commitments: retain in durable storage!
                        break
                    best_evict_idx = min(evictable_indices, key=lambda idx: evict_score(remaining_memories[idx]))
                    evicted = remaining_memories.pop(best_evict_idx)
                    evicted_items.append(evicted)

            # R2 & Verification 3: PERSIST ARCHIVE FIRST BEFORE REMOVING FROM ACTIVE STORAGE!
            archive_path = os.path.join(self.palace_path, "short_term_archive.json")
            archived_records = []
            if os.path.exists(archive_path):
                try:
                    with open(archive_path, "r", encoding="utf-8") as af:
                        archived_records = json.load(af)
                    if not isinstance(archived_records, list):
                        raise ValueError(f"Expected list, got {type(archived_records).__name__}")
                except Exception as read_err:
                    corrupt_backup = f"{archive_path}.corrupt.{int(time.time())}"
                    try:
                        import shutil
                        shutil.copy2(archive_path, corrupt_backup)
                        logging.warning(f"Preserved corrupt short_term_archive.json to {corrupt_backup}")
                    except Exception:
                        pass
                    logging.error(f"Cannot read existing short_term_archive.json: {read_err}")
                    raise RuntimeError(f"short_term_archive.json is corrupted: {read_err}. Preserved as {corrupt_backup}. Preserving active records and aborting eviction.") from read_err

            if evicted_items:
                existing_archive_ids = {r.get("id") for r in archived_records if isinstance(r, dict)}
                for item in evicted_items:
                    if item.get("id") not in existing_archive_ids:
                        item_copy = dict(item)
                        item_copy["archived_at"] = now_iso
                        archived_records.append(item_copy)
                        existing_archive_ids.add(item.get("id"))
                # Write archive to disk BEFORE updating active memories!
                atomic_json_write(archive_path, archived_records)
            elif not os.path.exists(archive_path):
                atomic_json_write(archive_path, [])

            # Active storage is updated ONLY after archive persistence has succeeded!
            self._save_short_term_memories(remaining_memories)
            self._increment_corpus_revision()

            resp = {"success": True, "id": mem_id, "date": now_iso}
            if len(remaining_memories) > max_memories:
                resp["overflow"] = True
                resp["unresolved_count"] = len([m for m in remaining_memories if m.get("status") == "unresolved"])
            if evicted_items:
                resp["archived_count"] = len(evicted_items)
            return resp

    @memory_state_operation
    def get_archived_short_term_memories(self, memory_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Inspect archived short-term working records by ID or return full archive list."""
        with self._cache_lock:
            archive_path = os.path.join(self.palace_path, "short_term_archive.json")
            if not os.path.exists(archive_path):
                return []
            try:
                with open(archive_path, "r", encoding="utf-8") as af:
                    data = json.load(af)
                if not isinstance(data, list):
                    raise ValueError(f"Expected list, got {type(data).__name__}")
                if memory_id:
                    return [m for m in data if m.get("id") == memory_id]
                return data
            except Exception as e:
                logging.error(f"Error reading short_term_archive.json: {e}")
                corrupt_backup = f"{archive_path}.corrupt.{int(time.time())}"
                try:
                    import shutil
                    shutil.copy2(archive_path, corrupt_backup)
                    logging.warning(f"Preserved corrupt short_term_archive.json to {corrupt_backup}")
                except Exception:
                    pass
                raise RuntimeError(f"Corrupt short_term_archive.json at {archive_path}; preserved as {corrupt_backup}") from e

    @memory_state_operation
    def remove_short_term_memory(self, memory_id: str) -> dict:
        """Removes a short-term memory by its ID, returning success/status."""
        with self._cache_lock:
            memories = self._load_short_term_memories()
            filtered = [m for m in memories if m.get("id") != memory_id]
            if len(filtered) != len(memories):
                self._save_short_term_memories(filtered)
                self._increment_corpus_revision()
                return {"success": True, "id": memory_id}
            return {"success": False, "error": f"Memory ID {memory_id} not found", "id": memory_id}

    @memory_state_operation
    def get_short_term_context(self, max_chars: Optional[int] = None) -> str:
        """Reads and formats bounded short-term memory context prioritizing corrections and unresolved commitments."""
        if max_chars is None:
            max_chars = self._get_budget("short_term_context_max_chars", 3200)
        memories = self._load_short_term_memories()
        if not memories:
            return ""

        # Prioritize: unresolved commitments & critical corrections first, then high priority, then newest
        prio_map = {"low": 1, "normal": 2, "high": 3, "critical": 4}
        def inclusion_rank(m):
            st = m.get("status") or "active"
            content = m.get("content", "")
            is_unresolved = 1 if st == "unresolved" else 0
            is_correction = 1 if "CRITICAL_CORRECTION" in content else 0
            p_val = prio_map.get(str(m.get("priority", "normal")).lower(), 2)
            date_val = str(m.get("date", ""))
            return (is_unresolved or is_correction, p_val, date_val)

        # Select items by highest inclusion rank
        sorted_candidates = sorted(memories, key=inclusion_rank, reverse=True)
        selected = []
        total_chars = 0
        for m in sorted_candidates:
            status_tag = f" [{m.get('status')}]" if m.get('status') and m.get('status') != 'active' else ""
            header = f"[ID: {m.get('id', 'N/A')}]{status_tag} [Date: {m.get('date', 'N/A')}]\n"
            content = m.get('content', '')
            full_entry = f"{header}{content}"
            sep_len = 2 if selected else 0
            if total_chars + len(full_entry) + sep_len <= max_chars:
                selected.append(m)
                total_chars += len(full_entry) + sep_len
            elif not selected:
                # Handle oversized single first entry
                trunc_suffix = "\n... [truncated]"
                avail_content = max(0, max_chars - len(header) - len(trunc_suffix))
                truncated_entry = f"{header}{content[:avail_content]}{trunc_suffix}"
                return truncated_entry

        # Format selected items chronologically
        selected.sort(key=lambda m: str(m.get("date", "")))
        lines = []
        for m in selected:
            status_tag = f" [{m.get('status')}]" if m.get('status') and m.get('status') != 'active' else ""
            header = f"[ID: {m.get('id', 'N/A')}]{status_tag} [Date: {m.get('date', 'N/A')}]\n"
            lines.append(f"{header}{m.get('content', '')}")

        res = "\n\n".join(lines)
        return res[:max_chars]

    @memory_state_operation
    def add_memory(self, content: str, wing: str = "default", room: str = "general", source_file: str = "agent_thoughts"):
        """Add a new memory to the MemPalace directly in-process; registers canonical logical ID in morpho_layer."""
        with _mempalace_lock:
            try:
                col = self._get_collection(create=True)
                if not col:
                    return {"success": False, "error": "No palace collection available."}

                from mempalace.mcp_server import sanitize_name, sanitize_content, make_drawer_id_from_content
                wing = sanitize_name(wing, "wing")
                room = sanitize_name(room, "room")
                content = sanitize_content(content)
                drawer_id = make_drawer_id_from_content(wing, room, content)

                chunk_size = 1000
                now_iso = datetime.datetime.now().isoformat()
                base_meta = {
                    "wing": wing,
                    "room": room,
                    "source_file": source_file or "",
                    "added_by": "the_agent",
                    "filed_at": now_iso,
                    "id_recipe": 2
                }

                if len(content) <= chunk_size:
                    col.upsert(
                        ids=[drawer_id],
                        documents=[content],
                        metadatas=[{**base_meta, "chunk_index": 0}]
                    )
                    res = {"success": True, "drawer_id": drawer_id, "wing": wing, "room": room, "chunks": 1}
                else:
                    chunk_ids = []
                    chunk_docs = []
                    chunk_metas = []
                    for i in range(0, len(content), chunk_size):
                        idx = i // chunk_size
                        chunk_ids.append(f"{drawer_id}_chunk_{idx:06d}")
                        chunk_docs.append(content[i : i + chunk_size])
                        chunk_metas.append({**base_meta, "chunk_index": idx, "parent_drawer_id": drawer_id})
                    col.upsert(ids=chunk_ids, documents=chunk_docs, metadatas=chunk_metas)
                    res = {"success": True, "drawer_id": drawer_id, "wing": wing, "room": room, "chunks": len(chunk_ids), "chunk_ids": chunk_ids}

                if self._morpho_enabled and self.morpho_layer:
                    try:
                        self.morpho_layer.register_drawer(drawer_id, wing=wing, room=room)
                    except Exception as me:
                        logging.warning(f"Morphological layer failed to register drawer: {me}")
                self.touch_drawers([drawer_id])
                self._increment_corpus_revision()
                return res
            except Exception as e:
                logging.error(f"MemPalaceManager.add_memory error: {e}", exc_info=True)
                return f"Failed to add memory: {e}"

    @memory_state_operation
    def delete_memory(self, drawer_id: str):
        """Delete a memory from MemPalace; removes from graph only after authoritative storage success."""
        with _mempalace_lock:
            try:
                col = self._get_collection(create=False)
                if not col:
                    return {"success": False, "error": "No palace collection available."}

                canon_id = self.morpho_layer._canonical_id(drawer_id) if self.morpho_layer else drawer_id
                ids_to_delete = []

                try:
                    direct = col.get(ids=[canon_id], include=[])
                    if direct and direct.get("ids"):
                        ids_to_delete.extend(direct["ids"])
                except Exception:
                    pass

                try:
                    chunks = col.get(where={"parent_drawer_id": canon_id}, include=[])
                    if chunks and chunks.get("ids"):
                        ids_to_delete.extend(chunks["ids"])
                except Exception:
                    pass

                ids_to_delete = list(set(ids_to_delete))
                if not ids_to_delete:
                    return {"success": False, "error": f"Drawer not found: {drawer_id}"}

                col.delete(ids=ids_to_delete)

                if self._morpho_enabled and self.morpho_layer:
                    try:
                        self.morpho_layer.remove_drawer(drawer_id=canon_id)
                    except Exception as me:
                        logging.warning(f"Morphological layer failed to remove drawer: {me}")
                self._increment_corpus_revision()
                return {
                    "success": True,
                    "drawer_id": canon_id,
                    "deleted_ids": ids_to_delete,
                    "chunks_deleted": len(ids_to_delete)
                }
            except Exception as e:
                logging.error(f"MemPalaceManager.delete_memory error: {e}", exc_info=True)
                return f"Failed to delete memory: {e}"

    @memory_state_operation
    def initialize_sanctuary(self, sanctuary_file: str = None):
        """Inject sanctuary_init.json data into the MemPalace if it hasn't been done yet"""
        if not sanctuary_file:
            sanctuary_file = os.path.abspath(os.path.join(
                os.path.dirname(__file__), "..", "memory", "sanctuary_init.json"))

        if not os.path.exists(sanctuary_file):
            return

        initialized_flag = os.path.join(
            self.palace_path, ".sanctuary_initialized")
        if os.path.exists(initialized_flag):
            return

        try:
            with open(sanctuary_file, 'r', encoding="utf-8") as f:
                data = json.load(f)

            # Process social records
            social = data.get("social_records", {})
            for person in social.get("people", []):
                content = f"Person: {person['name']}\nEntity Type: {person['entity_type']}\nPronouns: {person.get('pronouns', '')}\nRelation: {person['relation']}\nSubjective View: {person['subjective_view']}"
                self.add_memory(content=content, wing="sanctuary",
                                room="people", source_file="sanctuary_init.json")

            for mirror in social.get("mirrors", []):
                self.update_mirror(
                    mirror['perspective'], mirror['subjective_view'], provenance="stated")

            for community in social.get("communities", []):
                content = f"Community: {community['name']}\nEntity Type: {community['entity_type']}\nSubjective View: {community['subjective_view']}"
                self.add_memory(content=content, wing="sanctuary",
                                room="communities", source_file="sanctuary_init.json")

            # Process character defining memories
            for memory in data.get("character_defining_memories", []):
                self.add_memory(content=memory, wing="sanctuary",
                                room="character_memories", source_file="sanctuary_init.json")

            temp_path = f"{initialized_flag}.tmp.{uuid.uuid4().hex}"
            with open(temp_path, 'w', encoding="utf-8") as f:
                f.write("Initialized")
            os.replace(temp_path, initialized_flag)
            logging.info("Sanctuary initialized successfully.")
        except Exception as e:
            logging.error(f"Error initializing sanctuary: {e}", exc_info=True)

    _init_sanctuary = initialize_sanctuary

    def _read_identity_approvals(self):
        path = os.path.join(self.palace_path, "identity_approvals.json")
        if not os.path.exists(path):
            return []
        try:
            with open(path, encoding="utf-8") as f:
                records = json.load(f)
            if not isinstance(records, list) or any(not isinstance(r, dict) for r in records):
                raise ValueError("Expected approval records list")
            refs = [r.get("approval_ref") for r in records]
            if len(refs) != len(set(refs)):
                raise ValueError("Duplicate approval references")
            return records
        except (ValueError, TypeError) as exc:
            import shutil
            quarantine = f"{path}.corrupt.{uuid.uuid4().hex}"
            shutil.copy2(path, quarantine)
            raise RuntimeError(f"Invalid approvals ledger preserved to {quarantine}") from exc

    @memory_state_operation
    def register_identity_approval(self, target_section, delta_type, content,
                                   user_id="user", approval_ref=None,
                                   trusted_source=False, auth_token=None,
                                   signed_approval=None):
        """Import a human-signed approval. Caller booleans/tokens have no authority."""
        from core.identity_authorisation import verify_approval
        if not isinstance(signed_approval, dict):
            raise PermissionError("An administrator-signed approval is required.")
        record = dict(signed_approval)
        verify_approval(record, self.agent_id)
        expected = {"target_section": target_section, "delta_type": delta_type,
                    "content": content, "user_id": user_id}
        if any(record.get(k) != v for k, v in expected.items()):
            raise PermissionError("Signed approval does not match requested mutation.")
        ref = record["approval_ref"]
        if approval_ref is not None and approval_ref != ref:
            raise PermissionError("Signed reference mismatch")
        if target_section not in self._identity_sections or delta_type not in ("add", "modify", "update", "retire", "remove"):
            raise ValueError("Invalid identity mutation")
        with _palace_cross_instance_lock(self.palace_path), self._cache_lock:
            records = self._read_identity_approvals()
            if any(r.get("approval_ref") == ref for r in records):
                raise PermissionError("Approval reference already registered")
            record.update(status="approved", used=False,
                          created_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
            records.append(record)
            atomic_json_write(os.path.join(self.palace_path, "identity_approvals.json"), records)
        return ref

    _identity_sections = {
        "core_values": "core.agent.core-values",
        "overarching_goals": "core.agent.overarching-goals",
        "anti_patterns": "core.agent.anti-patterns",
    }

    def _fresh_identity_settings(self):
        # This must run INSIDE the palace process lock; constructor snapshots
        # taken before the lock can lose distinct concurrently approved writes.
        settings = SettingsManager(agent_id=self.agent_id)
        with settings._lock:
            with open(settings.settings_file, encoding="utf-8") as file:
                durable = json.load(file)
            if not isinstance(durable, dict) or not isinstance(durable.get("core"), dict):
                raise RuntimeError("Invalid durable identity settings; refusing defaults")
            settings.settings = settings.deep_merge(settings.load_settings(), durable)
        return settings

    def _identity_evolution_mode(self):
        settings = self._fresh_identity_settings()
        mode = settings.get("core.identity_evolution.mode", "autonomous")
        if mode not in ("autonomous", "signed_approval", "disabled"):
            raise ValueError("Invalid identity evolution mode")
        return mode

    def _verify_identity_operation(self, record):
        if record.get("authority") == "autonomous":
            if self._identity_evolution_mode() != "autonomous":
                raise PermissionError("Autonomous evolution is not enabled in this deployment")
            if record.get("agent_id") != self.agent_id or not str(record.get("rationale", "")).strip():
                raise PermissionError("Autonomous operation requires agent binding and rationale")
        else:
            from core.identity_authorisation import verify_approval
            verify_approval(record, self.agent_id)
            if self._identity_evolution_mode() == "disabled":
                raise PermissionError("Identity evolution is disabled")

    def _commit_identity_journal(self, entry, records):
        from core.identity_authorisation import verify_approval
        ref = entry.get("approval_ref")
        matches = [r for r in records if r.get("approval_ref") == ref]
        if len(matches) != 1:
            raise PermissionError("Recovery has no unique approved transaction")
        approved = matches[0]
        self._verify_identity_operation(approved)
        if approved.get("status") != "approved" or any(
                entry.get(k) != approved.get(k) for k in ("target_section", "delta_type", "content")):
            raise PermissionError("Journal does not match the signed approval")
        key = self._identity_sections.get(entry.get("target_section"))
        before, after = entry.get("before_items"), entry.get("after_items")
        if not key or not isinstance(before, list) or not isinstance(after, list):
            raise PermissionError("Invalid identity transaction journal")
        # Recompute the intended result; an edited after-image cannot bypass
        # the exact signed delta, even if the approval itself is genuine.
        expected = self._identity_mutated_items(before, approved["delta_type"], approved["content"])
        if expected != after:
            raise PermissionError("Journal after-image is not the approved mutation")
        settings = self._fresh_identity_settings()
        current = list(settings.get(key, []) or [])
        if current not in (before, after):
            raise RuntimeError("Identity settings diverged from pending transaction; journal retained")
        transaction_digest = hashlib.sha256(json.dumps(entry, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        if approved.get("completed") and approved.get("transaction_digest") == transaction_digest and current == after:
            os.remove(os.path.join(self.palace_path, "identity_delta_journal.json"))
            return
        if approved.get("used") and (approved.get("completed") or approved.get("transaction_digest") != transaction_digest):
            raise PermissionError("Consumed approval has no matching pending journal")
        if not approved.get("used"):
            approved.update(used=True, transaction_digest=transaction_digest,
                            used_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
            atomic_json_write(os.path.join(self.palace_path, "identity_approvals.json"), records)
        if current != after:
            settings.set(key, after)
            settings.save()  # raises on failure; journal is deliberately retained
        history_path = os.path.join(self.palace_path, "identity_evolution.json")
        history = []
        if os.path.exists(history_path):
            with open(history_path, encoding="utf-8") as f:
                history = json.load(f)
            if not isinstance(history, list):
                raise RuntimeError("Invalid identity history; journal retained")
        if not any(h.get("approval_ref") == ref for h in history):
            history.append({k: entry.get(k) for k in
                            ("timestamp", "target_section", "delta_type", "content", "rationale", "approval_ref")}
                           | {"user_id": approved["user_id"],
                              "authority": approved.get("authority", "signed_approval"),
                              "before_items": before, "after_items": after})
            atomic_json_write(history_path, history)
        self._sync_identity()
        if hasattr(self, "stack") and hasattr(self.stack, "l0"):
            self.stack.l0._text = None
        self._increment_corpus_revision()
        approved["completed"] = True
        atomic_json_write(os.path.join(self.palace_path, "identity_approvals.json"), records)
        os.remove(os.path.join(self.palace_path, "identity_delta_journal.json"))

    @staticmethod
    def _identity_mutated_items(items, delta_type, content):
        values = list(items)
        text = content.strip()
        if not text:
            raise ValueError("Identity content cannot be empty")
        if delta_type == "add":
            if text not in values:
                values.append(text)
        elif delta_type in ("modify", "update"):
            if " -> " not in text:
                raise ValueError("Modify requires 'exact_target -> replacement_value' syntax")
            old, new = [v.strip() for v in text.split(" -> ", 1)]
            if not old or not new or old not in values:
                raise ValueError("Exact modification target missing or invalid replacement")
            if new in values and new != old:
                raise ValueError("Replacement would duplicate an existing value")
            values[values.index(old)] = new
        elif delta_type in ("retire", "remove"):
            if text in values:
                values.remove(text)
        else:
            raise ValueError("Invalid identity delta type")
        return values

    @memory_state_operation
    def _recover_identity_delta_journal(self):
        path = os.path.join(self.palace_path, "identity_delta_journal.json")
        with _palace_cross_instance_lock(self.palace_path), self._cache_lock:
            if not os.path.exists(path):
                return
            with open(path, encoding="utf-8") as f:
                entry = json.load(f)
            if not isinstance(entry, dict):
                raise PermissionError("Invalid identity journal; preserved")
            self._commit_identity_journal(entry, self._read_identity_approvals())

    @memory_state_operation
    def apply_identity_delta(self, target_section, delta_type, content, rationale="",
                             approval_ref=None, operation_id=None):
        """Autonomous by default; optional signed governance shares durable recovery.

        Identity edits touch only the three identity lists, never tool/security
        permissions. Rationale is the agent's justification, not claimed consent.
        Use operation_id for idempotent retries; history contains reversal data.
        """
        try:
            with _palace_cross_instance_lock(self.palace_path), self._cache_lock:
                mode = self._identity_evolution_mode()
                if mode == "disabled":
                    return "Error: Identity evolution is disabled in this deployment."
                journal_path = os.path.join(self.palace_path, "identity_delta_journal.json")
                if os.path.exists(journal_path):
                    with open(journal_path, encoding="utf-8") as f:
                        pending = json.load(f)
                    if not isinstance(pending, dict):
                        raise PermissionError("Invalid identity journal; preserved")
                    self._commit_identity_journal(pending, self._read_identity_approvals())
                key = self._identity_sections.get(target_section)
                if not key:
                    return "Error: Invalid target_section; identity changes cannot alter permissions."
                records = self._read_identity_approvals()
                if mode == "signed_approval" or approval_ref:
                    if not approval_ref:
                        return "Error: A valid user approval reference (approval_ref) is required in signed_approval mode."
                    ref = approval_ref
                    matching = next((record for record in records if record.get("approval_ref") == ref), None)
                    if matching is None:
                        return "Error: Approval reference not found in approved records."
                    self._verify_identity_operation(matching)
                    if matching.get("used"):
                        return "Error: Approval reference has already been used and cannot be replayed."
                    if matching.get("status") != "approved" or any(
                            matching.get(k) != v for k, v in {
                                "target_section": target_section, "delta_type": delta_type, "content": content}.items()):
                        return "Error: Approval is not bound to this exact mutation."
                else:
                    if not isinstance(rationale, str) or not rationale.strip():
                        return "Error: Autonomous identity evolution requires a reasoned rationale."
                    ref = operation_id or "self_" + uuid.uuid4().hex
                    if not isinstance(ref, str) or not ref.strip():
                        return "Error: operation_id must be a non-empty string."
                    matching = next((record for record in records if record.get("approval_ref") == ref), None)
                    if matching is not None:
                        if matching.get("authority") != "autonomous" or any(matching.get(k) != v for k, v in {
                                "target_section": target_section, "delta_type": delta_type,
                                "content": content, "rationale": rationale}.items()):
                            return "Error: operation_id is already bound to a different change."
                        if matching.get("completed"):
                            return f"Notice: Identity operation {ref} already completed; no change repeated."
                        raise RuntimeError("Incomplete operation without a recoverable journal; inspection required")
                    matching = {"approval_ref": ref, "agent_id": self.agent_id,
                                "target_section": target_section, "delta_type": delta_type,
                                "content": content, "rationale": rationale,
                                "authority": "autonomous", "user_id": self.agent_id,
                                "status": "approved", "used": False}
                    # 'approved' here is a legacy transaction-state label, NOT human consent.
                    records.append(matching)
                settings = self._fresh_identity_settings()
                before = list(settings.get(key, []) or [])
                after = self._identity_mutated_items(before, delta_type, content)
                entry = {"approval_ref": ref, "target_section": target_section,
                         "delta_type": delta_type, "content": content, "rationale": rationale,
                         "before_items": before, "after_items": after,
                         "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()}
                if matching.get("authority") == "autonomous":
                    atomic_json_write(os.path.join(self.palace_path, "identity_approvals.json"), records)
                atomic_json_write(journal_path, entry)
                self._commit_identity_journal(entry, records)
                return f"Successfully applied identity delta to {target_section} ({delta_type}): '{content}'. Identity regenerated. Operation: {ref}."
        except (PermissionError, ValueError, RuntimeError, OSError) as exc:
            return f"Error: Identity transaction not completed: {exc}"

    @memory_state_operation
    def flush_turn_coactivation(self, is_sleep_cycle: bool = False):
        """Flushes turn touched drawers into morphological Hebbian layer using canonical IDs."""
        with self._cache_lock:
            touched = list(self._turn_touched)
            self._turn_touched.clear()

        if is_sleep_cycle:
            return

        if not self._morpho_enabled or not self.morpho_layer:
            return

        try:
            eta = float(self._morpho_settings.get("eta", 1.0))
            self.morpho_layer.record_coactivation(touched, eta=eta)
        except Exception as e:
            logging.warning(f"Morphological layer error during flush_turn_coactivation: {e}")

    @memory_state_operation
    def step_temporal_decay(self, elapsed_minutes: Optional[float] = None):
        """Step intrinsic temporal decay in the morphological graph."""
        if not self._morpho_enabled or not self.morpho_layer:
            return
        try:
            self.morpho_layer.apply_decay(elapsed_minutes=elapsed_minutes)
        except Exception as e:
            logging.warning(f"Morphological layer error during step_temporal_decay: {e}")

    def get_graph_status(self) -> dict:
        """Inspect the morphological associative graph."""
        if not self._morpho_enabled or not self.morpho_layer:
            return {"error": "Morphological memory is disabled."}
        try:
            return self.morpho_layer.get_graph_status()
        except Exception as e:
            logging.warning(f"Error getting graph status: {e}")
            return {"error": str(e)}

    @memory_state_operation
    def link_setpoint(self, goal_key: str, drawer_ids: list) -> str:
        """Link a goal setpoint anchor to target memory drawers."""
        if not self._morpho_enabled or not self.morpho_layer:
            return "Error: Morphological memory is disabled."
        try:
            if not isinstance(drawer_ids, list):
                drawer_ids = [str(drawer_ids)]
            res = self.morpho_layer.link_setpoint(goal_key, drawer_ids)
            if isinstance(res, dict):
                linked = res.get("linked", [])
                rejected = res.get("rejected", [])
                if not linked:
                    return f"Error linking setpoint '{goal_key}': none of the requested drawers could be linked (unregistered: {rejected})."
                if rejected:
                    return f"Partially linked setpoint '{goal_key}' to drawers {linked} (rejected unregistered drawers: {rejected})."
                return f"Successfully linked setpoint '{goal_key}' to drawers {linked}."
            return f"Successfully linked setpoint '{goal_key}' to drawers {drawer_ids}."
        except Exception as e:
            logging.warning(f"Error linking setpoint: {e}")
            return f"Error linking setpoint: {e}"

    @memory_state_operation
    def unlink_setpoint(self, goal_key: str, drawer_ids: list) -> bool:
        """Unlink explicit drawer links from a goal setpoint."""
        if not self._morpho_enabled or not self.morpho_layer:
            return False
        try:
            if not isinstance(drawer_ids, list):
                drawer_ids = [str(drawer_ids)]
            return self.morpho_layer.unlink_setpoint(goal_key, drawer_ids)
        except Exception as e:
            logging.warning(f"Error unlinking setpoint: {e}")
            return False
