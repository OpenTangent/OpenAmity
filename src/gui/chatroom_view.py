import re
import html as html_lib
import urllib.parse
from datetime import datetime
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                               QPushButton, QTextEdit, QTextBrowser)
from PySide6.QtCore import Signal, QObject, QUrl
from PySide6.QtGui import QTextCursor, QTextBlockFormat

from gui.chat_formatter import (
    setup_chat_browser,
    render_markdown_to_html,
    render_user_message_content,
    wrap_in_isolated_container,
    insert_message_into_log,
)
from core.chatroom_manager import ChatroomManager
from core.config_manager import ConfigManager

try:
    from gui.theme import PRIMARY_ACCENT_COLOR, SECONDARY_ACCENT_COLOR, MODERN_SCROLLBAR_STYLE
except ImportError:
    PRIMARY_ACCENT_COLOR = "#a12924"
    SECONDARY_ACCENT_COLOR = "#f7e3a5"
    MODERN_SCROLLBAR_STYLE = ""


class ChatroomBridgeSignals(QObject):
    new_event = Signal(dict)


class ChatroomView(QWidget):
    ui_event_received = Signal(dict)
    agent_tab_requested = Signal(str)

    def __init__(self, agent_manager, parent=None, chatroom_id: int = 1):
        super().__init__(parent)
        self.agent_manager = agent_manager
        self.chatroom_id = chatroom_id
        self.manager = ChatroomManager()
        self._uid_to_name_cache = {}
        self.BATCH_SIZE = 50
        self._loaded_messages = []
        self._has_more_history = True
        self._is_loading_history = False

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # Conversation Log (QTextBrowser for clickable anchor and agent links)
        self.conversation_log = QTextBrowser()
        setup_chat_browser(self.conversation_log)
        self.conversation_log.setOpenExternalLinks(False)
        self.conversation_log.setOpenLinks(False)
        self.conversation_log.anchorClicked.connect(self.on_anchor_clicked)
        self.conversation_log.verticalScrollBar().valueChanged.connect(self._on_scroll_value_changed)
        self.main_layout.addWidget(self.conversation_log, 1)

        # Console Log for Global Stream
        self.console_log = QTextEdit()
        self.console_log.setReadOnly(True)
        self.console_log.setStyleSheet(f"""
            QTextEdit {{
                background-color: #000;
                color: #0F0;
                border: none;
                font-family: 'Ubuntu Mono';
                font-size: 12px;
                padding: 10px;
            }}
            {MODERN_SCROLLBAR_STYLE}
        """)
        self.console_log.hide()

        # Footer
        self.footer_widget = QWidget()
        self.footer_widget.setObjectName("footerWidget")
        self.footer_widget.setStyleSheet(
            "QWidget#footerWidget { background-color: #222; border-top: 1px solid #333; }")
        self.footer_layout = QHBoxLayout(self.footer_widget)
        self.footer_layout.setContentsMargins(20, 10, 20, 10)
        self.footer_layout.setSpacing(10)

        from gui.main_window import PromptTextEdit
        self.text_input = PromptTextEdit()
        self.text_input.setPlaceholderText("Message the team...")
        self.text_input.returnPressed.connect(self.send_user_message)
        self.footer_layout.addWidget(self.text_input, 1)

        self.btn_send = QPushButton("⌯⌲")
        self.btn_send.setToolTip("Send")
        self.btn_send.setMinimumSize(80, 40)
        self.btn_send.clicked.connect(self.send_user_message)
        self.btn_send.setStyleSheet(
            "QPushButton { background-color: #333; color: #FFF; border: 1px solid #555; border-radius: 5px; font-size: 18px; font-family: 'Ubuntu', 'DejaVu Sans', 'Noto Sans Symbols', 'Noto Sans Symbols2', 'FreeSans', sans-serif; padding-bottom: 4px; } QPushButton:hover { background-color: #444; }")
        self.footer_layout.addWidget(self.btn_send)

        self.main_layout.addWidget(self.footer_widget)

        # Connect signals for thread-safe cross-thread updates
        self.bridge_signals = ChatroomBridgeSignals()
        self.bridge_signals.new_event.connect(self.on_chatroom_event)
        self.manager.register_listener(self._on_manager_event)

        self.set_chatroom(self.chatroom_id)

    def set_chatroom(self, chatroom_id: int):
        self.chatroom_id = chatroom_id
        room = self.manager.get_chatroom(chatroom_id)
        room_name = room["name"] if room else f"Room {chatroom_id}"
        self.text_input.setPlaceholderText(f"Message #{room_name}...")
        self.load_history()

    def _on_manager_event(self, event_dict: dict):
        try:
            self.bridge_signals.new_event.emit(event_dict)
        except (RuntimeError, AttributeError):
            try:
                self.manager.unregister_listener(self._on_manager_event)
            except Exception:
                pass

    def on_chatroom_event(self, event_dict: dict):
        event_rid = event_dict.get("chatroom_id")
        if event_rid is not None and event_rid != self.chatroom_id:
            return

        evt = event_dict.get("event")
        if evt == "new_message":
            msg = event_dict.get("message") or event_dict
            if msg and msg.get("chatroom_id") == self.chatroom_id:
                if any(m.get("id") == msg.get("id") for m in self._loaded_messages):
                    return
                sid = msg.get("sender_id", "")
                sname = msg.get("sender_name", "")
                if sid and sname and sid.startswith("+OA-"):
                    self._uid_to_name_cache[sid] = sname

                self._loaded_messages.append(msg)
                msg_map = {m["id"]: m for m in self._loaded_messages}
                scroll_bar = self.conversation_log.verticalScrollBar()
                was_at_bottom = (scroll_bar.maximum() - scroll_bar.value()) <= 60

                self._render_message(msg, msg_map)
                if was_at_bottom:
                    scroll_bar.setValue(scroll_bar.maximum())
                self.manager.mark_room_read(self.chatroom_id, "USER")
        elif evt in ("reaction_added", "reaction_removed", "new_reaction"):
            scroll_bar = self.conversation_log.verticalScrollBar()
            pos = scroll_bar.value()
            self._render_all_loaded_messages()
            scroll_bar.setValue(pos)
        elif evt == "room_read":
            pass
        else:
            self.load_history()

    def closeEvent(self, event):
        try:
            self.manager.unregister_listener(self._on_manager_event)
        except Exception:
            pass
        super().closeEvent(event)

    def _on_scroll_value_changed(self, value: int):
        if value <= 5 and self._has_more_history and not self._is_loading_history and self._loaded_messages:
            self.load_older_messages()

    def load_history(self):
        self._is_loading_history = True
        try:
            self._loaded_messages = []
            self._has_more_history = True
            messages = self.manager.get_all_ui_messages(chatroom_id=self.chatroom_id, limit=self.BATCH_SIZE)
            self.manager.mark_room_read(self.chatroom_id, "USER")

            if len(messages) < self.BATCH_SIZE:
                self._has_more_history = False

            for m in messages:
                sid = m.get("sender_id", "")
                sname = m.get("sender_name", "")
                if sid and sname and sid.startswith("+OA-"):
                    self._uid_to_name_cache[sid] = sname

            self._loaded_messages = messages
            self._render_all_loaded_messages()

            scroll_bar = self.conversation_log.verticalScrollBar()
            scroll_bar.setValue(scroll_bar.maximum())
        finally:
            self._is_loading_history = False

    def load_older_messages(self):
        if not self._loaded_messages or not self._has_more_history or self._is_loading_history:
            return
        self._is_loading_history = True
        try:
            oldest_id = self._loaded_messages[0]["id"]
            older_msgs = self.manager.get_all_ui_messages(
                chatroom_id=self.chatroom_id,
                limit=self.BATCH_SIZE,
                before_id=oldest_id
            )
            if not older_msgs:
                self._has_more_history = False
                return
            if len(older_msgs) < self.BATCH_SIZE:
                self._has_more_history = False

            for m in older_msgs:
                sid = m.get("sender_id", "")
                sname = m.get("sender_name", "")
                if sid and sname and sid.startswith("+OA-"):
                    self._uid_to_name_cache[sid] = sname

            scroll_bar = self.conversation_log.verticalScrollBar()
            old_max = scroll_bar.maximum()
            old_val = scroll_bar.value()

            self._loaded_messages = older_msgs + self._loaded_messages
            self._render_all_loaded_messages()

            new_max = scroll_bar.maximum()
            scroll_bar.setValue(old_val + (new_max - old_max))
        finally:
            self._is_loading_history = False

    def _render_all_loaded_messages(self):
        msg_map = {m["id"]: m for m in self._loaded_messages}
        self.conversation_log.clear()
        for m in self._loaded_messages:
            self._render_message(m, msg_map)

    def _render_message(self, m: dict, msg_map: dict = None):
        msg_id = m.get("id")
        sender_type = m.get("sender_type", "agent")
        sender_name = m.get("sender_name", "Unknown")
        sender_id = m.get("sender_id", "")
        recipient_id = m.get("recipient_id")
        content = m.get("content", "")
        reply_to_id = m.get("reply_to_id")
        timestamp_str = m.get("timestamp", "")
        reactions = m.get("reactions", [])

        # Format timestamp
        time_display = ""
        if timestamp_str:
            try:
                dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                time_display = dt.strftime("%H:%M")
            except Exception:
                time_display = timestamp_str[:5]

        # Markdown / HTML format content with @mentions resolved
        if sender_type == "user":
            rendered_content = render_user_message_content(content)
            formatted_content = self._resolve_mentions(rendered_content)
        else:
            rendered_md = render_markdown_to_html(content)
            formatted_content = self._resolve_mentions(rendered_md)

        # Reply snippet with clickable scroll anchor
        reply_html = ""
        if reply_to_id:
            orig_msg = msg_map.get(reply_to_id) if msg_map else None
            if not orig_msg:
                orig_msg = self.manager.get_message_by_id(reply_to_id)

            if orig_msg:
                orig_sender = orig_msg.get("sender_name", "Unknown")
                orig_content = orig_msg.get("content", "").replace("\n", " ").strip()
                orig_snippet = (orig_content[:47] + "...") if len(orig_content) > 50 else orig_content
                safe_orig_snippet = html_lib.escape(orig_snippet)
                reply_html = f"""
                <div style='font-size: 11px; margin-bottom: 4px;'>
                    <a href='#msg_{reply_to_id}' style='color: #888888; text-decoration: none;'>
                        ↳ in reply to <span style='color: #AAAAAA; font-weight: bold;'>{orig_sender}</span>: <i>"{safe_orig_snippet}"</i>
                    </a>
                </div>
                """
            else:
                reply_html = f"""
                <div style='font-size: 11px; margin-bottom: 4px;'>
                    <a href='#msg_{reply_to_id}' style='color: #888888; text-decoration: none;'>
                        ↳ in reply to #{reply_to_id}
                    </a>
                </div>
                """

        # Reaction chips
        reactions_html = ""
        if reactions:
            chip_items = []
            grouped = {}
            for r in reactions:
                emoji = r.get("reaction", "")
                grouped[emoji] = grouped.get(emoji, 0) + 1
            for emoji, count in grouped.items():
                chip_items.append(
                    f"<span style='background-color: #2b2b2b; border: 1px solid #444; border-radius: 10px; padding: 2px 7px; font-size: 12px; margin-right: 4px; color: #FFF;'>{emoji} {count}</span>"
                )
            reactions_html = f"<div style='margin-top: 5px;'>{''.join(chip_items)}</div>"

        anchor_tag = f"<a name='msg_{msg_id}' id='msg_{msg_id}'></a>"

        if recipient_id:
            # Direct Message styling
            recipient_name = recipient_id
            if self.agent_manager:
                aid = self.agent_manager.get_agent_id_by_uid(recipient_id)
                if aid:
                    recipient_name = self.agent_manager.get_agent_name(aid)
            if recipient_name == recipient_id and recipient_id in self._uid_to_name_cache:
                recipient_name = self._uid_to_name_cache[recipient_id]

            if sender_id.startswith("+OA-"):
                sender_header = (
                    f"<a href='agent:{sender_id}' style='text-decoration: none; color: {SECONDARY_ACCENT_COLOR}; font-weight: bold; font-size: 14px;'>{sender_name}</a> "
                    f"<a href='agent:{sender_id}' style='text-decoration: none; color: #888; font-size: 12px;'>({sender_id})</a>"
                )
            elif sender_type == "user":
                sender_header = f"<span style='color: {PRIMARY_ACCENT_COLOR}; font-weight: bold; font-size: 14px;'>{sender_name}</span>"
            else:
                sender_header = f"<span style='color: {SECONDARY_ACCENT_COLOR}; font-weight: bold; font-size: 14px;'>{sender_name}</span> <span style='color: #888; font-size: 12px;'>({sender_id})</span>"

            if recipient_id.startswith("+OA-"):
                recipient_header = (
                    f"<a href='agent:{recipient_id}' style='text-decoration: none; color: {SECONDARY_ACCENT_COLOR}; font-weight: bold; font-size: 14px;'>{recipient_name}</a> "
                    f"<a href='agent:{recipient_id}' style='text-decoration: none; color: #888; font-size: 12px;'>({recipient_id})</a>"
                )
            else:
                recipient_header = f"<span style='color: {SECONDARY_ACCENT_COLOR}; font-weight: bold; font-size: 14px;'>{recipient_name}</span> <span style='color: #888; font-size: 12px;'>({recipient_id})</span>"

            bg_color = "#1b212b"
            border_color = "#2a3648"
            border_left = "4px solid #3498db"

            html = f"""
            <div style='margin-bottom: 2px; text-align: left;'>
                {anchor_tag}
                {reply_html}
                <div style='display: flex; justify-content: space-between; align-items: center;'>
                    <span style='font-size: 12px; color: #3498db; font-weight: bold;'>🔒 PRIVATE DM</span>
                    {sender_header}
                    <span style='color: #AAA;'> ➔ </span>
                    {recipient_header}
                </div>
                <div style='margin-top: 6px; color: #e0e0e0; font-size: 18px; line-height: 1.65;'>
                    {formatted_content}
                </div>
                {reactions_html}
                <div style='text-align: right; color: #666; font-size: 11px; margin-top: 6px;'>
                    [#{msg_id}] {time_display}
                </div>
            </div>
            """
        elif sender_type == "user":
            bg_color = "#19191b"
            border_color = "#28282c"
            border_left = ""

            html = f"""
            <div style='margin-bottom: 2px; text-align: left;'>
                {anchor_tag}
                {reply_html}
                <div>
                    <span style='color: {PRIMARY_ACCENT_COLOR}; font-weight: bold; font-size: 14px;'>{sender_name}</span>
                </div>
                <div style='margin-top: 4px; color: #888888; font-size: 18px; line-height: 1.65;'>
                    {formatted_content}
                </div>
                {reactions_html}
                <div style='text-align: right; color: #555555; font-size: 11px; margin-top: 6px;'>
                    [#{msg_id}] {time_display}
                </div>
            </div>
            """
        else:
            # Public Agent Message
            if sender_id.startswith("+OA-"):
                sender_header = (
                    f"<a href='agent:{sender_id}' style='text-decoration: none; color: {SECONDARY_ACCENT_COLOR}; font-weight: bold; font-size: 14px;'>{sender_name}</a> "
                    f"<a href='agent:{sender_id}' style='text-decoration: none; color: #777; font-size: 12px; margin-left: 6px;'>{sender_id}</a>"
                )
            else:
                sender_header = f"<span style='color: {SECONDARY_ACCENT_COLOR}; font-weight: bold; font-size: 14px;'>{sender_name}</span> <span style='color: #777; font-size: 12px; margin-left: 6px;'>{sender_id}</span>"

            bg_color = "#222225"
            border_color = "#36363b"
            border_left = ""

            html = f"""
            <div style='margin-bottom: 2px; text-align: left;'>
                {anchor_tag}
                {reply_html}
                <div>
                    {sender_header}
                </div>
                <div style='margin-top: 4px; color: #FFFFFF; font-size: 18px; line-height: 1.65;'>
                    {formatted_content}
                </div>
                {reactions_html}
                <div style='text-align: right; color: #666; font-size: 11px; margin-top: 6px;'>
                    [#{msg_id}] {time_display}
                </div>
            </div>
            """

        isolated_html = wrap_in_isolated_container(
            html,
            margin_bottom=14,
            bg_color=bg_color,
            border_color=border_color,
            border_radius=8,
            padding="12px 18px",
            border_left=border_left,
        )
        insert_message_into_log(self.conversation_log, isolated_html)

    def on_anchor_clicked(self, url: QUrl):
        url_str = url.toString()
        if url.scheme() == "agent" or url_str.startswith("agent:") or url_str.startswith("agent://"):
            target = url.path() or url_str.replace("agent://", "").replace("agent:", "")
            target = urllib.parse.unquote(target).strip()
            if target:
                self.agent_tab_requested.emit(target)
            return

        anchor = url.fragment() or url_str.lstrip('#')
        if anchor:
            self.conversation_log.scrollToAnchor(anchor)

    def send_user_message(self):
        text = self.text_input.toPlainText().strip()
        if not text:
            return
        self.text_input.clear()
        try:
            config = ConfigManager()
            user_full_name = config.get("user-full-name", "").strip() or "User"
            self.manager.post_message(
                sender_id="USER",
                sender_name=user_full_name,
                sender_type="user",
                content=text,
                recipient_id=None,
                chatroom_id=self.chatroom_id,
            )
        except Exception as e:
            self.append_to_console(f"Error sending message to chatroom: {e}")

    def append_to_console(self, text: str):
        self.console_log.append(text)
        scroll_bar = self.console_log.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.maximum())

    def _resolve_mentions(self, text: str) -> str:
        """Resolves @+OA-XXXX-XXXX agent UIDs into clickable @AgentName badges."""
        def replace_uid(match):
            uid = match.group(1).upper()
            resolved_name = None
            if self.agent_manager:
                aid = self.agent_manager.get_agent_id_by_uid(uid)
                if aid:
                    resolved_name = self.agent_manager.get_agent_name(aid)
            if not resolved_name and uid in self._uid_to_name_cache:
                resolved_name = self._uid_to_name_cache[uid]

            display_tag = f"@{resolved_name}" if resolved_name else f"@{uid}"
            return (
                f'<a href="agent:{uid}" style="text-decoration: none;">'
                f'<span style="color: #f39c12; font-weight: bold; background-color: #2c2214; padding: 1px 5px; border-radius: 3px;" title="{uid}">'
                f'{display_tag}</span></a>'
            )

        mention_pattern = re.compile(r"@(\+OA-[0-9A-HJKMNP-Za-z]{4}-[0-9A-HJKMNP-Za-z]{4})")
        return mention_pattern.sub(replace_uid, text)

