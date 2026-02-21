from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QTextEdit, QPushButton
from PySide6.QtCore import Qt, Signal

class MirrorPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(10, 10, 10, 10)
        
        # Header
        self.header_label = QLabel("The Mirror (Thought Stream)")
        self.header_label.setStyleSheet("font-weight: bold; color: #00FFC8;")
        self.layout.addWidget(self.header_label)

        # Log area
        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setStyleSheet("background-color: #111; color: #EEE; font-family: Monospace;")
        self.layout.addWidget(self.log_area)

        # Close/Collapse button
        self.collapse_btn = QPushButton("Collapse")
        self.collapse_btn.clicked.connect(self.hide)
        self.layout.addWidget(self.collapse_btn)

    def log_event(self, source: str, message: str):
        """Append a log message with a source tag."""
        formatted_msg = f"<span style='color: #888;'>[{source}]</span> {message}"
        self.log_area.append(formatted_msg)

    def set_visible(self, visible: bool):
        super().setVisible(visible)
