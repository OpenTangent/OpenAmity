
import importlib
import inspect
import os
import logging
import re
import threading
from typing import Dict, List, Any, Optional, Set


DEFAULT_MANUAL_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "memory", "agent_manual.md")
)
DEFAULT_MANUAL_DIR = os.path.join(os.path.dirname(DEFAULT_MANUAL_PATH), "manual")

MANUAL_TOPICS: Dict[str, Dict[str, Any]] = {
    "memory": {
        "file": "memory.md",
        "summary": "MemPalace wings/rooms, S1..S10 Empathy Cone, Two-Beat Sleep Consolidation, and MemPalace_apply_identity_delta.",
        "tools": ("MemPalace",),
    },
    "browser": {
        "file": "browser.md",
        "summary": "WebSearch vs Browser, ephemeral element IDs, Set-of-Marks screenshots, uploads, tabs, 4s waits, downloads, and 24h cache.",
        "tools": ("Browser", "WebSearch"),
    },
    "terminal_files": {
        "file": "terminal_files.md",
        "summary": "Terminal_read_file search/paging options, host .sh script delegation with logging, sandbox paths, and xdg-open.",
        "tools": ("Terminal",),
    },
    "channels": {
        "file": "channels.md",
        "summary": "LOCAL_GUI GFM formatting, Gemini 3.8 TTS angle-bracket cues, 24h audio/media TTLs, CHATROOM rooms/DMs, and WhatsApp contacts.",
        "tools": ("Speaker", "Chatroom", "WhatsApp", "Media", "Contacts", "Email"),
    },
    "agency_pulses": {
        "file": "agency_pulses.md",
        "summary": "Trajectory why/sphere_level grounding, 5-step [AGENT_PULSE] flow, fatigue/System_sleep heuristics, and Subagent_spawn scoping.",
        "tools": ("Trajectory", "Pulse", "Subagent", "System"),
    },
    "backups_api": {
        "file": "backups_api.md",
        "summary": ".oaa backup flagging/management, positive TTS voice guidelines, and Pulse Hook API workflow (System_api_docs).",
        "tools": ("System", "Terminal"),
    },
}

MANUAL_TOPIC_ALIASES: Dict[str, str] = {
    "mempalace": "memory",
    "mirrors": "memory",
    "mirror": "memory",
    "sleep": "memory",
    "identity": "memory",
    "spheres": "memory",
    "web": "browser",
    "websearch": "browser",
    "browsing": "browser",
    "som": "browser",
    "terminal": "terminal_files",
    "files": "terminal_files",
    "shell": "terminal_files",
    "scripts": "terminal_files",
    "read_file": "terminal_files",
    "sandbox": "terminal_files",
    "speaker": "channels",
    "chatroom": "channels",
    "whatsapp": "channels",
    "media": "channels",
    "tts": "channels",
    "formatting": "channels",
    "contacts": "channels",
    "pulses": "agency_pulses",
    "pulse": "agency_pulses",
    "trajectory": "agency_pulses",
    "fatigue": "agency_pulses",
    "subagent": "agency_pulses",
    "subagents": "agency_pulses",
    "agency": "agency_pulses",
    "contemplate": "agency_pulses",
    "backup": "backups_api",
    "backups": "backups_api",
    "api": "backups_api",
    "hooks": "backups_api",
    "voice": "backups_api",
}


def resolve_manual_path(manual_path: str = "src/memory/agent_manual.md") -> str:
    """Resolve the manual path cleanly regardless of process working directory."""
    if os.path.exists(manual_path):
        return os.path.abspath(manual_path)
    if manual_path == "src/memory/agent_manual.md" and os.path.exists(DEFAULT_MANUAL_PATH):
        return DEFAULT_MANUAL_PATH
    return os.path.abspath(manual_path)


def is_manual_topic_enabled(topic_key: str, enabled_tools: Optional[Set[str]]) -> bool:
    """Return True when at least one relevant tool is loaded, or tool set is empty."""
    meta = MANUAL_TOPICS.get(topic_key)
    if not meta:
        return False
    if not enabled_tools:
        return True
    req_tools = meta.get("tools", ())
    return not req_tools or any(t in enabled_tools for t in req_tools)


def filter_manual_index_for_tools(manual_text: str, enabled_tools: Optional[Set[str]]) -> str:
    """Omit topic index table rows whose backing tools are all disabled."""
    if not enabled_tools or not manual_text:
        return manual_text
    disabled = [k for k in MANUAL_TOPICS if not is_manual_topic_enabled(k, enabled_tools)]
    if not disabled:
        return manual_text
    patterns = [re.compile(rf"^\|\s*`?{re.escape(k)}`?\s*\|") for k in disabled]
    kept = [
        line for line in manual_text.splitlines()
        if not any(pat.match(line.strip()) for pat in patterns)
    ]
    trailing_nl = "\n" if manual_text.endswith("\n") else ""
    return "\n".join(kept) + trailing_nl


def resolve_manual_topic_key(topic: str = "") -> str:
    """Normalize a manual topic name or alias to its canonical key (or 'index')."""
    norm = (topic or "").strip().lower().replace("-", "_").replace(" ", "_")
    if norm in ("", "index", "list", "topics", "overview", "help"):
        return "index"
    if norm in MANUAL_TOPICS:
        return norm
    return MANUAL_TOPIC_ALIASES.get(norm, norm)


def read_manual_topic(
    manual_dir: str = DEFAULT_MANUAL_DIR,
    topic: str = "",
    enabled_tools: Optional[Set[str]] = None,
) -> str:
    """Read a detailed manual topic guide or return the topic directory index."""
    resolved = resolve_manual_topic_key(topic)
    if resolved == "index":
        lines = ["Available Agent Manual Topics (call System_read_manual with topic='<key>'):"]
        for key, meta in MANUAL_TOPICS.items():
            if not is_manual_topic_enabled(key, enabled_tools):
                continue
            lines.append(f"- `{key}`: {meta['summary']}")
        return "\n".join(lines)

    meta = MANUAL_TOPICS.get(resolved)
    if not meta:
        valid = ", ".join(MANUAL_TOPICS.keys())
        return (
            f"Error: Unknown manual topic '{topic}'. Valid topics: index, {valid}. "
            f"(Aliases such as web, mempalace, speaker, chatroom, whatsapp, pulses, trajectory, backup, api also work.)"
        )

    topic_path = os.path.join(manual_dir, meta["file"])
    if not os.path.exists(topic_path):
        return f"Error: Manual topic file missing for '{resolved}' at {topic_path}."

    try:
        with open(topic_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception as e:
        logging.error(f"Error reading manual topic '{resolved}': {e}", exc_info=True)
        return f"Error reading manual topic '{resolved}': {e}"


class Tool:
    """Base class for Open Amity tools."""
    name: str = "BaseSkill"
    description: str = "A generic tool."
    commands: List[str] = []  # List of command names this tool handles
    icon: str = "🔧"
    color: str = "#888888"
    async_commands: List[str] = []

    _tool_classes: Dict[str, Any] = {}
    _tools_discovered: bool = False

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
        cls._tools_discovered = True
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
    def _lookup_registered_tool_class(cls, clean: str) -> Any:
        if clean in cls._tool_classes:
            return cls._tool_classes[clean]
        for k, v in cls._tool_classes.items():
            if k.lower() == clean.lower():
                return v
        return None

    @classmethod
    def get_tool_class(cls, name: str) -> Any:
        if not name:
            return None
        clean = name
        if clean.startswith("tool."):
            clean = clean[5:]
        tool_cls = cls._lookup_registered_tool_class(clean)
        if tool_cls is not None:
            return tool_cls
        if not cls._tools_discovered:
            cls.discover_tool_classes()
            return cls._lookup_registered_tool_class(clean)
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
        self.manual_path = resolve_manual_path(manual_path)
        candidate_manual_dir = os.path.join(os.path.dirname(self.manual_path), "manual")
        self.manual_dir = candidate_manual_dir if os.path.isdir(candidate_manual_dir) else DEFAULT_MANUAL_DIR
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

    def list_manual_topics(self, enabled_only: bool = True) -> List[str]:
        """Returns canonical manual topic keys, optionally filtered to loaded tools."""
        with self._tools_lock:
            enabled_tools = set(self.tools.keys()) if (enabled_only and self.tools) else None
        return [
            key for key in MANUAL_TOPICS
            if not enabled_only or is_manual_topic_enabled(key, enabled_tools)
        ]

    def read_manual(self, topic: str = "") -> str:
        """Reads a detailed Agent Manual topic guide or returns the topic index."""
        with self._tools_lock:
            enabled_tools = set(self.tools.keys()) if self.tools else None
        return read_manual_topic(
            manual_dir=self.manual_dir,
            topic=topic,
            enabled_tools=enabled_tools,
        )

    def get_agent_manual(self, include_tool_entries: bool = False) -> str:
        """Generates the Agent Manual from loaded tools and the manual index file."""
        manual = ""
        if os.path.exists(self.manual_path):
            try:
                with open(self.manual_path, 'r', encoding='utf-8') as f:
                    manual = f.read() + "\n\n"
            except Exception as e:
                logging.error(f"Error loading manual file: {e}", exc_info=True)

        with self._tools_lock:
            enabled_tools = set(self.tools.keys()) if self.tools else None
            manual = filter_manual_index_for_tools(manual, enabled_tools)
            if include_tool_entries:
                manual += "### Available Tools\n"
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
                    if k.lower() == skill_name.lower() or (skill_name.lower().endswith("tool") and k.lower() == skill_name.lower()[:-4]):
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
                            if k.lower() == skill_name.lower() or (skill_name.lower().endswith("tool") and k.lower() == skill_name.lower()[:-4]):
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
                    elif is_enabled and skill_name in self.tools:
                        self.tools[skill_name].__class__ = obj
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
