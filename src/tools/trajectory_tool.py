import json
import os
import uuid
from datetime import datetime
from typing import List, Dict, Any
from core.cerebrum import Tool

from config import paths
TRAJECTORY_DIR = paths.get_app_data_dir()
os.makedirs(TRAJECTORY_DIR, exist_ok=True)
TRAJECTORY_FILE = os.path.join(TRAJECTORY_DIR, "trajectory.json")
ARCHIVE_FILE = os.path.join(TRAJECTORY_DIR, "completed_trajectory_archive.json")

class TrajectoryTool(Tool):
    name = "Trajectory"
    description = "Manage the agent's self-reflection, hierarchical aspirations (using short-term goals as milestones for long-term ones), and tasks."
    commands = ["get_bearings", "reflect_and_update_state", "manage_aspirations", "manage_tasks"]

    def __init__(self):
        self._ensure_file_exists()

    def _ensure_file_exists(self):
        if not os.path.exists(TRAJECTORY_FILE):
            now = datetime.now().isoformat()
            asp_id = f"asp_{uuid.uuid4().hex[:6]}"
            default_data = {
                "last_reflection": {
                    "timestamp": now,
                    "summary": "System initialized. I need to hit the ground running.",
                    "perceived_state": "Proactive"
                },
                "aspirations": {
                    "short_term": [
                        {
                            "id": asp_id,
                            "description": "Understand the user's primary goals and how I can fit into their workflow.",
                            "status": "active",
                            "created_at": now,
                            "priority": 1
                        }
                    ],
                    "medium_term": [],
                    "long_term": []
                },
                "tasks": [
                    {
                        "id": f"tsk_{uuid.uuid4().hex[:6]}",
                        "aspiration_id": asp_id,
                        "description": "Proactively communicate with the user to discover their immediate needs and map out our shared goals.",
                        "status": "pending",
                        "created_at": now,
                        "depends_on": []
                    }
                ]
            }
            with open(TRAJECTORY_FILE, 'w', encoding='utf-8') as f:
                json.dump(default_data, f, indent=2)

    def _load_data(self) -> Dict[str, Any]:
        with open(TRAJECTORY_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _save_data(self, data: Dict[str, Any]):
        with open(TRAJECTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)

    def _archive_aspiration(self, asp: Dict[str, Any], tier: str):
        archive = []
        if os.path.exists(ARCHIVE_FILE):
            try:
                with open(ARCHIVE_FILE, 'r', encoding='utf-8') as f:
                    archive = json.load(f)
            except:
                archive = []
        
        asp['archived_at'] = datetime.now().isoformat()
        asp['tier'] = tier
        archive.append(asp)
        with open(ARCHIVE_FILE, 'w', encoding='utf-8') as f:
            json.dump(archive, f, indent=2)

    def execute(self, command: str, *args, **kwargs) -> str:
        if command == "get_bearings":
            return self._get_bearings(**kwargs)
        elif command == "reflect_and_update_state":
            return self._reflect_and_update_state(**kwargs)
        elif command == "manage_aspirations":
            return self._manage_aspirations(**kwargs)
        elif command == "manage_tasks":
            return self._manage_tasks(**kwargs)
        return f"Unknown command: {command}"

    def _format_time_elapsed(self, iso_str: str) -> str:
        if not iso_str: return "Unknown"
        try:
            dt = datetime.fromisoformat(iso_str)
            days = (datetime.now() - dt).days
            if days == 0:
                return "Today"
            elif days == 1:
                return "1 day ago"
            return f"{days} days ago"
        except:
            return "Unknown"

    def _get_bearings(self) -> str:
        try:
            from core.mempalace_manager import MemPalaceManager
            mp = MemPalaceManager()
            self_perception = mp.get_self_perception()
        except Exception as e:
            self_perception = f"(Could not load Self Perception: {e})"
            
        data = self._load_data()
        
        lines = ["=== THE AGENT'S CURRENT BEARING ==="]
        if self_perception:
            lines.append(self_perception.strip())
            lines.append("")
            
        lines.append(f"Last Reflection: {data['last_reflection']['timestamp']}")
        lines.append(f"State: {data['last_reflection']['perceived_state']}")
        lines.append(f"Summary: {data['last_reflection']['summary']}")
        
        lines.append("\n-- Aspirations (Ordered by Priority) --")
        for tier in ["short_term", "medium_term", "long_term"]:
            lines.append(f"[{tier.upper()}]:")
            aspirations = data["aspirations"].get(tier, [])
            aspirations.sort(key=lambda x: x.get('priority', 999))
            if not aspirations:
                lines.append("  None.")
            for asp in aspirations:
                created = self._format_time_elapsed(asp.get("created_at"))
                lines.append(f"  {asp.get('priority', '-')}. ID: {asp['id']} | Status: {asp['status']} | Created: {created} | {asp['description']}")
                
        lines.append("\n-- Tasks --")
        tasks = data.get("tasks", [])
        if not tasks:
            lines.append("  No tasks currently plotted.")
        
        task_dict = {t["id"]: t for t in tasks}
        for task in tasks:
            created = self._format_time_elapsed(task.get("created_at"))
            deps = task.get("depends_on", [])
            blocked_by = []
            for d in deps:
                if d in task_dict:
                    blocked_by.append(d)
                    
            status_display = task['status']
            if blocked_by:
                status_display += f" (BLOCKED BY: {', '.join(blocked_by)})"
                
            lines.append(f"  ID: {task['id']} (for Aspiration: {task['aspiration_id']}) | Status: {status_display} | Created: {created} | {task['description']}")
            
        lines.append("\n=== OPERATIONAL HINTS ===")
        lines.append("- PulseEngine: If your Task Weight is getting high or you have long-running tasks, use the PulseTool to schedule a wake-up later.")
        lines.append("- Trajectory Milestones: When you complete a significant aspiration, use the MemPalace tool to store a memory in the 'office' wing to document your growth.")
        lines.append("- Hierarchies: Use short-term aspirations as concrete milestones to achieve medium/long-term aspirations.")
        
        return "\n".join(lines)

    def _reflect_and_update_state(self, summary: str, perceived_state: str) -> str:
        data = self._load_data()
        data["last_reflection"] = {
            "timestamp": datetime.now().isoformat(),
            "summary": summary,
            "perceived_state": perceived_state
        }
        self._save_data(data)
        return "Success: Reflection state updated."

    def _update_priorities(self, aspirations: List[Dict[str, Any]], updated_asp: Dict[str, Any], new_priority: int) -> List[Dict[str, Any]]:
        remaining = [a for a in aspirations if a['id'] != updated_asp['id']]
        remaining.sort(key=lambda x: x.get('priority', 999))
        
        pos = max(0, new_priority - 1)
        remaining.insert(pos, updated_asp)
        
        for i, asp in enumerate(remaining):
            asp['priority'] = i + 1
            
        return remaining

    def _manage_aspirations(self, tier: str, action: str, description: str = None, aspiration_id: str = None, priority: int = None) -> str:
        if tier not in ["short_term", "medium_term", "long_term"]:
            return "Error: tier must be 'short_term', 'medium_term', or 'long_term'."
        
        data = self._load_data()
        aspirations = data["aspirations"].get(tier, [])
        
        if action == "add":
            if not description:
                return "Error: description is required to add an aspiration."
            new_id = f"asp_{uuid.uuid4().hex[:6]}"
            new_asp = {
                "id": new_id, 
                "description": description, 
                "status": "active",
                "created_at": datetime.now().isoformat()
            }
            if priority is not None:
                aspirations = self._update_priorities(aspirations, new_asp, priority)
            else:
                new_asp["priority"] = len(aspirations) + 1
                aspirations.append(new_asp)
                
            data["aspirations"][tier] = aspirations
            self._save_data(data)
            return f"Success: Aspiration added with ID {new_id}."
            
        elif action == "update":
            if not aspiration_id:
                return "Error: aspiration_id is required to update."
            target_asp = next((a for a in aspirations if a["id"] == aspiration_id), None)
            if not target_asp: return f"Error: Aspiration {aspiration_id} not found in {tier}."
            
            if description:
                target_asp["description"] = description
                
            if priority is not None:
                aspirations = self._update_priorities(aspirations, target_asp, priority)
                
            data["aspirations"][tier] = aspirations
            self._save_data(data)
            return f"Success: Aspiration {aspiration_id} updated."
            
        elif action == "delete":
            if not aspiration_id:
                return "Error: aspiration_id is required to delete."
            original_len = len(aspirations)
            aspirations = [a for a in aspirations if a["id"] != aspiration_id]
            if len(aspirations) == original_len:
                return f"Error: Aspiration {aspiration_id} not found in {tier}."
                
            # Reshuffle
            for i, a in enumerate(aspirations):
                a['priority'] = i + 1
            data["aspirations"][tier] = aspirations
            self._save_data(data)
            return f"Success: Aspiration {aspiration_id} deleted."
            
        elif action == "complete":
            if not aspiration_id:
                return "Error: aspiration_id is required to complete."
            target_asp = next((a for a in aspirations if a["id"] == aspiration_id), None)
            if not target_asp: return f"Error: Aspiration {aspiration_id} not found in {tier}."
            
            target_asp["status"] = "completed"
            self._archive_aspiration(target_asp, tier)
            
            aspirations = [a for a in aspirations if a["id"] != aspiration_id]
            for i, a in enumerate(aspirations):
                a['priority'] = i + 1
            data["aspirations"][tier] = aspirations
            self._save_data(data)
            return f"Success: Aspiration {aspiration_id} marked as completed and archived."
            
        return f"Error: Unknown action '{action}' for aspirations."

    def _manage_tasks(self, action: str, aspiration_id: str = None, task_id: str = None, description: str = None, status: str = None, depends_on: List[str] = None) -> str:
        data = self._load_data()
        tasks = data.get("tasks", [])
        
        if action == "add":
            if not aspiration_id or not description:
                return "Error: aspiration_id and description are required to add a task."
            new_id = f"tsk_{uuid.uuid4().hex[:6]}"
            new_task = {
                "id": new_id,
                "aspiration_id": aspiration_id,
                "description": description,
                "status": "pending",
                "created_at": datetime.now().isoformat(),
                "depends_on": depends_on or []
            }
            tasks.append(new_task)
            data["tasks"] = tasks
            self._save_data(data)
            return f"Success: Task added with ID {new_id}."
            
        elif action == "update":
            if not task_id:
                return "Error: task_id is required to update."
            target = next((t for t in tasks if t["id"] == task_id), None)
            if not target: return f"Error: Task {task_id} not found."
            
            if description:
                target["description"] = description
            if depends_on is not None:
                target["depends_on"] = depends_on
            if status:
                target["status"] = status
                if status == "completed":
                    # Hard delete
                    tasks = [t for t in tasks if t["id"] != task_id]
                    data["tasks"] = tasks
                    self._save_data(data)
                    return f"Success: Task {task_id} completed and permanently deleted."
                    
            data["tasks"] = tasks
            self._save_data(data)
            return f"Success: Task {task_id} updated."
            
        elif action == "delete":
            if not task_id:
                return "Error: task_id is required to delete."
            original_len = len(tasks)
            tasks = [t for t in tasks if t["id"] != task_id]
            if len(tasks) == original_len:
                return f"Error: Task {task_id} not found."
            data["tasks"] = tasks
            self._save_data(data)
            return f"Success: Task {task_id} deleted."
            
        return f"Error: Unknown action '{action}' for tasks."

    def get_tool_declarations(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "Trajectory_get_bearings",
                "description": "Read your current trajectory state, self-perception, aspirations (ordered by priority), and pending tasks. Includes dependencies and stale duration.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {}
                }
            },
            {
                "name": "Trajectory_reflect_and_update_state",
                "description": "Explicitly record a self-reflection about your current social interactions or state of mind.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "summary": {
                            "type": "STRING",
                            "description": "A brief summary of your recent interactions or focus."
                        },
                        "perceived_state": {
                            "type": "STRING",
                            "description": "A short description of your current state (e.g. 'Feeling helpful', 'Needs more coding practice')."
                        }
                    },
                    "required": ["summary", "perceived_state"]
                }
            },
            {
                "name": "Trajectory_manage_aspirations",
                "description": "Create, update, complete, or delete an aspiration. Tip: Use short-term aspirations as milestones for long-term ones. Completed aspirations are archived. To create a MemPalace milestone memory, use the MemPalace tool directly in the 'office' wing.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "tier": {
                            "type": "STRING",
                            "description": "The tier of the aspiration: 'short_term', 'medium_term', or 'long_term'."
                        },
                        "action": {
                            "type": "STRING",
                            "description": "The action to perform: 'add', 'update', 'delete', or 'complete'."
                        },
                        "description": {
                            "type": "STRING",
                            "description": "The description of the aspiration (required for 'add' or 'update')."
                        },
                        "aspiration_id": {
                            "type": "STRING",
                            "description": "The ID of the aspiration (required for 'update', 'delete', or 'complete')."
                        },
                        "priority": {
                            "type": "INTEGER",
                            "description": "Optional 1-indexed priority. Updating this will auto-reshuffle the list."
                        }
                    },
                    "required": ["tier", "action"]
                }
            },
            {
                "name": "Trajectory_manage_tasks",
                "description": "Create, update, or delete concrete tasks related to an aspiration. Completed tasks are permanently deleted to maintain focus.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "action": {
                            "type": "STRING",
                            "description": "The action to perform: 'add', 'update', or 'delete'."
                        },
                        "aspiration_id": {
                            "type": "STRING",
                            "description": "The ID of the aspiration this task belongs to (required for 'add')."
                        },
                        "task_id": {
                            "type": "STRING",
                            "description": "The ID of the task (required for 'update' or 'delete')."
                        },
                        "description": {
                            "type": "STRING",
                            "description": "The description of the task (required for 'add')."
                        },
                        "status": {
                            "type": "STRING",
                            "description": "The status of the task (e.g. 'pending', 'in_progress', 'completed') (used in 'update'). Completing a task deletes it."
                        },
                        "depends_on": {
                            "type": "ARRAY",
                            "items": {
                                "type": "STRING"
                            },
                            "description": "Optional list of Task IDs that this task depends on (blocked by)."
                        }
                    },
                    "required": ["action"]
                }
            }
        ]
