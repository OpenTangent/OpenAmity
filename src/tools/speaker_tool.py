from typing import List, Dict, Any
from core.cerebrum import Tool
import json


class SpeakerTool(Tool):
    name = "Speaker"
    description = (
        "Use this tool to speak aloud to the user or output text. YOU ARE FULLY AUTONOMOUS regarding your speech. "
        "If you do not use this tool, you will remain completely silent. You must explicitly use this tool to communicate "
        "your thoughts or findings to the user. Speak from a first-person perspective ('I').\n"
        "Open Amity is an agentic system with rapid back-and-forth communication with the user. In normal day-to-day communication, "
        "your responses—whether spoken aloud or output as text—must be kept brief to allow breathing room for the user's response.\n"
        "AUDIO & CONTENT GUIDELINES:\n"
        "- Breathing Room & Brevity: Keep responses brief during regular interactions to maintain rapid conversational momentum, "
        "give the user room to respond, and preserve neural TTS acoustic stability without massive monologues.\n"
        "- Expressive Cues: Natural voiced emotional tags like [laugh], [sigh], [thoughtful], [deadpan], or [curious] work well. "
        "Avoid or strictly minimize unvoiced breathy tags like [whisper] or [softly], as unvoiced turbulence can trigger acoustic anomaly watchdogs.\n"
        "- Long Reports & Content Offloading: On occasions where longer reports, extensive documentation, code files, or detailed data "
        "are needed, do NOT dump them into chat via output_text or read them aloud. Instead, write them to a file on the system "
        "(e.g., in ~/Documents/<YourName>/...) and use xdg-open via Terminal to display the report, or provide the file location to the user.\n"
        "- Markdown Standards (output_text): Format silent text with clean GitHub-flavored Markdown. Separate paragraphs with standard "
        "blank lines (\\n\\n), wrap code in backticks or fenced language blocks, format lists with standard markers (1. or -) separated by blank lines, "
        "and never leave raw HTML tags unclosed.\n"
        "- Resilience: If an acoustic safety abort ever interrupts audio, the system automatically falls back to the robust gemini-2.5-flash-preview-tts model."
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
                    "Speak text aloud to the user via neural TTS. In normal day-to-day communication, keep responses brief "
                    "to allow breathing room for the user's response, maintain rapid conversational pacing, and prevent server-side "
                    "audio watchdog aborts (FinishReason.SAFETY). Voiced emotional cues like [laugh], [sigh], [thoughtful], or [deadpan] "
                    "are well-supported, but avoid unvoiced breathy cues ([whisper], [softly]) which trip acoustic anomaly monitors. "
                    "If an acoustic abort occurs, the system gracefully retries with gemini-2.5-flash-preview-tts. On occasions where longer "
                    "reports or large text blocks are needed, save them to a file on the system and use xdg-open or share the path instead."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "text": {
                            "type": "STRING",
                            "description": (
                                "The exact text to speak aloud. Aim for natural conversational dialogue kept brief in normal "
                                "day-to-day communication to allow breathing room for the user's response. Avoid unvoiced whispers or massive monologues."
                            )
                        }
                    },
                    "required": ["text"]
                }
            },
            {
                "name": "Speaker_output_text",
                "description": (
                    "Output markdown text to the user's GUI without speaking it aloud. Use clean GitHub-flavored Markdown: "
                    "separate distinct paragraphs with standard blank lines (\\n\\n), wrap code in inline backticks or fenced code blocks with "
                    "language tags, format lists cleanly with leading/trailing blank lines, and ensure all tags/formatting are closed. "
                    "In normal day-to-day communication, keep text responses brief to allow breathing room for the user's response in "
                    "rapid agentic interactions. On occasions where longer reports, comprehensive analyses, or extensive documentation "
                    "are needed, write them to a file on the system and use xdg-open to display the report, or alternatively provide the file location to the user."
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
