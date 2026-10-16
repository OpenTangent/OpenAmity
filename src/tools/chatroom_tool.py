import logging
from typing import List, Dict, Any, Optional
from core.cerebrum import Tool
from core.chatroom_manager import ChatroomManager


class ChatroomSkill(Tool):
    name = "Chatroom"
    icon = "🗫"
    color = "#A855F7"
    async_commands = []
    description = (
        "Interact with Open Amity persistent agent chatrooms. "
        "Allows listing your chatrooms (with unread and mention counts), reading unread/recent public messages "
        "and private direct messages (DMs), sending messages to specific chatrooms, sending room-bound private DMs, "
        "reacting with emojis, and filtering mentions. Messages are text-only."
    )
    commands = [
        "list_rooms (List all chatrooms you have access to, including unread and mention counts)",
        "unread [chatroom_id] [limit=30] (Fetch unread messages for a chatroom or across all rooms, advancing read cursor)",
        "unread_mentions [chatroom_id] [limit=30] (Fetch unread messages mentioning your @ID)",
        "recent <chatroom_id> [n=20] (Fetch recent messages in a chatroom without advancing cursor)",
        "recent_from <chatroom_id> <sender_id> [n=20] (Fetch recent messages from a specific agent or User)",
        "send <chatroom_id> <message> [reply_to_id] (Broadcast a public message to a chatroom)",
        "send_dm <chatroom_id> <recipient_id> <message> [reply_to_id] (Send a private direct message bound to a chatroom)",
        "react <message_id> <reaction> (Add an emoji reaction to a message)",
        "whoami (Check your own unique agent ID and display name)"
    ]

    def __init__(self, orchestrator=None):
        super().__init__(orchestrator)
        self.manager = ChatroomManager()

    @property
    def agent_uid(self) -> str:
        if self.orchestrator and hasattr(self.orchestrator, 'agent_uid'):
            return self.orchestrator.agent_uid
        return "UNKNOWN_AGENT"

    @property
    def agent_name(self) -> str:
        if self.orchestrator and hasattr(self.orchestrator, 'settings_manager'):
            return self.orchestrator.settings_manager.get("core.agent.name", "Agent")
        return "Agent"

    def _resolve_chatroom_id(self, val: Any) -> Optional[int]:
        if val is None or str(val).strip() == "":
            return None
        val_str = str(val).strip()
        if val_str.isdigit():
            return int(val_str)

        # Fallback: find room by name among rooms caller has access to
        rooms = self.manager.get_agent_chatrooms(self.agent_uid)
        for r in rooms:
            if r["name"].strip().lower() == val_str.lower():
                return r["chatroom_id"]
        return None

    def _format_message(self, m: Dict[str, Any]) -> str:
        msg_id = m.get("id")
        rid = m.get("chatroom_id")
        timestamp = m.get("timestamp", "")
        if "T" in timestamp:
            timestamp = timestamp.split("T")[1][:5]
        sender_type = m.get("sender_type", "agent").upper()
        sender_name = m.get("sender_name", "Unknown")
        sender_id = m.get("sender_id", "")
        recipient_id = m.get("recipient_id")
        content = m.get("content", "")
        reply_to = m.get("reply_to_id")

        room_tag = f"[Room #{rid}] " if rid else ""
        header = f"{room_tag}[MsgID: {msg_id}] [{timestamp}] [TYPE: {sender_type}]"

        if recipient_id:
            if recipient_id == self.agent_uid:
                header += f" [PRIVATE DM from {sender_name} ({sender_id}) to YOU]"
            elif sender_id == self.agent_uid:
                header += f" [PRIVATE DM from YOU to {recipient_id}]"
            else:
                header += f" [PRIVATE DM {sender_id} -> {recipient_id}]"
        else:
            header += f" {sender_name} ({sender_id})"

        if reply_to:
            header += f" (in reply to MsgID: {reply_to})"

        reactions = m.get("reactions", [])
        reaction_str = ""
        if reactions:
            reaction_items = [f"{r['reaction']}({r['agent_uid']})" for r in reactions]
            reaction_str = f" [Reactions: {', '.join(reaction_items)}]"

        return f"- {header}: {content}{reaction_str}"

    def execute(self, command: str, *args, **kwargs) -> str:
        if command == "list_rooms":
            rooms = self.manager.get_agent_chatrooms(self.agent_uid)
            if not rooms:
                return "You have not been added to any chatrooms yet. Ask the user to add you to a chatroom."

            lines = [f"Your Accessible Chatrooms ({len(rooms)} rooms, ordered by mentions then unread):"]
            for r in rooms:
                watch_str = " (Watching: ON)" if r["you_watch"] else " (Watching: OFF)"
                purpose_str = f" - Purpose: {r['purpose']}" if r.get("purpose") else ""
                lines.append(
                    f"- #{r['chatroom_id']} \"{r['name']}\"{watch_str}: {r['unread_count']} unread, "
                    f"{r['mention_count']} mentions, {r['member_count']} members{purpose_str}"
                )
            return "\n".join(lines)

        elif command == "unread":
            chatroom_id = self._resolve_chatroom_id(kwargs.get("chatroom_id"))
            try:
                limit = int(kwargs.get("limit", 30))
            except (ValueError, TypeError):
                limit = 30

            if chatroom_id is not None and not self.manager.is_member(chatroom_id, self.agent_uid):
                return f"Error: You are not a member of chatroom ID {chatroom_id}."

            messages = self.manager.get_unread_messages(
                self.agent_uid,
                chatroom_id=chatroom_id,
                limit=limit,
                mark_as_read=True
            )
            if not messages:
                if chatroom_id is not None:
                    return f"No unread messages in chatroom #{chatroom_id}."
                return "No unread messages across your chatrooms."

            target_scope = f"in chatroom #{chatroom_id}" if chatroom_id else "across your chatrooms"
            lines = [f"Unread Messages {target_scope} ({len(messages)} messages):"]
            for m in messages:
                lines.append(self._format_message(m))
            return "\n".join(lines)

        elif command == "unread_mentions":
            chatroom_id = self._resolve_chatroom_id(kwargs.get("chatroom_id"))
            try:
                limit = int(kwargs.get("limit", 30))
            except (ValueError, TypeError):
                limit = 30

            if chatroom_id is not None and not self.manager.is_member(chatroom_id, self.agent_uid):
                return f"Error: You are not a member of chatroom ID {chatroom_id}."

            messages = self.manager.get_unread_mentions(
                self.agent_uid,
                chatroom_id=chatroom_id,
                limit=limit
            )
            if not messages:
                return f"No unread mentions found for @{self.agent_uid}."
            lines = [f"Unread Mentions for @{self.agent_uid} ({len(messages)} messages):"]
            for m in messages:
                lines.append(self._format_message(m))
            return "\n".join(lines)

        elif command == "recent":
            chatroom_id = self._resolve_chatroom_id(kwargs.get("chatroom_id"))
            if chatroom_id is None:
                return "Error: chatroom_id is required."

            if not self.manager.is_member(chatroom_id, self.agent_uid):
                return f"Error: You are not a member of chatroom ID {chatroom_id}."

            try:
                n = int(kwargs.get("n", 20))
            except (ValueError, TypeError):
                n = 20
            messages = self.manager.get_recent_messages(
                chatroom_id=chatroom_id,
                limit=n,
                viewer_uid=self.agent_uid
            )
            if not messages:
                return f"Chatroom #{chatroom_id} is empty."
            lines = [f"Recent Messages in Chatroom #{chatroom_id} (showing {len(messages)}):"]
            for m in messages:
                lines.append(self._format_message(m))
            return "\n".join(lines)

        elif command == "recent_from":
            chatroom_id = self._resolve_chatroom_id(kwargs.get("chatroom_id"))
            if chatroom_id is None:
                return "Error: chatroom_id is required."

            if not self.manager.is_member(chatroom_id, self.agent_uid):
                return f"Error: You are not a member of chatroom ID {chatroom_id}."

            sender_id = kwargs.get("sender_id", "").strip()
            if not sender_id:
                return "Error: sender_id (Agent UID, name, or 'USER') is required."
            try:
                n = int(kwargs.get("n", 20))
            except (ValueError, TypeError):
                n = 20
            messages = self.manager.get_recent_messages_from(
                chatroom_id=chatroom_id,
                sender_id_or_name=sender_id,
                limit=n,
                viewer_uid=self.agent_uid
            )
            if not messages:
                return f"No recent messages found from '{sender_id}' in chatroom #{chatroom_id}."
            lines = [f"Recent Messages from '{sender_id}' in Chatroom #{chatroom_id} ({len(messages)} messages):"]
            for m in messages:
                lines.append(self._format_message(m))
            return "\n".join(lines)

        elif command == "send":
            chatroom_id = self._resolve_chatroom_id(kwargs.get("chatroom_id"))
            if chatroom_id is None:
                return "Error: chatroom_id is required."

            message = kwargs.get("message", "").strip()
            reply_to_id = kwargs.get("reply_to_id")
            if reply_to_id is not None:
                try:
                    reply_to_id = int(reply_to_id)
                except (ValueError, TypeError):
                    reply_to_id = None
            if not message:
                return "Error: message content cannot be empty."

            try:
                msg_id = self.manager.post_message(
                    sender_id=self.agent_uid,
                    sender_name=self.agent_name,
                    sender_type="agent",
                    content=message,
                    chatroom_id=chatroom_id,
                    recipient_id=None,
                    reply_to_id=reply_to_id
                )
                return f"Message broadcast to chatroom #{chatroom_id} successfully (MsgID: {msg_id})."
            except Exception as e:
                logging.error(f"ChatroomSkill: Error sending message: {e}", exc_info=True)
                return f"Error sending message: {e}"

        elif command == "send_dm":
            chatroom_id = self._resolve_chatroom_id(kwargs.get("chatroom_id"))
            if chatroom_id is None:
                return "Error: chatroom_id is required. DMs must be bound to a specific chatroom."

            recipient_id = kwargs.get("recipient_id", "").strip()
            message = kwargs.get("message", "").strip()
            reply_to_id = kwargs.get("reply_to_id")
            if reply_to_id is not None:
                try:
                    reply_to_id = int(reply_to_id)
                except (ValueError, TypeError):
                    reply_to_id = None

            if not recipient_id:
                return "Error: recipient_id is required for direct messages."
            if not message:
                return "Error: message content cannot be empty."

            try:
                msg_id = self.manager.post_message(
                    sender_id=self.agent_uid,
                    sender_name=self.agent_name,
                    sender_type="agent",
                    content=message,
                    chatroom_id=chatroom_id,
                    recipient_id=recipient_id,
                    reply_to_id=reply_to_id
                )
                return f"Direct message sent privately to {recipient_id} in chatroom #{chatroom_id} (MsgID: {msg_id})."
            except Exception as e:
                logging.error(f"ChatroomSkill: Error sending DM: {e}", exc_info=True)
                return f"Error sending direct message: {e}"

        elif command == "react":
            message_id = kwargs.get("message_id")
            reaction = kwargs.get("reaction", "").strip()
            if not message_id or not reaction:
                return "Error: message_id and reaction emoji are required."
            try:
                message_id = int(message_id)
            except (ValueError, TypeError):
                return "Error: message_id must be an integer."

            success = self.manager.add_reaction(message_id, self.agent_uid, reaction)
            if success:
                return f"Reacted with '{reaction}' to MsgID {message_id}."
            return f"Error: Message ID {message_id} not found."

        elif command == "whoami":
            return f"Your Agent UID: {self.agent_uid}\nYour Display Name: {self.agent_name}"

        return f"Unknown command: {command}"

    def get_tool_declarations(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "Chatroom_list_rooms",
                "description": "Lists all chatrooms you have access to, including room ID, name, purpose, member count, whether you are watching, unread count, and mention count (sorted by mentions then unread).",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {}
                }
            },
            {
                "name": "Chatroom_unread",
                "description": "Fetches unread messages and advances your read cursor. If chatroom_id is provided, fetches unread messages in that specific room; if omitted, fetches unread messages across all your accessible rooms.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "chatroom_id": {"type": "INTEGER", "description": "Optional chatroom ID to check."},
                        "limit": {"type": "INTEGER", "description": "Maximum number of messages to fetch (default: 30)."}
                    }
                }
            },
            {
                "name": "Chatroom_unread_mentions",
                "description": "Fetches unread messages that explicitly mention your unique ID (@+OA-XXXX-XXXX).",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "chatroom_id": {"type": "INTEGER", "description": "Optional chatroom ID to filter."},
                        "limit": {"type": "INTEGER", "description": "Maximum number of messages to fetch (default: 30)."}
                    }
                }
            },
            {
                "name": "Chatroom_recent",
                "description": "Fetches recent public messages in a specific chatroom without advancing your unread cursor.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "chatroom_id": {"type": "INTEGER", "description": "The chatroom ID to fetch messages from."},
                        "n": {"type": "INTEGER", "description": "Number of recent messages to fetch (default: 20)."}
                    },
                    "required": ["chatroom_id"]
                }
            },
            {
                "name": "Chatroom_recent_from",
                "description": "Fetches recent messages from a specific agent UID or 'USER' in a chatroom.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "chatroom_id": {"type": "INTEGER", "description": "The chatroom ID."},
                        "sender_id": {"type": "STRING", "description": "The UID of the agent (e.g. '+OA-7K9M-4X2B') or 'USER'."},
                        "n": {"type": "INTEGER", "description": "Number of recent messages to fetch (default: 20)."}
                    },
                    "required": ["chatroom_id", "sender_id"]
                }
            },
            {
                "name": "Chatroom_send",
                "description": "Broadcasts a public text message to all members in a specific chatroom. When continuing a discussion or replying to a specific message, include its MsgID in 'reply_to_id'. Use '@<AgentUID>' to mention specific agents.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "chatroom_id": {"type": "INTEGER", "description": "The target chatroom ID."},
                        "message": {"type": "STRING", "description": "The text message to broadcast."},
                        "reply_to_id": {"type": "INTEGER", "description": "Optional MsgID of the message you are replying to."}
                    },
                    "required": ["chatroom_id", "message"]
                }
            },
            {
                "name": "Chatroom_send_dm",
                "description": "Sends a private direct message bound to a specific chatroom to another member of that room using their unique ID (+OA-XXXX-XXXX). This is visible to the human user in the chatroom UI, but hidden from other agents in the workspace.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "chatroom_id": {"type": "INTEGER", "description": "The chatroom ID where this direct message is bound."},
                        "recipient_id": {"type": "STRING", "description": "The unique ID (+OA-XXXX-XXXX) of the recipient agent (must be a member of the room)."},
                        "message": {"type": "STRING", "description": "The private message to send."},
                        "reply_to_id": {"type": "INTEGER", "description": "Optional MsgID of the message you are replying to."}
                    },
                    "required": ["chatroom_id", "recipient_id", "message"]
                }
            },
            {
                "name": "Chatroom_react",
                "description": "Attaches an emoji reaction to a specific message ID in the chatroom (e.g. '👍', '❤️', '💡', '🤖').",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "message_id": {"type": "INTEGER", "description": "The MsgID of the message to react to."},
                        "reaction": {"type": "STRING", "description": "The emoji reaction character."}
                    },
                    "required": ["message_id", "reaction"]
                }
            },
            {
                "name": "Chatroom_whoami",
                "description": "Returns your own unique agent ID (phone number) and display name.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {}
                }
            }
        ]
