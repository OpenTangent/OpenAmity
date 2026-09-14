import os
import json
import logging
import datetime
import uuid
import threading
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

    def reload_settings(self):
        """Reload settings and sync identity"""
        self._sync_identity()
        self._mirrors_cache = None
        self._short_term_cache = None
        if hasattr(self, 'stack') and hasattr(self.stack, 'l0'):
            self.stack.l0._text = None

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
        """Returns L0 + Self-Perception + Short-Term Context"""
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

        return base_context

    def recall(self, wing: str = None, room: str = None, n_results: int = 10) -> str:
        """Returns L2 context"""
        return self.stack.recall(wing=wing, room=room, n_results=n_results)

    def search(self, query: str, wing: str = None, room: str = None, n_results: int = 5) -> str:
        """Returns L3 deep search context"""
        return self.stack.search(query=query, wing=wing, room=room, n_results=n_results)

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
