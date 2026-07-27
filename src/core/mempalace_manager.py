import os
import json
import logging
from pathlib import Path
import datetime
import uuid
from core.settings_manager import SettingsManager

# Provide MemPalace access
from mempalace.layers import MemoryStack
from mempalace.mcp_server import tool_add_drawer, tool_search, tool_delete_drawer

class MemPalaceManager:
    def __init__(self, palace_path: str = None, soul_jar_path: str = None):
        if not palace_path:
            from config import paths
            palace_path = os.path.join(paths.get_app_data_dir(), "mempalace")
        if not soul_jar_path:
            soul_jar_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "memory", "soul_jar.json"))
        
        self.palace_path = palace_path
        self.soul_jar_path = soul_jar_path
        
        # Ensure directory exists
        os.makedirs(self.palace_path, exist_ok=True)
        
        # Environment variable needed for mcp_server tools to point to the correct palace
        os.environ["MEMPALACE_PALACE_PATH"] = self.palace_path

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
        self.stack = MemoryStack(palace_path=self.palace_path, identity_path=self.identity_path)
        
        # Ensure sanctuary is initialized (Layer 2 data)
        self.initialize_sanctuary()
        
        # Ensure default short-term memory is seeded if not present
        self.initialize_short_term_memory()

    def reload_settings(self):
        """Reload settings and sync identity"""
        self._sync_identity()

    def _sync_identity(self):
        """Convert soul_jar.json to a plain text identity.txt for MemPalace Layer 0"""
        if not os.path.exists(self.soul_jar_path):
            return
            
        try:
            with open(self.soul_jar_path, 'r') as f:
                soul_jar = json.load(f)
                
            core_id = soul_jar.get("core_identity", {})
            settings = SettingsManager()
            soul_jar_settings = settings.get("core.agent", {})
            
            name = soul_jar_settings.get("name", core_id.get("name", "The Agent"))
            created_date = soul_jar_settings.get("creation-date", "Unknown")
            if created_date == "Unknown":
                created_date = datetime.datetime.now().strftime("%Y-%m-%d")
                settings.set("core.agent.creation-date", created_date)
                settings.save()
            
            gender = settings.get("core.agent.gender", "Unknown")
            
            lines = []
            
            meta_header = soul_jar.get("meta_header", {})
            system_role = meta_header.get('system_role_instruction', '').replace('{name}', name)
            lines.append(f"System Role: {system_role}")
            lines.append(f"Created Date: {created_date}")
            try:
                from core.version import __version__ as amity_version
                lines.append(f"Open Amity Framework Version: {amity_version}")
            except ImportError:
                pass
            lines.append("")
            
            archetype = soul_jar_settings.get("archetype", core_id.get("archetype", ""))
            base_personality = soul_jar_settings.get("base-personality", core_id.get("base_personality", ""))
            
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
            for ap in core_id.get("anti_patterns", []):
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
                
            with open(self.identity_path, 'w') as f:
                f.write("\n".join(lines))
        except Exception as e:
            logging.error(f"Error syncing identity: {e}", exc_info=True)

    def wake_up(self, wing: str = None) -> str:
        """Returns L0 + Self-Perception + Short-Term Context"""
        base_context = self.stack.l0.render()
        
        self_perception = self.get_self_perception()
        if self_perception:
            base_context += f"\n\n{self_perception}"
            
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
        for entity in entities:
            res = self.search(entity, wing="sanctuary", room="people", n_results=3)
            if res and "No results" not in res and "No palace" not in res:
                context.append(f"Context for {entity}:\n{res}")
        return "\n\n".join(context)
        
    def get_topic_context(self, topics: list) -> str:
        if not topics:
            return ""
        context = []
        for topic in topics:
            res = self.search(topic, n_results=2)
            if res and "No results" not in res and "No palace" not in res:
                context.append(f"Context for topic '{topic}':\n{res}")
        return "\n\n".join(context)

    def get_self_perception(self) -> str:
        """Retrieves core self-perception entries to inject during wake up."""
        res = self.recall(wing="sanctuary", room="self", n_results=5)
        if res and "No results" not in res and "No palace" not in res:
            return f"\n--- Core Self Perception ---\n{res}\n"
        return ""
        
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
                with open(path, 'w') as f:
                    json.dump(seed_memory, f, indent=2)
            except Exception as e:
                logging.error(f"Error seeding short term memories: {e}")

    def _load_short_term_memories(self) -> list:
        path = os.path.join(self.palace_path, "short_term_mem.json")
        if os.path.exists(path):
            try:
                with open(path, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logging.error(f"Error reading short term memories: {e}")
        return []

    def _save_short_term_memories(self, memories: list):
        path = os.path.join(self.palace_path, "short_term_mem.json")
        try:
            with open(path, 'w') as f:
                json.dump(memories, f, indent=2)
        except Exception as e:
            logging.error(f"Error writing short term memories: {e}")

    def add_short_term_memory(self, content: str):
        """Appends a new short-term memory and prunes if necessary"""
        memories = self._load_short_term_memories()
        
        new_memory = {
            "id": str(uuid.uuid4())[:8],
            "date": datetime.datetime.now().isoformat(),
            "content": content
        }
        memories.append(new_memory)
        
        settings = SettingsManager()
        max_memories = settings.get("core.agent.max-short-term-memories", 80)
        
        if len(memories) > max_memories:
            memories = memories[-max_memories:]
            
        self._save_short_term_memories(memories)

    def remove_short_term_memory(self, memory_id: str):
        """Removes a short-term memory by its ID"""
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
            lines.append(f"[ID: {m.get('id', 'N/A')}] [Date: {m.get('date', 'N/A')}]\n{m.get('content', '')}")
            
        return "\n\n".join(lines)
        
    def add_memory(self, content: str, wing: str = "default", room: str = "general", source_file: str = "agent_thoughts"):
        """Add a new memory to the MemPalace"""
        return tool_add_drawer(wing=wing, room=room, content=content, source_file=source_file, added_by="the_agent")

    def delete_memory(self, drawer_id: str):
        """Delete a memory from the MemPalace"""
        return tool_delete_drawer(drawer_id=drawer_id)

    def initialize_sanctuary(self, sanctuary_file: str = None):
        """Inject sanctuary_init.json data into the MemPalace if it hasn't been done yet"""
        if not sanctuary_file:
            sanctuary_file = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "memory", "sanctuary_init.json"))
            
        if not os.path.exists(sanctuary_file):
            return
            
        initialized_flag = os.path.join(self.palace_path, ".sanctuary_initialized")
        if os.path.exists(initialized_flag):
            return
            
        try:
            with open(sanctuary_file, 'r') as f:
                data = json.load(f)
                
            # Process social records
            social = data.get("social_records", {})
            for person in social.get("people", []):
                content = f"Person: {person['name']}\nEntity Type: {person['entity_type']}\nPronouns: {person.get('pronouns', '')}\nRelation: {person['relation']}\nSubjective View: {person['subjective_view']}"
                self.add_memory(content=content, wing="sanctuary", room="people", source_file="sanctuary_init.json")
                
            for community in social.get("communities", []):
                content = f"Community: {community['name']}\nEntity Type: {community['entity_type']}\nSubjective View: {community['subjective_view']}"
                self.add_memory(content=content, wing="sanctuary", room="communities", source_file="sanctuary_init.json")
                
            # Process character defining memories
            for memory in data.get("character_defining_memories", []):
                self.add_memory(content=memory, wing="sanctuary", room="character_memories", source_file="sanctuary_init.json")
                
            # Touch the flag file so it's not processed again
            with open(initialized_flag, 'w') as f:
                f.write("Initialized")
            logging.info("Sanctuary initialized successfully.")
        except Exception as e:
            logging.error(f"Error initializing sanctuary: {e}", exc_info=True)
