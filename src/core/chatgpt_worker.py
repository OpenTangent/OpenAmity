import time
import threading
import logging
import json
from dotenv import load_dotenv
from .events import Signal
from .settings_manager import SettingsManager

try:
    import openai
except ImportError:
    openai = None

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


def _convert_to_openai_tool(genai_tool):
    return {
        "type": "function",
        "function": {
            "name": genai_tool["name"],
            "description": genai_tool.get("description", ""),
            "parameters": _convert_schema_types(genai_tool.get("parameters", {"type": "object", "properties": {}}))
        }
    }


class FunctionCallObject:
    def __init__(self, name, args):
        self.name = name
        self.args = args


class ChatGptWorker:
    def __init__(self, agent_id=None):
        super().__init__()
        self.agent_id = agent_id
        logging.debug("ChatGptWorker.__init__ called.")
        self.client = None
        self.settings = SettingsManager(agent_id=self.agent_id)
        self.thought_received = Signal()  # text, list of function calls
        self.error_occurred = Signal()
        self.tokens_consumed = Signal()  # int

        self.api_key = self.settings.get_env("OPENAI_API_KEY")
        self.available = False
        self.last_error = None
        if openai is None:
            self.last_error = "openai package is not installed"
            if not self.settings.get("core.first-run", False):
                logging.error(self.last_error)
                self.error_occurred.emit("OpenAI package not installed.")
        elif not self.api_key:
            self.last_error = "OPENAI_API_KEY not found in .env"
            if self.settings.get("core.first-run", False):
                logging.info(
                    "OPENAI_API_KEY not found in .env (expected on first run)")
            else:
                logging.error(self.last_error)
                self.error_occurred.emit(
                    "Missing OpenAI API Key. Please check settings.")
        else:
            try:
                self.client = openai.OpenAI(api_key=self.api_key)
                self.available = True
            except Exception as e:
                self.last_error = f"Error initializing OpenAI Client: {e}"
                logging.error(self.last_error, exc_info=True)
                self.error_occurred.emit(f"Client Init Error: {e}")

        self.running = False
        self.is_processing = False
        self._abort_flag = False
        self.sys_instruct = None
        self.tools = None
        self.openai_tools = None
        self.history = []
        self.consumed_tool_call_ids = set()
        self.current_model = "gpt-5.6-terra"

        # Audio STT setup
        from .local_stt import LocalSTT
        self.local_stt = LocalSTT()

    def is_running(self):
        return self.running

    def start_session(self, system_instruction=None, tools=None):
        logging.debug("start_session called in ChatGptWorker")
        if not getattr(self, 'available', False):
            logging.error(
                "Attempted to start session but ChatGptWorker is unavailable.")
            self.error_occurred.emit(
                "Session Start Error: ChatGPT Worker is not available")
            return

        self.sys_instruct = system_instruction or ""
        self.sys_instruct += "\n\nCRITICAL INSTRUCTION: You must always output your internal reasoning and thought process as plain text BEFORE invoking any tool. Explain what you are about to do and why."
        self.sys_instruct += "\n\nAUTONOMOUS SPEECH INSTRUCTION: You are fully autonomous regarding your speech. You will NOT speak automatically. If you wish to communicate with the user, you MUST explicitly use the Speaker tool (e.g., Speaker_speak_aloud). Otherwise, you will remain completely silent. Speak from a first-person perspective."

        self.tools = tools
        self.openai_tools = []
        if self.tools:
            for t in self.tools:
                self.openai_tools.append(_convert_to_openai_tool(t))

        self.history = []
        self.consumed_tool_call_ids = set()

        # Determine model
        is_low_token = self.settings.get("core.low-token-mode", False)

        # Load models from settings
        light_models = self.settings.get(
            "core.chatgpt.light-models", ["gpt-5.6-luna"])
        chatgpt_models = self.settings.get(
            "core.chatgpt.chatgpt-models", ["gpt-5.6-terra", "gpt-5.6-sol"])

        if not isinstance(light_models, list):
            light_models = [light_models]
        if not isinstance(chatgpt_models, list):
            chatgpt_models = [chatgpt_models]

        self.current_model = light_models[0] if is_low_token else chatgpt_models[0]

        self.running = True
        logging.info(
            f"ChatGPT SDK session started with model {self.current_model}.")

    def stop_session(self):
        self.running = False
        self.history = []
        self.consumed_tool_call_ids = set()
        logging.info("ChatGPT SDK session stopped.")

    def abort(self):
        self._abort_flag = True
        self.is_processing = False

    def send_prompt(self, prompt: str, image_path: str = None, yolo: bool = False, audio_path: str = None):
        logging.debug("send_prompt called in ChatGptWorker")
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

        for name, response in responses:
            clean_resp = dict(response) if isinstance(response, dict) else {"result": str(response)}
            if 'media' in clean_resp:
                del clean_resp['media']

            tool_call_id = None
            for msg in reversed(self.history):
                if msg.get("role") == "assistant" and "tool_calls" in msg:
                    for tc in msg["tool_calls"]:
                        func = tc.get("function", {})
                        if func.get("name") == name:
                            tid = tc.get("id")
                            if tid not in self.consumed_tool_call_ids:
                                tool_call_id = tid
                                break
                if tool_call_id:
                    break

            if not tool_call_id:
                tool_call_id = f"call_{name}_{int(time.time())}"

            self.consumed_tool_call_ids.add(tool_call_id)

            self.history.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": json.dumps(clean_resp)
            })

        threading.Thread(target=self._process_thought, args=(
            None, None, False, None), daemon=True).start()

    def _process_thought(self, prompt=None, image_path=None, yolo=False, audio_path=None):
        from core.logger_config import agent_id_var
        if hasattr(self, 'agent_id'):
            agent_id_var.set(self.agent_id)

        is_low_token = self.settings.get("core.low-token-mode", False)

        if prompt is not None:
            content_blocks = []
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
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type or 'image/jpeg'};base64,{image_data}"
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
                        logging.info(
                            f"Transcribing audio for ChatGPT: {audio_path}")
                        transcription = self.local_stt.transcribe(audio_path)
                        content_blocks.append(
                            {"type": "text", "text": f"[Audio Transcribed via Whisper]: {transcription}"})
                    except Exception as e:
                        self.error_occurred.emit(f"Audio load error: {e}")
                        self.is_processing = False
                        return

            # If only a single text block, pass as plain string for simplicity
            if len(content_blocks) == 1 and content_blocks[0].get("type") == "text":
                self.history.append({"role": "user", "content": content_blocks[0]["text"]})
            else:
                self.history.append({"role": "user", "content": content_blocks})

        # Culling old history to save tokens
        if len(self.history) > 20:
            self.history = self.history[-20:]

        try:
            logging.debug(
                f"Calling ChatGPT with message length {len(self.history)}...")

            messages = [{"role": "system", "content": self.sys_instruct or ""}] + self.history

            kwargs = {
                "model": self.current_model,
                "messages": messages,
                "stream": True,
                "stream_options": {"include_usage": True}
            }
            if self.openai_tools:
                kwargs["tools"] = self.openai_tools

            full_text = ""
            tool_calls_data = {}
            total_tokens = 0

            stream = self.client.chat.completions.create(**kwargs)
            for chunk in stream:
                if getattr(self, '_abort_flag', False):
                    self.is_processing = False
                    return

                if getattr(chunk, 'usage', None):
                    u = chunk.usage
                    ct = getattr(u, 'completion_tokens', None)
                    tt = getattr(u, 'total_tokens', None)
                    if isinstance(tt, (int, float)) and tt > 0:
                        total_usage_tokens = tt
                    elif isinstance(ct, (int, float)) and ct > 0:
                        output_tokens = ct

                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta
                if getattr(delta, 'content', None):
                    full_text += delta.content

                if getattr(delta, 'tool_calls', None):
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_calls_data:
                            tool_calls_data[idx] = {
                                "id": tc.id or "",
                                "name": (tc.function.name if tc.function and tc.function.name else ""),
                                "arguments": (tc.function.arguments if tc.function and tc.function.arguments else "")
                            }
                        else:
                            if tc.id:
                                tool_calls_data[idx]["id"] += tc.id
                            if tc.function:
                                if tc.function.name:
                                    tool_calls_data[idx]["name"] += tc.function.name
                                if tc.function.arguments:
                                    tool_calls_data[idx]["arguments"] += tc.function.arguments

            # Build assistant message
            assistant_msg = {"role": "assistant"}
            assistant_msg["content"] = full_text if full_text else None

            final_tool_calls = []
            function_calls = []

            for idx in sorted(tool_calls_data.keys()):
                tc_item = tool_calls_data[idx]
                call_id = tc_item["id"] or f"call_{tc_item['name']}_{int(time.time())}"
                raw_args = tc_item["arguments"]
                try:
                    parsed_args = json.loads(raw_args) if raw_args else {}
                except Exception:
                    parsed_args = {}

                final_tool_calls.append({
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": tc_item["name"],
                        "arguments": raw_args
                    }
                })
                function_calls.append(FunctionCallObject(tc_item["name"], parsed_args))

            if final_tool_calls:
                assistant_msg["tool_calls"] = final_tool_calls

            self.history.append(assistant_msg)

            # Token tracking (excluding static system instruction and tool declarations)
            tokens = 0
            if 'total_usage_tokens' in locals() and isinstance(total_usage_tokens, (int, float)) and total_usage_tokens > 0:
                tokens = int(total_usage_tokens)
            elif 'output_tokens' in locals() and isinstance(output_tokens, (int, float)) and output_tokens > 0:
                est_input_chars = sum(len(str(p)) for p in content_blocks) if ('content_blocks' in locals() and content_blocks) else 0
                input_tokens = int(est_input_chars / 4)
                tokens = int(output_tokens) + input_tokens
            else:
                est_content_chars = sum(len(str(p)) for p in content_blocks) if ('content_blocks' in locals() and content_blocks) else 0
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
            logging.error(f"ChatGPT API Error: {err_str}", exc_info=True)
            self.error_occurred.emit(f"ChatGPT API Error: {err_str}")
            self.is_processing = False
            return

    def reformulate_query(self, user_prompt: str, history: list) -> str:
        if not self.api_key or not history or not self.client:
            return user_prompt

        system_instruction = "You are a query reformulator. Your job is to rewrite the user's latest prompt to be standalone and context-independent by resolving any pronouns or ambiguous references using the provided conversation history. Output ONLY the rewritten query, nothing else. If the query is already standalone, output it exactly as is."

        prompt = "Conversation History:\n"
        for sender, text in history[-5:]:
            prompt += f"{sender}: {text}\n"
        prompt += f"\nUser's Latest Prompt: {user_prompt}\n\nRewritten Query:"

        try:
            light_models = self.settings.get(
                "core.chatgpt.light-models", ["gpt-5.6-luna"])
            if not isinstance(light_models, list):
                light_models = [light_models]
            reformulator_model = light_models[0]

            response = self.client.chat.completions.create(
                model=reformulator_model,
                messages=[
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=100
            )
            if response.choices and response.choices[0].message and response.choices[0].message.content:
                return response.choices[0].message.content.strip()
            return user_prompt
        except Exception as e:
            logging.warning(f"ChatGPT Reformulator API Error: {e}")
            return user_prompt
