from typing import List, Dict, Any
from core.cerebrum import Tool


class SubagentSkill(Tool):
    name = "Subagent"
    icon = "🤖"
    color = "#E040FB"
    async_commands = ["spawn"]
    description = (
        "Delegate background tasks to LLM subagents. Subagents run in a separate thread, "
        "do not share context, and are not persistent. They default to cheap 'light' models. "
        "Use this to parallelize research, offload isolated thinking, or delegate scoped file tasks. "
        "When a subagent finishes, its response will be added to your context via a [System Feedback] message. "
        "Subagents only have access to DateTime/WebSearch tools, plus optional File_read/File_edit access "
        "strictly limited to paths you permit via allowed_files."
    )
    commands = ["spawn", "message", "dispose", "status"]

    def execute(self, command: str, *args, **kwargs) -> Any:
        if not self.orchestrator:
            return "Error: Orchestrator not found."

        is_low_token = self.orchestrator.settings_manager.get(
            "core.low-token-mode", False)
        if is_low_token:
            return "Error: Subagents are disabled when Low Token Mode is active."

        if command == "spawn":
            task = kwargs.get("task_description")
            model_tier = kwargs.get("model_tier", "light")
            allowed_files = kwargs.get("allowed_files")
            call_id = kwargs.get("_call_id")
            if not task:
                return "Error: task_description is required."
            return self.orchestrator.spawn_subagent(
                task, model_tier, call_id=call_id, allowed_files=allowed_files
            )

        elif command == "message":
            sid = kwargs.get("subagent_id")
            msg = kwargs.get("message")
            if not sid or not msg:
                return "Error: subagent_id and message are required."
            return self.orchestrator.message_subagent(sid, msg)

        elif command == "dispose":
            sid = kwargs.get("subagent_id")
            if not sid:
                return "Error: subagent_id is required."
            return self.orchestrator.dispose_subagent(sid)

        elif command == "status":
            return self.orchestrator.list_subagents()

        return f"Unknown command: {command}"

    def get_tool_declarations(self) -> List[Dict[str, Any]]:
        is_low_token = self.orchestrator.settings_manager.get(
            "core.low-token-mode", False) if hasattr(self, 'orchestrator') and self.orchestrator else False
        if is_low_token:
            return []

        return [
            {
                "name": "Subagent_spawn",
                "description": (
                    "Spawn a new background subagent to perform a task. Returns a subagent_id. "
                    "Use model_tier 'light' (default) for most tasks, or 'standard' for complex reasoning. "
                    "Optionally pass allowed_files to grant File_read and File_edit access strictly to specific "
                    "file paths or directories; all other files remain blocked."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "task_description": {
                            "type": "STRING",
                            "description": "The detailed task for the subagent to perform."
                        },
                        "model_tier": {
                            "type": "STRING",
                            "description": "Either 'light' or 'standard'. Defaults to 'light'.",
                            "enum": ["light", "standard"]
                        },
                        "allowed_files": {
                            "type": "ARRAY",
                            "items": {
                                "type": "STRING"
                            },
                            "description": (
                                "Optional list of file or directory paths the subagent is permitted to read and edit "
                                "(absolute, relative to ~/Documents, or starting with '~'). Listing a directory grants "
                                "recursive access to files inside it. If omitted or empty, the subagent cannot access files."
                            )
                        }
                    },
                    "required": ["task_description"]
                }
            },
            {
                "name": "Subagent_message",
                "description": "Send a follow-up message or re-prompt an idle subagent.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "subagent_id": {
                            "type": "STRING",
                            "description": "The ID of the active subagent."
                        },
                        "message": {
                            "type": "STRING",
                            "description": "The message to send."
                        }
                    },
                    "required": ["subagent_id", "message"]
                }
            },
            {
                "name": "Subagent_dispose",
                "description": "Manually dispose of a subagent and free its resources. Subagents automatically dispose after 5 minutes of idle time.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "subagent_id": {
                            "type": "STRING",
                            "description": "The ID of the active subagent."
                        }
                    },
                    "required": ["subagent_id"]
                }
            },
            {
                "name": "Subagent_status",
                "description": "List all active subagents.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {}
                }
            }
        ]
