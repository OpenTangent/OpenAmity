import threading
import logging
from google import genai
from google.genai import types
from dotenv import load_dotenv
from .events import Signal
from .settings_manager import SettingsManager

load_dotenv()


class SubagentWorker:
    def __init__(self, subagent_id, orchestrator, model_tier="light"):
        self.subagent_id = subagent_id
        self.orchestrator = orchestrator
        self.agent_id = orchestrator.agent_id
        self.model_tier = model_tier
        self.settings = SettingsManager(agent_id=self.agent_id)
        self.api_key = self.settings.get_env("GEMINI_API_KEY")
        self.thought_received = Signal()  # text, function_calls
        self.error_occurred = Signal()  # str
        self.is_processing = False
        self.available = False

        self.logger = logging.getLogger(f"subagent.{subagent_id}")

        if not self.api_key:
            self.logger.error(
                f"[Subagent {self.subagent_id}] GEMINI_API_KEY not found in .env")
        else:
            try:
                self.client = genai.Client(api_key=self.api_key)
                self.available = True
            except Exception as e:
                self.logger.error(
                    f"[Subagent {self.subagent_id}] Error initializing Gemini Client: {e}")

        self.settings.get("core.low-token-mode", False)
        if self.model_tier == "standard":
            model_key = "core.gemini.gemini-models"
        else:
            model_key = "core.gemini.light-models"

        self.thinking_models = self.settings.get(
            model_key, ["gemini-3.1-flash-lite"])
        if not isinstance(self.thinking_models, list):
            self.thinking_models = [self.thinking_models]

        self.current_model = self.thinking_models[0]
        self.thinker_chat = None
        self._abort_flag = False
        self._process_lock = threading.Lock()

        self.setup_session()

    def setup_session(self):
        if not self.available:
            return

        sys_instruct = f"You are a subagent with ID {self.subagent_id}. You operate in the background to assist the primary agent. You must be concise, objective, and task-oriented. You will not interact directly with the user. Your output is read by the primary agent, so deliver exactly what is asked."
        sys_instruct += "\n\nCRITICAL INSTRUCTION: You must always output your internal reasoning and thought process as plain text BEFORE invoking any tool. Explain what you are about to do and why."

        # Load restricted tools from Cerebrum
        tool_declarations = []
        for name, tool in self.orchestrator.cerebrum.tools.items():
            if name in ["DateTime", "WebSearch"]:
                tool_declarations.extend(tool.get_tool_declarations())

        tool_list = []
        if tool_declarations:
            func_declarations = []
            for t in tool_declarations:
                func_declarations.append(types.FunctionDeclaration(
                    name=t['name'],
                    description=t['description'],
                    parameters=t.get('parameters')
                ))
            if func_declarations:
                tool_list.append(types.Tool(
                    function_declarations=func_declarations))

        self.thinker_config = types.GenerateContentConfig(
            system_instruction=sys_instruct,
            tools=tool_list if tool_list else None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True)
        )

        try:
            self.thinker_chat = self.client.chats.create(
                model=self.current_model, config=self.thinker_config)
            self.logger.info(
                f"[Subagent {self.subagent_id}] Session initialized with {self.current_model}.")
        except Exception as e:
            self.logger.error(
                f"[Subagent {self.subagent_id}] Failed to start session: {e}")
            self.available = False

    def abort(self):
        self._abort_flag = True
        self.is_processing = False

    def send_prompt(self, prompt: str):
        if not self.available or not self.thinker_chat:
            self.error_occurred.emit("Subagent session not available.")
            return

        self._abort_flag = False
        self.is_processing = True
        threading.Thread(target=self._process_thought,
                         args=([prompt],), daemon=True).start()

    def send_function_response(self, name: str, response: dict):
        if not self.available or not self.thinker_chat:
            return

        self._abort_flag = False
        self.is_processing = True
        part = types.Part.from_function_response(name=name, response=response)
        threading.Thread(target=self._process_thought,
                         args=([part],), daemon=True).start()

    def _process_thought(self, content):
        with self._process_lock:
            self._process_thought_locked(content)

    def _process_thought_locked(self, content):
        from core.logger_config import agent_id_var
        if hasattr(self, 'agent_id'):
            agent_id_var.set(self.agent_id)

        self.logger.info(f"[Subagent {self.subagent_id}] Processing prompt...")

        try:
            response_stream = self.thinker_chat.send_message_stream(
                message=content)
            full_text = ""
            function_calls = []

            for chunk in response_stream:
                if self._abort_flag:
                    self.is_processing = False
                    return

                if getattr(chunk, 'parts', None):
                    for part in chunk.parts:
                        if getattr(part, 'text', None):
                            full_text += part.text
                if chunk.function_calls:
                    function_calls.extend(chunk.function_calls)

            if full_text:
                self.logger.info(
                    f"[Subagent {self.subagent_id}] Thought: {full_text.strip()}")
            if function_calls:
                funcs = ", ".join([f.name for f in function_calls])
                self.logger.info(
                    f"[Subagent {self.subagent_id}] Function Calls: {funcs}")

            self.is_processing = False
            self.thought_received.emit(full_text, function_calls)

        except Exception as e:
            self.logger.error(
                f"[Subagent {self.subagent_id}] Error during execution: {e}")
            self.is_processing = False
            self.error_occurred.emit(str(e))
