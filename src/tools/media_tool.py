import os
import logging
import datetime
from io import BytesIO
from typing import List, Dict, Any
from core.cerebrum import Tool
from config.paths import get_agent_data_dir, get_generated_media_dir
from core.cache_manager import CacheManager
from google import genai
from google.genai import types
from google.genai.types import Modality
from PIL import Image
import mimetypes
import json
import base64
import urllib.request



class MediaSkill(Tool):
    name = "Media"
    icon = "🖼️"
    color = "#FF69B4"
    async_commands = []
    description = "Allows the agent to explicitly read and append media files from the local file system, generate images using a model, and transcribe audio files using speech-to-text."
    commands = ["read", "generate", "transcribe_audio"]

    def get_tool_declarations(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "Media_read",
                "description": "Reads a media file from the local file system and appends it to your multimodal context. Use this when you want to view an image, listen to an audio file, or read a PDF.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "file_path": {
                            "type": "STRING",
                            "description": "The absolute path to the media file to read."
                        }
                    },
                    "required": ["file_path"]
                }
            },
            {
                "name": "Media_generate",
                "description": "Generates an image from a prompt and places it in a temporary 24-hour cache directory. IMPORTANT: Generated images are automatically deleted from the cache after 24 hours. You must take action to use the image before it expires (e.g., saving permanently to ~/Pictures/<YourName>/, sending via socials/email, uploading to a website, or displaying via xdg-open).",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "prompt": {
                            "type": "STRING",
                            "description": "The description of the image to generate."
                        }
                    },
                    "required": ["prompt"]
                }
            },
            {
                "name": "Media_transcribe_audio",
                "description": "Transcribes an audio file into text using speech-to-text. Use this to extract messages or text from voice notes and audio recordings if multimodal audio input fails or is unavailable.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "file_path": {
                            "type": "STRING",
                            "description": "The absolute path to the audio file to transcribe."
                        }
                    },
                    "required": ["file_path"]
                }
            }
        ]

    def _save_generated_image(self, image: Image.Image) -> Dict[str, Any]:
        if not (self.orchestrator and getattr(self.orchestrator, 'agent_id', None)):
            return {"result": "Error: agent_id is required to generate media."}

        save_dir = get_generated_media_dir(self.orchestrator.agent_id)
        os.makedirs(save_dir, exist_ok=True)

        # Automatically prune files older than 24 hours
        try:
            CacheManager.clear_expired(save_dir, max_age_days=1.0)
        except Exception as e:
            logging.warning(f"Failed to clear expired media cache: {e}")

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        file_path = os.path.join(save_dir, f"generated_image_{timestamp}.jpg")
        image.save(file_path, format="JPEG")

        return {
            "result": (
                f"Successfully generated and saved image to cache at {file_path}. "
                "Note: Generated images are temporary and will be deleted after 24 hours. "
                "Be sure to use the image (save to permanent user storage, send via social tools, or upload) before it expires."
            ),
            "media": [file_path]
        }

    def execute(self, command: str, *args, **kwargs) -> Any:
        is_low_token = self.orchestrator.settings_manager.get(
            "core.low-token-mode", False) if hasattr(self, 'orchestrator') and self.orchestrator else False

        if command == "generate":
            if is_low_token:
                return {"result": "Error: Media_generate is disabled when Low Token Mode is active."}

            prompt = kwargs.get("prompt")
            if not prompt:
                return {"result": "Error: Missing prompt parameter."}

            settings = self.orchestrator.settings_manager if self.orchestrator else None
            image_provider = settings.get("core.media.image-provider") if settings else None
            if not image_provider:
                return {
                    "result": "Error: An image generation provider has not been set up. Please select an image provider (Gemini or OpenRouter) and configure an API key in the Media Provider settings."
                }

            if image_provider == "gemini":
                api_key = (
                    settings.get_env("GEMINI_IMAGE_API_KEY") or
                    settings.get_env("GEMINI_API_KEY") or
                    settings.get_env("GEMINI_TTS_API_KEY")
                ) if settings else None
                if not api_key or not api_key.strip():
                    return {
                        "result": "Error: An image generation provider has not been set up (no API key configured for Gemini). Please add an API key in the Media Provider settings."
                    }
                api_key = api_key.strip()

                model_setting = settings.get("core.media.gemini.model") or settings.get(
                    "core.gemini.image-models", ["gemini-3.1-flash-image"])
                image_models = [model_setting] if isinstance(model_setting, str) else model_setting

                client = genai.Client(api_key=api_key)
                last_error = None

                for model_name in image_models:
                    try:
                        result = client.models.generate_content(
                            model=model_name,
                            contents=prompt,
                            config=types.GenerateContentConfig(
                                response_modalities=[Modality.IMAGE],
                                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                                    disable=True
                                )
                            )
                        )

                        if not result.candidates or not result.candidates[0].content.parts:
                            last_error = f"Model {model_name} returned no image parts."
                            continue

                        generated_image_bytes = None
                        for part in result.candidates[0].content.parts:
                            if part.inline_data:
                                generated_image_bytes = part.inline_data.data
                                break

                        if not generated_image_bytes:
                            last_error = f"Model {model_name} returned no inline image data."
                            continue

                        image = Image.open(BytesIO(generated_image_bytes))

                        return self._save_generated_image(image)
                    except Exception as e:
                        logging.warning(
                            f"Failed to generate image with {model_name}: {e}")
                        last_error = str(e)

                return {"result": f"Error: All image models failed. Last error: {last_error}"}

            elif image_provider == "openrouter":
                api_key = (
                    settings.get_env("OPENROUTER_IMAGE_API_KEY") or
                    settings.get_env("OPENROUTER_API_KEY")
                ) if settings else None
                if not api_key or not api_key.strip():
                    return {
                        "result": "Error: An image generation provider has not been set up (no API key configured for OpenRouter). Please add an API key in the Media Provider settings."
                    }
                api_key = api_key.strip()

                model_name = settings.get("core.media.openrouter.model", "bytedance-seed/seedream-4.5")
                headers = {
                    "Authorization": f"Bearer {api_key.strip()}",
                    "Content-Type": "application/json",
                    "User-Agent": "OpenAmity",
                    "HTTP-Referer": "https://github.com/OpenTangent/OpenAmity",
                    "X-Title": "Open Amity"
                }
                payload = {
                    "model": model_name,
                    "prompt": prompt
                }
                req = urllib.request.Request(
                    "https://openrouter.ai/api/v1/images",
                    data=json.dumps(payload).encode("utf-8"),
                    headers=headers,
                    method="POST"
                )
                try:
                    with urllib.request.urlopen(req, timeout=60) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                        items = data.get("data", [])
                        if not items or not isinstance(items, list):
                            return {"result": f"Error: OpenRouter image generation returned no data. Response: {data}"}

                        first = items[0]
                        image_bytes = None
                        if "b64_json" in first and first["b64_json"]:
                            image_bytes = base64.b64decode(first["b64_json"])
                        elif "url" in first and first["url"]:
                            img_req = urllib.request.Request(first["url"], headers={"User-Agent": "OpenAmity"})
                            with urllib.request.urlopen(img_req, timeout=30) as img_resp:
                                image_bytes = img_resp.read()

                        if not image_bytes:
                            return {"result": "Error: OpenRouter returned no image data (neither b64_json nor url)."}

                        image = Image.open(BytesIO(image_bytes))

                        return self._save_generated_image(image)
                except Exception as e:
                    logging.warning(f"Failed to generate image with OpenRouter ({model_name}): {e}")
                    return {"result": f"Error generating image with OpenRouter ({model_name}): {e}"}

            else:
                return {"result": f"Error: An image generation provider has not been set up (unsupported provider: {image_provider})."}

        if command == "transcribe_audio":
            file_path = kwargs.get("file_path") or (args[0] if args else None)
            if not file_path:
                return {"result": "Error: Missing file_path parameter."}

            exp_path = os.path.expanduser(file_path)

            if not os.path.exists(exp_path):
                return {"result": f"Error: File not found at {exp_path}"}

            if not os.path.isfile(exp_path):
                return {"result": f"Error: {exp_path} is not a file."}

            file_size = os.path.getsize(exp_path)
            if file_size == 0:
                return {"result": f"Error: Audio file at {exp_path} is empty (0 bytes)."}

            mime_type, _ = mimetypes.guess_type(exp_path)
            ext = os.path.splitext(exp_path)[1].lower()
            valid_audio_exts = {".wav", ".mp3", ".ogg", ".opus", ".m4a", ".flac", ".aac", ".wma"}
            if not ((mime_type and mime_type.startswith("audio/")) or ext in valid_audio_exts):
                return {"result": f"Error: {exp_path} does not appear to be an audio file (mime: {mime_type}, ext: {ext})."}

            try:
                from core.local_stt import LocalSTT
                stt = LocalSTT()
                transcription = stt.transcribe(exp_path)
                return {"result": f"Audio Transcription ({os.path.basename(exp_path)}):\n{transcription}"}
            except Exception as e:
                logging.error(f"Media_transcribe_audio error: {e}", exc_info=True)
                return {"result": f"Error transcribing audio: {e}"}

        if command != "read":
            return {"result": f"Unknown command: {command}"}

        file_path = kwargs.get("file_path")
        if not file_path:
            return {"result": "Error: Missing file_path parameter."}

        exp_path = os.path.expanduser(file_path)

        if not os.path.exists(exp_path):
            return {"result": f"Error: File not found at {exp_path}"}

        if not os.path.isfile(exp_path):
            return {"result": f"Error: {exp_path} is not a file."}

        mime_type, _ = mimetypes.guess_type(exp_path)
        if not mime_type or not (mime_type.startswith('image/') or mime_type.startswith('audio/') or mime_type.startswith('video/') or mime_type == 'application/pdf'):
            return {"result": f"Error: {exp_path} does not appear to be a supported media type (mime: {mime_type})."}

        return {
            "result": f"Successfully attached media file: {exp_path}",
            "media": [exp_path]
        }
