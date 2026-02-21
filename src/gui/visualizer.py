
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
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_animation)
        self.timer.start(16)  # ~60 FPS

    def set_active(self, active: bool):
        self.is_active = active
        if not active:
            self.phase = 0.0
        self.update()

    def update_animation(self):
        if self.is_active:
            self.phase += 0.2
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
            # Draw a sine wave
            amplitude = height / 4
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
