import logging
import math
from typing import Optional, Dict

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QScrollArea, QSizePolicy, QMenu, QApplication
)
from PySide6.QtCore import Qt, Signal, QSize, QObject, QTimer, QPoint, QRect, QRectF, QEvent
from PySide6.QtGui import QPainter, QColor, QPen, QBrush

from core.chatroom_manager import ChatroomManager
from core.config_manager import ConfigManager

try:
    from gui.theme import PRIMARY_ACCENT_COLOR, SECONDARY_ACCENT_COLOR, MODERN_SCROLLBAR_STYLE
except ImportError:
    PRIMARY_ACCENT_COLOR = "#a12924"
    SECONDARY_ACCENT_COLOR = "#f7e3a5"
    MODERN_SCROLLBAR_STYLE = ""

SIDEBAR_BG_COLOR = "#222222"
SIDEBAR_BORDER_COLOR = "#333333"


def _interpolate_color(c1: QColor, c2: QColor, factor: float) -> QColor:
    factor = max(0.0, min(1.0, factor))
    r = int(c1.red() + (c2.red() - c1.red()) * factor)
    g = int(c1.green() + (c2.green() - c1.green()) * factor)
    b = int(c1.blue() + (c2.blue() - c1.blue()) * factor)
    return QColor(r, g, b)


def _dashed_action_button_style(is_sub_item: bool = False) -> str:
    margin = "1px 4px 1px 18px" if is_sub_item else "2px 4px 2px 4px"
    return f"""
        QPushButton {{
            background-color: transparent;
            color: #757575;
            border: 1px dashed #363636;
            border-radius: 4px;
            font-size: 11px;
            padding: 3px 4px;
            margin: {margin};
        }}
        QPushButton:hover {{
            background-color: #2c2c2c;
            color: #CCCCCC;
            border-color: #525252;
        }}
        QPushButton:pressed {{
            background-color: #343434;
            color: #FFFFFF;
            border-color: #666666;
        }}
    """


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

        if self.is_selected:
            bg_color = QColor("#3a3a3a")
            text_color = QColor("#FFFFFF")
        elif self._is_hovered:
            bg_color = QColor("#313131")
            text_color = QColor("#EEEEEE")
        else:
            bg_color = QColor("#292929")
            text_color = QColor("#BBBBBB")

        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(bg_color))
        painter.drawRoundedRect(draw_rect, 4, 4)

        if self.is_selected:
            accent_rect = draw_rect.adjusted(0, 0, 0, 0)
            accent_rect.setWidth(4)
            painter.setBrush(QBrush(QColor(PRIMARY_ACCENT_COLOR)))
            painter.drawRoundedRect(accent_rect, 2, 2)

        badge_width_needed = 0
        if self.mention_count > 0:
            badge_width_needed += 34
        if self.unread_count > 0:
            badge_width_needed += 30

        text_rect = draw_rect.adjusted(12, 0, -(badge_width_needed + 8), 0)
        full_text = f"{self.icon_prefix} {self.display_title}" if self.icon_prefix else self.display_title
        font = self.font()
        font.setPointSize(10)
        painter.setFont(font)
        painter.setPen(text_color)
        elided = painter.fontMetrics().elidedText(full_text, Qt.TextElideMode.ElideRight, max(0, text_rect.width()))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided)

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


class SidebarAgentButton(QPushButton):
    def __init__(
        self,
        agent_id: str,
        agent_name: str,
        parent=None,
        is_sub_item: bool = False,
        is_watching: bool = False,
    ):
        super().__init__(agent_name, parent)
        self.agent_id = agent_id
        self.is_sub_item = is_sub_item
        self.is_watching = is_watching
        self.is_selected = False
        self.is_paused = False
        self.activity_state = "idle"  # "idle", "thinking", "speaking"
        self._is_hovered = False

        self.setFixedHeight(28 if is_sub_item else 36)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        font = self.font()
        font.setFamily("Ubuntu, 'DejaVu Sans', 'Noto Sans Symbols', 'Noto Sans Symbols2', 'FreeSans', sans-serif")
        font.setPointSize(9 if is_sub_item else 10)
        self.setFont(font)

        self.pulse_phase = 0.0
        self.pulse_timer = QTimer(self)
        self.pulse_timer.setInterval(30)  # ~33 FPS
        self.pulse_timer.timeout.connect(self._on_pulse_tick)

        self.primary_base = QColor(PRIMARY_ACCENT_COLOR)
        self.primary_dark = QColor("#4e1412")
        self.primary_light = QColor("#cf3832")

        self.secondary_base = QColor(SECONDARY_ACCENT_COLOR)
        self.secondary_dark = QColor("#705e2c")
        self.secondary_light = QColor("#faecc2")

    def _on_pulse_tick(self):
        self.pulse_phase += 0.0943  # ~0.5 Hz full cycle
        if self.pulse_phase >= 2 * math.pi:
            self.pulse_phase -= 2 * math.pi
        self.update()

    def stop_animation(self):
        if self.pulse_timer.isActive():
            self.pulse_timer.stop()
        self.pulse_phase = 0.0

    def set_paused(self, paused: bool):
        if self.is_paused != paused:
            self.is_paused = paused
            if paused:
                self.stop_animation()
                self.activity_state = "idle"
            self.update()

    def _sync_pulse_timer(self):
        should_pulse = (
            not self.is_paused
            and self.activity_state in ("thinking", "speaking")
            and self.isVisible()
        )
        if should_pulse:
            if not self.pulse_timer.isActive():
                self.pulse_phase = 0.0
                self.pulse_timer.start()
        else:
            self.stop_animation()

    def set_activity_state(self, state: str):
        if self.is_paused:
            state = "idle"
        if self.activity_state == state:
            self._sync_pulse_timer()
            return
        self.activity_state = state
        self._sync_pulse_timer()
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_pulse_timer()

    def hideEvent(self, event):
        self.stop_animation()
        self._is_hovered = False
        super().hideEvent(event)

    def set_selected(self, selected: bool):
        if self.is_selected != selected:
            self.is_selected = selected
            self.update()

    def set_watching(self, watching: bool):
        if self.is_watching != watching:
            self.is_watching = watching
            self.update()

    def enterEvent(self, event):
        self._is_hovered = True
        if self.activity_state == "idle":
            self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._is_hovered = False
        if self.activity_state == "idle":
            self.update()
        super().leaveEvent(event)

    def closeEvent(self, event):
        self.stop_animation()
        super().closeEvent(event)

    def sizeHint(self):
        fm = self.fontMetrics()
        text_width = fm.horizontalAdvance(self.text())
        return QSize(max(80, text_width + 30), 28 if self.is_sub_item else 36)

    def _compute_content_layout(self, draw_rect: QRect, fm):
        """Returns (watch_center, pause_rect, text_rect) for right-aligned status indicators."""
        show_watch = self.is_sub_item and self.is_watching
        show_pause = self.is_paused
        watch_radius = 3
        watch_center: Optional[QPoint] = None
        pause_rect: Optional[QRect] = None
        leftmost_icon_left: Optional[int] = None

        if show_watch:
            watch_center = QPoint(draw_rect.right() - 10, draw_rect.center().y())
            leftmost_icon_left = watch_center.x() - watch_radius

        if show_pause:
            pause_width = max(8, fm.horizontalAdvance("⏸") + 2)
            if show_watch:
                pause_right = leftmost_icon_left - 5
                pause_left = pause_right - pause_width + 1
            else:
                pause_left = min(draw_rect.right() - 10 - pause_width // 2, draw_rect.right() - 6 - pause_width + 1)
            pause_rect = QRect(pause_left, draw_rect.top() - 1, pause_width, draw_rect.height())
            leftmost_icon_left = pause_rect.left()

        left_pad = 10 if self.is_sub_item else 12
        text_left = draw_rect.left() + left_pad
        text_right = (leftmost_icon_left - 8) if leftmost_icon_left is not None else (draw_rect.right() - 8)
        text_rect = QRect(text_left, draw_rect.top(), max(0, text_right - text_left + 1), draw_rect.height())
        return watch_center, pause_rect, text_rect

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        rect = self.rect()
        if self.is_sub_item:
            painter.setPen(QPen(QColor("#383838"), 1))
            painter.drawLine(12, rect.top(), 12, rect.bottom())
            draw_rect = rect.adjusted(18, 1, -4, -1)
        else:
            draw_rect = rect.adjusted(4, 2, -4, -2)

        border_color: Optional[QColor] = None
        if not self.is_paused and self.activity_state == "speaking":
            factor = (math.sin(self.pulse_phase) + 1.0) / 2.0
            bg_color = _interpolate_color(self.secondary_dark, self.secondary_light, factor)
            border_color = _interpolate_color(self.secondary_base, self.secondary_light, factor)
            text_color = QColor("#1a1a1a")
        elif not self.is_paused and self.activity_state == "thinking":
            factor = (math.sin(self.pulse_phase) + 1.0) / 2.0
            bg_color = _interpolate_color(self.primary_dark, self.primary_light, factor)
            border_color = _interpolate_color(self.primary_base, self.primary_light, factor)
            text_color = QColor("#FFFFFF")
        else:
            if self.is_selected:
                bg_color = QColor("#3a3a3a")
                text_color = QColor("#FFFFFF")
            elif self._is_hovered:
                bg_color = QColor("#313131")
                text_color = QColor("#EEEEEE")
            else:
                bg_color = QColor("#262626" if self.is_sub_item else "#292929")
                text_color = QColor("#BBBBBB")

        painter.setPen(QPen(border_color, 1) if border_color is not None else Qt.NoPen)
        painter.setBrush(QBrush(bg_color))
        painter.drawRoundedRect(draw_rect, 4, 4)

        if self.is_selected and not self.is_sub_item:
            accent_rect = QRect(draw_rect)
            accent_rect.setWidth(4)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor(PRIMARY_ACCENT_COLOR)))
            painter.drawRoundedRect(accent_rect, 2, 2)

        font = self.font()
        font.setPointSize(9 if self.is_sub_item else 10)
        painter.setFont(font)

        watch_center, pause_rect, text_rect = self._compute_content_layout(draw_rect, painter.fontMetrics())

        if watch_center is not None:
            dot_color = QColor("#1b5e20") if (self.activity_state == "speaking" and not self.is_paused) else QColor("#66bb6a")
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(dot_color))
            painter.drawEllipse(watch_center, 3, 3)

        if pause_rect is not None:
            painter.setPen(text_color)
            painter.drawText(pause_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignCenter, "⏸")

        painter.setPen(text_color)
        elided = painter.fontMetrics().elidedText(
            self.text(), Qt.TextElideMode.ElideRight, max(0, text_rect.width())
        )
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided)


AgentTabButton = SidebarAgentButton


class CollapsibleSectionHeader(QPushButton):
    def __init__(self, title: str, parent=None):
        super().__init__(title, parent)
        self.is_collapsed = False
        self._is_hovered = False

        self.setFixedHeight(38)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        font = self.font()
        font.setFamily("Ubuntu, 'DejaVu Sans', 'Noto Sans Symbols', 'Noto Sans Symbols2', 'FreeSans', sans-serif")
        font.setPixelSize(13)
        font.setBold(True)
        self.setFont(font)

    def set_collapsed(self, collapsed: bool):
        collapsed = bool(collapsed)
        if self.is_collapsed != collapsed:
            self.is_collapsed = collapsed
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
        pill_rect = rect.adjusted(2, 5, -2, -5)
        if self._is_hovered:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor("#2c2c2c")))
            painter.drawRoundedRect(pill_rect, 4, 4)

        painter.setFont(self.font())
        painter.setPen(QColor("#DDDDDD") if self._is_hovered else QColor("#949494"))
        painter.drawText(
            pill_rect.adjusted(6, 0, -20, 0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self.text(),
        )

        chevron = "▸" if self.is_collapsed else "▾"
        painter.drawText(
            pill_rect.adjusted(0, 0, -6, 0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
            chevron,
        )


class SidebarResizeHandle(QWidget):
    def __init__(self, sidebar: "ChatroomSidebar", parent=None):
        super().__init__(parent)
        self._sidebar = sidebar
        self._is_hovered = False
        self._is_dragging = False
        self._drag_start_x = 0
        self._drag_start_width = sidebar.EXPANDED_WIDTH

        self.setFixedWidth(5)
        self.setCursor(Qt.SizeHorCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self.setToolTip("Drag to resize sidebar (double-click to reset)")

    def enterEvent(self, event):
        self._is_hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._is_hovered = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._is_dragging = True
            self._drag_start_x = int(event.globalPosition().x())
            self._drag_start_width = self._sidebar._expanded_width
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._is_dragging:
            delta = int(event.globalPosition().x()) - self._drag_start_x
            target_width = self._drag_start_width + delta
            max_w = self._sidebar.MAX_EXPANDED_WIDTH
            parent = self._sidebar.parentWidget()
            if parent is not None and parent.width() > self._sidebar.MIN_EXPANDED_WIDTH + 250:
                max_w = min(max_w, max(self._sidebar.MIN_EXPANDED_WIDTH, parent.width() - 250))
            self._sidebar.set_expanded_width(min(target_width, max_w), persist=False)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._is_dragging and event.button() == Qt.LeftButton:
            self._is_dragging = False
            self._sidebar.set_expanded_width(self._sidebar._expanded_width, persist=True)
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._sidebar.set_expanded_width(self._sidebar.EXPANDED_WIDTH, persist=True)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        rect = self.rect()
        if self._is_dragging:
            painter.fillRect(
                max(0, rect.right() - 1),
                rect.top(),
                2,
                rect.height(),
                QColor(PRIMARY_ACCENT_COLOR),
            )
        elif self._is_hovered:
            painter.setPen(QPen(QColor("#5a5a5a"), 1))
            painter.drawLine(rect.right(), rect.top(), rect.right(), rect.bottom())
        else:
            painter.setPen(QPen(QColor(SIDEBAR_BORDER_COLOR), 1))
            painter.drawLine(rect.right(), rect.top(), rect.right(), rect.bottom())


class CollapsedSidebarStrip(QPushButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(28)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.setCursor(Qt.PointingHandCursor)

        self.display_text = ""
        self.activity_state = "idle"
        self._is_hovered = False

        self.pulse_phase = 0.0
        self.pulse_timer = QTimer(self)
        self.pulse_timer.setInterval(30)
        self.pulse_timer.timeout.connect(self._on_pulse_tick)

        self.primary_dark = QColor("#4e1412")
        self.primary_light = QColor("#cf3832")
        self.secondary_dark = QColor("#705e2c")
        self.secondary_light = QColor("#faecc2")

        font = self.font()
        font.setFamily("Ubuntu, 'DejaVu Sans', 'Noto Sans Symbols', 'Noto Sans Symbols2', 'FreeSans', sans-serif")
        font.setPointSize(10)
        self.setFont(font)
        self.setToolTip("Click to expand sidebar")

    def _on_pulse_tick(self):
        self.pulse_phase += 0.0943
        if self.pulse_phase >= 2 * math.pi:
            self.pulse_phase -= 2 * math.pi
        self.update()

    def stop_animation(self):
        if self.pulse_timer.isActive():
            self.pulse_timer.stop()
        self.pulse_phase = 0.0

    def set_display_text(self, text: str):
        text = text or ""
        if self.display_text != text:
            self.display_text = text
            self.update()
        self.setToolTip(f"{text} — Click to expand sidebar" if text else "Click to expand sidebar")

    def set_activity_state(self, state: str):
        if state not in ("idle", "thinking", "speaking"):
            state = "idle"
        self.activity_state = state
        if state in ("thinking", "speaking") and self.isVisible():
            if not self.pulse_timer.isActive():
                self.pulse_phase = 0.0
                self.pulse_timer.start()
        else:
            self.stop_animation()
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        if self.activity_state in ("thinking", "speaking") and not self.pulse_timer.isActive():
            self.pulse_phase = 0.0
            self.pulse_timer.start()

    def hideEvent(self, event):
        self.stop_animation()
        super().hideEvent(event)

    def closeEvent(self, event):
        self.stop_animation()
        super().closeEvent(event)

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
        bg_color = QColor("#2c2c2c") if self._is_hovered else QColor(SIDEBAR_BG_COLOR)
        painter.fillRect(rect, bg_color)

        border_color = QColor("#4a4a4a") if self._is_hovered else QColor(SIDEBAR_BORDER_COLOR)
        painter.setPen(QPen(border_color, 1))
        painter.drawLine(rect.right(), rect.top(), rect.right(), rect.bottom())

        if self.activity_state in ("speaking", "thinking"):
            factor = (math.sin(self.pulse_phase) + 1.0) / 2.0
            if self.activity_state == "speaking":
                accent_color = _interpolate_color(self.secondary_dark, self.secondary_light, factor)
            else:
                accent_color = _interpolate_color(self.primary_dark, self.primary_light, factor)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(accent_color))
            painter.drawRoundedRect(QRectF(0, 4, 3, max(0, rect.height() - 8)), 1.5, 1.5)

        if self.display_text:
            painter.save()
            painter.translate(0, rect.height())
            painter.rotate(-90)

            font = self.font()
            font.setPointSize(10)
            painter.setFont(font)
            painter.setPen(QColor("#FFFFFF") if self._is_hovered else QColor("#BBBBBB"))

            available_span = max(0, rect.height() - 28)
            elided = painter.fontMetrics().elidedText(
                self.display_text, Qt.TextElideMode.ElideRight, available_span
            )
            painter.drawText(
                QRect(14, 2, available_span, max(0, rect.width() - 3)),
                Qt.AlignmentFlag.AlignCenter,
                elided,
            )
            painter.restore()


class ChatroomSidebarBridgeSignals(QObject):
    event_received = Signal(dict)


class ChatroomSidebar(QWidget):
    EXPANDED_WIDTH = 200
    MIN_EXPANDED_WIDTH = 140
    MAX_EXPANDED_WIDTH = 420
    COLLAPSED_WIDTH = 28

    agent_selected = Signal(str)
    new_agent_requested = Signal()
    chatroom_selected = Signal(int)
    new_chatroom_requested = Signal()
    edit_chatroom_requested = Signal(int)
    collapsed_changed = Signal(bool)
    width_changed = Signal(int)

    def __init__(self, agent_manager=None, parent=None):
        super().__init__(parent)
        self.agent_manager = agent_manager
        self.manager = ChatroomManager()
        self.selected_context = ("all", None)  # ("all", None) or ("room", room_id)
        self.selected_agent_id: Optional[str] = None

        self.agent_buttons: Dict[str, SidebarAgentButton] = {}
        self.room_buttons: Dict[int, SidebarTabButton] = {}
        self.room_wrappers: Dict[int, QWidget] = {}
        self.room_member_containers: Dict[int, QWidget] = {}
        self.room_member_buttons: Dict[str, SidebarAgentButton] = {}
        self.room_edit_buttons: Dict[int, QPushButton] = {}

        self._agent_activity_states: Dict[str, str] = {}
        self._agent_paused_states: Dict[str, bool] = {}
        self._hamburger_menu: Optional[QMenu] = None
        self._is_collapsed = False
        self._agents_collapsed = False
        self._chatrooms_collapsed = False
        self._expanded_width = self.EXPANDED_WIDTH
        self._initializing_state = True

        self.setObjectName("chatroomSidebar")
        self.setStyleSheet(f"""
            QWidget#chatroomSidebar {{
                background-color: {SIDEBAR_BG_COLOR};
            }}
            QWidget#expandedSidebarContainer {{
                background-color: {SIDEBAR_BG_COLOR};
            }}
            QScrollArea {{
                background-color: transparent;
                border: none;
                padding-right: 4px;
            }}
            QScrollArea > QWidget > QWidget {{
                background-color: transparent;
            }}
            {MODERN_SCROLLBAR_STYLE}
        """)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Expanded Sidebar Container
        self.expanded_container = QWidget(self)
        self.expanded_container.setObjectName("expandedSidebarContainer")
        self.expanded_container.installEventFilter(self)
        expanded_layout = QVBoxLayout(self.expanded_container)
        expanded_layout.setContentsMargins(0, 0, 1, 0)
        expanded_layout.setSpacing(0)

        # Unified scroll area for Agents + Chatrooms
        self.scroll_area = QScrollArea(self.expanded_container)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setContentsMargins(6, 6, 6, 8)
        self.scroll_layout.setSpacing(4)
        self.scroll_layout.setAlignment(Qt.AlignTop)

        # 1. Collapsible Agents section
        self.btn_agents_header = CollapsibleSectionHeader("✦ Agents", parent=self.scroll_content)
        self.btn_agents_header.setToolTip("Agents — click to collapse or expand")
        self.btn_agents_header.clicked.connect(self.toggle_agents_collapsed)
        self.scroll_layout.addWidget(self.btn_agents_header)

        self.agents_container = QWidget(self.scroll_content)
        self.agents_layout = QVBoxLayout(self.agents_container)
        self.agents_layout.setContentsMargins(0, 0, 0, 0)
        self.agents_layout.setSpacing(4)
        self.agents_layout.setAlignment(Qt.AlignTop)

        self.btn_add_agent = QPushButton("+ New Agent", self.agents_container)
        self.btn_add_agent.setToolTip("Add Agent — create a new AI agent")
        self.btn_add_agent.setFixedHeight(30)
        self.btn_add_agent.setCursor(Qt.PointingHandCursor)
        self.btn_add_agent.setStyleSheet(_dashed_action_button_style(is_sub_item=False))
        self.btn_add_agent.clicked.connect(self.new_agent_requested.emit)
        self.agents_layout.addWidget(self.btn_add_agent)
        self.scroll_layout.addWidget(self.agents_container)

        # 2. Collapsible Chatrooms section
        self.scroll_layout.addSpacing(10)
        self.btn_chatrooms_header = CollapsibleSectionHeader("🗫 Chatrooms", parent=self.scroll_content)
        self.btn_chatrooms_header.setToolTip("Chatrooms — click to collapse or expand")
        self.btn_chatrooms_header.clicked.connect(self.toggle_chatrooms_collapsed)
        self.lbl_section = self.btn_chatrooms_header
        self.scroll_layout.addWidget(self.btn_chatrooms_header)

        self.rooms_container = QWidget(self.scroll_content)
        self.rooms_layout = QVBoxLayout(self.rooms_container)
        self.rooms_layout.setContentsMargins(0, 0, 0, 0)
        self.rooms_layout.setSpacing(4)
        self.rooms_layout.setAlignment(Qt.AlignTop)

        self.btn_add_room = QPushButton("+ New Chatroom", self.rooms_container)
        self.btn_add_room.setToolTip("New Chatroom — create a room and assign agent access")
        self.btn_add_room.setFixedHeight(30)
        self.btn_add_room.setCursor(Qt.PointingHandCursor)
        self.btn_add_room.setStyleSheet(_dashed_action_button_style(is_sub_item=False))
        self.btn_add_room.clicked.connect(self.new_chatroom_requested.emit)
        self.rooms_layout.addWidget(self.btn_add_room)
        self.scroll_layout.addWidget(self.rooms_container)

        self.scroll_area.setWidget(self.scroll_content)
        expanded_layout.addWidget(self.scroll_area, 1)

        # 3. Seamless docked bottom settings bar
        self.settings_bar = QWidget(self.expanded_container)
        self.settings_bar.setObjectName("sidebarSettingsBar")
        self.settings_bar.setFixedHeight(42)
        self.settings_bar.setStyleSheet(f"""
            QWidget#sidebarSettingsBar {{
                background-color: {SIDEBAR_BG_COLOR};
                border-top: 1px solid {SIDEBAR_BORDER_COLOR};
            }}
        """)
        settings_layout = QHBoxLayout(self.settings_bar)
        settings_layout.setContentsMargins(6, 6, 6, 6)
        settings_layout.setSpacing(4)

        self.hamburger_btn = QPushButton("☰", self.settings_bar)
        self.hamburger_btn.setFixedSize(30, 30)
        self.hamburger_btn.setCursor(Qt.PointingHandCursor)
        self.hamburger_btn.setToolTip("Menu")
        self.hamburger_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #CCCCCC;
                border: none;
                border-radius: 4px;
                font-size: 18px;
            }
            QPushButton:hover {
                background-color: #333333;
                color: #FFFFFF;
            }
        """)
        self.hamburger_btn.clicked.connect(self._show_hamburger_menu)
        settings_layout.addWidget(self.hamburger_btn)

        settings_layout.addStretch(1)

        self.btn_collapse_sidebar = QPushButton("«", self.settings_bar)
        self.btn_collapse_sidebar.setFixedSize(30, 30)
        self.btn_collapse_sidebar.setCursor(Qt.PointingHandCursor)
        self.btn_collapse_sidebar.setToolTip("Collapse sidebar (Ctrl+B)")
        self.btn_collapse_sidebar.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #999999;
                border: none;
                border-radius: 4px;
                font-size: 16px;
            }
            QPushButton:hover {
                background-color: #333333;
                color: #FFFFFF;
            }
        """)
        self.btn_collapse_sidebar.clicked.connect(lambda: self.set_collapsed(True))
        settings_layout.addWidget(self.btn_collapse_sidebar)

        expanded_layout.addWidget(self.settings_bar)

        # Right-edge resize handle overlaid inside expanded_container
        self.resize_handle = SidebarResizeHandle(self, parent=self.expanded_container)
        self._position_resize_handle()

        root_layout.addWidget(self.expanded_container)

        # Collapsed Sidebar Strip
        self.collapsed_strip = CollapsedSidebarStrip(self)
        self.collapsed_strip.hide()
        self.collapsed_strip.clicked.connect(lambda: self.set_collapsed(False))
        root_layout.addWidget(self.collapsed_strip)

        # Listen for manager events via bridge signal for cross-thread GUI safety
        self.bridge_signals = ChatroomSidebarBridgeSignals()
        self.bridge_signals.event_received.connect(self._handle_chatroom_event)
        self.manager.register_listener(self._on_chatroom_event)

        # Restore persisted collapse & width state before exposing live edits
        self._load_persisted_ui_state()
        self._initializing_state = False

        self.refresh_agents()
        self.refresh_rooms()

    def _position_resize_handle(self):
        if hasattr(self, "resize_handle") and self.resize_handle and hasattr(self, "expanded_container"):
            w = self.expanded_container.width()
            h = self.expanded_container.height()
            if w > 0 and h > 0:
                self.resize_handle.setGeometry(max(0, w - 5), 0, 5, h)
                self.resize_handle.raise_()

    def eventFilter(self, watched, event):
        if watched is self.expanded_container and event.type() == QEvent.Resize:
            self._position_resize_handle()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_resize_handle()

    def showEvent(self, event):
        super().showEvent(event)
        self._position_resize_handle()

    def _load_persisted_ui_state(self):
        sidebar_collapsed = False
        agents_collapsed = False
        chatrooms_collapsed = False
        sidebar_width = self.EXPANDED_WIDTH
        try:
            cm = ConfigManager()
            sidebar_collapsed = bool(cm.get("ui.sidebar-collapsed", False))
            agents_collapsed = bool(cm.get("ui.agents-collapsed", False))
            chatrooms_collapsed = bool(cm.get("ui.chatrooms-collapsed", False))
            raw_width = cm.get("ui.sidebar-width", self.EXPANDED_WIDTH)
            try:
                sidebar_width = int(raw_width)
            except (TypeError, ValueError):
                sidebar_width = self.EXPANDED_WIDTH
        except Exception as e:
            logging.warning(f"ChatroomSidebar: Failed to load persisted UI state: {e}")

        self.set_expanded_width(sidebar_width, persist=False)
        self.set_agents_collapsed(agents_collapsed, persist=False)
        self.set_chatrooms_collapsed(chatrooms_collapsed, persist=False)
        self.set_collapsed(sidebar_collapsed, persist=False)

    def _persist_ui_state(self, key: str, value):
        if self._initializing_state:
            return
        try:
            cm = ConfigManager()
            cm.set(f"ui.{key}", value)
            cm.save()
        except Exception as e:
            logging.warning(f"ChatroomSidebar: Failed to persist ui.{key}: {e}")

    def expanded_width(self) -> int:
        return self._expanded_width

    def set_expanded_width(self, width: int, persist: bool = True):
        clamped = max(self.MIN_EXPANDED_WIDTH, min(self.MAX_EXPANDED_WIDTH, int(width)))
        changed = (self._expanded_width != clamped)
        self._expanded_width = clamped

        if not self._is_collapsed:
            self.setFixedWidth(self._expanded_width)
            self._position_resize_handle()

        if persist:
            self._persist_ui_state("sidebar-width", self._expanded_width)
        if changed:
            self.width_changed.emit(self._expanded_width)

    def set_hamburger_menu(self, menu: QMenu):
        self._hamburger_menu = menu

    def _show_hamburger_menu(self):
        if self._hamburger_menu is None:
            return
        self._hamburger_menu.ensurePolished()
        self._hamburger_menu.adjustSize()
        menu_size = self._hamburger_menu.sizeHint()

        btn_top_left = self.hamburger_btn.mapToGlobal(QPoint(0, 0))
        popup_x = btn_top_left.x()
        popup_y = btn_top_left.y() - menu_size.height() - 4

        screen = self.hamburger_btn.screen() or QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            popup_x = max(avail.left(), min(popup_x, avail.right() - menu_size.width()))
            popup_y = max(avail.top(), min(popup_y, avail.bottom() - menu_size.height()))

        self._hamburger_menu.popup(QPoint(popup_x, popup_y))

    def set_collapsed(self, collapsed: bool, persist: bool = True):
        collapsed = bool(collapsed)
        changed = (self._is_collapsed != collapsed)
        self._is_collapsed = collapsed

        self.expanded_container.setVisible(not collapsed)
        self.settings_bar.setVisible(not collapsed)
        self.collapsed_strip.setVisible(collapsed)
        self.setFixedWidth(self.COLLAPSED_WIDTH if collapsed else self._expanded_width)
        if not collapsed:
            self._position_resize_handle()
        self.collapsed_strip.set_activity_state(self._compute_aggregate_activity())

        if persist:
            self._persist_ui_state("sidebar-collapsed", collapsed)
        if changed:
            self.collapsed_changed.emit(collapsed)

    def toggle_collapsed(self):
        self.set_collapsed(not self._is_collapsed)

    def is_collapsed(self) -> bool:
        return self._is_collapsed

    def set_agents_collapsed(self, collapsed: bool, persist: bool = True):
        collapsed = bool(collapsed)
        self._agents_collapsed = collapsed
        self.btn_agents_header.set_collapsed(collapsed)
        self.agents_container.setVisible(not collapsed)
        if persist:
            self._persist_ui_state("agents-collapsed", collapsed)

    def toggle_agents_collapsed(self):
        self.set_agents_collapsed(not self._agents_collapsed)

    def set_chatrooms_collapsed(self, collapsed: bool, persist: bool = True):
        collapsed = bool(collapsed)
        self._chatrooms_collapsed = collapsed
        self.btn_chatrooms_header.set_collapsed(collapsed)
        self.rooms_container.setVisible(not collapsed)
        if persist:
            self._persist_ui_state("chatrooms-collapsed", collapsed)

    def toggle_chatrooms_collapsed(self):
        self.set_chatrooms_collapsed(not self._chatrooms_collapsed)

    def _compute_aggregate_activity(self) -> str:
        has_thinking = False
        for aid, state in self._agent_activity_states.items():
            if self._agent_paused_states.get(aid, False):
                continue
            if state == "speaking":
                return "speaking"
            if state == "thinking":
                has_thinking = True
        return "thinking" if has_thinking else "idle"

    def _update_collapsed_strip_label(self):
        ctx_type, room_id = self.selected_context
        if ctx_type == "room" and room_id is not None:
            room = self.manager.get_chatroom(room_id)
            if room and room.get("name"):
                self.collapsed_strip.set_display_text(f"#{room['name']}")
                return
            btn = self.room_buttons.get(room_id)
            self.collapsed_strip.set_display_text(btn.display_title if btn else f"Room {room_id}")
            return

        if self.selected_agent_id:
            if self.agent_manager is not None:
                name = self.agent_manager.get_agent_name(self.selected_agent_id)
            elif self.selected_agent_id in self.agent_buttons:
                name = self.agent_buttons[self.selected_agent_id].text()
            else:
                name = self.selected_agent_id
            self.collapsed_strip.set_display_text(name)
            return

        self.collapsed_strip.set_display_text("")

    def _on_chatroom_event(self, event_dict: dict):
        try:
            self.bridge_signals.event_received.emit(event_dict)
        except (RuntimeError, AttributeError):
            try:
                self.manager.unregister_listener(self._on_chatroom_event)
            except Exception:
                pass

    def _handle_chatroom_event(self, event_dict: dict):
        evt = event_dict.get("event")
        if evt in ("room_created", "room_updated", "room_deleted"):
            self.refresh_rooms()
        elif evt == "membership_changed":
            self.update_badges()
            self.refresh_selected_room_members()
        elif evt in ("new_message", "room_read"):
            self.update_badges()

    def closeEvent(self, event):
        try:
            self.manager.unregister_listener(self._on_chatroom_event)
        except Exception:
            pass
        for btn in self.agent_buttons.values():
            btn.stop_animation()
        for btn in self.room_member_buttons.values():
            btn.stop_animation()
        self.collapsed_strip.stop_animation()
        super().closeEvent(event)

    def _on_agent_clicked(self, agent_id: str):
        self.set_selected_agent(agent_id)
        self.agent_selected.emit(agent_id)

    def _on_room_member_clicked(self, agent_id: str):
        self.set_agents_collapsed(False)
        self.set_selected_agent(agent_id)
        self.agent_selected.emit(agent_id)

    def _on_room_clicked(self, room_id: int):
        self.set_selected_chatroom(room_id)
        self.chatroom_selected.emit(room_id)

    def set_selected_agent(self, agent_id: Optional[str]):
        self.selected_context = ("all", None)
        self.selected_agent_id = agent_id
        for aid, btn in self.agent_buttons.items():
            btn.set_selected(aid == agent_id)
        for btn in self.room_buttons.values():
            btn.set_selected(False)
        self.refresh_selected_room_members()
        self._update_collapsed_strip_label()

    def set_selected_chatroom(self, room_id: int):
        self.selected_context = ("room", room_id)
        for btn in self.agent_buttons.values():
            btn.set_selected(False)
        for rid, btn in self.room_buttons.items():
            btn.set_selected(rid == room_id)
        self.refresh_selected_room_members()
        self._update_collapsed_strip_label()

    def set_selected_context(self, context_type: str, target_id: Optional[int] = None):
        if context_type == "room" and target_id is not None:
            self.set_selected_chatroom(target_id)
        else:
            self.set_selected_agent(self.selected_agent_id)

    def _resolve_initial_paused(self, agent_id: str) -> bool:
        if agent_id in self._agent_paused_states:
            return self._agent_paused_states[agent_id]
        if self.agent_manager is not None:
            orch = self.agent_manager.get_orchestrator(agent_id)
            if orch is not None:
                return bool(getattr(orch, "is_paused", False))
        return False

    @staticmethod
    def _room_member_tooltip(is_watching: bool, is_paused: bool) -> str:
        watch_line = (
            "Watching this room (notified of new messages)"
            if is_watching
            else "Room access only (not watching)"
        )
        if is_paused:
            watch_line += " [Paused]"
        return f"{watch_line}\nClick to open 1-on-1 chat"

    def set_agent_activity_state(self, agent_id: str, state: str):
        if self._agent_paused_states.get(agent_id, False):
            state = "idle"
        self._agent_activity_states[agent_id] = state
        if agent_id in self.agent_buttons:
            self.agent_buttons[agent_id].set_activity_state(state)
        if agent_id in self.room_member_buttons:
            self.room_member_buttons[agent_id].set_activity_state(state)
        self.collapsed_strip.set_activity_state(self._compute_aggregate_activity())

    def set_agent_paused(self, agent_id: str, paused: bool):
        paused = bool(paused)
        self._agent_paused_states[agent_id] = paused
        if paused:
            self._agent_activity_states[agent_id] = "idle"
        if agent_id in self.agent_buttons:
            self.agent_buttons[agent_id].set_paused(paused)
        if agent_id in self.room_member_buttons:
            sub_btn = self.room_member_buttons[agent_id]
            sub_btn.set_paused(paused)
            sub_btn.setToolTip(self._room_member_tooltip(sub_btn.is_watching, paused))
        self.collapsed_strip.set_activity_state(self._compute_aggregate_activity())

    def update_agent_names(self):
        if self.agent_manager is None:
            return
        for aid, btn in self.agent_buttons.items():
            btn.setText(self.agent_manager.get_agent_name(aid))
        for aid, sub_btn in self.room_member_buttons.items():
            sub_btn.setText(self.agent_manager.get_agent_name(aid))
        self._update_collapsed_strip_label()

    def remove_agent(self, agent_id: str):
        self._agent_activity_states.pop(agent_id, None)
        self._agent_paused_states.pop(agent_id, None)
        btn = self.agent_buttons.pop(agent_id, None)
        if btn is not None:
            btn.stop_animation()
            self.agents_layout.removeWidget(btn)
            btn.deleteLater()
        if self.selected_agent_id == agent_id:
            self.selected_agent_id = None
        self.refresh_selected_room_members()
        self.collapsed_strip.set_activity_state(self._compute_aggregate_activity())
        self._update_collapsed_strip_label()

    def refresh_agents(self):
        self.agents_layout.removeWidget(self.btn_add_agent)

        if self.agent_manager is None:
            self.agents_layout.addWidget(self.btn_add_agent)
            return

        agents_by_age = self.agent_manager.get_agents_by_age()
        active_set = set(agents_by_age)

        for aid in list(self.agent_buttons.keys()):
            btn = self.agent_buttons[aid]
            self.agents_layout.removeWidget(btn)
            if aid not in active_set:
                btn.stop_animation()
                self.agent_buttons.pop(aid)
                btn.deleteLater()

        curr_type, _ = self.selected_context
        for aid in agents_by_age:
            name = self.agent_manager.get_agent_name(aid)
            if aid in self.agent_buttons:
                btn = self.agent_buttons[aid]
                btn.setText(name)
            else:
                btn = SidebarAgentButton(aid, name, parent=self.agents_container)
                btn.clicked.connect(lambda checked=False, target_id=aid: self._on_agent_clicked(target_id))
                self.agent_buttons[aid] = btn

            is_paused = self._resolve_initial_paused(aid)
            self._agent_paused_states[aid] = is_paused
            btn.set_paused(is_paused)
            btn.set_activity_state(self._agent_activity_states.get(aid, "idle"))
            btn.set_selected(curr_type == "all" and aid == self.selected_agent_id)
            self.agents_layout.addWidget(btn)

        self.agents_layout.addWidget(self.btn_add_agent)
        self.collapsed_strip.set_activity_state(self._compute_aggregate_activity())
        if curr_type == "room":
            self.refresh_selected_room_members()
        self._update_collapsed_strip_label()

    def _clear_room_members(self, room_id: int):
        container = self.room_member_containers.get(room_id)
        if container is None:
            return
        layout = container.layout()
        if layout is not None:
            while layout.count():
                item = layout.takeAt(0)
                w = item.widget()
                if w is not None:
                    if isinstance(w, SidebarAgentButton):
                        w.stop_animation()
                        self.room_member_buttons.pop(w.agent_id, None)
                    w.deleteLater()
        self.room_edit_buttons.pop(room_id, None)
        container.hide()

    def refresh_selected_room_members(self):
        curr_type, curr_rid = self.selected_context

        for rid in list(self.room_member_containers.keys()):
            if curr_type != "room" or rid != curr_rid:
                self._clear_room_members(rid)

        if curr_type != "room" or curr_rid is None or curr_rid not in self.room_member_containers:
            self.room_member_buttons.clear()
            return

        self._clear_room_members(curr_rid)
        container = self.room_member_containers[curr_rid]
        layout = container.layout()

        members = self.manager.get_members(curr_rid)
        member_watch_by_uid = {m["agent_uid"]: bool(m.get("watch", False)) for m in members}

        if self.agent_manager is not None:
            for aid in self.agent_manager.get_agents_by_age():
                uid = self.agent_manager.get_agent_uid(aid)
                if uid in member_watch_by_uid:
                    is_watching = member_watch_by_uid[uid]
                    name = self.agent_manager.get_agent_name(aid)
                    is_paused = self._resolve_initial_paused(aid)
                    sub_btn = SidebarAgentButton(
                        aid,
                        name,
                        parent=container,
                        is_sub_item=True,
                        is_watching=is_watching,
                    )
                    sub_btn.set_paused(is_paused)
                    sub_btn.set_activity_state(self._agent_activity_states.get(aid, "idle"))
                    sub_btn.setToolTip(self._room_member_tooltip(is_watching, is_paused))
                    sub_btn.clicked.connect(lambda checked=False, target_id=aid: self._on_room_member_clicked(target_id))
                    layout.addWidget(sub_btn)
                    self.room_member_buttons[aid] = sub_btn

        btn_edit_room = QPushButton("+ Edit Room", container)
        btn_edit_room.setToolTip("Edit Room — manage room settings and participating agents")
        btn_edit_room.setFixedHeight(26)
        btn_edit_room.setCursor(Qt.PointingHandCursor)
        btn_edit_room.setStyleSheet(_dashed_action_button_style(is_sub_item=True))
        btn_edit_room.clicked.connect(lambda checked=False, room_id=curr_rid: self.edit_chatroom_requested.emit(room_id))
        layout.addWidget(btn_edit_room)
        self.room_edit_buttons[curr_rid] = btn_edit_room

        container.show()

    def update_badges(self):
        badges = self.manager.get_user_room_badges()
        active_rooms = self.manager.list_chatrooms(include_deleted=False)
        active_room_ids = {r["id"] for r in active_rooms}
        if set(self.room_buttons.keys()) != active_room_ids:
            self.refresh_rooms()
            return

        for rid, btn in self.room_buttons.items():
            room_badges = badges.get(rid, {})
            btn.set_badges(
                unread=room_badges.get("unread_count", 0),
                mentions=room_badges.get("mention_count", 0),
            )

    def refresh_rooms(self):
        rooms = self.manager.list_chatrooms(include_deleted=False)
        badges = self.manager.get_user_room_badges()

        for btn in self.room_member_buttons.values():
            btn.stop_animation()
        self.room_member_buttons.clear()
        self.room_edit_buttons.clear()

        self.rooms_layout.removeWidget(self.btn_add_room)

        for rid, wrapper in list(self.room_wrappers.items()):
            self.rooms_layout.removeWidget(wrapper)
            wrapper.deleteLater()
        self.room_wrappers.clear()
        self.room_buttons.clear()
        self.room_member_containers.clear()

        curr_type, curr_rid = self.selected_context

        for r in rooms:
            rid = r["id"]
            name = r["name"]
            purpose = r.get("purpose", "")

            wrapper = QWidget(self.rooms_container)
            wrapper_layout = QVBoxLayout(wrapper)
            wrapper_layout.setContentsMargins(0, 0, 0, 0)
            wrapper_layout.setSpacing(2)

            btn = SidebarTabButton(f"#{name}", parent=wrapper)
            tooltip = f"#{name}" + (f"\n{purpose}" if purpose else "")
            btn.setToolTip(tooltip)

            room_badges = badges.get(rid, {})
            btn.set_badges(
                unread=room_badges.get("unread_count", 0),
                mentions=room_badges.get("mention_count", 0),
            )

            is_sel = (curr_type == "room" and curr_rid == rid)
            btn.set_selected(is_sel)
            btn.clicked.connect(lambda checked=False, room_id=rid: self._on_room_clicked(room_id))
            wrapper_layout.addWidget(btn)

            members_container = QWidget(wrapper)
            members_layout = QVBoxLayout(members_container)
            members_layout.setContentsMargins(0, 0, 0, 2)
            members_layout.setSpacing(2)
            members_container.hide()
            wrapper_layout.addWidget(members_container)

            self.rooms_layout.addWidget(wrapper)
            self.room_wrappers[rid] = wrapper
            self.room_buttons[rid] = btn
            self.room_member_containers[rid] = members_container

        self.rooms_layout.addWidget(self.btn_add_room)
        self.refresh_selected_room_members()
        self._update_collapsed_strip_label()
