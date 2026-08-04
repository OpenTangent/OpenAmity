import os
import time
import subprocess
import requests
import json
import logging
from typing import List, Dict, Any
from core.cerebrum import Tool
from core.settings_manager import SettingsManager
from core.whatsapp_daemon import WhatsAppDaemon
from core.cache_manager import CacheManager
from config import paths

class WhatsAppSkill(Tool):
    name = "WhatsApp"
    description = "Allows you to read and send WhatsApp messages. Note: If you encounter a '500 Protocol mismatch' error, it means WhatsApp Web has updated and whatsapp-web.js now requires an update. Try WhatsApp_reset_connection but if you still get the protocol mismatch error just inform the user that the Whatsapp tool is down until an upstream patch is released."
    commands = [
        "unread (Fetch all unread messages)",
        "recent [n] (Fetch n most recent messages, default 30)",
        "recent_chat <target> [n] (Fetch n recent messages from a specific target/contact/group)",
        "send <target> <message> (Send a text message)",
        "send_voice <target> <message> (Send a text message that gets converted to voice/TTS and sent)",
        "react <msgId> <reaction> (React to a specific message with an emoji)",
        "mark_read <chatId> (Mark a chat as read)",
        "reset_connection (Clears WhatsApp cache and restarts the server if stuck in a QR loop)"
    ]

    def __init__(self):
        super().__init__()
        self.daemon = WhatsAppDaemon(port=3000)
        self.daemon.message_callback = self._on_message_received
        self.base_url = self.daemon.base_url
        self.data_dir = self.daemon.data_dir
        self.transcriber = None
        self.daemon.start()

    def _on_message_received(self, sender_id, sender_name):
        if hasattr(self, 'message_received_callback') and self.message_received_callback:
            self.message_received_callback(sender_id, sender_name)

    def get_tool_declarations(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "WhatsApp_unread",
                "description": "Fetches all currently unread messages in your inbox.",
                "parameters": {"type": "OBJECT", "properties": {}}
            },
            {
                "name": "WhatsApp_recent",
                "description": "Fetches the n most recent messages across all chats.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "n": {"type": "INTEGER", "description": "Number of messages to fetch (default 30)."}
                    }
                }
            },
            {
                "name": "WhatsApp_recent_chat",
                "description": "Fetches recent messages from a specific target (person or group).",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "target": {"type": "STRING", "description": "Name, phone, or ID of the contact/group."},
                        "n": {"type": "INTEGER", "description": "Number of messages to fetch (default 30)."}
                    },
                    "required": ["target"]
                }
            },
            {
                "name": "WhatsApp_send",
                "description": "Sends a standard text message to the specified target. CRITICAL: When answering a specific question or continuing a thread from a specific message, you MUST include its MsgID in the 'reply_to' field so the user sees exactly what you are responding to. CRITICAL: Do NOT use the LID when tagging/mentioning users in the message. Always use phone numbers (without the '+') for mentions. If you don't have the phone number, mention the user by name in plain text instead.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "target": {"type": "STRING", "description": "Name, phone, or ID of the contact/group."},
                        "message": {"type": "STRING", "description": "The text message to send. If a media_path is provided, this will be used as the caption."},
                        "media_path": {"type": "STRING", "description": "Optional absolute path to a media file (image, video, document) to attach to the message. Max 16MB."},
                        "reply_to": {"type": "STRING", "description": "Optional MsgID to reply directly to a specific message."}
                    },
                    "required": ["target"]
                }
            },
            {
                "name": "WhatsApp_send_voice",
                "description": "Synthesizes your voice using TTS and sends the resulting audio as a WhatsApp Voice Note. CRITICAL: When answering a specific question or continuing a thread from a specific message, you MUST include its MsgID in the 'reply_to' field.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "target": {"type": "STRING", "description": "Name, phone, or ID of the contact/group."},
                        "message": {"type": "STRING", "description": "The text message to convert to voice and send."},
                        "reply_to": {"type": "STRING", "description": "Optional MsgID to reply directly to a specific message."}
                    },
                    "required": ["target", "message"]
                }
            },
            {
                "name": "WhatsApp_mark_read",
                "description": "Explicitly marks a chat as read. You must do this after addressing an unread message.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "chatId": {"type": "STRING", "description": "The specific chatId to mark as read."}
                    },
                    "required": ["chatId"]
                }
            },
            {
                "name": "WhatsApp_react",
                "description": "Applies a reaction emoji to a specific message. Use this as a subtle acknowledgement of a message, perfect for busy group chats. Don't react to every single message.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "msgId": {"type": "STRING", "description": "The exact MsgID of the message to react to."},
                        "reaction": {"type": "STRING", "description": "The emoji to react with (e.g., '👍', '❤️', '😂')."}
                    },
                    "required": ["msgId", "reaction"]
                }
            },
            {
                "name": "WhatsApp_reset_connection",
                "description": "Restarts the WhatsApp server, clears caches, and updates the underlying whatsapp-web.js library to the latest commit to fix protocol mismatch errors (e.g., 'Failed to resolve chat', 'r: r'). Use this tool if the WhatsApp client is stuck initializing or throws internal evaluation errors. Note: You can temporarily change the 'core.whatsapp-web-target' setting in settings.json to a specific fork (e.g., a PR branch) to fix protocol mismatch errors, but this should only be used as a temporary solution and must always be reverted back to 'github:wwebjs/whatsapp-web.js#main' once an official patch is released.",
                "parameters": {"type": "OBJECT", "properties": {}}
            },
        ]



    def get_auth_status(self) -> Dict[str, Any]:
        try:
            res = requests.get(f"{self.base_url}/status", timeout=10)
            if res.status_code == 200:
                return res.json()
        except requests.exceptions.RequestException:
            pass
        return {"ready": False, "qr": None}

    def execute(self, command: str, *args, **kwargs) -> Any:
        if command in ["unread", "recent", "recent_chat"]:
            CacheManager.clear_expired(os.path.join(self.data_dir, 'uploads'), max_age_days=1.0)

        if command == "reset_connection":
            try:
                self.daemon.restart()
                return "WhatsApp server restarted. Please check the terminal if a new QR code is required."
            except Exception as e:
                return f"Failed to reset connection: {e}"

        try:
            status = requests.get(f"{self.base_url}/status", timeout=10).json()
            if not status.get("ready"):
                return "WhatsApp client is still initializing or not ready. If it is stuck, use WhatsApp_reset_connection to fix it."
        except requests.exceptions.RequestException:
            return "WhatsApp bridge is not running. If it crashed, use WhatsApp_reset_connection to restart it."

        if command == "unread":
            try:
                res = requests.get(f"{self.base_url}/unread", timeout=15)
                data = res.json()
                if "error" in data: return f"Error: {data['error']}"
                
                unread = data.get("unread", [])
                
                is_low_token = SettingsManager().get("core.low-token-mode", False)
                if is_low_token and len(unread) > 5:
                    unread = unread[:5]

                if not unread:
                    return "No unread messages."
                
                output = "Unread Messages:\n"
                for msg in unread:
                    content = msg.get('content', '')
                    if msg.get('mediaPath') and os.path.exists(msg['mediaPath']):
                        if msg['type'] in ['ptt', 'audio']:
                            content = f"[Voice Message (Path: {msg['mediaPath']})] {content}"
                        elif msg['type'] in ['image', 'document', 'video']:
                            content = f"[{msg['type'].capitalize()} (Path: {msg['mediaPath']})] {content}"
                        else:
                            content = f"[Media: {msg['type']} (Path: {msg['mediaPath']})] {content}"
                    elif msg.get('hasMedia'):
                        content = f"[Media: {msg['type']}] {content}"
                        
                    channel_type = "WHATSAPP_GROUP" if msg['chatId'].endswith("@g.us") else "WHATSAPP_DM"
                    sender_display = f"{msg['senderName']} (+{msg['senderNumber']})" if msg.get('senderNumber') else msg['senderName']
                    output += f"- [CHANNEL: {channel_type}] [SOURCE_ID: {msg['chatId']}] [{msg['chatName']}] {sender_display}: {content} (MsgID: {msg['id']})\n"
                
                output += "\n[SYSTEM NOTE: If you need to view or listen to any media attachments, use the Media_read tool. To respond, use WhatsApp_send or WhatsApp_send_voice.]"
                return output
            except Exception as e:
                return f"Failed to fetch unread messages: {e}"

        elif command == "recent":
            n = kwargs.get('n') or (args[0] if len(args) > 0 else 30)
            is_low_token = SettingsManager().get("core.low-token-mode", False)
            if is_low_token:
                n = min(int(n), 5)
            
            try:
                res = requests.get(f"{self.base_url}/recent?n={n}", timeout=15)
                data = res.json()
                if "error" in data: return f"Error: {data['error']}"
                
                messages = data.get("messages", [])
                if not messages: return "No recent messages."
                
                output = f"Recent Messages (Last {len(messages)}):\n"
                for msg in messages:
                    content = msg.get('content', '')
                    if msg.get('mediaPath') and os.path.exists(msg['mediaPath']):
                        if msg['type'] in ['ptt', 'audio']:
                            content = f"[Voice Message (Path: {msg['mediaPath']})] {content}"
                        elif msg['type'] in ['image', 'document', 'video']:
                            content = f"[{msg['type'].capitalize()} (Path: {msg['mediaPath']})] {content}"
                        else:
                            content = f"[Media: {msg['type']} (Path: {msg['mediaPath']})] {content}"
                    elif msg.get('hasMedia'):
                        content = f"[Media: {msg['type']}] {content}"

                    channel_type = "WHATSAPP_GROUP" if msg['chatId'].endswith("@g.us") else "WHATSAPP_DM"
                    sender_display = f"{msg['senderName']} (+{msg['senderNumber']})" if msg.get('senderNumber') else msg['senderName']
                    output += f"- [CHANNEL: {channel_type}] [SOURCE_ID: {msg['chatId']}] [{msg['chatName']}] {sender_display}: {content} (MsgID: {msg['id']})\n"
                
                output += "\n[SYSTEM NOTE: If you need to view or listen to any media attachments, use the Media_read tool. To respond, use WhatsApp_send or WhatsApp_send_voice.]"
                return output
            except Exception as e:
                return f"Failed to fetch recent messages: {e}"
                
        elif command == "recent_chat":
            target = kwargs.get('target') or (args[0] if args else None)
            if not target: return "Usage: recent_chat <target> [n]"
            n = kwargs.get('n') or (args[1] if len(args) > 1 else 30)
            is_low_token = SettingsManager().get("core.low-token-mode", False)
            if is_low_token:
                n = min(int(n), 5)
                
            try:
                try:
                    # 1. Attempt to resolve via internal Rolodex first
                    from core.rolodex import Rolodex
                    r = Rolodex()
                    entity = r._find_entity(target)
                    if entity and entity.get('whatsapp_id'):
                        target = entity['whatsapp_id']
                    elif entity and entity.get('phone'):
                        target = entity['phone']
                except Exception:
                    pass

                import urllib.parse
                safe_target = urllib.parse.quote(target)
                res = requests.get(f"{self.base_url}/recent?n={n}&target={safe_target}", timeout=15)
                data = res.json()
                if "error" in data: return f"Error: {data['error']}"
                
                messages = data.get("messages", [])
                if not messages: return f"No recent messages for {target}."
                
                output = f"Recent Messages for {target} (Last {len(messages)}):\n"
                for msg in messages:
                    content = msg.get('content', '')
                    if msg.get('mediaPath') and os.path.exists(msg['mediaPath']):
                        if msg['type'] in ['ptt', 'audio']:
                            content = f"[Voice Message (Path: {msg['mediaPath']})] {content}"
                        elif msg['type'] in ['image', 'document', 'video']:
                            content = f"[{msg['type'].capitalize()} (Path: {msg['mediaPath']})] {content}"
                        else:
                            content = f"[Media: {msg['type']} (Path: {msg['mediaPath']})] {content}"
                    elif msg.get('hasMedia'):
                        content = f"[Media: {msg['type']}] {content}"

                    channel_type = "WHATSAPP_GROUP" if msg['chatId'].endswith("@g.us") else "WHATSAPP_DM"
                    sender_display = f"{msg['senderName']} (+{msg['senderNumber']})" if msg.get('senderNumber') else msg['senderName']
                    output += f"- [CHANNEL: {channel_type}] [SOURCE_ID: {msg['chatId']}] [{msg['chatName']}] {sender_display}: {content} (MsgID: {msg['id']})\n"
                
                output += "\n[SYSTEM NOTE: If you need to view or listen to any media attachments, use the Media_read tool. To respond, use WhatsApp_send or WhatsApp_send_voice.]"
                return output
            except Exception as e:
                return f"Failed: {e}"

        elif command == "send":
            target = kwargs.get('target')
            message = kwargs.get('message', "")
            media_path = kwargs.get('media_path')
            reply_to = kwargs.get('reply_to')
            
            if not target and len(args) >= 1:
                target = args[0]
                if len(args) >= 2:
                    message = " ".join(args[1:])
                
            if not target or (not message and not media_path):
                return "Usage: send <target> <message> [media_path]"
            
            try:
                try:
                    # 1. Attempt to resolve via internal Rolodex first
                    from core.rolodex import Rolodex
                    r = Rolodex()
                    entity = r._find_entity(target)
                    if entity and entity.get('whatsapp_id'):
                        target = entity['whatsapp_id']
                    elif entity and entity.get('phone'):
                        target = entity['phone']
                except Exception:
                    pass

                payload = {"target": target, "text": message}
                if reply_to:
                    payload["reply_to"] = reply_to
                    
                if media_path:
                    if not os.path.exists(media_path):
                        return f"Error: Media file not found at {media_path}"
                    
                    file_size = os.path.getsize(media_path)
                    if file_size > 16 * 1024 * 1024:
                        return f"Error: File size ({file_size / (1024*1024):.1f}MB) exceeds the 16MB limit."
                        
                    import mimetypes
                    mime_type, _ = mimetypes.guess_type(media_path)
                    if not mime_type:
                        mime_type = "application/octet-stream"
                        
                    with open(media_path, 'rb') as f:
                        filename = os.path.basename(media_path)
                        files = {'media': (filename, f, mime_type)}
                        data_payload = {"target": target}
                        if message:
                            data_payload["text"] = message
                        if reply_to:
                            data_payload["reply_to"] = reply_to
                            
                        res = requests.post(f"{self.base_url}/send", data=data_payload, files=files, timeout=60)
                else:
                    res = requests.post(f"{self.base_url}/send", json=payload, timeout=15)
                    
                data = res.json()
                if "error" in data: return f"Error: {data['error']}"
                return f"Message sent to {target} successfully."
            except Exception as e:
                return f"Failed to send message: {e}"

        elif command == "send_voice":
            target = kwargs.get('target')
            message = kwargs.get('message')
            reply_to = kwargs.get('reply_to')
            
            if not target and len(args) >= 2:
                target = args[0]
                message = " ".join(args[1:])
                
            if not target or not message:
                return "Usage: send_voice <target> <message text to speak>"
            
            if SettingsManager().get("core.antigravity.agy-mode", False):
                return "Error: Voice notes are currently unavailable when agy-mode is active. Please use WhatsApp_send instead."
            
            try:
                try:
                    # 1. Attempt to resolve via internal Rolodex first
                    from core.rolodex import Rolodex
                    r = Rolodex()
                    entity = r._find_entity(target)
                    if entity and entity.get('whatsapp_id'):
                        target = entity['whatsapp_id']
                    elif entity and entity.get('phone'):
                        target = entity['phone']
                except Exception:
                    pass

                import tempfile
                from google import genai
                from google.genai import types

                settings = SettingsManager()
                voice = settings.get("core.tts.gemini.model-name", "Achernar")
                voice_prompt = settings.get("core.tts.gemini.prompt.profile", "")
                
                gemini_settings = settings.get("core.gemini", {})
                voice_models = gemini_settings.get("voice-models", ["gemini-3.1-flash-tts-preview"])
                model_name = voice_models[0] if voice_models else "gemini-3.1-flash-tts-preview"

                temp_fd, temp_wav = tempfile.mkstemp(suffix=".wav")
                os.close(temp_fd)
                temp_ogg = temp_wav.replace(".wav", ".ogg")
                
                try:
                    client = genai.Client()
                    full_text = f"{voice_prompt}{message}"
                    response = client.models.generate_content(
                        model=model_name,
                        contents=full_text,
                        config=types.GenerateContentConfig(
                            response_modalities=["AUDIO"],
                            speech_config=types.SpeechConfig(
                                voice_config=types.VoiceConfig(
                                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                        voice_name=voice,
                                    )
                                )
                            ),
                        )
                    )
                    
                    audio_data = response.candidates[0].content.parts[0].inline_data.data
                    with open(temp_wav, "wb") as f:
                        f.write(audio_data)
                        
                    subprocess.run(
                        ["ffmpeg", "-y", "-f", "s16le", "-ar", "24000", "-ac", "1", "-i", temp_wav, "-c:a", "libopus", temp_ogg],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    
                    if os.path.exists(temp_ogg):
                        with open(temp_ogg, 'rb') as f:
                            files = {'media': ('voice.ogg', f, 'audio/ogg')}
                            data = {'target': target, 'isVoice': 'true'}
                            if reply_to:
                                data['reply_to'] = reply_to
                                
                            res = requests.post(f"{self.base_url}/send", data=data, files=files, timeout=30)
                            res_data = res.json()
                        
                        if "error" in res_data:
                            return f"Error: {res_data['error']}"
                        return f"Voice message sent to {target} successfully."
                    else:
                        return "Failed to convert TTS audio to OGG."
                except Exception as e:
                    return f"Failed to generate TTS audio: {e}"
                finally:
                    if os.path.exists(temp_wav):
                        os.remove(temp_wav)
                    if os.path.exists(temp_ogg):
                        os.remove(temp_ogg)
                    
            except Exception as e:
                return f"Failed to send voice message: {e}"

        elif command == "react":
            msg_id = kwargs.get('msgId')
            reaction = kwargs.get('reaction')
            
            if not msg_id and len(args) >= 2:
                msg_id = args[0]
                reaction = args[1]
                
            if not msg_id or not reaction:
                return "Usage: react <msgId> <reaction>"
            
            try:
                res = requests.post(f"{self.base_url}/react", json={"msgId": msg_id, "reaction": reaction}, timeout=10)
                data = res.json()
                if "error" in data: return f"Error: {data['error']}"
                return f"Reacted with {reaction} successfully."
            except Exception as e:
                return f"Failed to react: {e}"

        elif command == "mark_read":
            chat_id = kwargs.get('chatId') or (args[0] if args else None)
            if not chat_id:
                return "Usage: mark_read <chatId>"
            try:
                res = requests.post(f"{self.base_url}/mark_read", json={"chatId": chat_id}, timeout=10)
                data = res.json()
                if "error" in data: return f"Error: {data['error']}"
                return f"Chat {chat_id} marked as read."
            except Exception as e:
                return f"Failed to mark as read: {e}"



        return f"Unknown command: {command}"

    def shutdown(self):
        self.daemon.stop()

    def __del__(self):
        self.shutdown()
