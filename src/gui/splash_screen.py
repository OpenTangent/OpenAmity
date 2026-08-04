import sys
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QFrame
from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import QPainter, QPen, QColor, QFont

class LoadingRotator(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(40, 40)
        self.angle = 0
        
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.rotate)
        self.timer.start(30)

    def rotate(self):
        self.angle = (self.angle + 10) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        pen = QPen(QColor("#00FFC8"))
        pen.setWidth(4)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        
        rect = QRectF(4, 4, self.width() - 8, self.height() - 8)
        
        # QPainter drawArc uses angles in 1/16th of a degree
        start_angle = -self.angle * 16
        span_angle = 270 * 16 # Draw 3/4 of a circle
        
        painter.drawArc(rect, start_angle, span_angle)


class LoadingCard(QWidget):
    def __init__(self):
        super().__init__()
        
        # Splash screen properties
        self.setWindowFlags(Qt.SplashScreen | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(256, 192)
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        # Main rounded container
        self.container = QFrame(self)
        self.container.setStyleSheet("""
            QFrame {
                background-color: #1a1a1a;
                border-radius: 15px;
                border: 1px solid #333;
            }
        """)
        
        container_layout = QVBoxLayout(self.container)
        container_layout.setAlignment(Qt.AlignCenter)
        container_layout.setSpacing(20)
        
        # App Title
        self.title_label = QLabel("Open Amity")
        font = QFont("Ubuntu")
        font.setPointSize(24)
        font.setBold(False)
        self.title_label.setFont(font)
        self.title_label.setStyleSheet("color: white; background: transparent; border: none;")
        self.title_label.setAlignment(Qt.AlignCenter)
        
        # Loading Rotator
        self.rotator = LoadingRotator()
        
        container_layout.addStretch()
        container_layout.addWidget(self.title_label, 0, Qt.AlignCenter)
        container_layout.addWidget(self.rotator, 0, Qt.AlignCenter)
        container_layout.addStretch()
        
        main_layout.addWidget(self.container)
