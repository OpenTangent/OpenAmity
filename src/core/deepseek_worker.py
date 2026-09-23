import os
import time
import threading
import logging
import json
import base64
import mimetypes
import uuid
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


class DeepSeekWorker:
    def __init__(self, agent_id=None):
        super().__init__()
        self.agent_id = agent_id
        logging.debug("DeepSeekWorker.__init__ called.")
        self.client = None
        self.settings = SettingsManager(agent_id=self.agent_id)
        self.thought_received = Signal()  # text, list of function calls
        self.error_occurred = Signal()
        self.tokens_consumed = Signal()  # int

        self.api_key = self.settings.get_env("DEEPSEEK_API_KEY")
        self.available = False
        self.last_error = None
        if openai is None:
            self.last_error = "openai package is not installed"
            if not self.settings.get("core.first-run", False):
                logging.error(self.last_error)
                self.error_occurred.emit("OpenAI package not installed.")
        elif not self.api_key:
            self.last_error = "DEEPSEEK_API_KEY not found in .env"
            if self.settings.get("core.first-run", False):
                logging.info(
                    "DEEPSEEK_API_KEY not found in .env (expected on first run)")
            else:
                logging.error(self.last_error)
                self.error_occurred.emit(
                    "Missing DeepSeek API Key. Please check settings.")
        else:
            try:
                self.client = openai.OpenAI(
                    api_key=self.api_key,
                    base_url="https://api.deepseek.com"
                )
                self.available = True
            except Exception as e:
                self.last_error = f"Error initializing DeepSeek Client: {e}"
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
        self._uploaded_file_ids = set()
        self.current_model = "deepseek-flash"

        # Audio STT setup
        from .local_stt import LocalSTT
        self.local_stt = LocalSTT()

    def is_running(self):
        return self.running

    def start_session(self, system_instruction=None, tools=None):
        logging.debug("start_session called in DeepSeekWorker")
        if not getattr(self, 'available', False):
            logging.error(
                "Attempted to start session but DeepSeekWorker is unavailable.")
            self.error_occurred.emit(
                "Session Start Error: DeepSeek Worker is not available")
            return

        self.sys_instruct = system_instruction or ""
        self.sys_instruct += "\n\nCRITICAL INSTRUCTION: You must output your internal reasoning as plain text BEFORE invoking any tool(s). You MUST invoke your intended tool(s) in the EXACT SAME TURN immediately following your reasoning. If you intend to use tools don't end your turn after your reasoning, you must emit the tool call(s) in that same turn."
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
        primary_model = self.settings.get("core.deepseek.model", "")
        if not primary_model:
            legacy = self.settings.get("core.deepseek.deepseek-models", ["deepseek-v4-pro"])
            primary_model = legacy[0] if isinstance(legacy, list) and legacy else "deepseek-v4-pro"

        light_model = self.settings.get("core.deepseek.light-model", "")
        if not light_model:
            legacy_l = self.settings.get("core.deepseek.light-models", ["deepseek-flash"])
            light_model = legacy_l[0] if isinstance(legacy_l, list) and legacy_l else "deepseek-flash"

        self.primary_model = primary_model
        self.light_model = light_model
        self.current_model = light_model if is_low_token else primary_model

        self.running = True
        logging.info(
            f"DeepSeek session started with model {self.current_model}.")

    def stop_session(self):
        self.running = False
        self.history = []
        self.consumed_tool_call_ids = set()
        if hasattr(self, '_uploaded_file_ids') and self._uploaded_file_ids and self.client:
            for fid in list(self._uploaded_file_ids):
                try:
                    self.client.files.delete(fid)
                except Exception:
                    pass
            self._uploaded_file_ids.clear()
        logging.info("DeepSeek session stopped.")

    def abort(self):
        self._abort_flag = True
        self.is_processing = False

    def send_prompt(self, prompt: str, image_path: str = None, yolo: bool = False, audio_path: str = None):
        logging.debug("send_prompt called in DeepSeekWorker")
        if not self.running:
            self.error_occurred.emit("Session not started.")
            return

        self._abort_flag = False
        self.is_processing = True
        threading.Thread(target=self._process_thought, args=(
            prompt, image_path, yolo, audio_path), daemon=True).start()

    def _prepare_image_block(self, image_path: str, detail: str = "auto") -> dict:
        if not image_path:
            return None

        # Check if external URL
        if image_path.startswith("http://") or image_path.startswith("https://"):
            return {
                "type": "image_url",
                "image_url": {
                    "url": image_path,
                    "detail": detail
                }
            }

        exp_path = os.path.expanduser(image_path)
        if not os.path.exists(exp_path):
            raise FileNotFoundError(f"Image file not found: {exp_path}")

        file_size = os.path.getsize(exp_path)
        mime_type, _ = mimetypes.guess_type(exp_path)
        supported_mimes = {"image/jpeg", "image/png", "image/gif", "image/webp"}
        if not mime_type or mime_type not in supported_mimes:
            ext = os.path.splitext(exp_path)[1].lower()
            if ext in [".jpg", ".jpeg"]:
                mime_type = "image/jpeg"
            elif ext == ".png":
                mime_type = "image/png"
            elif ext == ".gif":
                mime_type = "image/gif"
            elif ext == ".webp":
                mime_type = "image/webp"
            else:
                mime_type = "image/jpeg"

        # If file size is larger than 32 MiB inline limit, DeepSeek Chat Completions API does not support file attachments
        if file_size > 32 * 1024 * 1024:
            raise ValueError(
                f"Image {exp_path} exceeds 32 MiB inline limit. DeepSeek Chat Completions API does not support file attachments.")

        with open(exp_path, "rb") as image_file:
            image_data = base64.b64encode(image_file.read()).decode("utf-8")

        return {
            "type": "image_url",
            "image_url": {
                "url": f"data:{mime_type};base64,{image_data}",
                "detail": detail
            }
        }

    def _cull_media_from_history(self):
        # Intelligent Media Culling: Strip heavy multimodal attachments from older turns (> 4 turns)
        if len(self.history) > 4:
            for i in range(len(self.history) - 4):
                msg = self.history[i]
                if msg.get("role") == "user" and isinstance(msg.get("content"), list):
                    new_content = []
                    modified = False
                    for part in msg["content"]:
                        if isinstance(part, dict) and part.get("type") in ("image_url", "file"):
                            if part.get("type") == "file" and part.get("file_id") and self.client and hasattr(self.client, 'files'):
                                fid = part.get("file_id")
                                try:
                                    self.client.files.delete(fid)
                                    if hasattr(self, '_uploaded_file_ids'):
                                        self._uploaded_file_ids.discard(fid)
                                    logging.debug(f"DeepSeekWorker automatically deleted culled file: {fid}")
                                except Exception as e:
                                    logging.debug(f"DeepSeekWorker could not delete culled file {fid}: {e}")
                            new_content.append({
                                "type": "text",
                                "text": "[Media attachment automatically culled to save tokens/memory]"
                            })
                            modified = True
                        else:
                            new_content.append(part)
                    if modified:
                        msg["content"] = new_content

    def send_function_response(self, name: str, response: dict):
        self.send_function_responses([(name, response)])

    def send_function_responses(self, responses: list):
        if not self.running:
            self.error_occurred.emit("Session not started.")
            return

        self._abort_flag = False
        self.is_processing = True

        attached_media = []
        for name, response in responses:
            clean_resp = dict(response) if isinstance(response, dict) else {"result": str(response)}
            if 'media' in clean_resp:
                media_val = clean_resp.pop('media')
                if isinstance(media_val, list):
                    attached_media.extend(media_val)
                elif isinstance(media_val, str):
                    attached_media.append(media_val)

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
                tool_call_id = f"call_{name}_{uuid.uuid4().hex[:8]}"

            self.consumed_tool_call_ids.add(tool_call_id)

            self.history.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": json.dumps(clean_resp)
            })

        # DeepSeek API restricts image inputs to user messages only.
        # If any tool execution produced media attachments, append them in a follow-up user message.
        if attached_media:
            is_low_token = self.settings.get("core.low-token-mode", False)
            if is_low_token:
                self.history.append({
                    "role": "user",
                    "content": "[Tool media attachments skipped: Low Token Mode is active]"
                })
            else:
                user_content_blocks = [
                    {"type": "text", "text": "[Attached media from tool execution]:"}
                ]
                for m_path in attached_media:
                    try:
                        img_block = self._prepare_image_block(m_path)
                        if img_block:
                            user_content_blocks.append(img_block)
                    except Exception as e:
                        logging.warning(f"Failed to attach tool media {m_path}: {e}")
                        user_content_blocks.append({
                            "type": "text",
                            "text": f"[Error attaching media {m_path}: {e}]"
                        })
                self.history.append({
                    "role": "user",
                    "content": user_content_blocks
                })

        threading.Thread(target=self._process_thought, args=(
            None, None, False, None), daemon=True).start()

    def _reconcile_tool_calls(self):
        """
        Ensures every assistant message with 'tool_calls' is followed by matching
        'tool' messages responding to each 'tool_call_id' before any subsequent
        user or assistant message, or before the end of history.
        """
        new_history = []
        i = 0
        n = len(self.history)
        while i < n:
            msg = self.history[i]
            if msg.get("role") == "assistant" and not msg.get("tool_calls") and msg.get("content") is None:
                msg["content"] = ""
            new_history.append(msg)
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                expected_ids = [tc.get("id") for tc in msg["tool_calls"] if isinstance(tc, dict) and tc.get("id")]
                answered_ids = set()
                j = i + 1
                while j < n and self.history[j].get("role") == "tool":
                    tool_msg = self.history[j]
                    t_id = tool_msg.get("tool_call_id")
                    if t_id:
                        answered_ids.add(t_id)
                    new_history.append(tool_msg)
                    j += 1

                for tid in expected_ids:
                    if tid not in answered_ids:
                        dummy_resp = {
                            "role": "tool",
                            "tool_call_id": tid,
                            "content": json.dumps({"result": "[Action cancelled or interrupted by system]"})
                        }
                        new_history.append(dummy_resp)
                        self.consumed_tool_call_ids.add(tid)
                        logging.warning(
                            f"DeepSeekWorker synthesized dummy tool response for unfulfilled tool_call_id: {tid}")

                i = j - 1
            i += 1

        self.history = new_history

    def _process_thought(self, prompt=None, image_path=None, yolo=False, audio_path=None):
        from core.logger_config import agent_id_var
        if hasattr(self, 'agent_id'):
            agent_id_var.set(self.agent_id)

        history_snapshot_len = len(self.history)
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
                        img_block = self._prepare_image_block(image_path)
                        if img_block:
                            content_blocks.append(img_block)
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
                            f"Transcribing audio for DeepSeek: {audio_path}")
                        transcription = self.local_stt.transcribe(audio_path)
                        content_blocks.append(
                            {"type": "text", "text": f"[Audio Transcribed via Whisper]: {transcription}"})
                    except Exception as e:
                        self.error_occurred.emit(f"Audio load error: {e}")
                        self.is_processing = False
                        return

            # If only a single text block, pass as plain string for simplicity
            if len(content_blocks) == 1 and content_blocks[0].get("type") == "text":
                user_msg = {"role": "user", "content": content_blocks[0]["text"]}
            else:
                user_msg = {"role": "user", "content": content_blocks}
            self.history.append(user_msg)

        self._reconcile_tool_calls()
        self._cull_media_from_history()
        models_to_attempt = [self.current_model]
        if self.current_model != self.light_model:
            models_to_attempt.append(self.light_model)

        for attempt_idx, model_name in enumerate(models_to_attempt):
            try:
                logging.debug(
                    f"Calling DeepSeek with message length {len(self.history)} using model {model_name}...")

                messages = [{"role": "system", "content": self.sys_instruct or ""}] + self.history

                thinking_level = str(self.settings.get("core.deepseek.thinking-level", "low")).lower()
                thinking_enabled = thinking_level in ["low", "medium", "high", "max"] or self.settings.get("core.deepseek.thinking", False)
                thinking_type = "enabled" if thinking_enabled else "disabled"

                kwargs = {
                    "model": model_name,
                    "messages": messages,
                    "stream": True,
                    "stream_options": {"include_usage": True},
                    "extra_body": {"thinking": {"type": thinking_type}}
                }
                if self.openai_tools:
                    kwargs["tools"] = self.openai_tools

                full_text = ""
                full_reasoning = ""
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
                        if isinstance(ct, (int, float)) and ct > 0:
                            output_tokens = ct
                        if isinstance(tt, (int, float)) and tt > 0:
                            total_usage_tokens = tt

                    if not chunk.choices:
                        continue

                    delta = chunk.choices[0].delta
                    if getattr(delta, 'reasoning_content', None):
                        full_reasoning += delta.reasoning_content

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

                final_tool_calls = []
                function_calls = []

                for idx in sorted(tool_calls_data.keys()):
                    tc_item = tool_calls_data[idx]
                    call_id = tc_item["id"] or f"call_{tc_item['name']}_{uuid.uuid4().hex[:8]}"
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

                # Build assistant message
                assistant_msg = {"role": "assistant"}
                if final_tool_calls:
                    assistant_msg["tool_calls"] = final_tool_calls
                    assistant_msg["content"] = full_text if full_text else None
                else:
                    assistant_msg["content"] = full_text or ""

                if full_reasoning:
                    assistant_msg["reasoning_content"] = full_reasoning

                self.history.append(assistant_msg)
                self.current_model = model_name

                # Token tracking (excluding static system instruction and tool declarations)
                tokens = 0
                if 'output_tokens' in locals() and isinstance(output_tokens, (int, float)) and output_tokens > 0:
                    est_input_chars = sum(len(str(p)) for p in content_blocks) if ('content_blocks' in locals() and content_blocks) else 0
                    input_tokens = int(est_input_chars / 4)
                    tokens = int(output_tokens) + input_tokens
                elif 'total_usage_tokens' in locals() and isinstance(total_usage_tokens, (int, float)) and total_usage_tokens > 0:
                    tokens = int(total_usage_tokens)
                else:
                    est_content_chars = sum(len(str(p)) for p in content_blocks) if ('content_blocks' in locals() and content_blocks) else 0
                    thought_len = len(full_reasoning) + len(full_text)
                    tokens = int((est_content_chars + thought_len) / 4)

                if tokens > 0 and hasattr(self, 'tokens_consumed'):
                    self.tokens_consumed.emit(tokens)

                # Determine combined thought output for the agent's monologue
                emitted_thought = full_text
                if full_reasoning and full_text:
                    emitted_thought = f"{full_reasoning}\n\n{full_text}"
                elif full_reasoning and not full_text:
                    emitted_thought = full_reasoning

                logging.debug(
                    f"About to emit thought_received. Text length: {len(emitted_thought)}, Tools: {len(function_calls)}")
                self.thought_received.emit(emitted_thought, function_calls)
                self.is_processing = False
                return

            except Exception as e:
                is_last_attempt = (attempt_idx == len(models_to_attempt) - 1)
                err_str = str(e)
                if not is_last_attempt:
                    logging.warning(
                        f"DeepSeek model {model_name} failed: {err_str}. Falling back to light model {self.light_model}...")
                    continue
                logging.error(f"DeepSeek API Error: {err_str}", exc_info=True)
                self.history = self.history[:history_snapshot_len]
                self.error_occurred.emit(f"DeepSeek API Error: {err_str}")
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
            light_model = self.settings.get("core.deepseek.light-model", "")
            if not light_model:
                legacy_l = self.settings.get("core.deepseek.light-models", ["deepseek-flash"])
                light_model = legacy_l[0] if isinstance(legacy_l, list) and legacy_l else "deepseek-flash"

            response = self.client.chat.completions.create(
                model=light_model,
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
            logging.warning(f"DeepSeek Reformulator API Error: {e}")
            return user_prompt
