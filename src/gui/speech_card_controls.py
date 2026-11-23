import math
from typing import Dict, Optional
from PySide6.QtCore import QObject, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QTextTable
from PySide6.QtWidgets import QPushButton, QTextBrowser

from core.audio_output import SpeechPlaybackController
from gui.chat_formatter import SpeechCardBlockData, get_card_viewport_rect

try:
    from gui.theme import PRIMARY_ACCENT_COLOR, SECONDARY_ACCENT_COLOR
except ImportError:
    PRIMARY_ACCENT_COLOR = "#a12924"
    SECONDARY_ACCENT_COLOR = "#f7e3a5"


class SpeechCardButton(QPushButton):
    """Subtle 22x22 top-right Play/Stop vector button overlaid on speech transcript cards."""

    BUTTON_SIZE = 22

    def __init__(
        self,
        card_key: str,
        message_id=None,
        transcript_text: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.card_key = str(card_key)
        self.message_id = message_id
        self.transcript_text = transcript_text

        self._mode = "play"  # "play" or "stop"
        self._is_active_card = False
        self._is_generating = False
        self._hovered = False
        self._pressed = False
        self._pulse_phase = 0.0

        self.setFixedSize(self.BUTTON_SIZE, self.BUTTON_SIZE)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFlat(True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setToolTip("Play speech")

        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(50)
        self._pulse_timer.timeout.connect(self._advance_pulse)

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def is_active_card(self) -> bool:
        return self._is_active_card

    @property
    def is_generating(self) -> bool:
        return self._is_generating

    def update_metadata(self, message_id=None, transcript_text: str = ""):
        self.message_id = message_id
        self.transcript_text = transcript_text

    def set_playback_visual_state(
        self,
        is_any_active: bool,
        is_active_card: bool,
        is_generating: bool,
    ):
        """Updates button icon mode, subtle emphasis state, pulse timer, and tooltip."""
        new_mode = "stop" if is_any_active else "play"
        new_active = bool(is_any_active and is_active_card)
        new_generating = bool(new_active and is_generating)

        self._mode = new_mode
        self._is_active_card = new_active
        self._is_generating = new_generating

        if not is_any_active:
            self.setToolTip("Play speech")
        elif new_generating:
            self.setToolTip("Generating speech")
        else:
            self.setToolTip("Stop speech")

        self._sync_pulse_timer()
        self.update()

    def _sync_pulse_timer(self):
        should_pulse = (
            self._mode == "stop"
            and self._is_active_card
            and self._is_generating
            and self.isVisible()
        )
        if should_pulse:
            if not self._pulse_timer.isActive():
                self._pulse_phase = 0.0
                self._pulse_timer.start()
        else:
            self.stop_animation()

    def stop_animation(self):
        if self._pulse_timer.isActive():
            self._pulse_timer.stop()
        self._pulse_phase = 0.0

    def _advance_pulse(self):
        self._pulse_phase = (self._pulse_phase + 0.24) % (2.0 * math.pi)
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_pulse_timer()

    def hideEvent(self, event):
        self.stop_animation()
        self._hovered = False
        self._pressed = False
        super().hideEvent(event)

    def closeEvent(self, event):
        self.stop_animation()
        super().closeEvent(event)

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._pressed = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._pressed = True
            self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._pressed = False
        self.update()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        pill_rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        bg_color: Optional[QColor] = None
        border_color: Optional[QColor] = None

        if self._mode == "play":
            if self._pressed:
                bg_color = QColor(255, 255, 255, 34)
                border_color = QColor(247, 227, 165, 120)
                glyph_color = QColor(SECONDARY_ACCENT_COLOR)
            elif self._hovered:
                bg_color = QColor(255, 255, 255, 22)
                border_color = QColor(247, 227, 165, 85)
                glyph_color = QColor(SECONDARY_ACCENT_COLOR)
            else:
                glyph_color = QColor("#6e6e76")
        else:
            if self._is_active_card:
                pulse_factor = (
                    0.55 + 0.45 * ((math.sin(self._pulse_phase) + 1.0) / 2.0)
                    if self._is_generating
                    else 1.0
                )
                if self._pressed:
                    bg_alpha = 185
                    glyph_color = QColor("#ffffff")
                elif self._hovered:
                    bg_alpha = 148
                    glyph_color = QColor("#ffffff")
                elif self._is_generating:
                    bg_alpha = int(round(68 * pulse_factor))
                    glyph_color = QColor(SECONDARY_ACCENT_COLOR)
                    glyph_color.setAlpha(int(round(240 * pulse_factor)))
                else:
                    bg_alpha = 56
                    glyph_color = QColor("#ff8a80")

                base_accent = QColor(PRIMARY_ACCENT_COLOR)
                bg_color = QColor(base_accent.red(), base_accent.green(), base_accent.blue(), bg_alpha)
                if self._is_generating and not self._hovered and not self._pressed:
                    border_color = QColor(247, 227, 165, int(round(120 * pulse_factor)))
                else:
                    border_color = QColor(212, 85, 80, 155 if self._hovered else 130)
            else:
                if self._pressed:
                    bg_color = QColor(161, 41, 36, 130)
                    border_color = QColor(212, 85, 80, 160)
                    glyph_color = QColor("#ffffff")
                elif self._hovered:
                    bg_color = QColor(161, 41, 36, 68)
                    border_color = QColor(212, 85, 80, 130)
                    glyph_color = QColor("#ff8a80")
                else:
                    glyph_color = QColor("#827a7c")

        if bg_color is not None or border_color is not None:
            painter.setBrush(bg_color if bg_color is not None else Qt.NoBrush)
            painter.setPen(QPen(border_color, 1.0) if border_color is not None else Qt.NoPen)
            painter.drawRoundedRect(pill_rect, 5.0, 5.0)

        painter.setPen(Qt.NoPen)
        painter.setBrush(glyph_color)

        if self._mode == "play":
            cx = self.width() / 2.0
            cy = self.height() / 2.0
            path = QPainterPath()
            path.moveTo(cx - 3.0, cy - 4.5)
            path.lineTo(cx + 4.5, cy)
            path.lineTo(cx - 3.0, cy + 4.5)
            path.closeSubpath()
            painter.drawPath(path)
        else:
            side = 8.0
            left = (self.width() - side) / 2.0
            top = (self.height() - side) / 2.0
            painter.drawRoundedRect(QRectF(left, top, side, side), 1.5, 1.5)


class SpeechCardButtonManager(QObject):
    """Positions and synchronizes top-right Play/Stop buttons over speech transcript cards."""

    play_requested = Signal(str, object, str)
    stop_requested = Signal()

    def __init__(
        self,
        browser: QTextBrowser,
        agent_id: Optional[str] = None,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self.browser = browser
        self.agent_id = agent_id
        self._buttons: Dict[str, SpeechCardButton] = {}
        self._playback_state = SpeechPlaybackController.instance().get_state()

    @property
    def buttons(self) -> Dict[str, SpeechCardButton]:
        return dict(self._buttons)

    def set_playback_state(self, state: Optional[dict]):
        """Applies global playback state to every rendered transcript button immediately."""
        if isinstance(state, dict):
            self._playback_state = dict(state)
        else:
            self._playback_state = {
                "is_active": False,
                "phase": "idle",
                "agent_id": None,
                "card_key": None,
                "queued_count": 0,
            }

        is_any_active = bool(self._playback_state.get("is_active", False))
        is_generating = self._playback_state.get("phase") == "generating"
        active_agent = self._playback_state.get("agent_id")
        active_key = self._playback_state.get("card_key")

        for key, btn in self._buttons.items():
            is_active_card = bool(
                is_any_active
                and active_agent == self.agent_id
                and active_key is not None
                and active_key == key
            )
            btn.set_playback_visual_state(
                is_any_active=is_any_active,
                is_active_card=is_active_card,
                is_generating=is_generating,
            )

    def sync_buttons(self):
        """Positions visible speech card buttons against current QTextTable geometry."""
        doc = self.browser.document() if self.browser is not None else None
        viewport = self.browser.viewport() if self.browser is not None else None
        if doc is None or viewport is None:
            return

        vp_rect = viewport.rect()
        seen_keys = set()

        is_any_active = bool(self._playback_state.get("is_active", False))
        is_generating = self._playback_state.get("phase") == "generating"
        active_agent = self._playback_state.get("agent_id")
        active_key = self._playback_state.get("card_key")

        for frame in doc.rootFrame().childFrames():
            if not isinstance(frame, QTextTable) or frame.rows() < 1 or frame.columns() < 1:
                continue
            first_block = frame.cellAt(0, 0).firstCursorPosition().block()
            data = first_block.userData() if first_block.isValid() else None
            if not isinstance(data, SpeechCardBlockData):
                continue

            card_key = data.card_key
            seen_keys.add(card_key)

            btn = self._buttons.get(card_key)
            if btn is None:
                btn = SpeechCardButton(
                    card_key=card_key,
                    message_id=data.message_id,
                    transcript_text=data.transcript_text,
                    parent=viewport,
                )
                btn.clicked.connect(lambda checked=False, k=card_key: self._on_button_clicked(k))
                self._buttons[card_key] = btn
            else:
                btn.update_metadata(message_id=data.message_id, transcript_text=data.transcript_text)

            is_active_card = bool(
                is_any_active
                and active_agent == self.agent_id
                and active_key is not None
                and active_key == card_key
            )
            btn.set_playback_visual_state(
                is_any_active=is_any_active,
                is_active_card=is_active_card,
                is_generating=is_generating,
            )

            card_rect = get_card_viewport_rect(self.browser, frame)
            if card_rect is None or card_rect.width() <= 0 or card_rect.height() <= 0:
                btn.hide()
                continue

            x = max(0, int(card_rect.right() - btn.width() - 9))
            y = int(card_rect.top() + 9)
            btn_rect = QRect(x, y, btn.width(), btn.height())

            if btn_rect.intersects(vp_rect):
                btn.move(x, y)
                btn.show()
                btn.raise_()
            else:
                btn.hide()

        stale_keys = [k for k in self._buttons.keys() if k not in seen_keys]
        for key in stale_keys:
            btn = self._buttons.pop(key)
            btn.stop_animation()
            btn.hide()
            btn.deleteLater()

    def _on_button_clicked(self, card_key: str):
        if self._playback_state.get("is_active", False):
            self.stop_requested.emit()
            return
        btn = self._buttons.get(card_key)
        if btn is None:
            return
        self.play_requested.emit(btn.card_key, btn.message_id, btn.transcript_text)

    def cleanup(self):
        for btn in list(self._buttons.values()):
            btn.stop_animation()
            btn.hide()
            btn.deleteLater()
        self._buttons.clear()
