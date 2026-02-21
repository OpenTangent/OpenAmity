
import datetime
from core.cerebrum import Skill

class DateTimeSkill(Skill):
    name = "DateTime"
    description = "Provides date and time information."
    commands = ["date", "time"]

    def execute(self, command: str, *args, **kwargs) -> str:
        if command == "date":
            return f"Current date: {datetime.date.today()}"
        elif command == "time":
            return f"Current time: {datetime.datetime.now().strftime('%H:%M:%S')}"
        return f"Unknown command: {command}"
