import os
import subprocess
import requests
from typing import List, Dict, Any
from core.cerebrum import Tool
from core.cache_manager import CacheManager


class WhatsAppSkill(Tool):
    name = "WhatsApp"
    icon = "💬"
    color = "#25D366"
    async_commands = []
    description = "Allows you to read and send WhatsApp messages. Note: If you encounter a protocol mismatch error, try WhatsApp_reset_connection to download the latest WPPConnect engine patch and restart the server."
    commands = [
        "unread (Fetch all unread messages)",
        "recent [n] (Fetch n most recent messages, default 30)",
        "recent_chat <target> [n] (Fetch n recent messages from a specific target/contact/group)",
        "send <target> <message> (Send a text message)",
        "send_voice <target> <message> (Send a text message that gets converted to voice/TTS and sent)",
        "send_location <target> <latitude> <longitude> [name] [address] [url] (Send a location pin with GPS coordinates)",
        "send_contact <target> <contact_name> <contact_phone> [organization] [email] (Send a contact card / vCard)",
        "react <msgId> <reaction> (React to a specific message with an emoji)",
        "mark_read <chatId> (Mark a chat as read)",
        "reset_connection (Clears WhatsApp cache, checks for engine updates, and restarts the server if stuck in a QR loop)"
    ]

    def __init__(self, orchestrator=None):
        super().__init__(orchestrator)
        from core.whatsapp_daemon import WhatsAppDaemon
        agent_id = self.orchestrator.agent_id if self.orchestrator else None
        self.daemon = WhatsAppDaemon(agent_id=agent_id)
        self.daemon.message_callback = self._on_message_received
        self.daemon.status_callback = self._on_status_notice
        self.base_url = self.daemon.base_url
        self.data_dir = self.daemon.data_dir
        self.transcriber = None
        self.daemon.start()

    def _on_status_notice(self, notice: str):
        if hasattr(self, 'orchestrator') and self.orchestrator:
            if hasattr(self.orchestrator, 'on_message_appended') and self.orchestrator.on_message_appended:
                self.orchestrator.on_message_appended.emit("System", notice)

    def _on_message_received(self, sender_id, sender_name, **kwargs):
        if hasattr(self, 'message_received_callback') and self.message_received_callback:
            self.message_received_callback(sender_id, sender_name, **kwargs)

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
                "name": "WhatsApp_send_location",
                "description": "Sends a location pin with GPS coordinates (latitude and longitude) to the specified target. Optionally includes place name, address, URL, and a reply_to MsgID.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "target": {"type": "STRING", "description": "Name, phone, or ID of the contact/group."},
                        "latitude": {"type": "NUMBER", "description": "Latitude coordinate in decimal degrees (e.g., -29.740804)."},
                        "longitude": {"type": "NUMBER", "description": "Longitude coordinate in decimal degrees (e.g., 30.831676)."},
                        "name": {"type": "STRING", "description": "Optional name of the venue or place."},
                        "address": {"type": "STRING", "description": "Optional physical address of the location."},
                        "url": {"type": "STRING", "description": "Optional URL associated with the location."},
                        "reply_to": {"type": "STRING", "description": "Optional MsgID to reply directly to a specific message."}
                    },
                    "required": ["target", "latitude", "longitude"]
                }
            },
            {
                "name": "WhatsApp_send_contact",
                "description": "Sends a contact card (vCard) to the specified target. You can share a single contact (with name, phone number, optional organization and email) or multiple contacts, or pass a raw vCard string. Optionally include reply_to MsgID.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "target": {"type": "STRING", "description": "Name, phone, or ID of the contact/group."},
                        "contact_name": {"type": "STRING", "description": "Full name of the contact being shared (e.g., 'Alice Smith')."},
                        "contact_phone": {"type": "STRING", "description": "Phone number or WhatsApp ID of the contact being shared (e.g., '+1234567890' or '1234567890@c.us')."},
                        "organization": {"type": "STRING", "description": "Optional organization or company of the contact."},
                        "email": {"type": "STRING", "description": "Optional email address of the contact."},
                        "vcard": {"type": "STRING", "description": "Optional raw vCard RFC 2426 formatted string."},
                        "contacts": {"type": "STRING", "description": "Optional JSON string or array of contacts to share multiple contact cards at once (e.g. `[{\"name\": \"Alice\", \"phone\": \"+1234567890\"}, {\"name\": \"Bob\", \"phone\": \"+0987654321\"}]`)."},
                        "reply_to": {"type": "STRING", "description": "Optional MsgID to reply directly to a specific message."}
                    },
                    "required": ["target"]
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
                "description": "Restarts the WhatsApp server, clears caches, and updates the underlying WPPConnect WA-JS engine to the latest release to fix protocol mismatch errors. Use this tool if the WhatsApp client is stuck initializing or throws internal evaluation errors.",
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
        if getattr(self.daemon, "is_downloading_chrome", False):
            return "[System Notice: WhatsApp bridge is currently completing its one-time browser engine download. Please retry in a moment.]"

        if command in ["unread", "recent", "recent_chat"]:
            CacheManager.clear_expired(os.path.join(
                self.data_dir, 'uploads'), max_age_days=1.0)

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
            if getattr(self.daemon, "is_downloading_chrome", False):
                return "[System Notice: WhatsApp bridge is currently completing its one-time browser engine download. Please retry in a moment.]"
            return "WhatsApp bridge is not running. If it crashed, use WhatsApp_reset_connection to restart it."

        if command == "unread":
            try:
                res = requests.get(f"{self.base_url}/unread", timeout=15)
                data = res.json()
                if "error" in data:
                    return f"Error: {data['error']}"

                unread = data.get("unread", [])

                if hasattr(self, 'orchestrator') and self.orchestrator:
                    is_low_token = self.orchestrator.settings_manager.get(
                        "core.low-token-mode", False)
                else:
                    is_low_token = False

                if is_low_token and len(unread) > 5:
                    unread = unread[:5]

                if not unread:
                    return "No unread messages."

                output = "Unread Messages:\n"
                media_files = []
                for msg in unread:
                    content = msg.get('content', '')
                    msg_type = msg.get('type', 'chat')
                    if msg_type in ['location', 'live_location']:
                        # Location messages are structured and omit raw map thumbnails
                        pass
                    elif msg_type in ['vcard', 'multi_vcard', 'contact']:
                        # Contact card messages are structured and omit raw vcards/media
                        pass
                    elif msg.get('mediaPath') and os.path.exists(msg['mediaPath']):
                        if msg_type in ['ptt', 'audio']:
                            content = f"[Voice Message (Path: {msg['mediaPath']})] {content}"
                            media_files.append(msg['mediaPath'])
                        elif msg_type in ['image', 'document', 'video']:
                            content = f"[{msg_type.capitalize()} (Path: {msg['mediaPath']})] {content}"
                        else:
                            content = f"[Media: {msg_type} (Path: {msg['mediaPath']})] {content}"
                    elif msg.get('hasMedia'):
                        content = f"[Media: {msg_type}] {content}"

                    channel_type = "WHATSAPP_GROUP" if msg['chatId'].endswith(
                        "@g.us") else "WHATSAPP_DM"
                    sender_display = f"{msg['senderName']} (+{msg['senderNumber']})" if msg.get(
                        'senderNumber') else msg['senderName']
                    output += f"- [CHANNEL: {channel_type}] [SOURCE_ID: {msg['chatId']}] [{msg['chatName']}] {sender_display}: {content} (MsgID: {msg['id']})\n"

                if media_files:
                    output += "\n[SYSTEM NOTE: Voice message audio has been attached to your multimodal context. If your multimodal audio input fails to understand or process it, use the Media_transcribe_audio tool to transcribe the voice note. To respond, use WhatsApp_send, WhatsApp_send_voice, WhatsApp_send_location, or WhatsApp_send_contact.]"
                    return {
                        "result": output,
                        "media": media_files
                    }
                else:
                    output += "\n[SYSTEM NOTE: If you need to view or listen to any media attachments, use the Media_read tool. To respond, use WhatsApp_send, WhatsApp_send_voice, WhatsApp_send_location, or WhatsApp_send_contact.]"
                    return output
            except Exception as e:
                return f"Failed to fetch unread messages: {e}"

        elif command == "recent":
            n = kwargs.get('n') or (args[0] if len(args) > 0 else 30)
            if hasattr(self, 'orchestrator') and self.orchestrator:
                is_low_token = self.orchestrator.settings_manager.get(
                    "core.low-token-mode", False)
            else:
                is_low_token = False
            if is_low_token:
                n = min(int(n), 5)

            try:
                res = requests.get(f"{self.base_url}/recent?n={n}", timeout=15)
                data = res.json()
                if "error" in data:
                    return f"Error: {data['error']}"

                messages = data.get("messages", [])
                if not messages:
                    return "No recent messages."

                output = f"Recent Messages (Last {len(messages)}):\n"
                media_files = []
                for msg in messages:
                    content = msg.get('content', '')
                    msg_type = msg.get('type', 'chat')
                    if msg_type in ['location', 'live_location']:
                        # Location messages are structured and omit raw map thumbnails
                        pass
                    elif msg_type in ['vcard', 'multi_vcard', 'contact']:
                        # Contact card messages are structured and omit raw vcards/media
                        pass
                    elif msg.get('mediaPath') and os.path.exists(msg['mediaPath']):
                        if msg_type in ['ptt', 'audio']:
                            content = f"[Voice Message (Path: {msg['mediaPath']})] {content}"
                            if len(media_files) < 5:
                                media_files.append(msg['mediaPath'])
                        elif msg_type in ['image', 'document', 'video']:
                            content = f"[{msg_type.capitalize()} (Path: {msg['mediaPath']})] {content}"
                        else:
                            content = f"[Media: {msg_type} (Path: {msg['mediaPath']})] {content}"
                    elif msg.get('hasMedia'):
                        content = f"[Media: {msg_type}] {content}"

                    channel_type = "WHATSAPP_GROUP" if msg['chatId'].endswith(
                        "@g.us") else "WHATSAPP_DM"
                    sender_display = f"{msg['senderName']} (+{msg['senderNumber']})" if msg.get(
                        'senderNumber') else msg['senderName']
                    output += f"- [CHANNEL: {channel_type}] [SOURCE_ID: {msg['chatId']}] [{msg['chatName']}] {sender_display}: {content} (MsgID: {msg['id']})\n"

                if media_files:
                    output += "\n[SYSTEM NOTE: Voice message audio has been attached to your multimodal context. If your multimodal audio input fails to understand or process it, use the Media_transcribe_audio tool to transcribe the voice note. To respond, use WhatsApp_send, WhatsApp_send_voice, WhatsApp_send_location, or WhatsApp_send_contact.]"
                    return {
                        "result": output,
                        "media": media_files
                    }
                else:
                    output += "\n[SYSTEM NOTE: If you need to view or listen to any media attachments, use the Media_read tool. To respond, use WhatsApp_send, WhatsApp_send_voice, WhatsApp_send_location, or WhatsApp_send_contact.]"
                    return output
            except Exception as e:
                return f"Failed to fetch recent messages: {e}"

        elif command == "recent_chat":
            target = kwargs.get('target') or (args[0] if args else None)
            if not target:
                return "Usage: recent_chat <target> [n]"
            n = kwargs.get('n') or (args[1] if len(args) > 1 else 30)
            if hasattr(self, 'orchestrator') and self.orchestrator:
                is_low_token = self.orchestrator.settings_manager.get(
                    "core.low-token-mode", False)
            else:
                is_low_token = False
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
                res = requests.get(
                    f"{self.base_url}/recent?n={n}&target={safe_target}", timeout=15)
                data = res.json()
                if "error" in data:
                    return f"Error: {data['error']}"

                messages = data.get("messages", [])
                if not messages:
                    return f"No recent messages for {target}."

                output = f"Recent Messages for {target} (Last {len(messages)}):\n"
                media_files = []
                for msg in messages:
                    content = msg.get('content', '')
                    msg_type = msg.get('type', 'chat')
                    if msg_type in ['location', 'live_location']:
                        # Location messages are structured and omit raw map thumbnails
                        pass
                    elif msg_type in ['vcard', 'multi_vcard', 'contact']:
                        # Contact card messages are structured and omit raw vcards/media
                        pass
                    elif msg.get('mediaPath') and os.path.exists(msg['mediaPath']):
                        if msg_type in ['ptt', 'audio']:
                            content = f"[Voice Message (Path: {msg['mediaPath']})] {content}"
                            if len(media_files) < 5:
                                media_files.append(msg['mediaPath'])
                        elif msg_type in ['image', 'document', 'video']:
                            content = f"[{msg_type.capitalize()} (Path: {msg['mediaPath']})] {content}"
                        else:
                            content = f"[Media: {msg_type} (Path: {msg['mediaPath']})] {content}"
                    elif msg.get('hasMedia'):
                        content = f"[Media: {msg_type}] {content}"

                    channel_type = "WHATSAPP_GROUP" if msg['chatId'].endswith(
                        "@g.us") else "WHATSAPP_DM"
                    sender_display = f"{msg['senderName']} (+{msg['senderNumber']})" if msg.get(
                        'senderNumber') else msg['senderName']
                    output += f"- [CHANNEL: {channel_type}] [SOURCE_ID: {msg['chatId']}] [{msg['chatName']}] {sender_display}: {content} (MsgID: {msg['id']})\n"

                if media_files:
                    output += "\n[SYSTEM NOTE: Voice message audio has been attached to your multimodal context. If your multimodal audio input fails to understand or process it, use the Media_transcribe_audio tool to transcribe the voice note. To respond, use WhatsApp_send, WhatsApp_send_voice, WhatsApp_send_location, or WhatsApp_send_contact.]"
                    return {
                        "result": output,
                        "media": media_files
                    }
                else:
                    output += "\n[SYSTEM NOTE: If you need to view or listen to any media attachments, use the Media_read tool. To respond, use WhatsApp_send, WhatsApp_send_voice, WhatsApp_send_location, or WhatsApp_send_contact.]"
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

                        res = requests.post(
                            f"{self.base_url}/send", data=data_payload, files=files, timeout=60)
                else:
                    res = requests.post(
                        f"{self.base_url}/send", json=payload, timeout=15)

                data = res.json()
                if "error" in data:
                    return f"Error: {data['error']}"
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
                from core.audio_output import synthesize_speech_to_file

                settings = getattr(self.orchestrator, 'settings_manager', None) if hasattr(
                    self, 'orchestrator') and self.orchestrator else None
                agent_id = getattr(self.orchestrator, 'agent_id', None) if hasattr(
                    self, 'orchestrator') and self.orchestrator else None

                temp_fd, temp_ogg = tempfile.mkstemp(suffix=".ogg")
                os.close(temp_fd)

                try:
                    audio_path = synthesize_speech_to_file(
                        text=message,
                        output_path=temp_ogg,
                        agent_id=agent_id,
                        settings=settings
                    )

                    if os.path.exists(audio_path) and os.path.getsize(audio_path) > 0:
                        with open(audio_path, 'rb') as f:
                            files = {'media': ('voice.ogg', f, 'audio/ogg')}
                            data = {'target': target, 'isVoice': 'true'}
                            if reply_to:
                                data['reply_to'] = reply_to

                            res = requests.post(
                                f"{self.base_url}/send", data=data, files=files, timeout=30)
                            res_data = res.json()

                        if "error" in res_data:
                            return f"Error: {res_data['error']}"
                        return f"Voice message sent to {target} successfully."
                    else:
                        return "Failed to convert TTS audio to OGG."
                except Exception as e:
                    return f"Failed to generate TTS audio: {e}"
                finally:
                    if os.path.exists(temp_ogg):
                        os.remove(temp_ogg)

            except Exception as e:
                return f"Failed to send voice message: {e}"

        elif command == "send_location":
            target = kwargs.get('target')
            lat = kwargs.get('latitude') if kwargs.get('latitude') is not None else kwargs.get('lat')
            lng = kwargs.get('longitude') if kwargs.get('longitude') is not None else kwargs.get('lng')
            name = kwargs.get('name')
            address = kwargs.get('address')
            url = kwargs.get('url')
            reply_to = kwargs.get('reply_to')

            if not target and len(args) >= 1:
                target = args[0]
            if lat is None and len(args) >= 2:
                lat = args[1]
            if lng is None and len(args) >= 3:
                lng = args[2]
            if not name and len(args) >= 4:
                name = args[3]
            if not address and len(args) >= 5:
                address = args[4]
            if not url and len(args) >= 6:
                url = args[5]

            if not target or lat is None or lng is None:
                return "Usage: send_location <target> <latitude> <longitude> [name] [address] [url]"

            try:
                lat = float(lat)
                lng = float(lng)
            except (ValueError, TypeError):
                return "Error: Latitude and longitude must be valid numbers."

            try:
                try:
                    # Attempt to resolve via internal Rolodex first
                    from core.rolodex import Rolodex
                    r = Rolodex()
                    entity = r._find_entity(target)
                    if entity and entity.get('whatsapp_id'):
                        target = entity['whatsapp_id']
                    elif entity and entity.get('phone'):
                        target = entity['phone']
                except Exception:
                    pass

                payload = {
                    "target": target,
                    "lat": lat,
                    "lng": lng
                }
                if name:
                    payload["name"] = name
                if address:
                    payload["address"] = address
                if url:
                    payload["url"] = url
                if reply_to:
                    payload["reply_to"] = reply_to

                res = requests.post(f"{self.base_url}/send_location", json=payload, timeout=15)
                data = res.json()
                if "error" in data:
                    return f"Error: {data['error']}"
                return f"Location pin sent to {target} successfully."
            except Exception as e:
                return f"Failed to send location pin: {e}"

        elif command == "send_contact":
            target = kwargs.get('target')
            contact_name = kwargs.get('contact_name') or kwargs.get('name')
            contact_phone = kwargs.get('contact_phone') or kwargs.get('phone') or kwargs.get('contact_id')
            organization = kwargs.get('organization') or kwargs.get('org')
            email = kwargs.get('email')
            vcard = kwargs.get('vcard')
            contacts = kwargs.get('contacts')
            reply_to = kwargs.get('reply_to')

            if not target and len(args) >= 1:
                target = args[0]
            if not contact_name and len(args) >= 2:
                contact_name = args[1]
            if not contact_phone and len(args) >= 3:
                contact_phone = args[2]
            if not organization and len(args) >= 4:
                organization = args[3]
            if not email and len(args) >= 5:
                email = args[4]

            if not target or (not contact_name and not contact_phone and not vcard and not contacts):
                return "Usage: send_contact <target> <contact_name> <contact_phone> [organization] [email]"

            try:
                try:
                    # Attempt to resolve target via internal Rolodex first
                    from core.rolodex import Rolodex
                    r = Rolodex()
                    entity = r._find_entity(target)
                    if entity and entity.get('whatsapp_id'):
                        target = entity['whatsapp_id']
                    elif entity and entity.get('phone'):
                        target = entity['phone']
                except Exception:
                    pass

                # If contact_name is given without contact_phone or vcard or contacts, try Rolodex
                if not contact_phone and contact_name and not vcard and not contacts:
                    try:
                        from core.rolodex import Rolodex
                        r = Rolodex()
                        entity = r._find_entity(contact_name)
                        if entity:
                            contact_phone = entity.get('whatsapp_id') or entity.get('phone')
                    except Exception:
                        pass

                import json
                if isinstance(contacts, str):
                    try:
                        contacts = json.loads(contacts)
                    except Exception:
                        pass

                payload = {"target": target}
                if contact_name:
                    payload["name"] = contact_name
                if contact_phone:
                    payload["phone"] = contact_phone
                if organization:
                    payload["organization"] = organization
                if email:
                    payload["email"] = email
                if vcard:
                    payload["vcard"] = vcard
                if contacts:
                    payload["contacts"] = contacts
                if reply_to:
                    payload["reply_to"] = reply_to

                res = requests.post(f"{self.base_url}/send_contact", json=payload, timeout=15)
                data = res.json()
                if "error" in data:
                    return f"Error: {data['error']}"
                label = contact_name or (f"{len(contacts)} contacts" if isinstance(contacts, list) else "Contact card")
                return f"Contact card for {label} sent to {target} successfully."
            except Exception as e:
                return f"Failed to send contact card: {e}"

        elif command == "react":
            msg_id = kwargs.get('msgId')
            reaction = kwargs.get('reaction')

            if not msg_id and len(args) >= 2:
                msg_id = args[0]
                reaction = args[1]

            if not msg_id or not reaction:
                return "Usage: react <msgId> <reaction>"

            try:
                res = requests.post(
                    f"{self.base_url}/react", json={"msgId": msg_id, "reaction": reaction}, timeout=10)
                data = res.json()
                if "error" in data:
                    return f"Error: {data['error']}"
                return f"Reacted with {reaction} successfully."
            except Exception as e:
                return f"Failed to react: {e}"

        elif command == "mark_read":
            chat_id = kwargs.get('chatId') or (args[0] if args else None)
            if not chat_id:
                return "Usage: mark_read <chatId>"
            try:
                res = requests.post(
                    f"{self.base_url}/mark_read", json={"chatId": chat_id}, timeout=10)
                data = res.json()
                if "error" in data:
                    return f"Error: {data['error']}"
                return f"Chat {chat_id} marked as read."
            except Exception as e:
                return f"Failed to mark as read: {e}"

        return f"Unknown command: {command}"

    def shutdown(self):
        if hasattr(self, 'daemon') and self.daemon:
            self.daemon.stop()

    def __del__(self):
        self.shutdown()

