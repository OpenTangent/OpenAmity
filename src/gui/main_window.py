
from PySide6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QSplitter, QLabel
from PySide6.QtCore import Qt, QSize, QTimer
from .visualizer import SoundWaveVisualizer
from .mirror import MirrorPanel
import numpy as np
import sounddevice as sd

try:
    from core.gemini_worker import GeminiWorker
    from core.audio_input import AudioService
    from core.audio_output import TTSWorker
    from core.memory import MemorySystem
except ImportError:
    from ..core.gemini_worker import GeminiWorker
    from ..core.audio_input import AudioService
    from ..core.audio_output import TTSWorker
    from ..core.memory import MemorySystem

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
        
        # Status Label
        self.status_label = QLabel("Initializing...")
        self.status_label.setStyleSheet("font-size: 16px; color: #888;")
        self.left_layout.addWidget(self.status_label)

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
        
        self.btn_test_tts = QPushButton("Test Voice")
        self.btn_test_tts.clicked.connect(self.test_tts)
        self.controls_layout.addWidget(self.btn_test_tts)

        self.left_layout.addLayout(self.controls_layout)

        # Right Panel (Mirror)
        self.mirror = MirrorPanel()
        self.mirror.hide()

        # Add to main layout
        self.main_layout.addWidget(self.left_panel, 1)
        self.main_layout.addWidget(self.mirror, 0)

        # Workers
        self.memory_system = MemorySystem()
        self.system_prompt = self.memory_system.get_system_prompt()
        self.mirror.log_event("System", "Soul Jar loaded.")

        self.gemini_worker = GeminiWorker(self)
        self.gemini_worker.response_received.connect(self.handle_gemini_response)
        self.gemini_worker.error_occurred.connect(self.handle_gemini_error)

        self.audio_service = AudioService()
        self.audio_service.initialized.connect(self.on_audio_initialized)
        self.audio_service.wake_word_detected.connect(self.on_wake_word)
        self.audio_service.listening_started.connect(self.on_listening_start)
        self.audio_service.listening_stopped.connect(self.on_listening_stop)
        self.audio_service.transcription_finished.connect(self.on_transcription)
        self.audio_service.error_occurred.connect(self.on_audio_error)

        # Start initialization
        self.audio_service.start_initialization()
        self.mirror.log_event("System", "Initializing audio models...")

        # Styling
        self.setStyleSheet("""
            QMainWindow { background-color: #1a1a1a; color: #FFF; }
            QPushButton { background-color: #333; color: #FFF; border: 1px solid #555; padding: 5px; }
            QPushButton:hover { background-color: #444; }
        """)
        
        self.tts_worker = None

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
            self.gemini_worker.send_prompt(self.system_prompt)
            self.btn_gemini.setText("Stop Brain")
            self.mirror.log_event("System", "Brain starting... Soul Jar injected.")

    def test_tts(self):
        self.speak("Hello Andrew. My voice is operational.")

    def speak(self, text):
        self.mirror.log_event("Amity", f"Speaking: {text}")
        self.tts_worker = TTSWorker(text)
        self.tts_worker.started_playback.connect(lambda: self.visualizer.set_active(True))
        self.tts_worker.finished.connect(lambda: self.visualizer.set_active(False))
        self.tts_worker.finished.connect(self.tts_worker.deleteLater)
        self.tts_worker.start()

    def play_ding(self):
        # Simple sine wave beep
        fs = 44100
        duration = 0.1  # seconds
        f = 1000.0  # Hz
        t = np.arange(int(fs * duration)) / fs
        audio = 0.5 * np.sin(2 * np.pi * f * t)
        sd.play(audio, fs)

    # Audio Service Slots
    def on_audio_initialized(self):
        self.mirror.log_event("System", "Audio models initialized.")
        self.status_label.setText("Ready. Listening for 'Alexa' (as proxy)...")
        self.audio_service.start_wake_word_detection()

    def on_wake_word(self, keyword):
        self.mirror.log_event("Ear", f"Wake word detected: {keyword}")
        self.play_ding()

    def on_listening_start(self):
        self.status_label.setText("Listening...")
        self.status_label.setStyleSheet("font-size: 16px; color: #00FFC8;") # Cyan
        self.visualizer.set_active(False) # Ensure visualizer is off during input

    def on_listening_stop(self):
        self.status_label.setText("Processing...")
        self.status_label.setStyleSheet("font-size: 16px; color: #FFA500;") # Orange

    def on_transcription(self, text):
        self.mirror.log_event("Ear", f"Heard: {text}")
        self.status_label.setText(f"Heard: {text}")
        if self.gemini_worker.is_running():
            self.mirror.log_event("User", text)
            self.gemini_worker.send_prompt(text)
        else:
            self.mirror.log_event("System", "Brain not running, ignoring input.")
            self.speak("My brain is currently offline.")
            self.status_label.setText("Brain Offline")

    def on_audio_error(self, error):
        self.mirror.log_event("Error", f"Audio Error: {error}")
        self.status_label.setText("Audio Error")

    # Gemini Slots
    def handle_gemini_response(self, text):
        self.mirror.log_event("Gemini", text.strip())
        # Strip "Echo: " if using the dummy loop, but for real Gemini it will be raw text.
        # For now, let's just speak it.
        clean_text = text.replace("Echo: ", "").strip()
        self.speak(clean_text)

    def handle_gemini_error(self, text):
        self.mirror.log_event("Error", f"Gemini Error: {text}")
