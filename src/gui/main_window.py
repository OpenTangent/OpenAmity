import sys
import re
import logging
import threading
from datetime import datetime
import markdown
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                               QPushButton, QTextEdit, QProgressBar,
                               QMenu, QStackedLayout)
from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtGui import QKeyEvent, QAction, QTextCursor, QTextBlockFormat

from gui.visualizer import SoundWaveVisualizer
from gui.spellcheck import SpellCheckHighlighter
from gui.logger_formatter import QtLoggingHandler, StreamLogger

from core.orchestrator import AmityOrchestrator
from core.settings_manager import SettingsManager

class PromptTextEdit(QTextEdit):
    returnPressed = Signal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("Type a message...")
        self.setStyleSheet("background-color: #111; color: #FFF; border: 1px solid #444; padding: 10px; border-radius: 5px; font-size: 14px;")
        self.setFixedHeight(45)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.textChanged.connect(self.adjust_height)
        
        self.highlighter = SpellCheckHighlighter(self.document())

    def adjust_height(self):
        doc_height = int(self.document().size().height()) + 20
        max_height = 120 # ~4-5 lines
        new_height = min(doc_height, max_height)
        new_height = max(45, new_height)
        self.setFixedHeight(new_height)
        
        if doc_height > max_height:
            self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        else:
            self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key_Return or event.key() == Qt.Key_Enter:
            if event.modifiers() == Qt.ShiftModifier:
                super().keyPressEvent(event)
            else:
                self.returnPressed.emit()
        else:
            super().keyPressEvent(event)

class MainWindow(QMainWindow):
    # Cross-thread UI updates
    ui_append_conversation = Signal(str, str)
    ui_set_busy = Signal(bool, bool)
    ui_set_amplitude = Signal(float)
    ui_shutdown_complete = Signal()

    def __init__(self, orchestrator=None):
        super().__init__()
        self.setWindowTitle("Open Amity")
        self.resize(1000, 600)

        # Setup sys.stdout and sys.stderr redirection
        self.stdout_logger = StreamLogger(sys.stdout)
        self.stderr_logger = StreamLogger(sys.stderr)
        sys.stdout = self.stdout_logger
        sys.stderr = self.stderr_logger
        self.stdout_logger.new_message.connect(self.append_to_console)
        self.stderr_logger.new_message.connect(self.append_to_console)

        self.qt_logger = QtLoggingHandler()
        
        settings = SettingsManager()
        if settings.get("core.logging.show-debug", False):
            self.qt_logger.setLevel(logging.DEBUG)
        else:
            self.qt_logger.setLevel(logging.INFO)
            

        # Early log flushing moved down after console_log creation

        # Central widget and layout
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        
        self.stacked_layout = QStackedLayout(self.central_widget)
        self.stacked_layout.setContentsMargins(0, 0, 0, 0)
        
        # Main View with Console Split
        self.split_view = QWidget()
        self.split_layout = QHBoxLayout(self.split_view)
        self.split_layout.setContentsMargins(0, 0, 0, 0)
        self.split_layout.setSpacing(0)

        # Main Chat View
        self.chat_view = QWidget()
        self.main_layout = QVBoxLayout(self.chat_view)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)
        
        self.split_layout.addWidget(self.chat_view, 2)
        
        # Settings View
        from gui.settings_panel import SettingsPanelWidget
            
        self.settings_panel = SettingsPanelWidget()
        self.settings_panel.close_requested.connect(self.hide_settings)
        self.settings_panel.wizard_finished.connect(self.hide_settings)
        
        self.stacked_layout.addWidget(self.split_view)
        self.stacked_layout.addWidget(self.settings_panel)
        
        # Top indicators
        self.loading_bar = QProgressBar()
        self.loading_bar.setMaximumHeight(3)
        self.loading_bar.setTextVisible(False)
        self.loading_bar.setRange(0, 0) # Indeterminate
        self.loading_bar.hide()
        self.loading_bar.setStyleSheet("""
            QProgressBar { border: none; background-color: transparent; }
            QProgressBar::chunk { background-color: #00FFC8; }
        """)
        self.main_layout.addWidget(self.loading_bar)

        self.visualizer = SoundWaveVisualizer()
        self.visualizer.hide()
        self.main_layout.addWidget(self.visualizer)

        # Header Bar
        self.header_widget = QWidget()
        self.header_widget.setStyleSheet("background-color: #1a1a1a;")
        self.header_layout = QHBoxLayout(self.header_widget)
        self.header_layout.setContentsMargins(10, 5, 10, 5)
        self.header_layout.addStretch()
        
        self.hamburger_btn = QPushButton("☰")
        self.hamburger_btn.setFixedSize(30, 30)
        self.hamburger_btn.setStyleSheet("QPushButton { background-color: transparent; color: #FFF; border: none; font-size: 20px; } QPushButton::menu-indicator { image: none; }")
        
        self.hamburger_menu = QMenu(self)
        self.hamburger_menu.setStyleSheet("QMenu { background-color: #222; color: #FFF; border: 1px solid #444; } QMenu::item:selected { background-color: #333; }")
        
        sections = [
            "Basic Agent Settings",
            "Agent Values and Goals",
            "Agent Voice",
            "Gemini Setup",
            "Social Accounts",
            "System Settings"
        ]
        
        for i, title in enumerate(sections):
            action = QAction(title, self)
            action.triggered.connect(lambda checked=False, idx=i: self.show_settings_section(idx))
            self.hamburger_menu.addAction(action)
            
        self.hamburger_menu.addSeparator()
        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        self.hamburger_menu.addAction(exit_action)
        
        self.hamburger_btn.setMenu(self.hamburger_menu)
        self.header_layout.addWidget(self.hamburger_btn)
        
        self.main_layout.addWidget(self.header_widget)

        # Conversation Log
        self.conversation_log = QTextEdit()
        self.conversation_log.setReadOnly(True)
        self.conversation_log.setStyleSheet("background-color: #1a1a1a; color: #FFF; border: none; padding: 20px; font-family: 'Ubuntu Light'; font-weight: 300; font-size: 16px;")
        scroll_bar = self.conversation_log.verticalScrollBar()
        scroll_bar.rangeChanged.connect(lambda min, max: scroll_bar.setValue(max))
        self.main_layout.addWidget(self.conversation_log, 1)

        # Console Log
        self.console_log = QTextEdit()
        self.console_log.setReadOnly(True)
        self.console_log.setStyleSheet("background-color: #000; color: #0F0; border: none; font-family: 'Ubuntu Mono'; font-size: 12px; padding: 10px;")
        self.console_log.hide()
        self.split_layout.addWidget(self.console_log, 1)

        self.qt_logger.signals.new_message.connect(self.append_to_console)
        logging.getLogger().addHandler(self.qt_logger)
        
        # Flush early logs into the now-existing console
        from core.logger_config import EARLY_LOG_BUFFER, early_buffer_handler
        for record in EARLY_LOG_BUFFER:
            if record.levelno >= self.qt_logger.level:
                self.qt_logger.handle(record)
        
        logging.getLogger().removeHandler(early_buffer_handler)
        EARLY_LOG_BUFFER.clear()

        # Footer
        self.footer_widget = QWidget()
        self.footer_widget.setStyleSheet("background-color: #222; border-top: 1px solid #333;")
        self.footer_layout = QHBoxLayout(self.footer_widget)
        self.footer_layout.setContentsMargins(20, 10, 20, 10)
        self.footer_layout.setSpacing(10)
        
        self.text_input = PromptTextEdit()
        self.text_input.returnPressed.connect(self.send_text_prompt)
        
        self.footer_layout.addWidget(self.text_input, 1)
        
        self.btn_send = QPushButton("Send")
        self.btn_send.setMinimumSize(80, 40)
        self.btn_send.clicked.connect(self.send_text_prompt)
        self.btn_send.setStyleSheet("background-color: #333; color: #FFF; border: 1px solid #555; border-radius: 5px;")
        self.footer_layout.addWidget(self.btn_send)
        
        self.btn_mic = QPushButton("🎤")
        self.btn_mic.setFixedSize(40, 40)
        self.btn_mic.clicked.connect(self.toggle_mic)
        self.btn_mic.setStyleSheet("background-color: #333; color: #FFF; border: 1px solid #555; border-radius: 5px; font-size: 18px; font-family: 'Ubuntu', 'Noto Color Emoji', 'Twemoji Mozilla', emoji;")
        self.footer_layout.addWidget(self.btn_mic)
        
        self.btn_mute = QPushButton()
        self.btn_mute.setFixedSize(40, 40)
        self.btn_mute.setCheckable(True)
        self.btn_mute.clicked.connect(self.toggle_mute)
        is_muted = settings.get("core.mute", False)
        self.btn_mute.setChecked(is_muted)
        if is_muted:
            self.btn_mute.setText("🔇")
            self.btn_mute.setStyleSheet("background-color: #1a1a1a; color: #FFF; border: 1px inset #555; border-radius: 5px; font-size: 18px; font-family: 'Ubuntu', 'Noto Color Emoji', 'Twemoji Mozilla', emoji;")
        else:
            self.btn_mute.setText("🔊")
            self.btn_mute.setStyleSheet("background-color: #333; color: #FFF; border: 1px solid #555; border-radius: 5px; font-size: 18px; font-family: 'Ubuntu', 'Noto Color Emoji', 'Twemoji Mozilla', emoji;")
        self.footer_layout.addWidget(self.btn_mute)
        
        self.main_layout.addWidget(self.footer_widget)

        # Core Systems Setup
        self.orchestrator = orchestrator if orchestrator else AmityOrchestrator()
        
        # Connect orchestrator events to UI threads via signals
        self.orchestrator.on_message_appended.connect(lambda sender, text: self.ui_append_conversation.emit(sender, text))
        self.orchestrator.on_busy_state_changed.connect(lambda busy, speaking: self.ui_set_busy.emit(busy, speaking))
        self.orchestrator.on_amplitude_emitted.connect(lambda amp: self.ui_set_amplitude.emit(amp))
        self.orchestrator.on_shutdown_complete.connect(lambda: self.ui_shutdown_complete.emit())

        self.ui_append_conversation.connect(self.append_to_conversation)
        self.ui_set_busy.connect(self.set_busy_state)
        self.ui_set_amplitude.connect(self.visualizer.set_amplitude)
        self.ui_shutdown_complete.connect(self.close)
        
        self.text_input.textChanged.connect(self.orchestrator.user_interacted)
        self.settings_panel.wizard_finished.connect(self.orchestrator.init_worker)
        self.settings_panel.settings_saved.connect(self.orchestrator.reload_settings)

        # Styling
        self.setStyleSheet("""
            QMainWindow { background-color: #1a1a1a; color: #FFF; }
        """)
        
        self.check_first_launch()

    def check_first_launch(self):
        if self.settings_panel.settings.get("core.first-run", False):
            self.settings_panel.set_wizard_mode(True)
            self.settings_panel.set_section(0)
            self.stacked_layout.setCurrentIndex(1)
            
    def show_settings_section(self, index):
        self.settings_panel.set_wizard_mode(False)
        self.settings_panel.set_section(index)
        self.stacked_layout.setCurrentIndex(1)
        
    def hide_settings(self):
        self.stacked_layout.setCurrentIndex(0)

    def closeEvent(self, event):
        if hasattr(self, '_shutting_down') and self._shutting_down:
            event.accept()
            return

        if getattr(self.orchestrator, 'session_fatigue_tokens', 0) > 10000:
            logging.info("System: High fatigue detected, running sleep cycle before exit.")
            event.ignore()
            self._shutting_down = True
            self.append_to_conversation("System", "Consolidating memories for graceful shutdown... please wait.")
            self.orchestrator.shutdown(force_sleep=True)
        else:
            logging.info("System: Shutting down gracefully...")
            self.orchestrator.shutdown()
            event.accept()

    def keyPressEvent(self, event: QKeyEvent):
        self.orchestrator.user_interacted()
        if event.key() == Qt.Key_QuoteLeft or event.key() == Qt.Key_AsciiTilde: # Tilde / Backtick
            if self.text_input.hasFocus():
                super().keyPressEvent(event)
            else:
                self.console_log.setVisible(not self.console_log.isVisible())
        else:
            super().keyPressEvent(event)

    def append_to_console(self, text):
        if not hasattr(self, 'console_log'):
            return
        self.console_log.append(text)
        scroll_bar = self.console_log.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.maximum())

    def append_to_conversation(self, sender, text):
        name_color = "#00FFC8" if sender == "User" else "#FFA500"
        text_color = "#FFFFFF" if sender == "Agent" else "#808080"
        timestamp = datetime.now().strftime("%H:%M")
        
        if sender == "User":
            safe_text = text.replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
            formatted_text = safe_text
        else:
            formatted_text = markdown.markdown(text, extensions=['fenced_code', 'tables'])

        html = f"""
        <div style='margin-bottom: 10px; text-align: left;'>
            <span style='color: {name_color}; font-weight: bold;'>{sender}:</span>
            <div style='margin-top: 5px; color: {text_color};'>
                {formatted_text}
                <table width="100%" cellpadding="0" cellspacing="0" style="margin-top: 2px;">
                    <tr><td align="right">
                        <span style='color: #666; font-size: 12px;'>{timestamp}</span>
                    </td></tr>
                </table>
            </div>
        </div>
        """
        cursor = self.conversation_log.textCursor()
        cursor.movePosition(QTextCursor.End)
        
        if not self.conversation_log.document().isEmpty():
            block_format = QTextBlockFormat()
            cursor.insertBlock(block_format)
            
        self.conversation_log.setTextCursor(cursor)
        cursor.insertHtml(html)

    def set_busy_state(self, busy: bool, speaking: bool = False):
        import logging
        logging.debug(f"main_window.set_busy_state called with busy={busy}, speaking={speaking}")
        if busy:
            self.btn_mic.setText("🟥")
            self.btn_mic.setStyleSheet("background-color: #AA0000; color: #FFF; border: 1px solid #FF5555; border-radius: 5px; font-size: 18px; font-family: 'Ubuntu', 'Noto Color Emoji', 'Twemoji Mozilla', emoji;")
            
            if speaking:
                self.loading_bar.hide()
                self.visualizer.show()
                self.visualizer.set_active(True)
            else:
                self.visualizer.set_active(False)
                self.visualizer.hide()
                self.loading_bar.show()
        else:
            self.btn_mic.setText("🎤")
            self.btn_mic.setStyleSheet("background-color: #333; color: #FFF; border: 1px solid #555; border-radius: 5px; font-size: 18px; font-family: 'Ubuntu', 'Noto Color Emoji', 'Twemoji Mozilla', emoji;")
            self.loading_bar.hide()
            self.visualizer.set_active(False)
            self.visualizer.hide()

    def toggle_mic(self):
        self.orchestrator.toggle_mic()

    def toggle_mute(self, checked):
        self.orchestrator.settings_manager.set("core.mute", checked)
        self.orchestrator.settings_manager.save()
        if checked:
            self.btn_mute.setText("🔇")
            self.btn_mute.setStyleSheet("background-color: #1a1a1a; color: #FFF; border: 1px inset #555; border-radius: 5px; font-size: 18px; font-family: 'Ubuntu', 'Noto Color Emoji', 'Twemoji Mozilla', emoji;")
        else:
            self.btn_mute.setText("🔊")
            self.btn_mute.setStyleSheet("background-color: #333; color: #FFF; border: 1px solid #555; border-radius: 5px; font-size: 18px; font-family: 'Ubuntu', 'Noto Color Emoji', 'Twemoji Mozilla', emoji;")

    def send_text_prompt(self):
        text = self.text_input.toPlainText().strip()
        if not text:
            return
        
        self.text_input.clear()
        self.orchestrator.process_text_input(text)

