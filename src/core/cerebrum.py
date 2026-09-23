
import importlib
import inspect
import os
import logging
import threading
from typing import Dict, List, Any


class Tool:
    """Base class for Open Amity tools."""
    name: str = "BaseSkill"
    description: str = "A generic tool."
    commands: List[str] = []  # List of command names this tool handles
    icon: str = "🔧"
    color: str = "#888888"
    async_commands: List[str] = []

    _tool_classes: Dict[str, Any] = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if hasattr(cls, "name") and cls.name != "BaseSkill":
            Tool._tool_classes[cls.name] = cls
            Tool._tool_classes[cls.__name__] = cls
            # Register stripped versions (e.g. PulseTool -> Pulse)
            clean_name = cls.name
            if clean_name.endswith("Tool"):
                Tool._tool_classes[clean_name[:-4]] = cls
            elif clean_name.endswith("Skill"):
                Tool._tool_classes[clean_name[:-5]] = cls

    @classmethod
    def discover_tool_classes(cls):
        """Discovers tool classes from the tools directory if not yet loaded."""
        src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        tools_dir = os.path.join(src_dir, "tools")
        if not os.path.exists(tools_dir):
            return
        import sys
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)
        for filename in os.listdir(tools_dir):
            if filename.endswith(".py") and not filename.startswith("__"):
                module_name = f"tools.{filename[:-3]}"
                try:
                    importlib.import_module(module_name)
                except Exception:
                    pass

    @classmethod
    def get_tool_class(cls, name: str) -> Any:
        if not name:
            return None
        clean = name
        if clean.startswith("tool."):
            clean = clean[5:]
        if not cls._tool_classes:
            cls.discover_tool_classes()
        if clean in cls._tool_classes:
            return cls._tool_classes[clean]
        for k, v in cls._tool_classes.items():
            if k.lower() == clean.lower():
                return v
        return None

    @classmethod
    def get_tool_color(cls, name: str) -> str:
        tool_cls = cls.get_tool_class(name)
        if tool_cls and hasattr(tool_cls, "color"):
            return tool_cls.color
        return "#888888"

    @classmethod
    def get_tool_icon(cls, name: str) -> str:
        tool_cls = cls.get_tool_class(name)
        if tool_cls and hasattr(tool_cls, "icon"):
            return tool_cls.icon
        return "🔧"

    def is_command_async(self, command: str, args: dict = None) -> bool:
        """Determines if a given command is executed as a background process."""
        return command in self.async_commands

    def __init__(self, orchestrator=None):
        self.orchestrator = orchestrator

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
        self._tools_lock = threading.RLock()
        self.tools: Dict[str, Tool] = {}
        self.skills_dir = skills_dir
        self.manual_path = os.path.abspath(manual_path)
        self.load_skills()

    def load_skills(self):
        """Discovers and loads tools from the tools directory."""
        if not os.path.exists(self.skills_dir):
            os.makedirs(self.skills_dir)

        import sys
        src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)

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
                                    is_enabled = self.settings_manager.get(
                                        f"core.tools.{skill_name.lower()}", True)

                                if is_enabled:
                                    skill_instance = obj(
                                        orchestrator=self.orchestrator)
                                    self.register_skill(skill_instance)
                                    logging.info(
                                        f"Loaded tool: {skill_instance.name}")
                                else:
                                    logging.info(
                                        f"Tool {skill_name} is disabled in settings.")
                    except Exception as e:
                        logging.error(
                            f"Failed to load tool module {module_name}: {e}", exc_info=True)
        except Exception as e:
            logging.error(
                f"Error scanning tools directory: {e}", exc_info=True)

    def register_skill(self, tool: Tool):
        with self._tools_lock:
            self.tools[tool.name] = tool

    def get_agent_manual(self, include_tool_entries: bool = False) -> str:
        """Generates the Agent Manual from loaded tools and the manual file."""
        manual = ""
        if os.path.exists(self.manual_path):
            try:
                with open(self.manual_path, 'r', encoding='utf-8') as f:
                    manual = f.read() + "\n\n"
            except Exception as e:
                logging.error(f"Error loading manual file: {e}", exc_info=True)

        if include_tool_entries:
            manual += "### Available Tools\n"
            with self._tools_lock:
                for tool in self.tools.values():
                    manual += tool.get_manual_entry() + "\n"
        return manual

    def get_all_tool_declarations(self) -> list:
        """Returns all registered tool declarations formatted for the Gemini API."""
        tools = []
        with self._tools_lock:
            for tool in self.tools.values():
                tools.extend(tool.get_tool_declarations())
        return tools

    def execute_command(self, skill_name: str, command: str, *args, **kwargs) -> str:
        """Executes a command on a specific tool."""
        with self._tools_lock:
            tool = self.tools.get(skill_name)
            if not tool:
                for k, v in self.tools.items():
                    if k.lower() == skill_name.lower():
                        tool = v
                        break
        if tool:
            return tool.execute(command, *args, **kwargs)
        return f"Error: Tool '{skill_name}' not found."

    def execute_tool_call(self, function_name: str, args: dict, call_id: str = None) -> str:
        """Routes a GenAI tool call directly to the correct skill."""
        if "_" in function_name:
            skill_name, command = function_name.split("_", 1)
            call_kwargs = dict(args)
            try:
                with self._tools_lock:
                    tool = self.tools.get(skill_name)
                    if not tool:
                        for k, v in self.tools.items():
                            if k.lower() == skill_name.lower():
                                tool = v
                                break
                    if tool and call_id and tool.is_command_async(command, args):
                        call_kwargs["_call_id"] = call_id
                return self.execute_command(skill_name, command, **call_kwargs)
            except Exception as e:
                logging.getLogger("core.Cerebrum").exception(f"Error executing tool '{function_name}'")
                return f"Error executing '{function_name}': {type(e).__name__}: {str(e)}"
        return f"Error: Invalid tool name format {function_name}"

    def reload_skills(self):
        """Reloads skills dynamically, shutting down disabled ones and starting enabled ones."""
        if not os.path.exists(self.skills_dir):
            return

        import sys
        src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)

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
                        logging.error(
                            f"Failed to inspect tool module {module_name}: {e}", exc_info=True)

            with self._tools_lock:
                for skill_name, obj in discovered_tools.items():
                    is_enabled = True
                    if self.settings_manager:
                        is_enabled = self.settings_manager.get(
                            f"core.tools.{skill_name.lower()}", True)

                    if is_enabled and skill_name not in self.tools:
                        skill_instance = obj(orchestrator=self.orchestrator)
                        self.register_skill(skill_instance)
                        logging.info(f"Dynamically enabled tool: {skill_name}")
                    elif not is_enabled and skill_name in self.tools:
                        logging.info(f"Dynamically disabling tool: {skill_name}")
                        tool = self.tools.pop(skill_name)
                        if hasattr(tool, "shutdown"):
                            try:
                                tool.shutdown()
                            except Exception as e:
                                logging.error(
                                    f"Error shutting down tool {skill_name}: {e}", exc_info=True)

        except Exception as e:
            logging.error(
                f"Error dynamically reloading tools: {e}", exc_info=True)

    def shutdown(self):
        """Cleanly shuts down all loaded tools."""
        logging.info("Cerebrum: Shutting down tools...")
        with self._tools_lock:
            for skill_name, tool in list(self.tools.items()):
                if hasattr(tool, "shutdown"):
                    try:
                        tool.shutdown()
                    except Exception as e:
                        logging.error(
                            f"Error shutting down tool {skill_name}: {e}", exc_info=True)
