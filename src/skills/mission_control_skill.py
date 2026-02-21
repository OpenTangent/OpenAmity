import sqlite3
import os
from core.cerebrum import Skill
from core.mission_control import MissionControl

class MissionControlSkill(Skill):
    name = "MissionControl"
    description = "Manage goals, objectives, and tasks."
    commands = ["add_goal", "update_status", "list_goals"]

    def __init__(self):
        self.mc = MissionControl()

    def execute(self, command: str, *args, **kwargs) -> str:
        if command == "add_goal":
            if len(args) < 2:
                return "Usage: add_goal <type> <description> [parent_id]"
            
            goal_type = args[0]
            # Heuristic: if last arg is digit, treat as parent_id
            parent_id = None
            if args[-1].isdigit():
                parent_id = int(args[-1])
                description = " ".join(args[1:-1])
            else:
                description = " ".join(args[1:])
            
            goal_id = self.mc.add_goal(goal_type, description, parent_id)
            return f"Goal added: [{goal_id}] {description} ({goal_type})"

        elif command == "update_status":
            if len(args) < 2:
                return "Usage: update_status <id> <status>"
            try:
                goal_id = int(args[0])
                status = args[1]
                self.mc.update_status(goal_id, status)
                return f"Goal {goal_id} status updated to {status}."
            except ValueError:
                return "Invalid ID or status."

        elif command == "list_goals":
            goal_type = args[0] if args else None
            status = args[1] if len(args) > 1 else "in_progress"
            # If type is 'all', set to None
            if goal_type == 'all':
                goal_type = None
                
            goals = self.mc.get_goals(type=goal_type, status=status)
            if not goals:
                return "No goals found."
            
            result = f"Goals ({goal_type or 'all'} - {status}):\n"
            for g in goals:
                result += f"[{g['id']}] {g['type']}: {g['description']}\n"
            return result.strip()

        return f"Unknown command: {command}"
