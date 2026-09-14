import time
import threading
import logging
import json
from dotenv import load_dotenv
from .events import Signal
from .settings_manager import SettingsManager

try:
    import anthropic
except ImportError:
    anthropic = None

# Load environment variables
load_dotenv()


def _convert_schema_types(schema):
    if isinstance(schema, dict):
        new_schema = {}
        for k, v in schema.items():
            if k == "type" and isinstance(v, str):
                new_schema[k] = v.lower()
            elif isinstance(v, (dict, list)):
                new_schema[k] = _convert_schema_types(v)
            else:
                new_schema[k] = v
        return new_schema
    elif isinstance(schema, list):
        return [_convert_schema_types(item) for item in schema]
    return schema


def _convert_to_anthropic_tool(genai_tool):
    return {
        "name": genai_tool["name"],
        "description": genai_tool.get("description", ""),
        "input_schema": _convert_schema_types(genai_tool.get("parameters", {"type": "object", "properties": {}}))
    }


class FunctionCallObject:
    def __init__(self, name, args):
        self.name = name
        self.args = args


class ClaudeWorker:
    def __init__(self, agent_id=None):
        self.agent_id = agent_id
        logging.debug(f"ClaudeWorker.__init__ called.")
        self.client = None
        self.settings = SettingsManager(agent_id=self.agent_id)
        self.thought_received = Signal()  # text, list of function calls
        self.error_occurred = Signal()
        self.tokens_consumed = Signal()  # int

        self.api_key = self.settings.get_env("CLAUDE_API_KEY")
        self.available = False
        self.last_error = None
        if anthropic is None:
            self.last_error = "anthropic package is not installed"
            if not self.settings.get("core.first-run", False):
                logging.error(self.last_error)
                self.error_occurred.emit("Anthropic package not installed.")
        elif not self.api_key:
            self.last_error = "CLAUDE_API_KEY not found in .env"
            if self.settings.get("core.first-run", False):
                logging.info(
                    "CLAUDE_API_KEY not found in .env (expected on first run)")
            else:
                logging.error(self.last_error)
                self.error_occurred.emit(
                    "Missing Claude API Key. Please check settings.")
        else:
            try:
                self.client = anthropic.Anthropic(api_key=self.api_key)
                self.available = True
            except Exception as e:
                self.last_error = f"Error initializing Claude Client: {e}"
                logging.error(self.last_error, exc_info=True)
                self.error_occurred.emit(f"Client Init Error: {e}")

        self.running = False
        self.is_processing = False
        self._abort_flag = False
        self.sys_instruct = None
        self.tools = None
        self.anthropic_tools = None
        self._history_lock = threading.RLock()
        self.history = []
        self.consumed_tool_use_ids = set()
        self.current_model = "claude-fable-5-1"

        # Audio STT setup
        from .local_stt import LocalSTT
        self.local_stt = LocalSTT()

    def is_running(self):
        return self.running

    def start_session(self, system_instruction=None, tools=None):
        logging.debug("start_session called in ClaudeWorker")
        if not getattr(self, 'available', False):
            logging.error(
                "Attempted to start session but ClaudeWorker is unavailable.")
            self.error_occurred.emit(
                "Session Start Error: Claude Worker is not available")
            return

        self.sys_instruct = system_instruction or ""
        self.sys_instruct += "\n\nCRITICAL INSTRUCTION: You must always output your internal reasoning and thought process as plain text BEFORE invoking any tool. Explain what you are about to do and why."
        self.sys_instruct += "\n\nAUTONOMOUS SPEECH INSTRUCTION: You are fully autonomous regarding your speech. You will NOT speak automatically. If you wish to communicate with the user, you MUST explicitly use the Speaker tool (e.g., Speaker_speak_aloud). Otherwise, you will remain completely silent. Speak from a first-person perspective."

        self.tools = tools
        self.anthropic_tools = []
        if self.tools:
            for t in self.tools:
                self.anthropic_tools.append(_convert_to_anthropic_tool(t))

        with self._history_lock:
            self.history = []
            self.consumed_tool_use_ids = set()

        # Determine model
        is_low_token = self.settings.get("core.low-token-mode", False)

        # Load models from settings
        light_models = self.settings.get(
            "core.claude.light-models", ["claude-haiku-4-5"])
        claude_models = self.settings.get(
            "core.claude.claude-models", ["claude-fable-5-1", "claude-sonnet-5"])

        if not isinstance(light_models, list):
            light_models = [light_models]
        if not isinstance(claude_models, list):
            claude_models = [claude_models]

        self.current_model = light_models[0] if is_low_token else claude_models[0]

        self.running = True
        logging.info(
            f"Claude SDK sessions started with model {self.current_model}.")

    def stop_session(self):
        self.running = False
        with self._history_lock:
            self.history = []
            self.consumed_tool_use_ids = set()
        logging.info("Claude SDK session stopped.")

    def abort(self):
        self._abort_flag = True
        self.is_processing = False

    def send_prompt(self, prompt: str, image_path: str = None, yolo: bool = False, audio_path: str = None):
        logging.debug("send_prompt called in ClaudeWorker")
        if not self.running:
            self.error_occurred.emit("Session not started.")
            return

        self._abort_flag = False
        self.is_processing = True
        threading.Thread(target=self._process_thought, args=(
            prompt, image_path, yolo, audio_path), daemon=True).start()

    def send_function_response(self, name: str, response: dict):
        self.send_function_responses([(name, response)])

    def send_function_responses(self, responses: list):
        if not self.running:
            self.error_occurred.emit("Session not started.")
            return

        self._abort_flag = False
        self.is_processing = True

        parts = []
        for name, response in responses:
            if isinstance(response, dict):
                clean_resp = dict(response)
                # Remove media from response dict
                if 'media' in clean_resp:
                    del clean_resp['media']
                resp_content = json.dumps(clean_resp)
            elif isinstance(response, str):
                resp_content = response
            else:
                resp_content = json.dumps({"result": str(response)})

            tool_use_id = None
            with self._history_lock:
                for msg in reversed(self.history):
                    if msg.get("role") == "assistant" and isinstance(msg.get("content"), list):
                        for block in msg["content"]:
                            if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == name:
                                tid = getattr(block, "id", None)
                                if tid not in self.consumed_tool_use_ids:
                                    tool_use_id = tid
                                    break
                            elif isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == name:
                                tid = block.get("id")
                                if tid not in self.consumed_tool_use_ids:
                                    tool_use_id = tid
                                    break
                    if tool_use_id:
                        break

            if not tool_use_id:
                tool_use_id = f"tool_{name}_{int(time.time())}"

            self.consumed_tool_use_ids.add(tool_use_id)

            parts.append({
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": resp_content
            })

        threading.Thread(target=self._process_thought, args=(
            parts, None, False, None), daemon=True).start()

    def _reconcile_tool_calls(self):
        """
        Ensures every assistant message with 'tool_use' blocks is followed by
        matching 'tool_result' blocks in the subsequent user message.
        If tool calls are unfulfilled, synthesizes dummy tool results.
        """
        with self._history_lock:
            i = 0
            while i < len(self.history):
                msg = self.history[i]
                if msg.get("role") == "assistant":
                    tool_use_ids = []
                    content = msg.get("content")
                    if isinstance(content, list):
                        for block in content:
                            b_type = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
                            if b_type == "tool_use":
                                tid = getattr(block, "id", None) or (block.get("id") if isinstance(block, dict) else None)
                                if tid:
                                    tool_use_ids.append(tid)

                    if tool_use_ids:
                        if i + 1 < len(self.history):
                            next_msg = self.history[i + 1]
                            if next_msg.get("role") == "user":
                                answered = set()
                                next_content = next_msg.get("content")
                                if isinstance(next_content, list):
                                    for block in next_content:
                                        b_type = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
                                        if b_type == "tool_result":
                                            tid = getattr(block, "tool_use_id", None) or (block.get("tool_use_id") if isinstance(block, dict) else None)
                                            if tid:
                                                answered.add(tid)
                                elif isinstance(next_content, str):
                                    next_content = [{"type": "text", "text": next_content}]
                                    next_msg["content"] = next_content
                                else:
                                    next_content = []
                                    next_msg["content"] = next_content

                                missing_dummies = []
                                for tid in tool_use_ids:
                                    if tid not in answered:
                                        missing_dummies.append({
                                            "type": "tool_result",
                                            "tool_use_id": tid,
                                            "content": json.dumps({"result": "[Action cancelled or interrupted by system]"})
                                        })
                                        self.consumed_tool_use_ids.add(tid)
                                        logging.warning(
                                            f"ClaudeWorker synthesized dummy tool response for unfulfilled tool_use_id: {tid}")
                                if missing_dummies:
                                    next_msg["content"] = missing_dummies + next_content
                            else:
                                dummy_content = []
                                for tid in tool_use_ids:
                                    dummy_content.append({
                                        "type": "tool_result",
                                        "tool_use_id": tid,
                                        "content": json.dumps({"result": "[Action cancelled or interrupted by system]"})
                                    })
                                    self.consumed_tool_use_ids.add(tid)
                                    logging.warning(
                                        f"ClaudeWorker synthesized dummy tool response for unfulfilled tool_use_id: {tid}")
                                self.history.insert(i + 1, {"role": "user", "content": dummy_content})
                        else:
                            dummy_content = []
                            for tid in tool_use_ids:
                                dummy_content.append({
                                    "type": "tool_result",
                                    "tool_use_id": tid,
                                    "content": json.dumps({"result": "[Action cancelled or interrupted by system]"})
                                })
                                self.consumed_tool_use_ids.add(tid)
                                logging.warning(
                                    f"ClaudeWorker synthesized dummy tool response for unfulfilled tool_use_id: {tid}")
                            self.history.append({"role": "user", "content": dummy_content})
                i += 1

    def _process_thought(self, prompt, image_path, yolo, audio_path=None):
        from core.logger_config import agent_id_var
        if hasattr(self, 'agent_id'):
            agent_id_var.set(self.agent_id)

        is_low_token = self.settings.get("core.low-token-mode", False)
        content_blocks = []

        # Check if prompt is a list of parts (like tool_results)
        if isinstance(prompt, list):
            content_blocks.extend(prompt)
        elif isinstance(prompt, dict):
            content_blocks.append(prompt)
        elif prompt is not None:
            content_blocks.append({"type": "text", "text": str(prompt)})

        if image_path:
            if is_low_token:
                content_blocks.append(
                    {"type": "text", "text": "[Image attachment skipped: Low Token Mode is active]"})
            else:
                try:
                    import mimetypes
                    import base64
                    mime_type, _ = mimetypes.guess_type(image_path)
                    with open(image_path, "rb") as image_file:
                        image_data = base64.b64encode(
                            image_file.read()).decode("utf-8")
                    content_blocks.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime_type or 'image/jpeg',
                            "data": image_data,
                        }
                    })
                except Exception as e:
                    self.error_occurred.emit(f"Image load error: {e}")
                    self.is_processing = False
                    return

        if audio_path:
            if is_low_token:
                content_blocks.append(
                    {"type": "text", "text": "[Audio attachment skipped: Low Token Mode is active]"})
            else:
                try:
                    # Transcribe using faster-whisper via LocalSTT
                    logging.info(
                        f"Transcribing audio for Claude: {audio_path}")
                    transcription = self.local_stt.transcribe(audio_path)
                    content_blocks.append(
                        {"type": "text", "text": f"[Audio Transcribed via Whisper]: {transcription}"})
                except Exception as e:
                    self.error_occurred.emit(f"Audio load error: {e}")
                    self.is_processing = False
                    return

        # Add to history (keep reference for rollback on error)
        user_msg = {"role": "user", "content": content_blocks}
        with self._history_lock:
            self.history.append(user_msg)
            self._reconcile_tool_calls()

        try:
            logging.debug(
                f"Calling Claude with message length {len(self.history)}...")

            # Enable Anthropic Prompt Caching on system instructions
            system_payload = [
                {
                    "type": "text",
                    "text": self.sys_instruct or "",
                    "cache_control": {"type": "ephemeral"}
                }
            ]

            # Configure adaptive thinking effort level
            effort = self.settings.get("core.claude.thinking-effort", "high")

            kwargs = {
                "model": self.current_model,
                "system": system_payload,
                "messages": self.history,
                "max_tokens": 16384,
                "output_config": {"effort": effort}
            }

            # Enable Anthropic Prompt Caching on the full tool suite
            if self.anthropic_tools:
                tools_payload = [dict(t) for t in self.anthropic_tools]
                tools_payload[-1]["cache_control"] = {"type": "ephemeral"}
                kwargs["tools"] = tools_payload

            full_text = ""
            full_thinking = ""
            function_calls = []

            with self.client.messages.stream(**kwargs) as stream:
                for text_chunk in stream.text_stream:
                    if getattr(self, '_abort_flag', False):
                        self.is_processing = False
                        return
                    full_text += text_chunk

                # We need to capture tool uses and thinking blocks from the final message
                message = stream.get_final_message()

                # Append assistant message to history (preserves ThinkingBlock and ToolUseBlock)
                with self._history_lock:
                    self.history.append(
                        {"role": "assistant", "content": message.content})

                # Process blocks from message
                for block in message.content:
                    b_type = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
                    if b_type == "tool_use":
                        name = getattr(block, "name", None) or (block.get("name") if isinstance(block, dict) else None)
                        args = getattr(block, "input", None) or (block.get("input") if isinstance(block, dict) else {})
                        function_calls.append(FunctionCallObject(name, args))
                    elif b_type == "thinking":
                        th_text = getattr(block, "thinking", None) or (block.get("thinking") if isinstance(block, dict) else "")
                        if th_text:
                            full_thinking += th_text

            # Token tracking (input, output, and cache creation/read tokens)
            tokens = 0
            if hasattr(message, 'usage') and message.usage:
                input_tokens = getattr(message.usage, 'input_tokens', 0) or 0
                output_tokens = getattr(message.usage, 'output_tokens', 0) or 0
                cache_read_tokens = getattr(message.usage, 'cache_read_input_tokens', 0) or 0
                cache_creation_tokens = getattr(message.usage, 'cache_creation_input_tokens', 0) or 0
                tokens = input_tokens + output_tokens + cache_read_tokens + cache_creation_tokens
            else:
                # Fallback character-based estimation (unlikely with current SDK)
                est_content_chars = sum(len(str(p)) for p in content_blocks) if content_blocks else 0
                thought_len = len(full_thinking) + len(full_text)
                tokens = int((est_content_chars + thought_len) / 4)

            if tokens > 0 and hasattr(self, 'tokens_consumed'):
                self.tokens_consumed.emit(tokens)

            # Determine combined thought output for the agent's internal monologue
            emitted_thought = full_text
            if full_thinking and full_text:
                emitted_thought = f"{full_thinking}\n\n{full_text}"
            elif full_thinking and not full_text:
                emitted_thought = full_thinking

            logging.debug(
                f"About to emit thought_received. Text length: {len(emitted_thought)}, Tools: {len(function_calls)}")
            self.thought_received.emit(emitted_thought, function_calls)
            self.is_processing = False
            return

        except Exception as e:
            # Roll back the unpaired user message to keep history valid
            with self._history_lock:
                if self.history and self.history[-1] is user_msg:
                    self.history.pop()
            err_str = str(e)
            logging.error(f"Claude API Error: {err_str}", exc_info=True)
            self.error_occurred.emit(f"Claude API Error: {err_str}")
            self.is_processing = False
            return

    def reformulate_query(self, user_prompt: str, history: list) -> str:
        if not self.api_key or not history:
            return user_prompt

        system_instruction = "You are a query reformulator. Your job is to rewrite the user's latest prompt to be standalone and context-independent by resolving any pronouns or ambiguous references using the provided conversation history. Output ONLY the rewritten query, nothing else. If the query is already standalone, output it exactly as is."

        prompt = "Conversation History:\n"
        for sender, text in history[-5:]:
            prompt += f"{sender}: {text}\n"
        prompt += f"\nUser's Latest Prompt: {user_prompt}\n\nRewritten Query:"

        try:
            # Use the first light model for fast, cheap tasks like query reformulation
            light_models = self.settings.get(
                "core.claude.light-models", ["claude-haiku-4-5"])
            if not isinstance(light_models, list):
                light_models = [light_models]
            reformulator_model = light_models[0]

            response = self.client.messages.create(
                model=reformulator_model,
                system=system_instruction,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=100
            )
            return response.content[0].text.strip()
        except Exception as e:
            logging.warning(f"Reformulator API Error: {e}")
            return user_prompt
