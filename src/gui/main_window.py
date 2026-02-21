from PySide6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QSplitter
from PySide6.QtCore import Qt, QSize
from .visualizer import SoundWaveVisualizer
from .mirror import MirrorPanel
# If run from src/main.py, 'core' is available.
try:
    from core.gemini_worker import GeminiWorker
except ImportError:
    # If run from gui dir directly (not recommended but possible)
    from ..core.gemini_worker import GeminiWorker

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Amity 4")
        self.resize(1000, 600)

        # Central widget and main layout
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.main_layout = QHBoxLayout(self.central_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # Left Panel (Main Content)
        self.left_panel = QWidget()
        self.left_layout = QVBoxLayout(self.left_panel)
        self.left_layout.setContentsMargins(20, 20, 20, 20)
        
        # Visualizer
        self.visualizer = SoundWaveVisualizer()
        self.left_layout.addWidget(self.visualizer)
        
        # Spacer
        self.left_layout.addStretch()

        # Controls
        self.controls_layout = QHBoxLayout()
        
        self.btn_toggle_vis = QPushButton("Visualizer")
        self.btn_toggle_vis.clicked.connect(self.toggle_visualizer)
        self.controls_layout.addWidget(self.btn_toggle_vis)

        self.btn_toggle_mirror = QPushButton("Mirror")
        self.btn_toggle_mirror.clicked.connect(self.toggle_mirror)
        self.controls_layout.addWidget(self.btn_toggle_mirror)
        
        self.btn_gemini = QPushButton("Start Brain")
        self.btn_gemini.clicked.connect(self.toggle_gemini)
        self.controls_layout.addWidget(self.btn_gemini)
        
        self.btn_test_prompt = QPushButton("Test Prompt")
        self.btn_test_prompt.clicked.connect(self.send_test_prompt)
        self.controls_layout.addWidget(self.btn_test_prompt)

        self.left_layout.addLayout(self.controls_layout)

        # Right Panel (Mirror)
        self.mirror = MirrorPanel()
        self.mirror.hide()  # Initially hidden

        # Add to main layout
        self.main_layout.addWidget(self.left_panel, 1) # Stretch factor 1
        self.main_layout.addWidget(self.mirror, 0) # Stretch factor 0 (fixed width)

        # Gemini Integration
        self.gemini_worker = GeminiWorker(self)
        self.gemini_worker.response_received.connect(self.handle_gemini_response)
        self.gemini_worker.error_occurred.connect(self.handle_gemini_error)

        # Styling
        self.setStyleSheet("""
            QMainWindow { background-color: #1a1a1a; color: #FFF; }
            QPushButton { background-color: #333; color: #FFF; border: 1px solid #555; padding: 5px; }
            QPushButton:hover { background-color: #444; }
        """)

    def toggle_visualizer(self):
        self.visualizer.set_active(not self.visualizer.is_active)
        self.mirror.log_event("GUI", f"Visualizer active: {self.visualizer.is_active}")

    def toggle_mirror(self):
        if self.mirror.isVisible():
            self.mirror.hide()
        else:
            self.mirror.show()

    def toggle_gemini(self):
        if self.gemini_worker.is_running():
            self.gemini_worker.stop_session()
            self.btn_gemini.setText("Start Brain")
            self.mirror.log_event("System", "Brain stopped.")
        else:
            self.gemini_worker.start_session()
            self.btn_gemini.setText("Stop Brain")
            self.mirror.log_event("System", "Brain starting...")

    def send_test_prompt(self):
        if self.gemini_worker.is_running():
            prompt = "Hello Amity!"
            self.gemini_worker.send_prompt(prompt)
            self.mirror.log_event("User", prompt)
        else:
            self.mirror.log_event("System", "Cannot send prompt: Brain not running.")

    def handle_gemini_response(self, text: str):
        self.mirror.log_event("Gemini", text.strip())

    def handle_gemini_error(self, text: str):
        self.mirror.log_event("Error", text.strip())
