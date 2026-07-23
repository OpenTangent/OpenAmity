
import datetime
from typing import List, Dict, Any
from core.cerebrum import Tool

class DateTimeSkill(Tool):
    name = "DateTime"
    description = "Provides date and time information."
    commands = ["datetime"]

    def get_tool_declarations(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "DateTime_datetime",
                "description": "Returns the current local date and time.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {}
                }
            }
        ]

    def execute(self, command: str, *args, **kwargs) -> str:
        if command == "datetime":
            return f"Current date and time: {datetime.datetime.now()}"
        return f"Unknown command: {command}"
