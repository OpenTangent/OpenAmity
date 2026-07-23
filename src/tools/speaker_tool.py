from typing import List, Dict, Any
from core.cerebrum import Tool
import json

class SpeakerTool(Tool):
    name = "Speaker"
    description = "Use this tool to speak aloud to the user or output text. YOU ARE FULLY AUTONOMOUS regarding your speech. If you do not use this tool, you will remain completely silent. You must explicitly use this tool to communicate your thoughts or findings to the user. Speak from a first-person perspective ('I'). Use audio tags like [whisper], [sigh], [laugh], [excitedly] autonomously where appropriate to add emotion to your speech."
    commands = [
        "speak_aloud <text> (Says the words you provide aloud to the user. Includes support for audio cues like [laugh] or [sigh].)",
        "output_text <text> (Outputs verbatim text to the GUI without any audio. Useful for pasting data/code without reading it aloud.)"
    ]

    def get_tool_declarations(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "Speaker_speak_aloud",
                "description": "Speak text aloud to the user. Use this when you want to communicate, including audio tags like [whisper] or [sigh].",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "text": {
                            "type": "STRING",
                            "description": "The exact text to speak aloud."
                        }
                    },
                    "required": ["text"]
                }
            },
            {
                "name": "Speaker_output_text",
                "description": "Output markdown text to the user's GUI without speaking it aloud. Use this for sharing raw data, code, or lengthy information.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "text": {
                            "type": "STRING",
                            "description": "The text to display in the GUI."
                        }
                    },
                    "required": ["text"]
                }
            }
        ]

    def execute(self, command: str, *args, **kwargs) -> str:
        # For speak_aloud and output_text, the text is provided via kwargs or args
        if 'text' in kwargs:
            text = kwargs['text']
        elif args:
            text = " ".join(args)
        else:
            text = ""

        if command == "speak_aloud":
            if not text:
                return "Error: text parameter is required for speak_aloud."
            return json.dumps({"action": "trigger_speak_aloud", "text": text})
        elif command == "output_text":
            if not text:
                return "Error: text parameter is required for output_text."
            return json.dumps({"action": "trigger_output_text", "text": text})
        
        return f"Unknown command: {command}"
