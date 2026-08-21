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


class ClaudeWorker:
    def __init__(self, agent_id=None):
        super().__init__()
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
        self.sys_instruct = None
        self.tools = None
        self.anthropic_tools = None
        self.history = []
        self.current_model = "claude-3-5-sonnet-20240620"

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

        self.history = []

        # Determine model
        is_low_token = self.settings.get("core.low-token-mode", False)

        # Load models from settings
        light_models = self.settings.get(
            "core.claude.light-models", ["claude-haiku-4-5"])
        claude_models = self.settings.get(
            "core.claude.claude-models", ["claude-sonnet-5"])

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
        self.history = []
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
        if not self.running:
            self.error_occurred.emit("Session not started.")
            return

        self._abort_flag = False
        self.is_processing = True

        tool_use_id = None
        for msg in reversed(self.history):
            if msg["role"] == "assistant" and isinstance(msg["content"], list):
                for block in msg["content"]:
                    if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == name:
                        tid = getattr(block, "id", None)
                        if tid not in getattr(self, 'consumed_tool_use_ids', set()):
                            tool_use_id = tid
                            break
            elif msg["role"] == "assistant" and isinstance(msg["content"], str):
                pass
            if tool_use_id:
                break

        if not tool_use_id:
            logging.error(f"Could not find matching tool_use for {name}")
            # Fallback, might fail API validation
            tool_use_id = f"tool_{name}_{int(time.time())}"

        if not hasattr(self, 'consumed_tool_use_ids'):
            self.consumed_tool_use_ids = set()
        self.consumed_tool_use_ids.add(tool_use_id)

        part = {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": json.dumps(response)
        }

        threading.Thread(target=self._process_thought, args=(
            part, None, False), daemon=True).start()

    def send_function_responses(self, responses: list):
        if not self.running:
            self.error_occurred.emit("Session not started.")
            return

        self._abort_flag = False
        self.is_processing = True

        parts = []
        for name, response in responses:
            if isinstance(response, dict):
                # Remove media from response dict
                if 'media' in response:
                    del response['media']

            tool_use_id = None
            for msg in reversed(self.history):
                if msg["role"] == "assistant" and isinstance(msg["content"], list):
                    for block in msg["content"]:
                        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == name:
                            tid = getattr(block, "id", None)
                            if tid not in getattr(self, 'consumed_tool_use_ids', set()):
                                tool_use_id = tid
                                break
                if tool_use_id:
                    break

            if not tool_use_id:
                tool_use_id = f"tool_{name}_{int(time.time())}"

            if not hasattr(self, 'consumed_tool_use_ids'):
                self.consumed_tool_use_ids = set()
            self.consumed_tool_use_ids.add(tool_use_id)

            parts.append({
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": json.dumps(response)
            })

        threading.Thread(target=self._process_thought, args=(
            parts, None, False, None), daemon=True).start()

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
        else:
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

        # Add to history
        self.history.append({"role": "user", "content": content_blocks})

        # Culling old history to save tokens
        if len(self.history) > 20:
            self.history = self.history[-20:]

        try:
            logging.debug(
                f"Calling Claude with message length {len(self.history)}...")
            kwargs = {
                "model": self.current_model,
                "system": self.sys_instruct,
                "messages": self.history,
                "max_tokens": 4096
            }
            if self.anthropic_tools:
                kwargs["tools"] = self.anthropic_tools

            full_text = ""
            function_calls = []

            with self.client.messages.stream(**kwargs) as stream:
                for text_chunk in stream.text_stream:
                    if getattr(self, '_abort_flag', False):
                        self.is_processing = False
                        return
                    full_text += text_chunk

                # We need to capture tool uses after stream finishes or from stream events
                # The stream context manager provides a convenient way to get the final message
                message = stream.get_final_message()

                # Append assistant message to history
                self.history.append(
                    {"role": "assistant", "content": message.content})

                # Process tools from message
                for block in message.content:
                    if block.type == "tool_use":
                        # Convert anthropic tool format to GenAI format expected by Orchestrator
                        # GenAI format uses a custom object that has name and args
                        class FunctionCallObject:
                            def __init__(self, name, args):
                                self.name = name
                                self.args = args

                        function_calls.append(
                            FunctionCallObject(block.name, block.input))

            # Token tracking (excluding static system instruction and tool declarations)
            tokens = 0
            if hasattr(message, 'usage') and message.usage:
                output_tokens = getattr(message.usage, 'output_tokens', 0) or 0
                est_input_chars = sum(len(str(p)) for p in content) if ('content' in locals() and content) else 0
                input_tokens = int(est_input_chars / 4)
                tokens = output_tokens + input_tokens
            else:
                est_content_chars = sum(len(str(p)) for p in content) if ('content' in locals() and content) else 0
                tokens = int((est_content_chars + len(full_text)) / 4)

            if tokens > 0 and hasattr(self, 'tokens_consumed'):
                self.tokens_consumed.emit(tokens)

            logging.debug(
                f"About to emit thought_received. Text length: {len(full_text)}, Tools: {len(function_calls)}")
            self.thought_received.emit(full_text, function_calls)
            self.is_processing = False
            return

        except Exception as e:
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
