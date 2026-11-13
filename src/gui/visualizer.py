from collections import deque
import math
import time
from PySide6.QtWidgets import QWidget
from PySide6.QtCore import QTimer, Qt, QPointF
from PySide6.QtGui import QPainter, QColor, QPen, QPainterPath

try:
    from gui.theme import PRIMARY_ACCENT_COLOR, SECONDARY_ACCENT_COLOR
except ImportError:
    PRIMARY_ACCENT_COLOR = "#a12924"
    SECONDARY_ACCENT_COLOR = "#f7e3a5"


class SoundWaveVisualizer(QWidget):
    """
    Oscilloscope voice-reactive waveform visualizer ("Karen"-style waveform mouth).
    Concentrates active speech waveform articulation in the middle 50% of the bar
    while keeping the outer 25% on each side flat, rendered in champagne gold
    with a warm amber/crimson CRT phosphor glow.
    """

    NUM_POINTS = 96
    FPS = 60
    FRAME_DURATION = 1.0 / FPS

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(100)
        self.is_active = False
        self.phase = 0.0
        self.target_amplitude = 0.0
        self.current_amplitude = 0.0

        # Real-time synchronized waveform frame buffer
        self._frame_queue = deque()
        self._playback_start_time = None
        self._frames_consumed = 0
        self.current_waveform = [0.0] * self.NUM_POINTS

        #Precomputed window envelope for the middle 50% span
        self._window_envelope = [
            math.sin(math.pi * (i / (self.NUM_POINTS - 1))) ** 1.6
            for i in range(self.NUM_POINTS)
        ]

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_animation)
        self.timer.start(16)  # ~60 FPS

    def set_active(self, active: bool):
        self.is_active = active
        if not active:
            self.phase = 0.0
            self.target_amplitude = 0.0
            self.current_amplitude = 0.0
            self._frame_queue.clear()
            self._playback_start_time = None
            self._frames_consumed = 0
            self.current_waveform = [0.0] * self.NUM_POINTS
        self.update()

    def set_amplitude(self, amplitude: float):
        # Expected amplitude range roughly 0.0 to 1.0
        self.target_amplitude = min(1.0, max(0.0, float(amplitude)))

    def enqueue_waveform_frames(self, frames):
        """Enqueues 60-FPS normalized waveform slices for wall-clock synchronized playback."""
        if not frames:
            return
        now = time.monotonic()
        if self._playback_start_time is None or not self._frame_queue:
            self._playback_start_time = now
            self._frames_consumed = 0

        for frame in frames:
            if isinstance(frame, (list, tuple)):
                self._frame_queue.append(frame)

    def _synthesize_fallback_waveform(self, amp: float) -> list[float]:
        """Generates a crisp multi-harmonic oscilloscope waveform when only scalar amplitude is provided."""
        if amp < 0.008:
            return [0.0] * self.NUM_POINTS

        boosted = math.tanh(amp * 2.6)
        points = []
        p = self.phase
        for i in range(self.NUM_POINTS):
            u = i / (self.NUM_POINTS - 1)
            # Multi-harmonic standing/traveling voice oscillation
            theta = u * math.pi * 6.0
            val = (
                0.50 * math.sin(theta + p)
                + 0.30 * math.sin(theta * 2.3 - p * 1.4)
                + 0.20 * math.sin(theta * 4.7 + p * 2.1)
                + 0.12 * math.cos(theta * 7.1 - p * 2.8)
            )
            points.append(max(-1.0, min(1.0, val * boosted)))
        return points

    def update_animation(self):
        if not self.is_active:
            return

        self.phase += 0.35

        if self._frame_queue and self._playback_start_time is not None:
            now = time.monotonic()
            elapsed = max(0.0, now - self._playback_start_time)
            target_idx = int(elapsed / self.FRAME_DURATION)
            steps = max(1, target_idx - self._frames_consumed)

            frame = None
            for _ in range(steps):
                if self._frame_queue:
                    frame = self._frame_queue.popleft()
                    self._frames_consumed += 1
                else:
                    break

            if not self._frame_queue and frame is None:
                self._playback_start_time = None
                self._frames_consumed = 0
                self.current_waveform = [0.0] * self.NUM_POINTS
                self.current_amplitude = 0.0
                self.target_amplitude = 0.0
            elif frame is not None:
                if not frame:
                    # Silent frame (micro-pause between syllables/words): snap flat rapidly
                    self.current_waveform = [
                        v * 0.15 if abs(v) > 0.01 else 0.0
                        for v in self.current_waveform
                    ]
                    self.current_amplitude = 0.0
                else:
                    n = len(frame)
                    if n == self.NUM_POINTS:
                        resampled = frame
                    else:
                        resampled = [
                            frame[min(n - 1, int(i * n / self.NUM_POINTS))]
                            for i in range(self.NUM_POINTS)
                        ]
                    # Subtle phosphor persistence (85% new, 15% previous) for crisp yet smooth motion
                    self.current_waveform = [
                        0.85 * float(new_v) + 0.15 * old_v
                        for new_v, old_v in zip(resampled, self.current_waveform)
                    ]
                    self.current_amplitude = max(abs(v) for v in self.current_waveform)
        else:
            # Fallback scalar amplitude mode
            self._playback_start_time = None
            self._frames_consumed = 0
            if self.target_amplitude > self.current_amplitude:
                self.current_amplitude += (self.target_amplitude - self.current_amplitude) * 0.55
            else:
                self.current_amplitude += (self.target_amplitude - self.current_amplitude) * 0.35

            self.target_amplitude = max(0.0, self.target_amplitude - 0.08)
            if self.current_amplitude < 0.008:
                self.current_amplitude = 0.0
                self.current_waveform = [0.0] * self.NUM_POINTS
            else:
                self.current_waveform = self._synthesize_fallback_waveform(self.current_amplitude)

        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect()
        width = float(rect.width())
        height = float(rect.height())
        mid_y = height / 2.0

        # Oscilloscope dark background with subtle top separator line
        painter.fillRect(rect, QColor(20, 20, 20))
        border_pen = QPen(QColor(42, 42, 46))
        border_pen.setWidth(1)
        painter.setPen(border_pen)
        painter.drawLine(0, 0, int(width), 0)

        # Horizontal span: outer 25% on each side is flat; middle 50% holds the active waveform
        left_x = width * 0.25
        right_x = width * 0.75
        active_width = right_x - left_x
        max_vertical_amp = (height / 2.0) * 0.82

        path = QPainterPath()
        path.moveTo(0.0, mid_y)

        has_signal = self.is_active and any(abs(v) > 0.002 for v in self.current_waveform)
        if has_signal and active_width > 4.0:
            path.lineTo(left_x, mid_y)
            n = len(self.current_waveform)
            for i in range(n):
                u = i / (n - 1)
                x = left_x + u * active_width
                envelope = self._window_envelope[i] if i < len(self._window_envelope) else math.sin(math.pi * u) ** 1.6
                y = mid_y - (self.current_waveform[i] * envelope * max_vertical_amp)
                path.lineTo(x, y)
            path.lineTo(right_x, mid_y)
            path.lineTo(width, mid_y)
        else:
            path.lineTo(width, mid_y)

        # Pass 1: Outer crimson/amber glow (PRIMARY_ACCENT_COLOR #a12924 bloom)
        outer_glow_color = QColor(PRIMARY_ACCENT_COLOR)
        outer_glow_color.setAlpha(95)
        outer_pen = QPen(outer_glow_color)
        outer_pen.setWidthF(9.0)
        outer_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        outer_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(outer_pen)
        painter.drawPath(path)

        # Pass 2: Mid warm amber phosphor halo
        mid_glow_color = QColor(225, 130, 45, 145)
        mid_pen = QPen(mid_glow_color)
        mid_pen.setWidthF(4.5)
        mid_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        mid_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(mid_pen)
        painter.drawPath(path)

        # Pass 3: Crisp champagne gold core trace (SECONDARY_ACCENT_COLOR #f7e3a5)
        core_pen = QPen(QColor(SECONDARY_ACCENT_COLOR))
        core_pen.setWidthF(2.0)
        core_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        core_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(core_pen)
        painter.drawPath(path)

