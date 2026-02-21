
import importlib
import inspect
import os
import pkgutil
from typing import Dict, List, Any

class Skill:
    """Base class for Amity 4 skills."""
    name: str = "BaseSkill"
    description: str = "A generic skill."
    commands: List[str] = [] # List of command names this skill handles
    
    def execute(self, command: str, *args, **kwargs) -> str:
        """Executes a command provided by the skill."""
        raise NotImplementedError

    def get_manual_entry(self) -> str:
        """Returns the documentation entry for the Amity Manual."""
        return f"- **{self.name}**: {self.description}
  Commands: {', '.join(self.commands)}"

class Cerebrum:
    """The brain center that manages skills and their execution."""
    def __init__(self, skills_dir="src/skills"):
        self.skills: Dict[str, Skill] = {}
        self.skills_dir = skills_dir
        self.load_skills()

    def load_skills(self):
        """Discovers and loads skills from the skills directory."""
        if not os.path.exists(self.skills_dir):
            os.makedirs(self.skills_dir)
            
        import sys
        sys.path.append(os.getcwd()) # Ensure root is in path
        
        try:
            for filename in os.listdir(self.skills_dir):
                if filename.endswith(".py") and not filename.startswith("__"):
                    module_name = f"skills.{filename[:-3]}"
                    try:
                        module = importlib.import_module(module_name)
                        for name, obj in inspect.getmembers(module):
                            if inspect.isclass(obj) and issubclass(obj, Skill) and obj is not Skill:
                                skill_instance = obj()
                                self.register_skill(skill_instance)
                                print(f"Loaded skill: {skill_instance.name}")
                    except Exception as e:
                        print(f"Failed to load skill module {module_name}: {e}")
        except Exception as e:
            print(f"Error scanning skills directory: {e}")

    def register_skill(self, skill: Skill):
        self.skills[skill.name] = skill

    def get_amity_manual(self) -> str:
        """Generates the Amity Manual from loaded skills."""
        manual = "## Amity 4 Manual

### Available Skills
"
        for skill in self.skills.values():
            manual += skill.get_manual_entry() + "
"
        return manual

    def execute_command(self, skill_name: str, command: str, *args, **kwargs) -> str:
        """Executes a command on a specific skill."""
        if skill_name in self.skills:
            return self.skills[skill_name].execute(command, *args, **kwargs)
        return f"Error: Skill '{skill_name}' not found."
