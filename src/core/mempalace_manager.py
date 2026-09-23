import os
import json
import logging
import datetime
import uuid
import threading
import hashlib
from typing import Optional
from core.settings_manager import SettingsManager
from core.file_utils import atomic_json_write

from mempalace.layers import MemoryStack

_mempalace_lock = threading.Lock()


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

        # Morphological Memory (MemPalace v2)
        self._turn_touched = []
        self.morpho_layer = None
        self._load_morpho_settings()
        if self._morpho_enabled:
            self._init_morphological_layer()

        # Initialize the stack
        try:
            self.stack = MemoryStack(
                palace_path=self.palace_path, identity_path=self.identity_path)
        except Exception as e:
            logging.error(
                f"MemPalaceManager: Error initializing MemoryStack (possibly ChromaDB schema mismatch): {e}")
            import time
            import shutil

            timestamp = int(time.time())
            backup_dir = f"{self.palace_path}_quarantined_{timestamp}"
            logging.warning(
                f"MemPalaceManager: Quarantining incompatible MemPalace directory to {backup_dir} and re-initializing.")

            shutil.move(self.palace_path, backup_dir)
            os.makedirs(self.palace_path, exist_ok=True)

            # Restore core Open Amity state files that are safe
            for safe_file in ["mirrors.json", "short_term_mem.json", "identity.txt", ".sanctuary_initialized"]:
                src = os.path.join(backup_dir, safe_file)
                if os.path.exists(src):
                    shutil.copy2(src, os.path.join(
                        self.palace_path, safe_file))

            self.stack = MemoryStack(
                palace_path=self.palace_path, identity_path=self.identity_path)

        # Ensure sanctuary is initialized (Layer 2 data)
        self.initialize_sanctuary()

        # Ensure default short-term memory is seeded if not present
        self.initialize_short_term_memory()

    def _load_morpho_settings(self):
        settings = SettingsManager(agent_id=self.agent_id)
        self._morpho_settings = settings.get("core.morphological_memory")
        if self._morpho_settings is None:
            self._morpho_settings = settings.get("core.morphological-memory")
        if not isinstance(self._morpho_settings, dict):
            self._morpho_settings = {}
        self._morpho_enabled = self._morpho_settings.get("enabled", True)

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
        """Backfill existing ChromaDB drawers into morpho_nodes (§9.2)."""
        if not self.morpho_layer:
            return
        try:
            col = self._get_collection(create=False)
            if col:
                data = col.get(include=["metadatas"])
                ids = data.get("ids") or []
                metas = data.get("metadatas") or []
                drawers = []
                for did, meta in zip(ids, metas):
                    meta = meta or {}
                    w = meta.get("wing", "default")
                    r = meta.get("room", "general")
                    drawers.append((did, w, r))
                if drawers:
                    self.morpho_layer.register_drawers_batch(drawers)
                logging.info(f"Backfill migration: registered {len(drawers)} existing ChromaDB drawers into morpho_nodes.")
        except Exception as e:
            logging.warning(f"Error during morphological backfill migration: {e}")

    def touch_drawers(self, drawer_ids: list):
        """Record touched drawer IDs in the current turn."""
        with self._cache_lock:
            for did in drawer_ids:
                if did and did not in self._turn_touched:
                    self._turn_touched.append(did)

    def reload_settings(self):
        """Reload settings and sync identity"""
        self._sync_identity()
        self._mirrors_cache = None
        self._short_term_cache = None
        if hasattr(self, 'stack') and hasattr(self.stack, 'l0'):
            self.stack.l0._text = None
        self._load_morpho_settings()
        if self._morpho_enabled and not self.morpho_layer:
            self._init_morphological_layer()
        elif not self._morpho_enabled:
            self.morpho_layer = None

    def _sync_identity(self):
        """Convert soul_jar.json to a plain text identity.txt for MemPalace Layer 0"""
        if not os.path.exists(self.soul_jar_path):
            return

        try:
            with open(self.soul_jar_path, 'r') as f:
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
            with open(temp_path, 'w') as f:
                f.write("\n".join(lines))
            os.replace(temp_path, self.identity_path)

            # Invalidate cached Layer0 text so future render() calls re-read from disk
            if hasattr(self, 'stack') and hasattr(self.stack, 'l0'):
                self.stack.l0._text = None
        except Exception as e:
            logging.error(f"Error syncing identity: {e}", exc_info=True)

    def wake_up(self, wing: str = None) -> str:
        """Returns L0 + Self-Perception + Short-Term Context + Morphological Prospective Context"""
        base_context = self.stack.l0.render()

        self_perception = self.get_self_perception()
        if self_perception:
            base_context += f"\n\n{self_perception}"

        memories = self._load_short_term_memories()
        num_items = len(memories)
        num_words = sum(len(m.get('content', '').split()) for m in memories)
        try:
            num_drawers = self.stack.status().get('total_drawers', 0)
        except Exception:
            num_drawers = 0
        base_context += f"\n\n[Memory Status: Short-Term ({num_items} items / {num_words} words), Palace ({num_drawers} drawers)]"

        short_term = self.get_short_term_context()
        if short_term:
            base_context += f"\n\n--- Short-Term Memory (Continuity) ---\n{short_term}\n"

        # Morphological Memory (Prospective Attractors)
        if self._morpho_enabled and self.morpho_layer:
            try:
                self.step_temporal_decay()
                prospective_section = self._get_prospective_memory_context()
                if prospective_section:
                    base_context += f"\n\n{prospective_section}"
            except Exception as e:
                logging.warning(f"Error generating morphological prospective memories: {e}")

        return base_context

    def _get_prospective_memory_context(self) -> str:
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

            for goal_key, goal_text in goals:
                text_hash = hashlib.sha1(goal_text.encode("utf-8")).hexdigest()
                setpoint = self.morpho_layer.get_setpoint(goal_key)
                if setpoint and setpoint.get("text_hash") == text_hash:
                    target_drawers = setpoint.get("target_drawers", [])
                else:
                    matching_ids = []
                    if col:
                        try:
                            q = col.query(query_texts=[goal_text], n_results=3, include=["distances"])
                            ids_list = q.get("ids", [[]])
                            dists_list = q.get("distances", [[]])
                            if ids_list and ids_list[0] and dists_list and dists_list[0]:
                                for did, dist in zip(ids_list[0], dists_list[0]):
                                    if dist is not None and float(dist) < 0.45:
                                        matching_ids.append(did)
                        except Exception as qe:
                            logging.warning(f"Error querying ChromaDB for goal setpoint: {qe}")

                    self.morpho_layer.upsert_setpoint(goal_key, text_hash, matching_ids)
                    updated_sp = self.morpho_layer.get_setpoint(goal_key)
                    target_drawers = updated_sp.get("target_drawers", []) if updated_sp else matching_ids

                all_target_drawers.extend(target_drawers)

            all_target_drawers = list(dict.fromkeys(all_target_drawers))
            if not all_target_drawers:
                return ""

            top_k = int(self._morpho_settings.get("top_k", 5))
            results = self.morpho_layer.relax_setpoint(all_target_drawers, top_k=top_k)
            if not results or not col:
                return ""

            lines = ["--- Morphological Memory (Prospective Attractors) ---"]
            total_chars = 0
            max_total_chars = 2400  # ~600 tokens per §9.4

            for did, potential in results:
                doc_text = ""
                try:
                    res = col.get(ids=[did], include=["documents"])
                    docs = res.get("documents", [])
                    if docs and docs[0]:
                        doc_text = str(docs[0]).strip().replace("\n", " ")
                except Exception as de:
                    logging.warning(f"Error fetching document for prospective drawer {did}: {de}")
                    continue

                if not doc_text:
                    continue

                if len(doc_text) > 300:
                    doc_text = doc_text[:297] + "..."

                entry = f"[Drawer #{did}] {doc_text}"
                if total_chars + len(entry) > max_total_chars and len(lines) > 1:
                    break
                lines.append(entry)
                total_chars += len(entry)

            if len(lines) <= 1:
                return ""
            return "\n".join(lines)
        except Exception as e:
            logging.warning(f"Error preparing prospective memory context: {e}", exc_info=True)
            return ""

    def recall(self, wing: str = None, room: str = None, n_results: int = 10) -> str:
        """Returns L2 context"""
        res = self.stack.recall(wing=wing, room=room, n_results=n_results)
        if self._morpho_enabled and self.morpho_layer:
            try:
                col = self._get_collection(create=False)
                if col:
                    from mempalace.layers import build_where_filter
                    where = build_where_filter(wing, room)
                    kwargs = {"include": ["metadatas"], "limit": n_results}
                    if where:
                        kwargs["where"] = where
                    g_res = col.get(**kwargs)
                    ids = g_res.get("ids", [])
                    if ids:
                        self.touch_drawers(ids)
            except Exception as me:
                logging.warning(f"Morphological layer recall tracking failed: {me}")
        return res

    def search(self, query: str, wing: str = None, room: str = None, n_results: int = 5) -> str:
        """Returns L3 deep search context"""
        res = self.stack.search(query=query, wing=wing, room=room, n_results=n_results)
        if self._morpho_enabled and self.morpho_layer:
            try:
                col = self._get_collection(create=False)
                if col:
                    from mempalace.layers import build_where_filter
                    where = build_where_filter(wing, room)
                    kwargs = {"query_texts": [query], "n_results": n_results, "include": ["metadatas"]}
                    if where:
                        kwargs["where"] = where
                    q_res = col.query(**kwargs)
                    ids = q_res.get("ids", [[]])
                    if ids and ids[0]:
                        self.touch_drawers(ids[0])
            except Exception as me:
                logging.warning(f"Morphological layer search tracking failed: {me}")
        return res

    def get_entity_context(self, entities: list) -> str:
        if not entities:
            return ""
        context = []
        from concurrent.futures import ThreadPoolExecutor

        def fetch(entity):
            res = self.search(entity, wing="sanctuary",
                              room="people", n_results=3)
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

    def _load_mirrors(self) -> dict:
        with self._cache_lock:
            if getattr(self, '_mirrors_cache', None) is not None:
                return self._mirrors_cache

            path = os.path.join(self.palace_path, "mirrors.json")
            if os.path.exists(path):
                try:
                    with open(path, 'r') as f:
                        mirrors = json.load(f)

                        modified = False
                        for perspective, data in mirrors.items():
                            if not isinstance(data, dict):
                                mirrors[perspective] = {"current_view": str(
                                    data), "history": [], "provenance": "unknown"}
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
                            self._mirrors_cache = mirrors

                        return mirrors
                except Exception as e:
                    logging.error(f"Error reading mirrors: {e}")
            return {}

    def _save_mirrors(self, mirrors: dict):
        with self._cache_lock:
            self._mirrors_cache = mirrors
            path = os.path.join(self.palace_path, "mirrors.json")
            try:
                atomic_json_write(path, mirrors)
            except Exception as e:
                logging.error(f"Error writing mirrors: {e}")

    def update_mirror(self, perspective: str, subjective_view: str, provenance: str = "inferred",
                      confidence: float = 1.0, evidence_refs: list = None, contradicted_by: list = None):
        with self._cache_lock:
            mirrors = self._load_mirrors()
            now_str = datetime.datetime.now().isoformat()

            if perspective not in mirrors:
                mirrors[perspective] = {
                    "current_view": subjective_view,
                    "history": []
                }
            else:
                old_view = mirrors[perspective].get("current_view", "")
                mirrors[perspective].setdefault("history", []).append({
                    "view": old_view,
                    "date": now_str,
                    "provenance": mirrors[perspective].get("provenance", "unknown"),
                    "confidence": mirrors[perspective].get("confidence", 1.0)
                })
                mirrors[perspective]["current_view"] = subjective_view

            mirrors[perspective]["provenance"] = provenance
            mirrors[perspective]["confidence"] = float(confidence) if confidence is not None else 1.0
            mirrors[perspective]["evidence_refs"] = list(evidence_refs) if evidence_refs else []
            mirrors[perspective]["contradicted_by"] = list(contradicted_by) if contradicted_by else []
            mirrors[perspective]["last_updated"] = now_str

            self._save_mirrors(mirrors)
            return True

    def get_self_perception(self, limit: int = 24) -> str:
        """Retrieves core self-perception entries (up to `limit` most recently updated) to inject during wake up or bearings."""
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
                with open(temp_path, 'w') as f:
                    json.dump(seed_memory, f, indent=2)
                os.replace(temp_path, path)
            except Exception as e:
                logging.error(f"Error seeding short term memories: {e}")

    def _load_short_term_memories(self) -> list:
        with self._cache_lock:
            if getattr(self, '_short_term_cache', None) is not None:
                return self._short_term_cache

            path = os.path.join(self.palace_path, "short_term_mem.json")
            if os.path.exists(path):
                try:
                    with open(path, 'r') as f:
                        memories = json.load(f)

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
                            self._short_term_cache = memories

                        return memories
                except Exception as e:
                    logging.error(f"Error reading short term memories: {e}")
            return []

    def _save_short_term_memories(self, memories: list):
        with self._cache_lock:
            self._short_term_cache = memories
            path = os.path.join(self.palace_path, "short_term_mem.json")
            try:
                atomic_json_write(path, memories)
            except Exception as e:
                logging.error(f"Error writing short term memories: {e}")

    def add_short_term_memory(self, content: str, supersedes: list = None):
        """Appends a new short-term memory and prunes if necessary"""
        with self._cache_lock:
            memories = self._load_short_term_memories()

            if supersedes:
                memories = [m for m in memories if m.get("id") not in supersedes]

            new_memory = {
                "id": str(uuid.uuid4())[:8],
                "date": datetime.datetime.now().isoformat(),
                "content": content
            }
            memories.append(new_memory)

            settings = SettingsManager(agent_id=self.agent_id)
            max_memories = settings.get("core.agent.max-short-term-memories", 80)

            if len(memories) > max_memories:
                memories = memories[-max_memories:]

            self._save_short_term_memories(memories)

    def remove_short_term_memory(self, memory_id: str):
        """Removes a short-term memory by its ID"""
        with self._cache_lock:
            memories = self._load_short_term_memories()
            filtered = [m for m in memories if m.get("id") != memory_id]
            if len(filtered) != len(memories):
                self._save_short_term_memories(filtered)

    def get_short_term_context(self) -> str:
        """Reads and formats the short term memory context"""
        memories = self._load_short_term_memories()
        if not memories:
            return ""

        lines = []
        for m in memories:
            lines.append(
                f"[ID: {m.get('id', 'N/A')}] [Date: {m.get('date', 'N/A')}]\n{m.get('content', '')}")

        return "\n\n".join(lines)

    def add_memory(self, content: str, wing: str = "default", room: str = "general", source_file: str = "agent_thoughts"):
        """Add a new memory to the MemPalace directly in-process."""
        with _mempalace_lock:
            try:
                os.environ['MEMPALACE_PALACE_PATH'] = self.palace_path
                from mempalace.mcp_server import tool_add_drawer
                res = tool_add_drawer(
                    wing=wing,
                    room=room,
                    content=content,
                    source_file=source_file,
                    added_by="the_agent"
                )
                if isinstance(res, dict) and res.get("success") and res.get("drawer_id"):
                    did = res["drawer_id"]
                    if self._morpho_enabled and self.morpho_layer:
                        try:
                            self.morpho_layer.register_drawer(did, wing=wing, room=room)
                        except Exception as me:
                            logging.warning(f"Morphological layer failed to register drawer: {me}")
                    self.touch_drawers([did])
                return res
            except Exception as e:
                logging.error(f"MemPalaceManager.add_memory error: {e}", exc_info=True)
                return f"Failed to add memory: {e}"

    def delete_memory(self, drawer_id: str):
        """Delete a memory from the MemPalace directly in-process."""
        with _mempalace_lock:
            try:
                os.environ['MEMPALACE_PALACE_PATH'] = self.palace_path
                from mempalace.mcp_server import tool_delete_drawer
                res = tool_delete_drawer(drawer_id=drawer_id)
                if self._morpho_enabled and self.morpho_layer:
                    try:
                        self.morpho_layer.remove_drawer(drawer_id=drawer_id)
                    except Exception as me:
                        logging.warning(f"Morphological layer failed to remove drawer: {me}")
                return res
            except Exception as e:
                logging.error(f"MemPalaceManager.delete_memory error: {e}", exc_info=True)
                return f"Failed to delete memory: {e}"

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
            with open(sanctuary_file, 'r') as f:
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

            # Touch the flag file so it's not processed again
            temp_path = f"{initialized_flag}.tmp.{uuid.uuid4().hex}"
            with open(temp_path, 'w') as f:
                f.write("Initialized")
            os.replace(temp_path, initialized_flag)
            logging.info("Sanctuary initialized successfully.")
        except Exception as e:
            logging.error(f"Error initializing sanctuary: {e}", exc_info=True)

    _init_sanctuary = initialize_sanctuary

    def apply_identity_delta(self, target_section: str, delta_type: str, content: str, rationale: str = "") -> str:
        """
        Autonomously applies an approved Identity Delta to the agent's Layer 0 settings,
        logs the evolution history, re-syncs identity.txt, and invalidates L0 cache.
        """
        settings = SettingsManager(agent_id=self.agent_id)
        valid_sections = {
            "core_values": "core.agent.core-values",
            "overarching_goals": "core.agent.overarching-goals",
            "anti_patterns": "core.agent.anti-patterns"
        }
        config_key = valid_sections.get(target_section)
        if not config_key:
            return f"Error: target_section must be one of {list(valid_sections.keys())}"

        current_items = settings.get(config_key, [])
        if not isinstance(current_items, list):
            current_items = []
        current_items = list(current_items)

        now_iso = datetime.datetime.now().isoformat()
        if delta_type == "add":
            if content not in current_items:
                current_items.append(content)
        elif delta_type in ("modify", "update"):
            current_items.append(content)
        elif delta_type in ("retire", "remove"):
            current_items = [item for item in current_items if item != content and content not in item]
        else:
            return "Error: delta_type must be 'add', 'modify', or 'retire'"

        settings.set(config_key, current_items)
        settings.save()

        # Record evolution history in identity_evolution.json
        history_path = os.path.join(self.palace_path, "identity_evolution.json")
        history = []
        if os.path.exists(history_path):
            try:
                with open(history_path, "r", encoding="utf-8") as f:
                    history = json.load(f)
            except Exception:
                history = []

        history.append({
            "timestamp": now_iso,
            "target_section": target_section,
            "delta_type": delta_type,
            "content": content,
            "rationale": rationale
        })
        atomic_json_write(history_path, history)

        # Re-sync Layer 0 identity.txt
        self._sync_identity()
        if hasattr(self, 'stack') and hasattr(self.stack, 'l0'):
            self.stack.l0._text = None

        logging.info(f"Identity Delta applied successfully: {delta_type} {target_section} -> '{content}'")
        return f"Successfully applied identity delta to {target_section} ({delta_type}): '{content}'. Identity regenerated."

    def flush_turn_coactivation(self, is_sleep_cycle: bool = False):
        """
        Flushes the current turn's touched drawers into the morphological Hebbian layer.
        Per §9.3, sleep cycles bypass coactivation.
        """
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

    def link_setpoint(self, goal_key: str, drawer_ids: list) -> str:
        """Link a goal setpoint anchor to target memory drawers."""
        if not self._morpho_enabled or not self.morpho_layer:
            return "Error: Morphological memory is disabled."
        try:
            if not isinstance(drawer_ids, list):
                drawer_ids = [str(drawer_ids)]
            self.morpho_layer.link_setpoint(goal_key, drawer_ids)
            return f"Successfully linked setpoint '{goal_key}' to drawers {drawer_ids}."
        except Exception as e:
            logging.warning(f"Error linking setpoint: {e}")
            return f"Error linking setpoint: {e}"

