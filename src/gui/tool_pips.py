import time
import math
import html
from typing import List, Optional
from PySide6.QtWidgets import QWidget, QToolTip
from PySide6.QtCore import Qt, QObject, QTimer, QRectF, QPoint, QEvent
from PySide6.QtGui import QPainter, QColor, QPen, QCursor


class ToolPip(QWidget):
    """
    A 24x24 pixel floating pip element representing a tool call.
    Features a semi-transparent dark pill background, 1px border colored with
    the tool's accent color, centered emoji/symbol glyph, and instant rich HTML tooltip on hover.
    """

    def __init__(
        self,
        call_id: str,
        tool_name: str,
        icon: str,
        color: str,
        call_text: str,
        is_async: bool,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.call_id = call_id
        self.tool_name = tool_name
        self.icon = icon
        self.color = color
        self.call_text = call_text
        self.is_async = is_async

        self.setFixedSize(24, 24)
        self.setAttribute(Qt.WA_Hover, True)

        self.created_at = time.monotonic()
        self.duration = 4.0 if not is_async else None
        self.fade_duration = 0.35  # seconds
        self.state = "visible"  # "visible", "fading", "dead"
        self.fade_start_time: Optional[float] = None
        self.fade_start_opacity = 1.0

        self.current_x = 2.0
        self.target_x = 2.0
        self.current_opacity = 1.0
        self.pulse_phase = 0.0

        # Rich HTML tooltip (max 512 characters with trailing ellipses)
        self._setup_tooltip()

    def _setup_tooltip(self):
        # Truncate call text to 512 chars with ellipses if needed
        clean_text = self.call_text.strip()
        if len(clean_text) > 512:
            clean_text = clean_text[:509] + "..."

        escaped_title = html.escape(self.tool_name)
        escaped_call = html.escape(clean_text, quote=False)

        tooltip_html = (
            f"<div style=\"font-family: 'Ubuntu', 'Segoe UI', sans-serif; font-size: 12px;\">"
            f"<div style=\"font-weight: bold; margin-bottom: 3px;\">{escaped_title}</div>"
            f"<div style=\"font-family: 'Ubuntu Mono', monospace; font-size: 11px; color: {self.color};\">"
            f"{escaped_call}</div>"
            f"</div>"
        )
        self.setToolTip(tooltip_html)

    def enterEvent(self, event):
        super().enterEvent(event)
        tooltip_text = self.toolTip()
        if tooltip_text:
            # Display tooltip instantly on hover without waiting for Qt default delay
            QToolTip.showText(QCursor.pos(), tooltip_text, self, self.rect())

    def leaveEvent(self, event):
        super().leaveEvent(event)
        QToolTip.hideText()

    def hideEvent(self, event):
        super().hideEvent(event)
        if self.underMouse():
            QToolTip.hideText()

    def start_fade(self):
        if self.state != "fading":
            self.state = "fading"
            self.fade_start_time = time.monotonic()
            self.fade_start_opacity = self.current_opacity
            if self.underMouse():
                QToolTip.hideText()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        painter.setOpacity(self.current_opacity)

        # Draw semi-transparent pill container (24x24 with rounded corners)
        rect = QRectF(0.5, 0.5, 23.0, 23.0)
        bg_color = QColor(26, 26, 26, 204)  # #1a1a1acc
        border_color = QColor(self.color)

        pen = QPen(border_color, 1.0)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(bg_color)
        painter.drawRoundedRect(rect, 5.0, 5.0)

        # Draw centered icon/symbol glyph
        painter.setPen(QColor("#FFFFFF"))
        font = painter.font()
        font.setPointSize(12)
        painter.setFont(font)
        painter.drawText(QRectF(0, 0, 24, 24), Qt.AlignCenter, self.icon)
        painter.end()


class ToolPipManager(QObject):
    """
    Manages the lifecycle, layout, and smooth animations of floating tool pips.
    Positions pips right up against the chat log container edges (bypassing internal
    scroll area padding) and stacks them above all sibling scrollbars via overlay parenting.
    """

    def __init__(
        self,
        container: QWidget,
        overlay_parent: Optional[QWidget] = None,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self.container = container
        self.overlay_parent = overlay_parent
        self.container.installEventFilter(self)

        target = self.target_parent
        if target != self.container:
            target.installEventFilter(self)

        self.sync_pips: List[ToolPip] = []
        self.async_pips: List[ToolPip] = []
        self.fading_pips: List[ToolPip] = []
        self.is_active = True

        self._last_tick = time.monotonic()
        self.animation_timer = QTimer(self)
        self.animation_timer.setInterval(16)  # ~60 FPS
        self.animation_timer.timeout.connect(self._on_animation_tick)

    @property
    def target_parent(self) -> QWidget:
        if self.overlay_parent is not None:
            return self.overlay_parent
        return self.container.parentWidget() or self.container

    def _get_container_bounds(self):
        target = self.target_parent
        if target == self.container:
            return 0, 0, self.container.width()
        top_left = self.container.mapTo(target, QPoint(0, 0))
        return top_left.x(), top_left.y(), self.container.width()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() in (QEvent.Resize, QEvent.Move):
            if watched in (self.container, self.target_parent):
                self._recalculate_async_targets(immediate=True)
                self._recalculate_sync_targets(immediate=True)
        return super().eventFilter(watched, event)

    def set_active(self, is_active: bool):
        """Controls visibility based on whether this agent's tab is currently selected."""
        self.is_active = is_active
        all_pips = self.sync_pips + self.async_pips + self.fading_pips
        for pip in all_pips:
            if is_active:
                pip.show()
                pip.raise_()
            else:
                pip.hide()

        if is_active:
            self._ensure_timer_running()
        else:
            self.animation_timer.stop()

    def add_pip(
        self,
        call_id: str,
        tool_name: str,
        icon: str,
        color: str,
        call_text: str,
        is_async: bool,
    ):
        parent_widget = self.target_parent
        pip = ToolPip(
            call_id=call_id,
            tool_name=tool_name,
            icon=icon,
            color=color,
            call_text=call_text,
            is_async=is_async,
            parent=parent_widget,
        )

        origin_x, origin_y, container_w = self._get_container_bounds()
        pip_y = origin_y + 2

        # 24px pip + 2px gap = 26px slot pitch
        if not is_async:
            slot_idx = len(self.sync_pips)
            target_x = origin_x + 2.0 + slot_idx * 26.0
            pip.current_x = target_x
            pip.target_x = target_x
            pip.move(int(round(target_x)), pip_y)
            self.sync_pips.append(pip)
        else:
            slot_idx = len(self.async_pips)
            target_x = max(origin_x + 2.0, origin_x + container_w - 26.0 - slot_idx * 26.0)
            pip.current_x = target_x
            pip.target_x = target_x
            pip.move(int(round(target_x)), pip_y)
            self.async_pips.append(pip)

        if self.is_active:
            pip.show()
            pip.raise_()
        else:
            pip.hide()

        self._ensure_timer_running()

    def complete_async_pip(self, call_id: str):
        target_pip = None
        for pip in self.async_pips:
            if pip.call_id == call_id:
                target_pip = pip
                break

        if target_pip:
            self.async_pips.remove(target_pip)
            target_pip.start_fade()
            self.fading_pips.append(target_pip)
            self._recalculate_async_targets(immediate=False)
            self._ensure_timer_running()

    def _recalculate_async_targets(self, immediate: bool = False):
        origin_x, origin_y, container_w = self._get_container_bounds()
        pip_y = origin_y + 2
        for i, pip in enumerate(self.async_pips):
            target = max(origin_x + 2.0, origin_x + container_w - 26.0 - i * 26.0)
            pip.target_x = target
            if immediate:
                pip.current_x = target
                pip.move(int(round(target)), pip_y)

    def _recalculate_sync_targets(self, immediate: bool = False):
        origin_x, origin_y, _ = self._get_container_bounds()
        pip_y = origin_y + 2
        for i, pip in enumerate(self.sync_pips):
            target = origin_x + 2.0 + i * 26.0
            pip.target_x = target
            if immediate:
                pip.current_x = target
                pip.move(int(round(target)), pip_y)

    def _ensure_timer_running(self):
        has_work = bool(self.sync_pips or self.async_pips or self.fading_pips)
        if self.is_active and has_work:
            if not self.animation_timer.isActive():
                self._last_tick = time.monotonic()
                self.animation_timer.start()
        else:
            if not has_work:
                self.animation_timer.stop()

    def _on_animation_tick(self):
        now = time.monotonic()
        dt = max(0.001, min(0.1, now - self._last_tick))
        self._last_tick = now

        origin_x, origin_y, _ = self._get_container_bounds()
        pip_y = origin_y + 2

        # 1. Check synchronous pips for 4.0s expiration
        expired_sync = []
        for pip in self.sync_pips:
            if pip.duration:
                # If user is hovering over the pip to read tooltip, extend duration
                if pip.underMouse():
                    pip.created_at = now - (pip.duration - 2.0)
                    continue
                if (now - pip.created_at) >= pip.duration:
                    expired_sync.append(pip)

        if expired_sync:
            for pip in expired_sync:
                self.sync_pips.remove(pip)
                pip.start_fade()
                self.fading_pips.append(pip)
            self._recalculate_sync_targets()

        # 2. Smoothly slide synchronous pips to the left towards their targets
        for pip in self.sync_pips:
            diff = pip.target_x - pip.current_x
            if abs(diff) > 0.05:
                pip.current_x += diff * min(1.0, 15.0 * dt)
                pip.move(int(round(pip.current_x)), pip_y)
            else:
                pip.current_x = pip.target_x
                pip.move(int(round(pip.target_x)), pip_y)

        # 3. Smoothly slide asynchronous pips and pulse their breathing glow
        for pip in self.async_pips:
            diff = pip.target_x - pip.current_x


            if abs(diff) > 0.05:
                pip.current_x += diff * min(1.0, 15.0 * dt)
                pip.move(int(round(pip.current_x)), pip_y)
            else:
                pip.current_x = pip.target_x
                pip.move(int(round(pip.target_x)), pip_y)

            # Gentle breathing pulse between 0.80 and 1.0 opacity (~0.5 Hz)
            pip.pulse_phase += 3.14 * dt
            if pip.pulse_phase >= 2 * math.pi:
                pip.pulse_phase -= 2 * math.pi
            pip.current_opacity = 0.90 + 0.10 * math.sin(pip.pulse_phase)
            pip.update()

        # 4. Fade out expiring / completed pips simultaneously
        dead_pips = []
        for pip in self.fading_pips:
            elapsed = now - (pip.fade_start_time or now)
            if elapsed >= pip.fade_duration:
                pip.current_opacity = 0.0
                dead_pips.append(pip)
            else:
                ratio = max(0.0, min(1.0, elapsed / pip.fade_duration))
                pip.current_opacity = pip.fade_start_opacity * (1.0 - ratio)
                pip.update()

        for pip in dead_pips:
            self.fading_pips.remove(pip)
            pip.hide()
            pip.deleteLater()

        # Check if timer can sleep
        self._ensure_timer_running()

    def clear(self):
        """Cleans up all active and fading pips."""
        self.animation_timer.stop()
        all_pips = self.sync_pips + self.async_pips + self.fading_pips
        for pip in all_pips:
            pip.hide()
            pip.deleteLater()
        self.sync_pips.clear()
        self.async_pips.clear()
        self.fading_pips.clear()
