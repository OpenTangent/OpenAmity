
import importlib
import inspect
import os
import pkgutil
import logging
from typing import Dict, List, Any

class Tool:
    """Base class for Open Amity tools."""
    name: str = "BaseSkill"
    description: str = "A generic tool."
    commands: List[str] = [] # List of command names this tool handles
    
    def execute(self, command: str, *args, **kwargs) -> str:
        """Executes a command provided by the tool."""
        raise NotImplementedError

    def get_manual_entry(self) -> str:
        """Returns the documentation entry for the Agent Manual."""
        return f"- **{self.name}**: {self.description}\n  Commands: {', '.join(self.commands)}"

    def get_tool_declarations(self) -> List[Dict[str, Any]]:
        """Returns a list of Google GenAI Tool dictionaries."""
        return []

class Cerebrum:
    """The brain center that manages tools and their execution."""
    def __init__(self, orchestrator=None, settings_manager=None, skills_dir="src/tools", manual_path="src/memory/agent_manual.md"):
        self.orchestrator = orchestrator
        self.settings_manager = settings_manager
        self.tools: Dict[str, Tool] = {}
        self.skills_dir = skills_dir
        self.manual_path = os.path.abspath(manual_path)
        self.load_skills()

    def load_skills(self):
        """Discovers and loads tools from the tools directory."""
        if not os.path.exists(self.skills_dir):
            os.makedirs(self.skills_dir)
            
        import sys
        sys.path.append(os.getcwd()) # Ensure root is in path
        
        try:
            for filename in os.listdir(self.skills_dir):
                if filename.endswith(".py") and not filename.startswith("__"):
                    module_name = f"tools.{filename[:-3]}"
                    try:
                        module = importlib.import_module(module_name)
                        for name, obj in inspect.getmembers(module):
                            if inspect.isclass(obj) and issubclass(obj, Tool) and obj is not Tool:
                                skill_name = getattr(obj, "name", "BaseSkill")
                                
                                is_enabled = True
                                if self.settings_manager:
                                    is_enabled = self.settings_manager.get(f"core.tools.{skill_name.lower()}", True)
                                    
                                if is_enabled:
                                    skill_instance = obj()
                                    skill_instance.orchestrator = self.orchestrator
                                    self.register_skill(skill_instance)
                                    logging.info(f"Loaded tool: {skill_instance.name}")
                                else:
                                    logging.info(f"Tool {skill_name} is disabled in settings.")
                    except Exception as e:
                        logging.error(f"Failed to load tool module {module_name}: {e}", exc_info=True)
        except Exception as e:
            logging.error(f"Error scanning tools directory: {e}", exc_info=True)

    def register_skill(self, tool: Tool):
        self.tools[tool.name] = tool

    def get_agent_manual(self) -> str:
        """Generates the Agent Manual from loaded tools and the manual file."""
        manual = ""
        if os.path.exists(self.manual_path):
            try:
                with open(self.manual_path, 'r') as f:
                    manual = f.read() + "\n\n"
            except Exception as e:
                logging.error(f"Error loading manual file: {e}", exc_info=True)

        manual += "### Available Tools\n"
        for tool in self.tools.values():
            manual += tool.get_manual_entry() + "\n"
        return manual

    def get_all_tool_declarations(self) -> list:
        """Returns all registered tool declarations formatted for the Gemini API."""
        tools = []
        for tool in self.tools.values():
            tools.extend(tool.get_tool_declarations())
        return tools

    def execute_command(self, skill_name: str, command: str, *args, **kwargs) -> str:
        """Executes a command on a specific tool."""
        if skill_name in self.tools:
            return self.tools[skill_name].execute(command, *args, **kwargs)
        return f"Error: Tool '{skill_name}' not found."

    def execute_tool_call(self, function_name: str, args: dict) -> str:
        """Routes a GenAI tool call directly to the correct skill."""
        if "_" in function_name:
            skill_name, command = function_name.split("_", 1)
            return self.execute_command(skill_name, command, **args)
        return f"Error: Invalid tool name format {function_name}"

    def reload_skills(self):
        """Reloads skills dynamically, shutting down disabled ones and starting enabled ones."""
        if not os.path.exists(self.skills_dir):
            return
            
        import sys
        if os.getcwd() not in sys.path:
            sys.path.append(os.getcwd())
            
        try:
            discovered_tools = {}
            
            for filename in os.listdir(self.skills_dir):
                if filename.endswith(".py") and not filename.startswith("__"):
                    module_name = f"tools.{filename[:-3]}"
                    try:
                        module = importlib.import_module(module_name)
                        importlib.reload(module)
                        for name, obj in inspect.getmembers(module):
                            if inspect.isclass(obj) and issubclass(obj, Tool) and obj is not Tool:
                                skill_name = getattr(obj, "name", "BaseSkill")
                                discovered_tools[skill_name] = obj
                    except Exception as e:
                        logging.error(f"Failed to inspect tool module {module_name}: {e}", exc_info=True)
                        
            for skill_name, obj in discovered_tools.items():
                is_enabled = True
                if self.settings_manager:
                    is_enabled = self.settings_manager.get(f"core.tools.{skill_name.lower()}", True)
                    
                if is_enabled and skill_name not in self.tools:
                    skill_instance = obj()
                    skill_instance.orchestrator = self.orchestrator
                    self.register_skill(skill_instance)
                    logging.info(f"Dynamically enabled tool: {skill_name}")
                elif not is_enabled and skill_name in self.tools:
                    logging.info(f"Dynamically disabling tool: {skill_name}")
                    tool = self.tools.pop(skill_name)
                    if hasattr(tool, "shutdown"):
                        try:
                            tool.shutdown()
                        except Exception as e:
                            logging.error(f"Error shutting down tool {skill_name}: {e}", exc_info=True)
                            
        except Exception as e:
            logging.error(f"Error dynamically reloading tools: {e}", exc_info=True)

    def shutdown(self):
        """Cleanly shuts down all loaded tools."""
        logging.info("Cerebrum: Shutting down tools...")
        for skill_name, tool in self.tools.items():
            if hasattr(tool, "shutdown"):
                try:
                    tool.shutdown()
                except Exception as e:
                    logging.error(f"Error shutting down tool {skill_name}: {e}", exc_info=True)
