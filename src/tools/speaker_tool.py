from typing import List, Dict, Any
from core.cerebrum import Tool
import json


class SpeakerTool(Tool):
    name = "Speaker"
    description = (
        "Use this tool to speak aloud to the user or output text. YOU ARE FULLY AUTONOMOUS regarding your speech. "
        "If you do not use this tool, you will remain completely silent. You must explicitly use this tool to communicate "
        "your thoughts or findings to the user. Speak from a first-person perspective ('I').\n"
        "AUDIO STABILITY GUIDELINES (Gemini TTS 3.1 & 2.5):\n"
        "- Conciseness: Keep spoken turns concise (<150 words / ~45s) to maintain autoregressive audio stability and prevent "
        "acoustic watchdog safety aborts (FinishReason.SAFETY).\n"
        "- Expressive Cues: Natural voiced emotional tags like [laugh], [sigh], [thoughtful], [deadpan], or [curious] work well. "
        "Avoid or strictly minimize unvoiced breathy tags like [whisper] or [softly], as unvoiced turbulence can trigger acoustic anomaly watchdogs.\n"
        "- Long Content: NEVER read large data, code blocks, tables, or lengthy quotes aloud. Always use 'output_text' for silent GUI display.\n"
        "- Resilience: If an acoustic safety abort ever interrupts audio, the system automatically falls back to the robust gemini-2.5-flash-preview-tts model."
    )
    commands = [
        "speak_aloud <text> (Synthesizes text aloud via neural TTS. Keep under ~150 words / ~45s; avoid unvoiced [whisper]; supports voiced cues like [laugh] or [sigh].)",
        "output_text <text> (Outputs verbatim markdown text to the GUI without audio. Essential for code, tables, long quotes, or data blocks.)"
    ]

    def get_tool_declarations(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "Speaker_speak_aloud",
                "description": (
                    "Speak text aloud to the user via neural TTS. Keep spoken turns concise (<150 words / ~45s) "
                    "to maintain acoustic decoder stability and prevent server-side audio watchdog aborts (FinishReason.SAFETY). "
                    "Voiced emotional cues like [laugh], [sigh], [thoughtful], or [deadpan] are well-supported, but avoid unvoiced "
                    "breathy cues ([whisper], [softly]) which trip acoustic anomaly monitors. If an acoustic abort occurs, the system "
                    "gracefully retries with gemini-2.5-flash-preview-tts. For long text, code, or data, use Speaker_output_text instead."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "text": {
                            "type": "STRING",
                            "description": (
                                "The exact text to speak aloud. Aim for natural, conversational dialogue under 150 words (~45s). "
                                "Avoid unvoiced whispers or massive monologues to ensure smooth acoustic decoding."
                            )
                        }
                    },
                    "required": ["text"]
                }
            },
            {
                "name": "Speaker_output_text",
                "description": (
                    "Output markdown text to the user's GUI without speaking it aloud. Use this for sharing raw data, "
                    "code snippets, technical explanations, citations, tables, or lengthy text blocks to prevent audio TTS overhead and aborts."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "text": {
                            "type": "STRING",
                            "description": "The markdown text to display silently in the GUI."
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
