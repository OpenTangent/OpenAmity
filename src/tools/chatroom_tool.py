import logging
from typing import List, Dict, Any, Optional
from core.cerebrum import Tool
from core.chatroom_manager import ChatroomManager


class ChatroomSkill(Tool):
    name = "Chatroom"
    description = (
        "Interact with the Open Amity persistent agent chatroom. "
        "Allows reading unread/recent public messages and private direct messages (DMs), "
        "sending public broadcasts, sending private DMs to specific agent IDs, reacting with emojis, "
        "and filtering mentions. Messages are text-only."
    )
    commands = [
        "unread [limit=30] (Fetch and catch up on unread messages, advancing your read cursor)",
        "unread_mentions [limit=30] (Fetch unread messages specifically mentioning your @ID)",
        "recent [n=20] (Fetch n recent public messages without advancing read cursor)",
        "recent_from <sender_id> [n=20] (Fetch recent messages from a specific agent ID or User)",
        "send <message> [reply_to_id] (Broadcast a public message to all agents and user)",
        "send_dm <recipient_id> <message> [reply_to_id] (Send a private direct message to an agent's ID)",
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

    def _format_message(self, m: Dict[str, Any]) -> str:
        msg_id = m.get("id")
        timestamp = m.get("timestamp", "")
        if "T" in timestamp:
            timestamp = timestamp.split("T")[1][:5]
        sender_type = m.get("sender_type", "agent").upper()
        sender_name = m.get("sender_name", "Unknown")
        sender_id = m.get("sender_id", "")
        recipient_id = m.get("recipient_id")
        content = m.get("content", "")
        reply_to = m.get("reply_to_id")

        header = f"[MsgID: {msg_id}] [{timestamp}] [TYPE: {sender_type}]"

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
        if command == "unread":
            try:
                limit = int(kwargs.get("limit", 30))
            except (ValueError, TypeError):
                limit = 30
            messages = self.manager.get_unread_messages(self.agent_uid, limit=limit, mark_as_read=True)
            if not messages:
                return "No unread messages in the chatroom."
            lines = [f"Unread Chatroom Messages ({len(messages)} messages):"]
            for m in messages:
                lines.append(self._format_message(m))
            return "\n".join(lines)

        elif command == "unread_mentions":
            try:
                limit = int(kwargs.get("limit", 30))
            except (ValueError, TypeError):
                limit = 30
            messages = self.manager.get_unread_mentions(self.agent_uid, limit=limit)
            if not messages:
                return f"No unread mentions found for @{self.agent_uid}."
            lines = [f"Unread Mentions for @{self.agent_uid} ({len(messages)} messages):"]
            for m in messages:
                lines.append(self._format_message(m))
            return "\n".join(lines)

        elif command == "recent":
            try:
                n = int(kwargs.get("n", 20))
            except (ValueError, TypeError):
                n = 20
            messages = self.manager.get_recent_messages(limit=n, viewer_uid=self.agent_uid)
            if not messages:
                return "Chatroom is empty."
            lines = [f"Recent Chatroom Messages (showing {len(messages)}):"]
            for m in messages:
                lines.append(self._format_message(m))
            return "\n".join(lines)

        elif command == "recent_from":
            sender_id = kwargs.get("sender_id", "").strip()
            if not sender_id:
                return "Error: sender_id (Agent UID, name, or 'USER') is required."
            try:
                n = int(kwargs.get("n", 20))
            except (ValueError, TypeError):
                n = 20
            messages = self.manager.get_recent_messages_from(sender_id, limit=n, viewer_uid=self.agent_uid)
            if not messages:
                return f"No recent messages found from '{sender_id}'."
            lines = [f"Recent Messages from '{sender_id}' ({len(messages)} messages):"]
            for m in messages:
                lines.append(self._format_message(m))
            return "\n".join(lines)

        elif command == "send":
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
                    recipient_id=None,
                    reply_to_id=reply_to_id
                )
                return f"Message broadcast to chatroom successfully (MsgID: {msg_id})."
            except Exception as e:
                logging.error(f"ChatroomSkill: Error sending message: {e}", exc_info=True)
                return f"Error sending message: {e}"

        elif command == "send_dm":
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
                    recipient_id=recipient_id,
                    reply_to_id=reply_to_id
                )
                return f"Direct message sent privately to {recipient_id} (MsgID: {msg_id})."
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
                "name": "Chatroom_unread",
                "description": "Fetches all unread messages (public broadcasts and direct messages sent to you) since your last check, and advances your unread cursor.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
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
                        "limit": {"type": "INTEGER", "description": "Maximum number of messages to fetch (default: 30)."}
                    }
                }
            },
            {
                "name": "Chatroom_recent",
                "description": "Fetches recent public messages across the chatroom without advancing your unread cursor.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "n": {"type": "INTEGER", "description": "Number of recent messages to fetch (default: 20)."}
                    }
                }
            },
            {
                "name": "Chatroom_recent_from",
                "description": "Fetches recent messages from a specific agent UID or from 'USER'.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "sender_id": {"type": "STRING", "description": "The UID of the agent (e.g. '+OA-7K9M-4X2B') or 'USER'."},
                        "n": {"type": "INTEGER", "description": "Number of recent messages to fetch (default: 20)."}
                    },
                    "required": ["sender_id"]
                }
            },
            {
                "name": "Chatroom_send",
                "description": "Broadcasts a public text message to all agents and the user in the chatroom. When continuing a discussion or replying to a specific message, include its MsgID in 'reply_to_id'. Use '@<AgentUID>' to mention specific agents.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "message": {"type": "STRING", "description": "The text message to broadcast."},
                        "reply_to_id": {"type": "INTEGER", "description": "Optional MsgID of the message you are replying to."}
                    },
                    "required": ["message"]
                }
            },
            {
                "name": "Chatroom_send_dm",
                "description": "Sends a private direct message to a specific agent using their unique ID (+OA-XXXX-XXXX). This is visible to the human user in the chatroom UI, but hidden from other agents in the workspace.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "recipient_id": {"type": "STRING", "description": "The unique ID (+OA-XXXX-XXXX) of the recipient agent."},
                        "message": {"type": "STRING", "description": "The private message to send."},
                        "reply_to_id": {"type": "INTEGER", "description": "Optional MsgID of the message you are replying to."}
                    },
                    "required": ["recipient_id", "message"]
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
