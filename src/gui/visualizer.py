from PySide6.QtWidgets import QWidget
from PySide6.QtCore import QTimer, Qt, QRectF
from PySide6.QtGui import QPainter, QColor, QBrush, QPen, QPainterPath
import math

class SoundWaveVisualizer(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(100)
        self.is_active = False
        self.phase = 0.0
        self.target_amplitude = 0.0
        self.current_amplitude = 0.0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_animation)
        self.timer.start(16)  # ~60 FPS

    def set_active(self, active: bool):
        self.is_active = active
        if not active:
            self.phase = 0.0
            self.target_amplitude = 0.0
            self.current_amplitude = 0.0
        self.update()

    def set_amplitude(self, amplitude: float):
        # Expected amplitude range roughly 0.0 to 1.0
        self.target_amplitude = min(1.0, max(0.0, amplitude))

    def update_animation(self):
        if self.is_active:
            self.phase += 0.2
            # Smooth interpolation of amplitude
            self.current_amplitude += (self.target_amplitude - self.current_amplitude) * 0.2
            
            # Decay target amplitude quickly so it falls back to 0 if no new audio comes in
            self.target_amplitude = max(0.0, self.target_amplitude - 0.05)
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Background (transparent or dark to fit theme)
        painter.fillRect(self.rect(), QColor(20, 20, 20))

        width = self.width()
        height = self.height()
        mid_y = height / 2

        path = QPainterPath()
        path.moveTo(0, mid_y)

        if self.is_active:
            # Draw a sine wave based on dynamic amplitude
            # Base line width with small ambient movement, plus audio reactive
            base_amp = height * 0.05
            reactive_amp = (height / 3) * self.current_amplitude
            amplitude = base_amp + reactive_amp
            
            frequency = 0.05
            for x in range(0, width + 1, 2):
                y = mid_y + amplitude * math.sin((x * frequency) + self.phase)
                path.lineTo(x, y)
        else:
            # Draw a flat line
            path.lineTo(width, mid_y)

        # Style the line
        pen = QPen(QColor(0, 255, 200))  # Cyan/Teal color
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawPath(path)
