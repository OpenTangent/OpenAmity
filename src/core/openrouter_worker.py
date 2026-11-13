from core.prompt_context import (
    MemoryContextWorkerMixin,
    fit_system_prompt,
    format_prompt_footprint,
    inject_static_worker_instructions,
)
import os
import time
import threading
import logging
import json
import base64
import mimetypes
import uuid
import urllib.request
from dotenv import load_dotenv
from .events import Signal
from .settings_manager import SettingsManager

try:
    import openai
except ImportError:
    openai = None

# Load environment variables
load_dotenv()

logger = logging.getLogger("core.OpenrouterWorker")


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


def _normalize_model_name(model_name: str) -> str:
    """
    Normalizes a model identifier for OpenRouter.
    If the model already includes an organization prefix (contains '/'), it is returned as-is.
    Otherwise, maps well-known model families to their canonical OpenRouter slugs.
    """
    if not model_name:
        return model_name
    m = model_name.strip()
    if "/" in m:
        return m
    low = m.lower()
    if "mimo" in low:
        return f"xiaomi/{low}"
    if low.startswith("gpt-") or low.startswith("o1") or low.startswith("o3") or low.startswith("o4"):
        return f"openai/{low}"
    if low.startswith("claude-"):
        return f"anthropic/{low}"
    if low.startswith("deepseek-"):
        return f"deepseek/{low}"
    if low.startswith("gemini-"):
        return f"google/{low}"
    if low.startswith("llama-"):
        return f"meta-llama/{low}"
    if low.startswith("qwen-"):
        return f"qwen/{low}"
    if low.startswith("mistral-"):
        return f"mistralai/{low}"
    return m


_unsupported_audio_models = set()
_model_modalities_cache = {}
_cache_loaded = False


def _get_cache_file_path(filename: str) -> str:
    try:
        from config.paths import get_app_cache_dir
        cache_dir = get_app_cache_dir()
    except Exception:
        cache_dir = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, filename)


def _load_audio_capabilities_cache():
    global _unsupported_audio_models, _model_modalities_cache, _cache_loaded
    if _cache_loaded:
        return
    _cache_loaded = True

    # 1. Load unsupported models list
    try:
        unsupported_file = _get_cache_file_path("openrouter_unsupported_audio_models.json")
        if os.path.exists(unsupported_file):
            with open(unsupported_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    _unsupported_audio_models.update(data)
    except Exception as e:
        logger.debug(f"Failed to load OpenRouter unsupported audio cache: {e}")

    # 2. Load model modalities cache
    try:
        modalities_file = _get_cache_file_path("openrouter_model_modalities.json")
        if os.path.exists(modalities_file):
            with open(modalities_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    _model_modalities_cache.update(data)
    except Exception as e:
        logger.debug(f"Failed to load OpenRouter modalities cache: {e}")


def _save_unsupported_audio_cache():
    try:
        unsupported_file = _get_cache_file_path("openrouter_unsupported_audio_models.json")
        with open(unsupported_file, "w", encoding="utf-8") as f:
            json.dump(sorted(list(_unsupported_audio_models)), f, indent=2)
    except Exception as e:
        logger.debug(f"Failed to save OpenRouter unsupported audio cache: {e}")


def _save_modalities_cache():
    try:
        modalities_file = _get_cache_file_path("openrouter_model_modalities.json")
        with open(modalities_file, "w", encoding="utf-8") as f:
            json.dump(_model_modalities_cache, f, indent=2)
    except Exception as e:
        logger.debug(f"Failed to save OpenRouter modalities cache: {e}")


def _model_supports_audio_input(model_name: str) -> bool:
    """
    Determines whether a given OpenRouter model supports native multimodal audio input (input_audio).
    Checks known unsupported records, cached catalog modalities, and model family heuristics.
    Fails closed (returns False) for non-audio or unknown models to prevent HTTP 404 routing errors.
    """
    if not model_name:
        return False
    _load_audio_capabilities_cache()
    norm = _normalize_model_name(model_name)
    low = norm.lower()

    # 1. If previously recorded as unsupported on OpenRouter, fail closed
    if norm in _unsupported_audio_models or model_name in _unsupported_audio_models or low in _unsupported_audio_models:
        return False

    # 2. Check catalog modalities cache if available
    if norm in _model_modalities_cache:
        mods = _model_modalities_cache[norm]
        if isinstance(mods, list):
            return "audio" in mods
    if model_name in _model_modalities_cache:
        mods = _model_modalities_cache[model_name]
        if isinstance(mods, list):
            return "audio" in mods

    # 3. Known non-audio prefixes: OpenRouter routes these through 'Filter by Input Audio Support' with 0 endpoints
    non_audio_prefixes = (
        "openai/",
        "anthropic/",
        "deepseek/",
        "meta-llama/",
        "mistralai/",
        "cohere/",
        "x-ai/",
        "amazon/",
        "gryphe/",
        "nousresearch/",
        "microsoft/",
        "liquid/",
        "perplexity/",
        "google/gemma",
    )
    # Models with explicit audio markers support audio
    if "-audio" in low or "-omni" in low:
        return True

    # If it starts with a known non-audio family, return False immediately
    if any(low.startswith(p) for p in non_audio_prefixes):
        return False

    # Known audio-capable families on OpenRouter
    if low.startswith("google/gemini") and not ("-image" in low or "banana" in low):
        return True
    if any(prefix in low for prefix in ["xiaomi/mimo", "meta/muse", "thinkingmachines/inkling"]):
        return True

    # Safe default: fail closed to avoid HTTP 404 routing failures
    return False


def _record_unsupported_audio_model(model_name: str):
    """Permanently records that a model does not support audio input on OpenRouter."""
    if not model_name:
        return
    _load_audio_capabilities_cache()
    norm = _normalize_model_name(model_name)
    _unsupported_audio_models.add(norm)
    _unsupported_audio_models.add(model_name)
    _unsupported_audio_models.add(norm.lower())
    _save_unsupported_audio_cache()


def _is_quota_error(err: Exception) -> bool:
    """
    Checks whether an error indicates that the standard AI model has reached its quota
    or hit a rate limit (HTTP 429, 402, or explicit quota/credit exhaustion).
    """
    if err is None:
        return False
    status_code = getattr(err, "status_code", None)
    if status_code is None and hasattr(err, "response") and err.response is not None:
        status_code = getattr(err.response, "status_code", None)
    if status_code in (429, 402):
        return True

    err_str = str(err).lower()
    quota_indicators = (
        "429",
        "quota",
        "rate limit",
        "rate_limit",
        "ratelimit",
        "resource_exhausted",
        "credits",
        "insufficient_quota",
        "payment required",
        "balance is too low",
        "credit balance",
        "insufficient balance",
    )
    return any(indicator in err_str for indicator in quota_indicators)


def _strip_multimodal_from_content(content):
    """
    Strips multimodal content blocks (image_url, input_audio, file) from a message's content,
    replacing them with descriptive text placeholders.
    If all resulting blocks are text, collapses the list into a single string to avoid schema validation errors.
    Returns (sanitized_content, was_modified).
    """
    if isinstance(content, str):
        return content, False
    if not isinstance(content, list):
        return content, False

    new_content = []
    modified = False
    for part in content:
        if isinstance(part, dict):
            ptype = part.get("type")
            if ptype == "input_audio":
                text_content = "[Audio attachment stripped: multimodal audio input is unsupported or failed]"
                audio_info = part.get("input_audio", {})
                b64_data = audio_info.get("data") if isinstance(audio_info, dict) else None
                if b64_data:
                    try:
                        import tempfile
                        import base64
                        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_f:
                            tmp_f.write(base64.b64decode(b64_data))
                            tmp_wav = tmp_f.name
                        from core.local_stt import LocalSTT
                        stt_text = LocalSTT().transcribe(tmp_wav)
                        if os.path.exists(tmp_wav):
                            os.remove(tmp_wav)
                        if stt_text and not stt_text.startswith("Failed"):
                            text_content += f"\n[Voice Message Transcription: {stt_text}]"
                    except Exception:
                        pass
                new_content.append({
                    "type": "text",
                    "text": text_content
                })
                modified = True
            elif ptype == "image_url":
                new_content.append({
                    "type": "text",
                    "text": "[Image attachment stripped: multimodal image input is unsupported or failed]"
                })
                modified = True
            elif ptype == "file":
                new_content.append({
                    "type": "text",
                    "text": "[File attachment stripped: multimodal file input is unsupported or failed]"
                })
                modified = True
            else:
                new_content.append(part)
        else:
            new_content.append(part)

    if not modified:
        return content, False

    if all(isinstance(p, dict) and p.get("type") == "text" for p in new_content):
        collapsed = "\n".join(p.get("text", "") for p in new_content if p.get("text"))
        return collapsed, True

    return new_content, True


def _strip_multimodal_from_history(history: list) -> bool:
    """Strips multimodal content blocks in-place from history turns. Returns True if any were modified."""
    modified_any = False
    for msg in history:
        if isinstance(msg, dict) and msg.get("role") == "user" and isinstance(msg.get("content"), list):
            new_content, modified = _strip_multimodal_from_content(msg["content"])
            if modified:
                msg["content"] = new_content
                modified_any = True
    return modified_any


def _has_multimodal_content(history: list) -> bool:
    """Checks if any user message in history contains multimodal content blocks."""
    for msg in history:
        if isinstance(msg, dict) and msg.get("role") == "user" and isinstance(msg.get("content"), list):
            for part in msg["content"]:
                if isinstance(part, dict) and part.get("type") in ("image_url", "input_audio", "file"):
                    return True
    return False


class FunctionCallObject:
    def __init__(self, name, args):
        self.name = name
        self.args = args


class OpenRouterWorker(MemoryContextWorkerMixin):
    normalize_model_name = staticmethod(_normalize_model_name)
    _normalize_model_name = staticmethod(_normalize_model_name)
    model_supports_audio_input = staticmethod(_model_supports_audio_input)
    record_unsupported_audio_model = staticmethod(_record_unsupported_audio_model)

    def __init__(self, agent_id=None):
        super().__init__()
        self.agent_id = agent_id
        logging.debug("OpenRouterWorker.__init__ called.")
        self.client = None
        self.settings = SettingsManager(agent_id=self.agent_id)
        self.thought_received = Signal()  # text, list of function calls
        self.error_occurred = Signal()
        self.tokens_consumed = Signal()  # int

        self.api_key = self.settings.get_env("OPENROUTER_API_KEY")
        self.available = False
        self.last_error = None
        if openai is None:
            self.last_error = "openai package is not installed"
            if not self.settings.get("core.first-run", False):
                logging.error(self.last_error)
                self.error_occurred.emit("OpenAI package not installed.")
        elif not self.api_key:
            self.last_error = "OPENROUTER_API_KEY not found in .env"
            if self.settings.get("core.first-run", False):
                logging.info(
                    "OPENROUTER_API_KEY not found in .env (expected on first run)")
            else:
                logging.error(self.last_error)
                self.error_occurred.emit(
                    "Missing OpenRouter API Key. Please check settings.")
        else:
            try:
                self.client = openai.OpenAI(
                    api_key=self.api_key,
                    base_url="https://openrouter.ai/api/v1",
                    default_headers={
                        "HTTP-Referer": "https://github.com/OpenTangent/OpenAmity",
                        "X-Title": "Open Amity"
                    }
                )
                self.available = True
            except Exception as e:
                self.last_error = f"Error initializing OpenRouter Client: {e}"
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
        self.current_model = "MiMo-V2.6-Pro"
        self.primary_model = "MiMo-V2.6-Pro"
        self.light_model = "gpt-6-luna"
        self.multimodal_failed = False

        # Audio STT setup
        from .local_stt import LocalSTT
        self.local_stt = LocalSTT()

    def is_running(self):
        return self.running

    def start_session(self, system_instruction=None, tools=None, clear_history=False):
        logging.debug("start_session called in OpenRouterWorker")
        if not getattr(self, 'available', False):
            logging.error(
                "Attempted to start session but OpenRouterWorker is unavailable.")
            self.error_occurred.emit(
                "Session Start Error: OpenRouter Worker is not available")
            return

        self._append_worker_static_instructions = True
        self.sys_instruct = inject_static_worker_instructions(system_instruction or "")
        self.sys_instruct = fit_system_prompt(self.sys_instruct, self.settings, tools)
        logging.debug(f"OpenRouterWorker prompt footprint: {format_prompt_footprint(self.sys_instruct, tools)}")
        self.tools = tools
        self.openai_tools = []
        if self.tools:
            for t in self.tools:
                self.openai_tools.append(_convert_to_openai_tool(t))

        if clear_history:
            self.history = []
            self.consumed_tool_call_ids = set()
            self.multimodal_failed = False

        # Determine model
        is_low_token = self.settings.get("core.low-token-mode", False)
        primary_model = self.settings.get("core.openrouter.model", "")
        if not primary_model:
            legacy = self.settings.get("core.openrouter.openrouter-models", ["MiMo-V2.6-Pro"])
            primary_model = legacy[0] if isinstance(legacy, list) and legacy else "MiMo-V2.6-Pro"

        light_model = self.settings.get("core.openrouter.light-model", "")
        if not light_model:
            legacy_l = self.settings.get("core.openrouter.light-models", ["gpt-6-luna"])
            light_model = legacy_l[0] if isinstance(legacy_l, list) and legacy_l else "gpt-6-luna"

        self.primary_model = primary_model
        self.light_model = light_model
        self.current_model = light_model if is_low_token else primary_model

        self.running = True
        logging.info(
            f"OpenRouter session started with model {self.current_model}.")

    def get_session_history(self) -> list:
        """Exports active chat history into a normalized list of turns."""
        messages = []
        for m in self.history:
            role = m.get("role", "user")
            raw_c = m.get("content", "") or ""
            text_parts = []
            if m.get("reasoning"):
                text_parts.append(m["reasoning"])
            elif m.get("reasoning_content"):
                text_parts.append(m["reasoning_content"])
            if isinstance(raw_c, str):
                if raw_c.strip():
                    text_parts.append(raw_c)
            elif isinstance(raw_c, list):
                for block in raw_c:
                    if isinstance(block, dict):
                        b_type = block.get("type", "")
                        if b_type == "text":
                            t = block.get("text", "")
                            if t:
                                text_parts.append(t)
                        elif b_type in ("image_url", "file", "input_audio"):
                            text_parts.append("[Media attachment from earlier in session]")
            content = "\n".join(text_parts).strip()
            if m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    fn = tc.get("function", {}) if isinstance(tc, dict) else getattr(tc, "function", None)
                    if isinstance(fn, dict):
                        name = fn.get("name", "")
                        args = fn.get("arguments", "")
                    else:
                        name = getattr(fn, "name", "")
                        args = getattr(fn, "arguments", "")
                    content += f"\n[Tool invocation: {name}({args})]"
            if role == "tool":
                messages.append({"role": "user", "content": f"[Tool Result: {content.strip()}]"})
            else:
                messages.append({"role": role, "content": content.strip()})
        return messages

    def set_session_history(self, messages: list):
        """Restores chat history from normalized turns."""
        from core.session_cache import normalize_session_messages
        norm_messages = normalize_session_messages(messages, close_trailing_user=True)
        self.history = []
        self.consumed_tool_call_ids = set()
        for msg in norm_messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if content:
                self.history.append({"role": role, "content": content})

    def stop_session(self):
        self.running = False
        self.history = []
        self.consumed_tool_call_ids = set()
        self.multimodal_failed = False
        logging.info("OpenRouter session stopped.")

    def abort(self):
        self._abort_flag = True
        self.is_processing = False

    def send_prompt(self, prompt: str, image_path: str = None, yolo: bool = False, audio_path: str = None):
        logging.debug("send_prompt called in OpenRouterWorker")
        if not self.running:
            self.error_occurred.emit("Session not started.")
            return

        self._abort_flag = False
        self.is_processing = True
        threading.Thread(target=self._process_thought, args=(
            prompt, image_path, yolo, audio_path), daemon=True).start()

    def _prepare_media_block(self, media_path: str, detail: str = "auto") -> dict:
        if not media_path:
            return None

        # Check if external URL
        if media_path.startswith("http://") or media_path.startswith("https://"):
            ext = os.path.splitext(media_path.split("?")[0])[1].lower()
            if ext == ".pdf":
                return {
                    "type": "file",
                    "file": {
                        "filename": os.path.basename(media_path.split("?")[0]) or "document.pdf",
                        "file_data": media_path
                    }
                }
            return {
                "type": "image_url",
                "image_url": {
                    "url": media_path,
                    "detail": detail
                }
            }

        exp_path = os.path.expanduser(media_path)
        if not os.path.exists(exp_path):
            raise FileNotFoundError(f"Media file not found: {exp_path}")

        file_size = os.path.getsize(exp_path)
        if file_size > 32 * 1024 * 1024:
            raise ValueError(
                f"Media {exp_path} exceeds 32 MiB inline limit.")

        mime_type, _ = mimetypes.guess_type(exp_path)
        ext = os.path.splitext(exp_path)[1].lower()

        # Handle PDF documents via OpenRouter's native file content block
        if mime_type == "application/pdf" or ext == ".pdf":
            with open(exp_path, "rb") as doc_file:
                b64_data = base64.b64encode(doc_file.read()).decode("utf-8")
            return {
                "type": "file",
                "file": {
                    "filename": os.path.basename(exp_path),
                    "file_data": f"data:application/pdf;base64,{b64_data}"
                }
            }

        # Handle Audio files (native multimodal audio input for audio models, LocalSTT transcription for text/vision models)
        if (mime_type and mime_type.startswith("audio/")) or ext in [".wav", ".mp3", ".ogg", ".flac", ".m4a", ".opus", ".aac"]:
            active_model = getattr(self, "current_model", None) or getattr(self, "primary_model", None)
            if not self.model_supports_audio_input(active_model):
                transcription = None
                try:
                    from core.local_stt import LocalSTT
                    transcription = LocalSTT().transcribe(exp_path)
                except Exception as e:
                    logger.debug(f"LocalSTT failed for {exp_path}: {e}")

                if transcription and not transcription.startswith("Failed"):
                    return {
                        "type": "text",
                        "text": f"[Voice Message Audio: {os.path.basename(exp_path)}]\n[Voice Message Transcription: {transcription}]"
                    }
                else:
                    return {
                        "type": "text",
                        "text": f"[Audio file attached: {os.path.basename(exp_path)}. Note: Model {active_model} does not support multimodal audio input directly. To extract text or messages from this audio file, use the Media_transcribe_audio tool.]"
                    }

            with open(exp_path, "rb") as f:
                b64_data = base64.b64encode(f.read()).decode("utf-8")
            fmt = ext.lstrip(".").lower()
            if fmt == "opus":
                fmt = "ogg"
            elif fmt not in ["wav", "mp3", "ogg", "flac", "aac", "m4a", "pcm16"]:
                fmt = "wav"
            return {
                "type": "input_audio",
                "input_audio": {
                    "data": b64_data,
                    "format": fmt
                }
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

            with open(exp_path, "rb") as image_file:
                image_data = base64.b64encode(image_file.read()).decode("utf-8")

            return {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{norm_mime};base64,{image_data}",
                    "detail": detail
                }
            }

        raise ValueError(
            f"Unsupported media format '{mime_type or ext}' for {exp_path}. "
            "Supported formats: images (JPEG, PNG, GIF, WebP), PDF documents, audio files."
        )

    def _prepare_image_block(self, image_path: str, detail: str = "auto") -> dict:
        return self._prepare_media_block(image_path, detail=detail)

    def _cull_media_from_history(self):
        # Intelligent Media Culling: Strip heavy multimodal attachments from older turns (> 4 turns)
        if len(self.history) > 4:
            for i in range(len(self.history) - 4):
                msg = self.history[i]
                if msg.get("role") == "user" and isinstance(msg.get("content"), list):
                    new_content = []
                    modified = False
                    for part in msg["content"]:
                        if isinstance(part, dict) and part.get("type") in ("image_url", "file", "input_audio"):
                            new_content.append({
                                "type": "text",
                                "text": "[Media attachment automatically culled to save tokens/memory]"
                            })
                            modified = True
                        else:
                            new_content.append(part)
                    if modified:
                        if all(isinstance(p, dict) and p.get("type") == "text" for p in new_content):
                            msg["content"] = "\n".join(p["text"] for p in new_content if p.get("text"))
                        else:
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

        # If any tool execution produced media attachments, append them in a follow-up user message
        if attached_media:
            is_low_token = self.settings.get("core.low-token-mode", False)
            if is_low_token:
                self.history.append({
                    "role": "user",
                    "content": "[Tool media attachments skipped: Low Token Mode is active]"
                })
            elif getattr(self, "multimodal_failed", False):
                self.history.append({
                    "role": "user",
                    "content": f"[Tool media attachments skipped: multimodal input is unsupported or failed on this model: {', '.join(os.path.basename(m) for m in attached_media)}]"
                })
            else:
                user_content_blocks = [
                    {"type": "text", "text": "[Attached media from tool execution]:"}
                ]
                for m_path in attached_media:
                    try:
                        media_block = self._prepare_media_block(m_path)
                        if media_block:
                            user_content_blocks.append(media_block)
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
        Also guarantees valid role alternation by inserting assistant spacers
        if a tool message is followed by a user message, and merging consecutive user messages.
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
                            f"OpenRouterWorker synthesized dummy tool response for unfulfilled tool_call_id: {tid}")

                i = j - 1
            i += 1

        def _is_tool_media(msg):
            if not isinstance(msg, dict) or msg.get("role") != "user":
                return False
            c = msg.get("content")
            if isinstance(c, str):
                return c.startswith("[Attached media from tool execution]") or c.startswith("[Tool media attachments skipped")
            if isinstance(c, list) and c:
                first = c[0]
                if isinstance(first, dict) and first.get("type") == "text":
                    t = first.get("text", "")
                    return t.startswith("[Attached media from tool execution]") or t.startswith("[Tool media attachments skipped")
            return False

        # Second pass: ensure role alternation and that unfulfilled or completed tool turns
        # are cleanly closed with an assistant spacer before any new user/pulse turn starts.
        sanitized_history = []
        for m in new_history:
            if sanitized_history:
                prev_msg = sanitized_history[-1]
                prev_role = prev_msg.get("role")
                curr_role = m.get("role")
                is_curr_tool_media = _is_tool_media(m)
                is_prev_tool_media = _is_tool_media(prev_msg)

                if curr_role == "user" and not is_curr_tool_media:
                    # New user or pulse turn arriving after tool execution or tool media
                    if prev_role == "tool" or is_prev_tool_media:
                        sanitized_history.append({"role": "assistant", "content": ""})
                    elif prev_role == "user":
                        # Consecutive user turns: merge them if plain strings
                        prev_c = prev_msg.get("content", "")
                        curr_c = m.get("content", "")
                        if isinstance(prev_c, str) and isinstance(curr_c, str):
                            prev_msg["content"] = f"{prev_c}\n\n{curr_c}"
                            continue
                        else:
                            sanitized_history.append({"role": "assistant", "content": ""})
            sanitized_history.append(m)

        self.history = sanitized_history

    def _process_thought(self, prompt=None, image_path=None, yolo=False, audio_path=None):
        from core.logger_config import agent_id_var
        if hasattr(self, 'agent_id'):
            agent_id_var.set(self.agent_id)

        try:
            self._process_thought_inner(prompt, image_path, yolo, audio_path)
        except Exception as e:
            logging.error(f"OpenRouterWorker unhandled error in _process_thought: {e}", exc_info=True)
            self.error_occurred.emit(f"OpenRouter API Error: {e}")
        finally:
            self.is_processing = False

    def _process_thought_inner(self, prompt=None, image_path=None, yolo=False, audio_path=None):

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
                elif getattr(self, "multimodal_failed", False):
                    content_blocks.append(
                        {"type": "text", "text": f"[Image attachment skipped: multimodal input is unsupported or failed on this model: {os.path.basename(image_path)}]"})
                else:
                    try:
                        img_block = self._prepare_media_block(image_path)
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
                elif getattr(self, "multimodal_failed", False) or not self.model_supports_audio_input(getattr(self, "current_model", None) or getattr(self, "primary_model", None)):
                    text_placeholder = f"[Audio attachment skipped: multimodal audio input is unsupported or failed on this model: {os.path.basename(audio_path)}]"
                    try:
                        from core.local_stt import LocalSTT
                        stt_text = LocalSTT().transcribe(audio_path)
                        if stt_text and not stt_text.startswith("Failed"):
                            text_placeholder += f"\n[Voice Message Transcription: {stt_text}]"
                    except Exception:
                        pass
                    content_blocks.append({
                        "type": "text",
                        "text": text_placeholder
                    })
                else:
                    try:
                        ext = os.path.splitext(audio_path)[1].lower()
                        fmt = ext.lstrip(".").lower()
                        if fmt == "opus":
                            fmt = "ogg"
                        elif fmt not in ["wav", "mp3", "ogg", "flac", "aac", "m4a", "pcm16"]:
                            fmt = "wav"
                        with open(audio_path, "rb") as f:
                            b64_audio = base64.b64encode(f.read()).decode("utf-8")
                        content_blocks.append({
                            "type": "input_audio",
                            "input_audio": {
                                "data": b64_audio,
                                "format": fmt
                            }
                        })
                    except Exception as e:
                        logging.warning(f"Audio load error for OpenRouter: {e}, attempting LocalSTT...")
                        try:
                            from core.local_stt import LocalSTT
                            stt_text = LocalSTT().transcribe(audio_path)
                            if stt_text and not stt_text.startswith("Failed"):
                                content_blocks.append({
                                    "type": "text",
                                    "text": f"[Voice Message Transcription: {stt_text}]"
                                })
                            else:
                                content_blocks.append({
                                    "type": "text",
                                    "text": f"[Audio attachment skipped: {e}]"
                                })
                        except Exception:
                            content_blocks.append({
                                "type": "text",
                                "text": f"[Audio attachment skipped: {e}]"
                            })

            if all(isinstance(p, dict) and p.get("type") == "text" for p in content_blocks):
                user_msg = {"role": "user", "content": "\n".join(p["text"] for p in content_blocks if p.get("text"))}
            else:
                user_msg = {"role": "user", "content": content_blocks}
            self.history.append(user_msg)

        self._reconcile_tool_calls()
        self._cull_media_from_history()

        active_model = self.current_model
        multimodal_retried = False

        while True:
            try:
                norm_model = _normalize_model_name(active_model)
                logging.debug(
                    f"Calling OpenRouter with message length {len(self.history)} using model {norm_model} (configured: {active_model})...")

                messages = [{"role": "system", "content": self.sys_instruct or ""}] + self.history

                thinking_level = str(self.settings.get("core.openrouter.thinking-level", "low")).lower()
                if thinking_level not in ["low", "medium", "high", "max"]:
                    thinking_level = "low"

                kwargs = {
                    "model": norm_model,
                    "messages": messages,
                    "stream": True,
                    "stream_options": {"include_usage": True},
                    "extra_body": {
                        "reasoning": {
                            "effort": thinking_level
                        }
                    }
                }
                if self.openai_tools:
                    kwargs["tools"] = self.openai_tools

                full_text = ""
                full_reasoning = ""
                tool_calls_data = {}
                output_tokens = 0
                total_usage_tokens = 0

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

                    # Extract reasoning tokens from OpenRouter normalized responses
                    r = getattr(delta, 'reasoning', None)
                    rc = getattr(delta, 'reasoning_content', None)
                    rd = getattr(delta, 'reasoning_details', None)

                    if isinstance(r, str) and r:
                        full_reasoning += r
                    elif isinstance(rc, str) and rc:
                        full_reasoning += rc
                    elif rd:
                        if isinstance(rd, list):
                            for r_item in rd:
                                if isinstance(r_item, dict) and r_item.get("text"):
                                    full_reasoning += str(r_item["text"])
                                elif hasattr(r_item, "text") and isinstance(r_item.text, str):
                                    full_reasoning += r_item.text

                    c = getattr(delta, 'content', None)
                    if isinstance(c, str) and c:
                        full_text += c

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
                    assistant_msg["reasoning"] = full_reasoning

                self.history.append(assistant_msg)
                self.current_model = active_model

                # Token tracking (excluding static system instruction and tool declarations)
                tokens = 0
                if output_tokens > 0:
                    est_input_chars = sum(len(str(p)) for p in content_blocks) if ('content_blocks' in locals() and content_blocks) else 0
                    input_tokens = int(est_input_chars / 4)
                    tokens = int(output_tokens) + input_tokens
                elif total_usage_tokens > 0:
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
                err_str = str(e)

                # 1. If Multimodal input fails on standard model: strip input and retry with standard model
                if not multimodal_retried and active_model != self.light_model and _has_multimodal_content(self.history) and not _is_quota_error(e):
                    logging.warning(
                        f"OpenRouter model {active_model} failed with multimodal input: {err_str}. "
                        f"Stripping multimodal input and retrying on standard model {active_model}..."
                    )
                    self.multimodal_failed = True
                    if any(term in err_str.lower() for term in ("input audio", "input_audio", "support audio", "modality")):
                        self.record_unsupported_audio_model(active_model)
                    _strip_multimodal_from_history(self.history)
                    multimodal_retried = True
                    continue

                # 2. Only fall back to light model if standard AI model has reached its quota
                if active_model != self.light_model and _is_quota_error(e):
                    logging.warning(
                        f"OpenRouter model {active_model} reached quota: {err_str}. "
                        f"Falling back to light model {self.light_model}..."
                    )
                    self.multimodal_failed = True
                    _strip_multimodal_from_history(self.history)
                    active_model = self.light_model
                    continue

                # 3. Non-quota error or light model failure: do not fall back to light model
                logging.error(f"OpenRouter API Error: {err_str}", exc_info=True)
                self.history = self.history[:history_snapshot_len]
                self.error_occurred.emit(f"OpenRouter API Error: {err_str}")
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
            light_model = self.settings.get("core.openrouter.light-model", "")
            if not light_model:
                legacy_l = self.settings.get("core.openrouter.light-models", ["gpt-6-luna"])
                light_model = legacy_l[0] if isinstance(legacy_l, list) and legacy_l else "gpt-6-luna"

            norm_light = _normalize_model_name(light_model)
            response = self.client.chat.completions.create(
                model=norm_light,
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
            logging.warning(f"OpenRouter Reformulator API Error: {e}")
            return user_prompt

    @classmethod
    def fetch_available_models(cls, api_key: str = None) -> list:
        """
        Queries OpenRouter's /api/v1/models endpoint to fetch the list of active model IDs.
        Can run without an API key (public catalog) or with an API key.
        Returns a sorted list of model ID strings.
        """
        headers = {
            "User-Agent": "OpenAmity",
            "HTTP-Referer": "https://github.com/OpenTangent/OpenAmity",
            "X-Title": "Open Amity"
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key.strip()}"

        logger.debug("Fetching OpenRouter model catalog from https://openrouter.ai/api/v1/models...")
        req = urllib.request.Request("https://openrouter.ai/api/v1/models", headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            raw_models = data.get("data", [])
            _load_audio_capabilities_cache()
            for m in raw_models:
                if isinstance(m, dict) and "id" in m:
                    arch = m.get("architecture", {})
                    if isinstance(arch, dict) and "input_modalities" in arch:
                        _model_modalities_cache[m["id"]] = arch["input_modalities"]
            _save_modalities_cache()
            model_ids = [m["id"] for m in raw_models if isinstance(m, dict) and "id" in m]
            sorted_models = sorted(model_ids, key=lambda s: s.lower())
            logger.info(f"Successfully fetched {len(sorted_models)} models from OpenRouter catalog.")
            return sorted_models

    @classmethod
    def fetch_available_image_models(cls, api_key: str = None) -> list:
        """
        Queries OpenRouter's /api/v1/models?output_modalities=image endpoint to fetch the list of active image generation model IDs.
        Can run without an API key (public catalog) or with an API key.
        Returns a sorted list of model ID strings.
        """
        headers = {
            "User-Agent": "OpenAmity",
            "HTTP-Referer": "https://github.com/OpenTangent/OpenAmity",
            "X-Title": "Open Amity"
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key.strip()}"

        logger.debug("Fetching OpenRouter image model catalog from https://openrouter.ai/api/v1/models?output_modalities=image...")
        req = urllib.request.Request("https://openrouter.ai/api/v1/models?output_modalities=image", headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            raw_models = data.get("data", [])
            model_ids = []
            for m in raw_models:
                if not isinstance(m, dict) or "id" not in m:
                    continue
                arch = m.get("architecture", {})
                out_mods = arch.get("output_modalities", []) if isinstance(arch, dict) else []
                if out_mods and "image" not in out_mods:
                    continue
                model_ids.append(m["id"])
            sorted_models = sorted(list(set(model_ids)), key=lambda s: s.lower())
            logger.info(f"Successfully fetched {len(sorted_models)} image models from OpenRouter catalog.")
            return sorted_models

