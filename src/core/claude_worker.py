from core.prompt_context import MemoryContextWorkerMixin, fit_system_prompt
import os
import base64
import mimetypes
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


class ClaudeWorker(MemoryContextWorkerMixin):
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

    def start_session(self, system_instruction=None, tools=None, clear_history=False):
        logging.debug("start_session called in ClaudeWorker")
        if not getattr(self, 'available', False):
            logging.error(
                "Attempted to start session but ClaudeWorker is unavailable.")
            self.error_occurred.emit(
                "Session Start Error: Claude Worker is not available")
            return

        self.sys_instruct = system_instruction or ""
        self.sys_instruct += "\n\nCRITICAL INSTRUCTION: You must output your internal reasoning as plain text BEFORE invoking any tool(s). You MUST invoke your intended tool(s) in the EXACT SAME TURN immediately following your reasoning. If you intend to use tools don't end your turn after your reasoning, you must emit the tool call(s) in that same turn."
        self.sys_instruct += "\n\nAUTONOMOUS SPEECH INSTRUCTION: You are fully autonomous regarding your speech. You will NOT speak automatically. If you wish to communicate with the user, you MUST explicitly use the Speaker tool (e.g., Speaker_speak_aloud). Otherwise, you will remain completely silent. Speak from a first-person perspective."

        self.sys_instruct = fit_system_prompt(self.sys_instruct, self.settings, tools)
        self.tools = tools
        self.anthropic_tools = []
        if self.tools:
            for t in self.tools:
                self.anthropic_tools.append(_convert_to_anthropic_tool(t))

        with self._history_lock:
            if clear_history:
                self.history = []
                self.consumed_tool_use_ids = set()

        # Determine model
        is_low_token = self.settings.get("core.low-token-mode", False)
        primary_model = self.settings.get("core.claude.model", "")
        if not primary_model:
            legacy = self.settings.get("core.claude.claude-models", ["claude-fable-5-1"])
            primary_model = legacy[0] if isinstance(legacy, list) and legacy else "claude-fable-5-1"

        light_model = self.settings.get("core.claude.light-model", "")
        if not light_model:
            legacy_l = self.settings.get("core.claude.light-models", ["claude-haiku-4-5"])
            light_model = legacy_l[0] if isinstance(legacy_l, list) and legacy_l else "claude-haiku-4-5"

        self.primary_model = primary_model
        self.light_model = light_model
        self.current_model = light_model if is_low_token else primary_model

        self.running = True
        logging.info(
            f"Claude SDK sessions started with model {self.current_model}.")

    def get_session_history(self) -> list:
        """Exports active chat history into a normalized list of turns."""
        with self._history_lock:
            messages = []
            for m in self.history:
                role = m.get("role", "user")
                c = m.get("content", "")
                if isinstance(c, str):
                    messages.append({"role": role, "content": c})
                elif isinstance(c, list):
                    texts = []
                    for item in c:
                        if isinstance(item, dict):
                            if item.get("type") == "text":
                                texts.append(item.get("text", ""))
                            elif item.get("type") == "tool_use":
                                texts.append(f"[Tool invocation: {item.get('name')}({json.dumps(item.get('input', {}))})]")
                            elif item.get("type") == "tool_result":
                                texts.append(f"[Tool Result: {json.dumps(item.get('content', ''))}]")
                    messages.append({"role": role, "content": "\n".join(texts)})
            return messages

    def set_session_history(self, messages: list):
        """Restores chat history from normalized turns."""
        with self._history_lock:
            self.history = []
            self.consumed_tool_use_ids = set()
            for msg in messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if content:
                    self.history.append({"role": role, "content": content})

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

    def _prepare_media_block(self, media_path: str) -> dict:
        if not media_path:
            return None

        exp_path = os.path.expanduser(media_path)
        if not os.path.exists(exp_path):
            raise FileNotFoundError(f"Media file not found: {exp_path}")

        file_size = os.path.getsize(exp_path)
        if file_size > 32 * 1024 * 1024:
            raise ValueError(f"Media {exp_path} exceeds 32 MiB inline limit.")

        mime_type, _ = mimetypes.guess_type(exp_path)
        ext = os.path.splitext(exp_path)[1].lower()

        # Handle PDF documents via Claude's native document block
        if mime_type == "application/pdf" or ext == ".pdf":
            with open(exp_path, "rb") as f:
                b64_data = base64.b64encode(f.read()).decode("utf-8")
            return {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": b64_data
                }
            }

        # Claude does not natively support multimodal audio input.
        if (mime_type and mime_type.startswith("audio/")) or ext in [".wav", ".mp3", ".ogg", ".flac", ".m4a", ".opus", ".aac"]:
            return {
                "type": "text",
                "text": f"[Audio file attached: {exp_path}. Note: Claude does not support multimodal audio input directly. To extract text or messages from this audio file, use the Media_transcribe_audio tool.]"
            }

        # Handle Images
        supported_mimes = {"image/jpeg", "image/png", "image/gif", "image/webp"}
        if (mime_type and mime_type in supported_mimes) or ext in [".jpg", ".jpeg", ".png", ".gif", ".webp"]:
            if ext in [".jpg", ".jpeg"]:
                norm_mime = "image/jpeg"
            elif ext == ".png":
                norm_mime = "image/png"
            elif ext == ".gif":
                norm_mime = "image/gif"
            elif ext == ".webp":
                norm_mime = "image/webp"
            else:
                norm_mime = mime_type or "image/jpeg"

            with open(exp_path, "rb") as f:
                b64_data = base64.b64encode(f.read()).decode("utf-8")

            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": norm_mime,
                    "data": b64_data
                }
            }

        raise ValueError(
            f"Unsupported media format '{mime_type or ext}' for {exp_path}. "
            "Supported formats: images (JPEG, PNG, GIF, WebP), PDF documents, audio files."
        )

    def _prepare_image_block(self, image_path: str) -> dict:
        return self._prepare_media_block(image_path)

    def _cull_media_from_history(self):
        # Intelligent Media Culling: Strip heavy multimodal attachments from older turns (> 4 turns)
        if len(self.history) > 4:
            for i in range(len(self.history) - 4):
                msg = self.history[i]
                if msg.get("role") == "user" and isinstance(msg.get("content"), list):
                    new_content = []
                    modified = False
                    for part in msg["content"]:
                        if isinstance(part, dict) and part.get("type") in ("image", "document"):
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

        parts = []
        attached_media = []
        for name, response in responses:
            if isinstance(response, dict):
                clean_resp = dict(response)
                # Remove media from response dict
                if 'media' in clean_resp:
                    media_val = clean_resp.pop('media')
                    if isinstance(media_val, list):
                        attached_media.extend(media_val)
                    elif isinstance(media_val, str):
                        attached_media.append(media_val)
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

        if attached_media:
            is_low_token = self.settings.get("core.low-token-mode", False)
            if is_low_token:
                parts.append({"type": "text", "text": "[Tool media attachments skipped: Low Token Mode is active]"})
            else:
                for m_path in attached_media:
                    try:
                        media_block = self._prepare_media_block(m_path)
                        if media_block:
                            parts.append(media_block)
                    except Exception as e:
                        logging.warning(f"Failed to attach tool media for Claude {m_path}: {e}")

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
                    media_block = self._prepare_media_block(image_path)
                    if media_block:
                        content_blocks.append(media_block)
                except Exception as e:
                    self.error_occurred.emit(f"Image load error: {e}")
                    self.is_processing = False
                    return

        if audio_path:
            content_blocks.append(
                {"type": "text", "text": f"[Audio attached: {audio_path}. Claude does not support direct audio input; use Media_transcribe_audio to transcribe if needed.]"})

        # Add to history (keep reference for rollback on error)
        user_msg = {"role": "user", "content": content_blocks}
        with self._history_lock:
            self.history.append(user_msg)
            self._reconcile_tool_calls()
            self._cull_media_from_history()

        models_to_attempt = [self.current_model]
        if self.current_model != self.light_model:
            models_to_attempt.append(self.light_model)

        for attempt_idx, model_name in enumerate(models_to_attempt):
            try:
                logging.debug(
                    f"Calling Claude with message length {len(self.history)} using model {model_name}...")

                # Enable Anthropic Prompt Caching on system instructions
                system_payload = [
                    {
                        "type": "text",
                        "text": self.sys_instruct or "",
                        "cache_control": {"type": "ephemeral"}
                    }
                ]

                # Configure adaptive thinking effort level
                effort = self.settings.get("core.claude.thinking-level", "")
                if not effort:
                    effort = self.settings.get("core.claude.thinking-effort", "low")
                effort = str(effort).lower()

                kwargs = {
                    "model": model_name,
                    "system": system_payload,
                    "messages": self.history,
                    "max_tokens": 16384
                }
                if effort in ["low", "medium", "high", "max"]:
                    kwargs["output_config"] = {"effort": effort}
                else:
                    kwargs["thinking"] = {"type": "disabled"}

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

                self.current_model = model_name

                # Token tracking (excluding static system instruction, tool declarations, and prior history)
                tokens = 0
                if hasattr(message, 'usage') and message.usage:
                    output_tokens = getattr(message.usage, 'output_tokens', 0) or 0
                    est_input_chars = sum(len(str(p)) for p in content_blocks) if content_blocks else 0
                    input_tokens = int(est_input_chars / 4)
                    tokens = output_tokens + input_tokens
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
                is_last_attempt = (attempt_idx == len(models_to_attempt) - 1)
                err_str = str(e)
                if not is_last_attempt:
                    logging.warning(
                        f"Claude model {model_name} failed: {err_str}. Falling back to light model {self.light_model}...")
                    continue

                # Roll back the unpaired user message to keep history valid
                with self._history_lock:
                    if self.history and self.history[-1] is user_msg:
                        self.history.pop()
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
            # Use light model for fast, cheap tasks like query reformulation
            light_model = self.settings.get("core.claude.light-model", "")
            if not light_model:
                legacy_l = self.settings.get("core.claude.light-models", ["claude-haiku-4-5"])
                light_model = legacy_l[0] if isinstance(legacy_l, list) and legacy_l else "claude-haiku-4-5"

            response = self.client.messages.create(
                model=light_model,
                system=system_instruction,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=100
            )
            return response.content[0].text.strip()
        except Exception as e:
            logging.warning(f"Reformulator API Error: {e}")
            return user_prompt
