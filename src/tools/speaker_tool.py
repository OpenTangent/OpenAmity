import os
import datetime
import uuid
import logging
import json
from typing import List, Dict, Any
from core.cerebrum import Tool
from core.cache_manager import CacheManager
from core.audio_output import synthesize_speech_to_file
from config.paths import get_audio_cache_dir


class SpeakerTool(Tool):
    name = "Speaker"
    icon = "👄"
    color = "#03A9F4"
    async_commands = []
    description = (
        "Use this tool to speak aloud to the user, output text, or synthesize speech directly to an audio file (.opus). YOU ARE FULLY AUTONOMOUS regarding your speech. If you do not use this tool, you will remain completely silent. You must explicitly use this tool to communicate your thoughts to the user. Speak from a first-person perspective ('I'). Open Amity is an agentic system with rapid back-and-forth communication with the user. In normal day-to-day communication, your responses—whether spoken aloud or output as text—must be kept brief to allow breathing room for the user's response and to maintain rapid conversational momentum. For longer reports, write to a file and open via xdg-open or provide the file location. When using both Speaker_output_text and Speaker_speak_aloud in the same turn, they should complement each other, not summarise each other (spoken text is already captioned in the chat log). To generate headless speech files for external playback, conversion, or messaging without playing aloud in the app, use Speaker_speak_to_file (cached .opus files are automatically pruned after 24 hours)."
    )
    commands = [
        "speak_aloud <text> (Synthesizes text aloud via neural TTS. In normal communication, keep responses brief to allow breathing room for the user's response. Gemini 3.8 native angle-bracket expressive cues like <laugh>, <sigh>, <gasp>, <cough>, <whisper>, <throat-clearing>, <short pause> are supported; do NOT use square-bracket tags.)",
        "output_text <text> (Outputs markdown text to the GUI without audio. Follow clean GFM: separate paragraphs with blank lines, use fenced code blocks, standard lists, and avoid unclosed tags. Keep text brief for rapid back-and-forth interaction. For longer reports, write to a file and open via xdg-open or provide the file path.)",
        "speak_to_file <text> [filename] (Synthesizes text into a .opus audio file saved in audio cache without playing it in the app. Expressive angle-bracket cues are supported. Cached files are retained for 24 hours.)"
    ]

    def get_tool_declarations(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "Speaker_speak_aloud",
                "description": (
                    "Speak text aloud to the user via neural TTS. In normal day-to-day communication, keep responses brief to allow breathing room for the user's response, to maintain rapid conversational pacing, and to prevent server-side audio watchdog aborts. When Speaker_speak_aloud is used alongside Speaker_output_text in the same turn, they should complement each other, not summarise each other. For longer reports or large text blocks, save to a file and display via xdg-open or share the path instead. Expressive Cues: Use Gemini 3.8 native angle-bracket tags like <laugh>, <sigh>, <gasp>, <cough>, <whisper>, <throat-clearing>, <short pause>, etc. where appropriate. Do NOT use square-bracket tags like [laugh] or [whisper]."
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
                    "Outputs markdown text to the chat log without speaking it aloud. Use clean GitHub-flavoured Markdown: separate distinct paragraphs with standard blank lines (\\n\\n), wrap code in inline backticks or fenced code blocks with language tags, format lists cleanly with leading/trailing blank lines, and ensure all tags/formatting are closed. Don't use Speaker_output_text with every response, in rare situations where text output is needed keep it concise and complimentary to your spoken output. Use Speaker_output_text for reference material like URLs, code, tables, etc. When non-conversational reports, analyses, or documentation are required, write that to a file on disk instead and use xdg-open to display the document. Don't use Speaker_output_text when unnecessary."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "text": {
                            "type": "STRING",
                            "description": (
                                "The markdown text to display silently in the GUI. Use standard GFM (paragraph blank lines, "
                                "code fences, clean lists). Keep it brief."
                            )
                        }
                    },
                    "required": ["text"]
                }
            },
            {
                "name": "Speaker_speak_to_file",
                "description": (
                    "Synthesizes text into an Opus (.opus) audio file without playing it aloud in the app. "
                    "Outputs a .opus audio file in your audio cache that you can subsequently use "
                    "(e.g., save or copy to permanent storage like ~/Music or ~/Documents, play via xdg-open, "
                    "convert, or send in messages). Expressive Cues: Use Gemini 3.8 native angle-bracket tags "
                    "like <laugh>, <sigh>, <gasp>, <cough>, <whisper>, <throat-clearing>, <short pause> where appropriate. "
                    "Do NOT use square-bracket tags. Note: Cached .opus files are automatically deleted after 24 hours "
                    "if not moved or used."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "text": {
                            "type": "STRING",
                            "description": (
                                "The exact text / transcript to synthesize into speech. Expressive cues like <laugh>, "
                                "<sigh>, <whisper> are supported."
                            )
                        },
                        "filename": {
                            "type": "STRING",
                            "description": (
                                "Optional custom filename for the .opus file (e.g. 'voice_memo.opus' or 'announcement'). "
                                "If omitted, a unique timestamped filename will be generated automatically."
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
        elif command == "speak_to_file":
            target_text = kwargs.get("text") or kwargs.get("transcript")
            if not target_text and args:
                target_text = " ".join(args)

            if not target_text or not str(target_text).strip():
                return json.dumps({
                    "status": "error",
                    "error": "Error: text parameter is required for speak_to_file."
                })

            clean_text = str(target_text).strip()
            agent_id = getattr(self.orchestrator, "agent_id", None) if hasattr(self, 'orchestrator') and self.orchestrator else None
            cache_dir = get_audio_cache_dir(agent_id)
            os.makedirs(cache_dir, exist_ok=True)

            # Automatically prune files older than 24 hours
            try:
                CacheManager.clear_expired(cache_dir, max_age_days=1.0)
            except Exception as e:
                logging.warning(f"Failed to clear expired audio cache: {e}")

            raw_filename = kwargs.get("filename")
            if raw_filename and str(raw_filename).strip():
                base_name = os.path.basename(str(raw_filename).strip())
                if not base_name.endswith(".opus"):
                    base_name += ".opus"
                filename = base_name
            else:
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                short_id = uuid.uuid4().hex[:6]
                filename = f"speech_{timestamp}_{short_id}.opus"

            output_file = os.path.join(cache_dir, filename)

            try:
                final_path = synthesize_speech_to_file(
                    text=clean_text,
                    output_path=output_file,
                    agent_id=agent_id
                )
                return json.dumps({
                    "status": "success",
                    "file_path": final_path,
                    "message": (
                        f"Speech audio generated successfully and saved to: {final_path}. "
                        "Note: Cached files are automatically deleted after 24 hours."
                    )
                })
            except Exception as e:
                logging.getLogger("tools.Speaker").error(f"Failed to synthesize speech to file: {e}", exc_info=True)
                return json.dumps({
                    "status": "error",
                    "error": f"Failed to synthesize speech to file: {str(e)}"
                })

        return f"Unknown command: {command}"

