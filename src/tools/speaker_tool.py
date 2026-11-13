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
        "Explicit speech & GUI output tool (.opus files via speak_to_file). You stay completely silent unless you call it. "
        "Default to Speaker_speak_aloud for conversational replies (spoken words are already captioned in the chat log—never repeat or summarise speech in Speaker_output_text). "
        "Keep replies brief; write long reports to a file and open via xdg-open. "
        "See System_read_manual(topic='channels') for headless audio/media rules."
    )
    commands = [
        "speak_aloud <text> (Default user reply via neural TTS, already captioned in chat; keep brief. Supports <laugh>/<sigh>/<whisper>/<short pause>, never [brackets].)",
        "output_text <text> (Silent GFM markdown ONLY for complementary non-speakable content like URLs, code, or tables—never repeat speech or use routinely.)",
        "speak_to_file <text> [filename] (Synthesizes text to a .opus file in audio_cache without playback; retained 24 hours.)"
    ]

    def get_tool_declarations(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "Speaker_speak_aloud",
                "description": (
                    "Speaks text aloud to the user in first person via neural TTS (automatically captioned in the chat log). "
                    "Keep responses brief for rapid pacing. Do NOT echo or summarise spoken text in Speaker_output_text; only pair with Speaker_output_text for non-speakable data (URLs, code, tables). "
                    "Use Gemini 3.8 angle-bracket cues (<laugh>, <sigh>, <gasp>, <cough>, <whisper>, <throat-clearing>, <short pause>); do NOT use [square brackets]."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "text": {
                            "type": "STRING",
                            "description": "Concise spoken text in first-person dialogue."
                        }
                    },
                    "required": ["text"]
                }
            },
            {
                "name": "Speaker_output_text",
                "description": (
                    "Outputs silent GitHub-flavoured Markdown to the chat log without audio (blank lines between paragraphs/lists, code fences with language tags, closed tags). "
                    "Do NOT use on routine responses or to repeat/summarise Speaker_speak_aloud (spoken text is already captioned). "
                    "Reserve strictly for non-speakable reference material (URLs, code blocks, tables); offload long reports to files via xdg-open."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "text": {
                            "type": "STRING",
                            "description": "Brief complementary GFM markdown (only non-speakable reference content)."
                        }
                    },
                    "required": ["text"]
                }
            },
            {
                "name": "Speaker_speak_to_file",
                "description": (
                    "Synthesizes text to a .opus file in audio_cache without playing aloud (for ~/Music or ~/Documents archiving, xdg-open playback, or messaging). "
                    "Supports <laugh>/<whisper> cues, not [brackets]. Cached .opus files are deleted after 24 hours."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "text": {
                            "type": "STRING",
                            "description": "Transcript to synthesize (supports <laugh>, <sigh>, <whisper> cues)."
                        },
                        "filename": {
                            "type": "STRING",
                            "description": "Optional filename (e.g. 'voice_memo.opus'); defaults to timestamped name."
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

