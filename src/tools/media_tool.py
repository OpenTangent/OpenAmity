import os
import logging
import datetime
from io import BytesIO
from typing import List, Dict, Any
from core.cerebrum import Tool
from core.settings_manager import SettingsManager
from config.paths import get_app_data_dir
from google import genai
from google.genai import types
from google.genai.types import Modality
from PIL import Image
import mimetypes

class MediaSkill(Tool):
    name = "Media"
    description = "Allows the agent to explicitly read and append media files from the local file system, and generate images using a model."
    commands = ["read", "generate"]

    def get_tool_declarations(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "Media_read",
                "description": "Reads a media file from the local file system and appends it to your multimodal context. Use this when you want to view an image, listen to an audio file, or read a PDF that you have found in the file system.",
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
                "description": "Generates an image from a prompt and places it in a launchpad directory. IMPORTANT: You must take action to use the image after generating it (e.g., displaying it via xdg-open in the terminal, sending via WhatsApp, or saving to ~/Pictures/<YourName>/).",
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
            }
        ]

    def execute(self, command: str, *args, **kwargs) -> Any:
        if command == "generate":
            prompt = kwargs.get("prompt")
            if not prompt:
                return {"result": "Error: Missing prompt parameter."}
            
            settings = SettingsManager()
            api_key = settings.get_env("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY")
            if not api_key:
                return {"result": "Error: GEMINI_API_KEY is not set."}
            
            image_models = settings.get("core.gemini.image-models", ["gemini-3.1-flash-image"])
            if not isinstance(image_models, list):
                image_models = [image_models]
            
            client = genai.Client(api_key=api_key)
            last_error = None
            
            for model_name in image_models:
                try:
                    result = client.models.generate_content(
                        model=model_name,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            response_modalities=[Modality.IMAGE]
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
                    
                    save_dir = os.path.join(get_app_data_dir(), "generated_media")
                    os.makedirs(save_dir, exist_ok=True)
                    
                    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                    file_path = os.path.join(save_dir, f"generated_image_{timestamp}.jpg")
                    image.save(file_path, format="JPEG")
                    
                    return {
                        "result": f"Successfully generated and saved image to {file_path}. Please decide what to do with it next.",
                        "media": [file_path]
                    }
                except Exception as e:
                    logging.warning(f"Failed to generate image with {model_name}: {e}")
                    last_error = str(e)
            
            return {"result": f"Error: All image models failed. Last error: {last_error}"}

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
