import os
import logging
from typing import List, Dict, Any
from core.cerebrum import Tool
import mimetypes

class MediaSkill(Tool):
    name = "Media"
    description = "Allows the agent to explicitly read and append media files (images, audio, video, pdf) from the local file system to their context."
    commands = ["read"]

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
            }
        ]

    def execute(self, command: str, *args, **kwargs) -> Any:
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
