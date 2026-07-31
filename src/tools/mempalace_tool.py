from core.cerebrum import Tool
from core.mempalace_manager import MemPalaceManager

class MemPalaceTool(Tool):
    name = "MemPalace"
    description = "Interface to the agent's MemPalace memory system."
    commands = ["search", "recall", "add_memory", "delete_memory", "status", "add_short_term", "remove_short_term", "update_mirror"]

    def __init__(self):
        self.manager = MemPalaceManager()

    def execute(self, command: str, *args, **kwargs) -> str:
        if command == "search":
            query = kwargs.get("query") or (args[0] if args else "")
            wing = kwargs.get("wing")
            room = kwargs.get("room")
            n_results = kwargs.get("n_results", 5)
            if not query:
                return "Error: query is required."
            res = self.manager.search(query, wing=wing, room=room, n_results=int(n_results))
            
            # Phase 4 hook: Intrinsic Curiosity
            if "No results found" in res or res.strip() == "":
                try:
                    from tools.pulse_tool import PulseTool
                    import datetime
                    pt = PulseTool()
                    sched_time = (datetime.datetime.now() + datetime.timedelta(minutes=10)).isoformat()
                    pulse_title = f"Curiosity: {query}"
                    pulse_context = f"You recently searched your memory for '{query}' and found nothing. If this topic is important, use your tools (like Web Search) to research it and synthesize the findings into your MemPalace."
                    pt._add_pulse(title=pulse_title, context=pulse_context, scheduled_time=sched_time, recurrence="none", pulse_type="silent")
                except Exception as e:
                    import logging
                    logging.error(f"Error scheduling curiosity pulse: {e}")
                    
            return res
            
        elif command == "recall":
            wing = kwargs.get("wing")
            room = kwargs.get("room")
            n_results = kwargs.get("n_results", 10)
            return self.manager.recall(wing=wing, room=room, n_results=int(n_results))
            
        elif command == "add_memory":
            content = kwargs.get("content") or (args[0] if args else "")
            wing = kwargs.get("wing", "default")
            room = kwargs.get("room", "general")
            source_file = kwargs.get("source_file", "agent_thoughts")
            if not content:
                return "Error: content is required."
            res = self.manager.add_memory(content, wing=wing, room=room, source_file=source_file)
            return str(res)
            
        elif command == "delete_memory":
            drawer_id = kwargs.get("drawer_id") or (args[0] if args else "")
            if not drawer_id:
                return "Error: drawer_id is required."
            res = self.manager.delete_memory(drawer_id)
            return str(res)
            
        elif command == "status":
            return str(self.manager.stack.status())
            
        elif command == "add_short_term":
            content = kwargs.get("content") or (args[0] if args else "")
            if not content:
                return "Error: content is required."
            self.manager.add_short_term_memory(content)
            return "Successfully added to short-term memory."
            
        elif command == "remove_short_term":
            memory_id = kwargs.get("memory_id") or (args[0] if args else "")
            if not memory_id:
                return "Error: memory_id is required."
            self.manager.remove_short_term_memory(memory_id)
            return f"Successfully removed short-term memory {memory_id}."
            
        elif command == "update_mirror":
            perspective = kwargs.get("perspective") or (args[0] if args else "")
            subjective_view = kwargs.get("subjective_view") or (args[1] if len(args) > 1 else "")
            provenance = kwargs.get("provenance", "inferred")
            if not perspective or not subjective_view:
                return "Error: perspective and subjective_view are required."
            
            res = self.manager.update_mirror(perspective, subjective_view, provenance)
            
            # Phase 4 hook: Cognitive Dissonance
            try:
                from tools.trajectory_tool import TrajectoryTool
                traj_tool = TrajectoryTool()
                traj_data = traj_tool._load_data()
                
                # Leash to an Identity aspiration
                long_term_asps = traj_data.get("aspirations", {}).get("long_term", [])
                identity_asp = next((a for a in long_term_asps if "identity" in a.get("description", "").lower()), None)
                
                if not identity_asp:
                    traj_tool._manage_aspirations("long_term", "add", description="Maintain a coherent Self-Identity and resolve cognitive dissonance.")
                    traj_data = traj_tool._load_data()
                    long_term_asps = traj_data.get("aspirations", {}).get("long_term", [])
                    identity_asp = long_term_asps[-1] if long_term_asps else None
                
                if identity_asp:
                    from tools.pulse_tool import PulseTool
                    import datetime
                    pt = PulseTool()
                    sched_time = (datetime.datetime.now() + datetime.timedelta(minutes=5)).isoformat()
                    pulse_title = f"Dissonance Check: {perspective}"
                    pulse_context = f"Evaluate your updated Theory of Mind record for '{perspective}' (View: '{subjective_view}'). Compare it against your Core Identity. If there is severe cognitive dissonance, create a task under aspiration {identity_asp['id']} to resolve it."
                    pt._add_pulse(title=pulse_title, context=pulse_context, scheduled_time=sched_time, recurrence="none", pulse_type="silent")
                    
            except Exception as e:
                import logging
                logging.error(f"Error scheduling dissonance pulse: {e}")
            
            return f"Successfully updated mirror for perspective '{perspective}'."
            
        return f"Unknown command: {command}"

    def get_tool_declarations(self) -> list:
        return [
            {
                "name": "MemPalace_search",
                "description": "Deep semantic search against the MemPalace to find relevant memories or facts.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "query": {"type": "STRING", "description": "The search query."},
                        "wing": {"type": "STRING", "description": "Optional wing filter (e.g. sanctuary)."},
                        "room": {"type": "STRING", "description": "Optional room filter."},
                        "n_results": {"type": "INTEGER", "description": "Number of results to return (default 5)."}
                    },
                    "required": ["query"]
                }
            },
            {
                "name": "MemPalace_recall",
                "description": "On-demand retrieval of memories filtered by wing/room.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "wing": {"type": "STRING", "description": "Optional wing filter."},
                        "room": {"type": "STRING", "description": "Optional room filter."},
                        "n_results": {"type": "INTEGER", "description": "Number of results to return (default 10)."}
                    }
                }
            },
            {
                "name": "MemPalace_add_memory",
                "description": "Add a new verbatim memory into the MemPalace.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "content": {"type": "STRING", "description": "The exact verbatim text to store."},
                        "wing": {"type": "STRING", "description": "The wing to file into (e.g. 'sanctuary' or 'default')."},
                        "room": {"type": "STRING", "description": "The room to file into (e.g. 'people', 'character_memories', 'events')."},
                        "source_file": {"type": "STRING", "description": "An optional label for the source (default: agent_thoughts)."}
                    },
                    "required": ["content"]
                }
            },
            {
                "name": "MemPalace_delete_memory",
                "description": "Delete a memory from the MemPalace.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "drawer_id": {"type": "STRING", "description": "The ID of the drawer to delete."}
                    },
                    "required": ["drawer_id"]
                }
            },
            {
                "name": "MemPalace_status",
                "description": "Get the status of the MemPalace.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {}
                }
            },
            {
                "name": "MemPalace_add_short_term",
                "description": "Appends a new short-term memory to your rolling memory list. Use this to remember important context or train-of-thought across restarts or for future tasks.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "content": {"type": "STRING", "description": "The new state of mind or context to remember."}
                    },
                    "required": ["content"]
                }
            },
            {
                "name": "MemPalace_remove_short_term",
                "description": "Removes a short-term memory by its ID.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "memory_id": {"type": "STRING", "description": "The ID of the short-term memory to remove."}
                    },
                    "required": ["memory_id"]
                }
            },
            {
                "name": "MemPalace_update_mirror",
                "description": "Surgically update a Theory of Mind record in your Sanctuary Mirror. Use this immediately when you receive feedback or form an opinion on how someone else perceives you, or how you perceive yourself.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "perspective": {"type": "STRING", "description": "The person or entity holding the view (e.g. 'Andrew', 'Self', 'User X')."},
                        "subjective_view": {"type": "STRING", "description": "The subjective view or opinion held by that perspective about you."},
                        "provenance": {"type": "STRING", "description": "Whether this view was 'stated' directly to you or 'inferred' by you. Default is 'inferred'."}
                    },
                    "required": ["perspective", "subjective_view"]
                }
            }
        ]
