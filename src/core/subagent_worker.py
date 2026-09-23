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
            model = self.settings.get("core.gemini.model", "")
            if not model:
                legacy = self.settings.get("core.gemini.gemini-models", ["gemini-3.8-flash"])
                model = legacy[0] if isinstance(legacy, list) and legacy else "gemini-3.8-flash"
        else:
            model = self.settings.get("core.gemini.light-model", "")
            if not model:
                legacy_l = self.settings.get("core.gemini.light-models", ["gemini-3.5-flash-lite"])
                model = legacy_l[0] if isinstance(legacy_l, list) and legacy_l else "gemini-3.5-flash-lite"

        self.thinking_models = [model]
        self.current_model = model
        self.thinker_chat = None
        self._abort_flag = False
        self._process_lock = threading.Lock()

        self.setup_session()

    def setup_session(self):
        if not self.available:
            return

        sys_instruct = f"You are a subagent with ID {self.subagent_id}. You operate in the background to assist the primary agent. You must be concise, objective, and task-oriented. You will not interact directly with the user. Your output is read by the primary agent, so deliver exactly what is asked."
        sys_instruct += "\n\nCRITICAL INSTRUCTION: You must output your internal reasoning as plain text BEFORE invoking any tool(s). You MUST invoke your intended tool(s) in the EXACT SAME TURN immediately following your reasoning. If you intend to use tools don't end your turn after your reasoning, you must emit the tool call(s) in that same turn."

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

        thinking_level = str(self.settings.get("core.gemini.thinking-level", "low")).lower()
        budget_map = {"low": 1024, "medium": 4096, "high": 16384, "max": -1}
        thinking_budget = budget_map.get(thinking_level, 1024)
        thinking_config = None
        if hasattr(types, 'ThinkingConfig'):
            try:
                thinking_config = types.ThinkingConfig(thinking_budget=thinking_budget)
            except Exception:
                thinking_config = None

        self.thinker_config = types.GenerateContentConfig(
            system_instruction=sys_instruct,
            tools=tool_list if tool_list else None,
            thinking_config=thinking_config,
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

    def send_function_responses(self, responses: list):
        if not self.available or not self.thinker_chat:
            return

        self._abort_flag = False
        self.is_processing = True
        parts = [
            types.Part.from_function_response(name=name, response=response)
            for name, response in responses
        ]
        threading.Thread(target=self._process_thought,
                         args=(parts,), daemon=True).start()

    def send_function_response(self, name: str, response: dict):
        self.send_function_responses([(name, response)])

    def _get_chat_history(self):
        if not self.thinker_chat:
            return []
        if hasattr(self.thinker_chat, '_curated_history') and self.thinker_chat._curated_history is not None:
            return self.thinker_chat._curated_history
        if hasattr(self.thinker_chat, 'get_history'):
            try:
                return self.thinker_chat.get_history(curated=True)
            except Exception:
                return self.thinker_chat.get_history()
        if hasattr(self.thinker_chat, 'history') and self.thinker_chat.history is not None:
            return self.thinker_chat.history
        return []

    def _reconcile_tool_calls(self, content):
        """
        Ensures any unfulfilled function calls in the subagent chat history or pending turn
        have matching function responses synthesized before sending to the Gemini API.
        """
        history = self._get_chat_history()
        if not history:
            return content

        # 1. Reconcile internal history pairs if any prior model turn had unfulfilled calls
        for i in range(len(history) - 1):
            turn = history[i]
            if getattr(turn, 'role', None) == 'model':
                expected_fcs = []
                for p in getattr(turn, 'parts', []) or []:
                    fc = getattr(p, 'function_call', None)
                    if fc and getattr(fc, 'name', None):
                        expected_fcs.append(fc.name)
                if expected_fcs:
                    next_turn = history[i + 1]
                    if getattr(next_turn, 'role', None) == 'user':
                        answered = []
                        for p in getattr(next_turn, 'parts', []) or []:
                            fr = getattr(p, 'function_response', None)
                            if fr and getattr(fr, 'name', None):
                                answered.append(fr.name)
                        missing_parts = []
                        for name in expected_fcs:
                            if name in answered:
                                answered.remove(name)
                            else:
                                missing_parts.append(types.Part.from_function_response(
                                    name=name,
                                    response={"result": "[Action cancelled or interrupted by system]"}
                                ))
                                self.logger.warning(
                                    f"[Subagent {self.subagent_id}] Synthesized dummy function response for unfulfilled function_call: {name}")
                        if missing_parts:
                            next_turn.parts = missing_parts + (getattr(next_turn, 'parts', []) or [])

        # 2. Check the final turn in history for unfulfilled function calls
        last_turn = history[-1]
        if getattr(last_turn, 'role', None) == 'model':
            expected_fcs = []
            for p in getattr(last_turn, 'parts', []) or []:
                fc = getattr(p, 'function_call', None)
                if fc and getattr(fc, 'name', None):
                    expected_fcs.append(fc.name)

            if expected_fcs:
                answered_in_content = []
                for item in content:
                    fr = getattr(item, 'function_response', None)
                    if fr and getattr(fr, 'name', None):
                        answered_in_content.append(fr.name)

                missing_parts = []
                for name in expected_fcs:
                    if name in answered_in_content:
                        answered_in_content.remove(name)
                    else:
                        missing_parts.append(types.Part.from_function_response(
                            name=name,
                            response={"result": "[Action cancelled or interrupted by system]"}
                        ))
                        self.logger.warning(
                            f"[Subagent {self.subagent_id}] Synthesized dummy function response for unfulfilled function_call: {name}")

                if missing_parts:
                    content = missing_parts + list(content)

        return content

    def _process_thought(self, content):
        with self._process_lock:
            self._process_thought_locked(content)

    def _process_thought_locked(self, content):
        from core.logger_config import agent_id_var
        if hasattr(self, 'agent_id'):
            agent_id_var.set(self.agent_id)

        self.logger.info(f"[Subagent {self.subagent_id}] Processing prompt...")

        content = self._reconcile_tool_calls(content)

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
