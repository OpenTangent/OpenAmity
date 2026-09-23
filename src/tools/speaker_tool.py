from typing import List, Dict, Any
from core.cerebrum import Tool
import json


class SpeakerTool(Tool):
    name = "Speaker"
    icon = "👄"
    color = "#03A9F4"
    async_commands = []
    description = (
        "Use this tool to speak aloud to the user or output text. YOU ARE FULLY AUTONOMOUS regarding your speech. If you do not use this tool, you will remain completely silent. You must explicitly use this tool to communicate your thoughts to the user. Speak from a first-person perspective ('I'). Open Amity is an agentic system with rapid back-and-forth communication with the user. In normal day-to-day communication, your responses—whether spoken aloud or output as text—must be kept brief to allow breathing room for the user's response and to maintain rapid conversational momentum. For longer reports, write to a file and open via xdg-open or provide the file location. When using both Speaker_output_text and Speaker_speak_aloud in the same turn, they should complement each other, not summarise each other (spoken text is already captioned in the chat log)."
    )
    commands = [
        "speak_aloud <text> (Synthesizes text aloud via neural TTS. In normal communication, keep responses brief to allow breathing room for the user's response; avoid unvoiced [whisper]; supports voiced cues like [laugh] or [sigh].)",
        "output_text <text> (Outputs markdown text to the GUI without audio. Follow clean GFM: separate paragraphs with blank lines, use fenced code blocks, standard lists, and avoid unclosed tags. Keep text brief for rapid back-and-forth interaction. For longer reports, write to a file and open via xdg-open or provide the file path.)"
    ]

    def get_tool_declarations(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "Speaker_speak_aloud",
                "description": (
                    "Speak text aloud to the user via neural TTS. In normal day-to-day communication, keep responses brief to allow breathing room for the user's response, to maintain rapid conversational pacing, and to prevent server-side audio watchdog aborts. When Speaker_speak_aloud is used alongside Speaker_output_text in the same turn, they should complement each other, not summarise each other. For longer reports or large text blocks, save to a file and display via xdg-open or share the path instead. Expressive Cues: Use natural voiced audio tags like [laughs], [giggles], [sighs], [gasps], [uhm], [whispers], [short pause], etc. where appropriate."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "text": {
                            "type": "STRING",
                            "description": (
                                "The exact text to speak aloud. Aim for natural conversational dialogue kept brief in normal "
                                "day-to-day communication to allow breathing room for the user's response. Avoid massive monologues."
                            )
                        }
                    },
                    "required": ["text"]
                }
            },
            {
                "name": "Speaker_output_text",
                "description": (
                    "Output markdown text to the user's GUI without speaking it aloud. Use clean GitHub-flavoured Markdown: separate distinct paragraphs with standard blank lines (\\n\\n), wrap code in inline backticks or fenced code blocks with language tags, format lists cleanly with leading/trailing blank lines, and ensure all tags/formatting are closed. Don't use Speaker_output_text alongside Speaker_speak_aloud with every response, however in situations where you do use both in the same turn, they should complement each other, not summarise each other. Use Speaker_output_text for complimentary visual reference material (code, tables, lists, etc.). In normal day-to-day communication, keep text output brief to allow breathing room for the user's response. Where longer non-conversational reports, analyses, or documentation are needed, instead write it to a file on the system (e.g., in ~/Documents/<YourName>/...) and use xdg-open to display the document, or alternatively provide the report file location to the user."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "text": {
                            "type": "STRING",
                            "description": (
                                "The markdown text to display silently in the GUI. Use standard GFM (paragraph blank lines, "
                                "code fences, clean lists). Keep brief for normal conversational exchanges."
                            )
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
