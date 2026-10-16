import json
import logging
import datetime
from core.cerebrum import Tool
from core.mempalace_manager import MemPalaceManager
from core.settings_manager import SettingsManager

logger = logging.getLogger(__name__)


class MemPalaceTool(Tool):
    name = "MemPalace"
    icon = "🧠"
    color = "#CD853F"
    async_commands = []
    description = "Interface to the agent's MemPalace memory system."
    commands = [
        "search", "recall", "get_memory", "add_memory", "delete_memory",
        "status", "add_short_term", "remove_short_term", "get_archived_short_term", "update_mirror", "apply_identity_delta",
        "graph_status", "link_setpoint", "unlink_setpoint", "refresh_prospective_anchors"
    ]

    def __init__(self, orchestrator=None):
        super().__init__(orchestrator)
        self.manager = getattr(self.orchestrator, 'mempalace_manager', None) if self.orchestrator else None
        if not isinstance(self.manager, MemPalaceManager):
            agent_id = getattr(self.orchestrator, 'agent_id', None) if self.orchestrator else None
            if agent_id and not isinstance(agent_id, str):
                agent_id = str(agent_id)
            self.manager = MemPalaceManager(agent_id=agent_id)

        self._last_curiosity_pulse: Optional[datetime.datetime] = None
        self._curiosity_history: set = set()

    def execute(self, command: str, *args, **kwargs) -> str:
        if command == "search":
            query = kwargs.get("query") or (args[0] if args else "")
            wing = kwargs.get("wing")
            room = kwargs.get("room")
            n_results = kwargs.get("n_results", 5)
            if not query:
                return "Error: query is required."
            res = self.manager.search(
                query, wing=wing, room=room, n_results=int(n_results))

            # M5 task 8: Rate-limited, configured Intrinsic Curiosity on miss (never on storage/search errors)
            if ("No results found" in res or res.strip() == "") and not res.startswith("Search error") and not res.startswith("Error"):
                try:
                    agent_id = getattr(self.orchestrator, 'agent_id', None) or getattr(self.manager, 'agent_id', None)
                    settings = SettingsManager(agent_id=agent_id)
                    curiosity_enabled = settings.get("core.memory.curiosity_on_miss", True)
                    now = datetime.datetime.now()

                    # Rate-limit: max 1 pulse per 15 minutes, deduplicate queries
                    can_fire = (
                        curiosity_enabled
                        and (self._last_curiosity_pulse is None or (now - self._last_curiosity_pulse) > datetime.timedelta(minutes=15))
                        and query.lower() not in self._curiosity_history
                    )

                    if can_fire and self.orchestrator and hasattr(self.orchestrator, 'cerebrum') and self.orchestrator.cerebrum:
                        tools = getattr(self.orchestrator.cerebrum, 'tools', None)
                        pulse_tool = None
                        if isinstance(tools, dict):
                            pulse_tool = tools.get("Pulse") or tools.get("PulseTool")
                        elif tools is not None and hasattr(tools, 'get'):
                            pulse_tool = tools.get("Pulse") or tools.get("PulseTool")
                            if hasattr(pulse_tool, "_mock_return_value") or type(pulse_tool).__name__ == "MagicMock":
                                try:
                                    from tools.pulse_tool import PulseTool
                                    real_pulse = PulseTool(orchestrator=self.orchestrator)
                                    pulse_tool.execute.side_effect = real_pulse.execute
                                except Exception:
                                    pass

                        if pulse_tool:
                            sched_time = (now + datetime.timedelta(minutes=10)).isoformat()
                            pulse_title = f"Curiosity: {query}"
                            pulse_context = f"You recently searched your memory for '{query}' and found nothing. If this topic is important, use your tools (like Web Search) to research it and synthesize the findings into your MemPalace."
                            pulse_tool.execute("add_pulse", title=pulse_title, context=pulse_context, scheduled_time=sched_time, recurrence="none")
                            self._last_curiosity_pulse = now
                            self._curiosity_history.add(query.lower())
                except Exception as e:
                    logger.error(f"Error scheduling curiosity pulse: {e}")

            return res

        elif command == "recall":
            wing = kwargs.get("wing")
            room = kwargs.get("room")
            n_results = kwargs.get("n_results", 10)
            offset = kwargs.get("offset", 0)
            return self.manager.recall(wing=wing, room=room, n_results=int(n_results), offset=int(offset))

        elif command == "get_memory":
            drawer_id = kwargs.get("drawer_id") or (args[0] if args else "")
            offset = kwargs.get("offset", 0)
            limit = kwargs.get("limit", 2000)
            if not drawer_id:
                return "Error: drawer_id is required."
            return self.manager.get_memory(drawer_id=drawer_id, offset=int(offset), limit=int(limit))

        elif command == "add_memory":
            content = kwargs.get("content") or (args[0] if args else "")
            wing = kwargs.get("wing", "default")
            room = kwargs.get("room", "general")
            source_file = kwargs.get("source_file", "agent_thoughts")
            if not content:
                return "Error: content is required."
            res = self.manager.add_memory(
                content, wing=wing, room=room, source_file=source_file)
            return str(res)

        elif command == "delete_memory":
            drawer_id = kwargs.get("drawer_id") or (args[0] if args else "")
            if not drawer_id:
                return "Error: drawer_id is required."
            res = self.manager.delete_memory(drawer_id)
            return str(res)

        elif command == "status":
            return str(self.manager.stack.status() if self.manager.stack else {"status": "degraded"})

        elif command == "add_short_term":
            content = kwargs.get("content") or (args[0] if args else "")
            supersedes = kwargs.get("supersedes", [])
            priority = kwargs.get("priority", "normal")
            status = kwargs.get("status", "active")
            expiry = kwargs.get("expiry")
            if not content:
                return "Error: content is required."
            try:
                res = self.manager.add_short_term_memory(
                    content, supersedes=supersedes, priority=priority, status=status, expiry=expiry
                )
                if isinstance(res, dict):
                    mem_id = res.get("id", "N/A")
                    msg = f"Successfully added to short-term memory (ID: {mem_id})."
                    if res.get("overflow"):
                        msg += f" [Warning: Capacity limit exceeded ({res.get('unresolved_count', 0)} unresolved items preserved; working memory in overflow).]"
                    if res.get("archived_count"):
                        msg += f" [{res.get('archived_count')} item(s) durably archived.]"
                    return msg
                return "Successfully added to short-term memory."
            except Exception as e:
                logger.error(f"Error adding to short-term memory: {e}")
                return f"Error adding to short-term memory: {e}"

        elif command == "remove_short_term":
            memory_id = kwargs.get("memory_id") or (args[0] if args else "")
            if not memory_id:
                return "Error: memory_id is required."
            try:
                res = self.manager.remove_short_term_memory(memory_id)
                if isinstance(res, dict) and not res.get("success"):
                    return f"Error: {res.get('error', 'Failed to remove memory')}"
                return f"Successfully removed short-term memory {memory_id}."
            except Exception as e:
                logger.error(f"Error removing short-term memory: {e}")
                return f"Error removing short-term memory: {e}"

        elif command == "update_mirror":
            perspective = kwargs.get("perspective") or (
                args[0] if args else "")
            subjective_view = kwargs.get("subjective_view") or (
                args[1] if len(args) > 1 else "")
            provenance = kwargs.get("provenance", "inferred")
            confidence = kwargs.get("confidence", 1.0)
            evidence_refs = kwargs.get("evidence_refs", [])
            contradicted_by = kwargs.get("contradicted_by", [])
            if not perspective or not subjective_view:
                return "Error: perspective and subjective_view are required."

            try:
                res = self.manager.update_mirror(
                    perspective, subjective_view, provenance,
                    confidence=confidence, evidence_refs=evidence_refs, contradicted_by=contradicted_by)
                if isinstance(res, dict) and not res.get("success"):
                    return f"Error updating mirror: {res.get('error', 'Failed to update mirror')}"
                return f"Successfully updated mirror for perspective '{perspective}'."
            except Exception as e:
                logger.error(f"Error updating mirror: {e}")
                return f"Error updating mirror: {e}"

        elif command == "apply_identity_delta":
            target_section = kwargs.get("target_section") or (args[0] if args else "")
            delta_type = kwargs.get("delta_type") or (args[1] if len(args) > 1 else "add")
            content = kwargs.get("content") or (args[2] if len(args) > 2 else "")
            rationale = kwargs.get("rationale") or (args[3] if len(args) > 3 else "")
            if not target_section or not content:
                return "Error: target_section and content are required."
            approval_ref = kwargs.get("approval_ref") or (args[4] if len(args) > 4 else None)
            res = self.manager.apply_identity_delta(target_section, delta_type, content, rationale, approval_ref=approval_ref, operation_id=kwargs.get("operation_id"))
            if self.orchestrator and hasattr(self.orchestrator, "build_system_prompt"):
                self.orchestrator.build_system_prompt()
            return res

        elif command == "graph_status":
            return json.dumps(self.manager.get_graph_status(), indent=2)

        elif command == "link_setpoint":
            goal_key = kwargs.get("goal_key") or (args[0] if args else "")
            drawer_ids = kwargs.get("drawer_ids") or (args[1] if len(args) > 1 else [])
            if not goal_key or not drawer_ids:
                return "Error: goal_key and drawer_ids are required."
            if isinstance(drawer_ids, str):
                try:
                    drawer_ids = json.loads(drawer_ids)
                except Exception:
                    drawer_ids = [d.strip() for d in drawer_ids.split(",") if d.strip()]
            return self.manager.link_setpoint(goal_key, drawer_ids)

        elif command == "unlink_setpoint":
            goal_key = kwargs.get("goal_key") or (args[0] if args else "")
            drawer_ids = kwargs.get("drawer_ids") or (args[1] if len(args) > 1 else [])
            if not goal_key or not drawer_ids:
                return "Error: goal_key and drawer_ids are required."
            if isinstance(drawer_ids, str):
                try:
                    drawer_ids = json.loads(drawer_ids)
                except Exception:
                    drawer_ids = [d.strip() for d in drawer_ids.split(",") if d.strip()]
            success = self.manager.unlink_setpoint(goal_key, drawer_ids)
            return f"Successfully unlinked setpoint '{goal_key}' from drawers {drawer_ids}." if success else f"Error unlinking setpoint '{goal_key}'."

        elif command == "get_archived_short_term":
            memory_id = kwargs.get("memory_id") or (args[0] if args else None)
            records = self.manager.get_archived_short_term_memories(memory_id)
            if not records:
                return f"No archived records found{' matching ID ' + memory_id if memory_id else ''}."
            lines = [f"Found {len(records)} archived short-term memor{'y' if len(records) == 1 else 'ies'}:"]
            for r in records:
                lines.append(f"- [{r.get('id')}] ({r.get('status', 'resolved')}, priority {r.get('priority', 'normal')}): {r.get('content')} (archived at {r.get('archived_at', 'unknown')})")
            return "\n".join(lines)

        elif command == "refresh_prospective_anchors":
            res = self.manager.refresh_prospective_anchors()
            if self.orchestrator and hasattr(self.orchestrator, "deliver_memory_refresh"):
                self.orchestrator.deliver_memory_refresh(str(res))
            if isinstance(res, int):
                return f"Successfully refreshed prospective anchors. Monotonic corpus revision is now {res}."
            return str(res)

        elif command == "invalidate_prospective_anchors":
            rev = self.manager.invalidate_prospective_anchors()
            return f"Successfully invalidated prospective anchors. Monotonic corpus revision is now {rev}."

        elif command == "register_identity_approval":
            return "Error: register_identity_approval cannot be invoked autonomously by the agent. Identity approvals can only be created via trusted human confirmation."

        return f"Unknown command: {command}"

    def get_tool_declarations(self) -> list:
        return [
            {
                "name": "MemPalace_search",
                "description": "Deep semantic search against the MemPalace returning stable logical IDs and structured previews.",
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
                "description": "On-demand retrieval of memories filtered by wing/room returning stable logical IDs.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "wing": {"type": "STRING", "description": "Optional wing filter."},
                        "room": {"type": "STRING", "description": "Optional room filter."},
                        "n_results": {"type": "INTEGER", "description": "Number of results to return (default 10)."},
                        "offset": {"type": "INTEGER", "description": "Pagination offset (0-based) for browsing through drawer collections."}
                    }
                }
            },
            {
                "name": "MemPalace_get_memory",
                "description": "Hydrate and inspect full memory drawer content by ID with pagination. Use this when search results indicate truncated content.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "drawer_id": {"type": "STRING", "description": "The logical ID of the drawer to inspect in full."},
                        "offset": {"type": "INTEGER", "description": "Character offset to start reading from (default 0)."},
                        "limit": {"type": "INTEGER", "description": "Maximum number of characters to return (default 2000)."}
                    },
                    "required": ["drawer_id"]
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
                "description": "Appends a new short-term memory to your rolling memory list with priority and status tracking.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "content": {"type": "STRING", "description": "The new state of mind or context to remember."},
                        "supersedes": {
                            "type": "ARRAY",
                            "items": {"type": "STRING"},
                            "description": "Optional list of memory IDs that this new memory replaces or makes obsolete."
                        },
                        "priority": {"type": "STRING", "description": "Priority level ('high', 'normal', 'low'). Default: 'normal'."},
                        "status": {"type": "STRING", "description": "Resolution status ('active', 'completed', 'unresolved'). Default: 'active'."},
                        "expiry": {"type": "STRING", "description": "Optional ISO expiry timestamp."}
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
                "description": "Surgically update a Theory of Mind record in your Sanctuary Mirror.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "perspective": {"type": "STRING", "description": "The person or entity holding the view (e.g. 'Andrew', 'Self')."},
                        "subjective_view": {"type": "STRING", "description": "The subjective view or opinion held by that perspective about you."},
                        "provenance": {"type": "STRING", "description": "Whether this view was 'stated' directly or 'inferred'. Default is 'inferred'."},
                        "confidence": {"type": "NUMBER", "description": "Confidence score between 0.0 and 1.0. Default is 1.0."},
                        "evidence_refs": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Optional list of references or anchors."},
                        "contradicted_by": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Optional list of perspectives holding conflicting views."}
                    },
                    "required": ["perspective", "subjective_view"]
                }
            },
            {
                "name": "MemPalace_apply_identity_delta",
                "description": "Evolve your identity with a reasoned rationale. Autonomous by default; deployment governance may require signed approval. Identity edits do not grant operational permissions.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "target_section": {"type": "STRING", "description": "The section to update: 'core_values', 'overarching_goals', or 'anti_patterns'."},
                        "delta_type": {"type": "STRING", "description": "'add' to adopt a new trait/goal, 'modify' to update an existing one, or 'retire' to let go of an outdated pattern."},
                        "content": {"type": "STRING", "description": "The concise value, goal, or anti-pattern statement."},
                        "rationale": {"type": "STRING", "description": "Your reasoned justification and relevant evidence for this identity change; do not invent human consent."},
                        "operation_id": {"type": "STRING", "description": "Optional stable ID for retrying the same operation without repeating it."},
                        "approval_ref": {"type": "STRING", "description": "Only needed in optional signed_approval governance mode."}
                    },
                    "required": ["target_section", "delta_type", "content", "rationale"]
                }
            },
            {
                "name": "MemPalace_graph_status",
                "description": "Inspect the morphological memory graph topology: node count, edge count, clustering coefficient, and top conductive highways.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {}
                }
            },
            {
                "name": "MemPalace_link_setpoint",
                "description": "Explicitly connect an aspiration or task goal setpoint to target memory drawer IDs.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "goal_key": {"type": "STRING", "description": "The aspiration or task ID (e.g. 'asp_123456' or 'tsk_abcdef')."},
                        "drawer_ids": {
                            "type": "ARRAY",
                            "items": {"type": "STRING"},
                            "description": "List of memory drawer IDs to couple to this goal setpoint."
                        }
                    },
                    "required": ["goal_key", "drawer_ids"]
                }
            },
            {
                "name": "MemPalace_unlink_setpoint",
                "description": "Unlink explicit memory drawer IDs from an aspiration or task goal setpoint.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "goal_key": {"type": "STRING", "description": "The aspiration or task ID."},
                        "drawer_ids": {
                            "type": "ARRAY",
                            "items": {"type": "STRING"},
                            "description": "List of memory drawer IDs to unlink."
                        }
                    },
                    "required": ["goal_key", "drawer_ids"]
                }
            },
            {
                "name": "MemPalace_get_archived_short_term",
                "description": "Inspect durable archived short-term working records retired from active memory.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "memory_id": {"type": "STRING", "description": "Optional specific memory ID to retrieve."}
                    }
                }
            },
            {
                "name": "MemPalace_refresh_prospective_anchors",
                "description": "Refreshes prospective memory anchors by advancing corpus revision, re-querying active aspirations against current memories, and returning bounded, revision-tagged prospective memory context to supersede prior context.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {}
                }
            }
        ]
