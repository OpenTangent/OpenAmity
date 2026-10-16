from typing import Optional, Dict
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QScrollArea, QFrame, QSizePolicy
)
from PySide6.QtCore import Qt, Signal, QSize, QObject
from PySide6.QtGui import QPainter, QColor, QPen, QBrush

from core.chatroom_manager import ChatroomManager

try:
    from gui.theme import PRIMARY_ACCENT_COLOR, MODERN_SCROLLBAR_STYLE
except ImportError:
    PRIMARY_ACCENT_COLOR = "#a12924"
    MODERN_SCROLLBAR_STYLE = ""


class SidebarTabButton(QPushButton):
    def __init__(self, text: str, icon_prefix: str = "", parent=None):
        super().__init__(text, parent)
        self.icon_prefix = icon_prefix
        self.display_title = text
        self.is_selected = False
        self._is_hovered = False
        self.unread_count = 0
        self.mention_count = 0

        self.setFixedHeight(36)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        font = self.font()
        font.setFamily("Ubuntu, 'DejaVu Sans', 'Noto Sans Symbols', 'Noto Sans Symbols2', 'FreeSans', sans-serif")
        self.setFont(font)

    def set_selected(self, selected: bool):
        if self.is_selected != selected:
            self.is_selected = selected
            self.update()

    def set_badges(self, unread: int, mentions: int):
        if self.unread_count != unread or self.mention_count != mentions:
            self.unread_count = unread
            self.mention_count = mentions
            self.update()

    def enterEvent(self, event):
        self._is_hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._is_hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        rect = self.rect()
        draw_rect = rect.adjusted(4, 2, -4, -2)

        # Background color
        if self.is_selected:
            bg_color = QColor("#333333")
            text_color = QColor("#FFFFFF")
        elif self._is_hovered:
            bg_color = QColor("#262626")
            text_color = QColor("#EEEEEE")
        else:
            bg_color = QColor("#1c1c1c")
            text_color = QColor("#BBBBBB")

        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(bg_color))
        painter.drawRoundedRect(draw_rect, 4, 4)

        # Selected accent marker on left edge
        if self.is_selected:
            accent_rect = draw_rect.adjusted(0, 0, 0, 0)
            accent_rect.setWidth(4)
            painter.setBrush(QBrush(QColor(PRIMARY_ACCENT_COLOR)))
            painter.drawRoundedRect(accent_rect, 2, 2)

        # Calculate space for badges on the right
        badge_width_needed = 0
        if self.mention_count > 0:
            badge_width_needed += 34
        if self.unread_count > 0:
            badge_width_needed += 30

        # Draw Title text (elided)
        text_rect = draw_rect.adjusted(12, 0, -(badge_width_needed + 8), 0)
        full_text = f"{self.icon_prefix} {self.display_title}" if self.icon_prefix else self.display_title
        font = self.font()
        font.setPointSize(10)
        painter.setFont(font)
        painter.setPen(text_color)
        elided = painter.fontMetrics().elidedText(full_text, Qt.TextElideMode.ElideRight, text_rect.width())
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided)

        # Draw Badges on the right
        right_x = draw_rect.right() - 6
        if self.mention_count > 0:
            m_text = f"@{self.mention_count}"
            m_width = max(24, painter.fontMetrics().horizontalAdvance(m_text) + 10)
            m_rect = draw_rect.adjusted(0, 0, 0, 0)
            m_rect.setLeft(right_x - m_width)
            m_rect.setRight(right_x)
            m_rect.setTop(draw_rect.top() + (draw_rect.height() - 18) // 2)
            m_rect.setHeight(18)

            painter.setBrush(QBrush(QColor(PRIMARY_ACCENT_COLOR)))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(m_rect, 9, 9)

            painter.setPen(QColor("#FFFFFF"))
            bfont = self.font()
            bfont.setPointSize(8)
            bfont.setBold(True)
            painter.setFont(bfont)
            painter.drawText(m_rect, Qt.AlignmentFlag.AlignCenter, m_text)
            right_x -= (m_width + 4)

        if self.unread_count > 0:
            u_text = str(self.unread_count)
            u_width = max(20, painter.fontMetrics().horizontalAdvance(u_text) + 8)
            u_rect = draw_rect.adjusted(0, 0, 0, 0)
            u_rect.setLeft(right_x - u_width)
            u_rect.setRight(right_x)
            u_rect.setTop(draw_rect.top() + (draw_rect.height() - 18) // 2)
            u_rect.setHeight(18)

            painter.setBrush(QBrush(QColor("#484848")))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(u_rect, 9, 9)

            painter.setPen(QColor("#FFFFFF"))
            bfont = self.font()
            bfont.setPointSize(8)
            bfont.setBold(False)
            painter.setFont(bfont)
            painter.drawText(u_rect, Qt.AlignmentFlag.AlignCenter, u_text)


class ChatroomSidebarBridgeSignals(QObject):
    event_received = Signal(dict)


class ChatroomSidebar(QWidget):
    all_agents_selected = Signal()
    chatroom_selected = Signal(int)
    new_chatroom_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.manager = ChatroomManager()
        self.selected_context = ("all", None)  # ("all", None) or ("room", room_id)
        self.room_buttons: Dict[int, SidebarTabButton] = {}

        self.setFixedWidth(200)
        self.setObjectName("chatroomSidebar")
        self.setStyleSheet(f"""
            QWidget#chatroomSidebar {{
                background-color: #141414;
                border-right: 1px solid #282828;
            }}
            QScrollArea {{
                background-color: transparent;
                border: none;
            }}
            QScrollArea > QWidget > QWidget {{
                background-color: transparent;
            }}
            {MODERN_SCROLLBAR_STYLE}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 8, 6, 8)
        layout.setSpacing(6)

        # 1. Pinned 'Agents' button
        self.btn_all_agents = SidebarTabButton("Agents", icon_prefix="✦", parent=self)
        self.btn_all_agents.setToolTip("Agents — direct messaging with individual agents")
        self.btn_all_agents.clicked.connect(self._on_all_agents_clicked)
        self.btn_all_agents.set_selected(True)
        layout.addWidget(self.btn_all_agents)

        # 2. Section divider label
        self.lbl_section = QLabel("🗫 Chatrooms")
        font = self.lbl_section.font()
        font.setFamily("Ubuntu, 'DejaVu Sans', 'Noto Sans Symbols', 'Noto Sans Symbols2', 'FreeSans', sans-serif")
        self.lbl_section.setFont(font)
        self.lbl_section.setStyleSheet("font-size: 11px; font-weight: bold; color: #777777; padding-left: 8px; margin-top: 6px; margin-bottom: 2px;")
        layout.addWidget(self.lbl_section)

        # 3. Scroll area for rooms
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        self.scroll_content = QWidget()
        self.rooms_layout = QVBoxLayout(self.scroll_content)
        self.rooms_layout.setContentsMargins(0, 0, 0, 0)
        self.rooms_layout.setSpacing(4)
        self.rooms_layout.setAlignment(Qt.AlignTop)
        self.scroll_area.setWidget(self.scroll_content)
        layout.addWidget(self.scroll_area, 1)

        # 4. [+] New Chatroom button inside scroll area after rooms
        self.btn_add_room = QPushButton("+ New Chatroom")
        self.btn_add_room.setToolTip("New Chatroom — create a room and assign agent access")
        self.btn_add_room.setFixedHeight(32)
        self.btn_add_room.setCursor(Qt.PointingHandCursor)
        self.btn_add_room.setStyleSheet("""
            QPushButton {
                background-color: #222222;
                color: #AAAAAA;
                border: 1px dashed #444444;
                border-radius: 4px;
                font-size: 12px;
                padding: 4px;
                margin: 4px;
            }
            QPushButton:hover {
                background-color: #2e2e2e;
                color: #FFFFFF;
                border-color: #666666;
            }
        """)
        self.btn_add_room.clicked.connect(self.new_chatroom_requested.emit)

        # Listen for manager events via bridge signal for cross-thread GUI safety
        self.bridge_signals = ChatroomSidebarBridgeSignals()
        self.bridge_signals.event_received.connect(self._handle_chatroom_event)
        self.manager.register_listener(self._on_chatroom_event)

        self.refresh_rooms()

    def _on_chatroom_event(self, event_dict: dict):
        # Called from background threads (e.g. agent tool execution)
        try:
            self.bridge_signals.event_received.emit(event_dict)
        except (RuntimeError, AttributeError):
            try:
                self.manager.unregister_listener(self._on_chatroom_event)
            except Exception:
                pass

    def _handle_chatroom_event(self, event_dict: dict):
        # Executes safely on the main GUI thread
        evt = event_dict.get("event")
        if evt in ("room_created", "room_updated", "room_deleted"):
            self.refresh_rooms()
        elif evt in ("new_message", "room_read", "membership_changed"):
            self.update_badges()

    def closeEvent(self, event):
        try:
            self.manager.unregister_listener(self._on_chatroom_event)
        except Exception:
            pass
        super().closeEvent(event)

    def _on_all_agents_clicked(self):
        self.set_selected_context("all", None)
        self.all_agents_selected.emit()

    def _on_room_clicked(self, room_id: int):
        self.set_selected_context("room", room_id)
        self.chatroom_selected.emit(room_id)

    def set_selected_context(self, context_type: str, room_id: Optional[int] = None):
        self.selected_context = (context_type, room_id)
        self.btn_all_agents.set_selected(context_type == "all")
        for rid, btn in self.room_buttons.items():
            btn.set_selected(context_type == "room" and rid == room_id)

    def update_badges(self):
        badges = self.manager.get_user_room_badges()
        active_rooms = self.manager.list_chatrooms(include_deleted=False)
        active_room_ids = {r["id"] for r in active_rooms}
        # If active rooms list does not match our current buttons, perform full refresh
        if set(self.room_buttons.keys()) != active_room_ids:
            self.refresh_rooms()
            return

        for rid, btn in self.room_buttons.items():
            room_badges = badges.get(rid, {})
            btn.set_badges(
                unread=room_badges.get("unread_count", 0),
                mentions=room_badges.get("mention_count", 0)
            )

    def refresh_rooms(self):
        # Fetch active rooms and user badges
        rooms = self.manager.list_chatrooms(include_deleted=False)
        badges = self.manager.get_user_room_badges()

        # Temporarily detach btn_add_room from layout without destroying it
        self.rooms_layout.removeWidget(self.btn_add_room)

        # Remove existing room buttons and schedule clean deletion
        for rid, btn in list(self.room_buttons.items()):
            self.rooms_layout.removeWidget(btn)
            btn.deleteLater()
        self.room_buttons.clear()

        curr_type, curr_rid = self.selected_context

        for r in rooms:
            rid = r["id"]
            name = r["name"]
            purpose = r.get("purpose", "")
            btn = SidebarTabButton(f"#{name}", parent=self.scroll_content)
            tooltip = f"#{name}" + (f"\n{purpose}" if purpose else "")
            btn.setToolTip(tooltip)

            room_badges = badges.get(rid, {})
            btn.set_badges(
                unread=room_badges.get("unread_count", 0),
                mentions=room_badges.get("mention_count", 0)
            )

            is_sel = (curr_type == "room" and curr_rid == rid)
            btn.set_selected(is_sel)
            btn.clicked.connect(lambda checked=False, room_id=rid: self._on_room_clicked(room_id))

            self.rooms_layout.addWidget(btn)
            self.room_buttons[rid] = btn

        # Re-add the [+] button right after the last room
        self.rooms_layout.addWidget(self.btn_add_room)
