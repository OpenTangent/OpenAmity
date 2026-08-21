import markdown
from datetime import datetime
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                               QPushButton, QTextEdit, QProgressBar)
from PySide6.QtCore import Signal
from PySide6.QtGui import QTextCursor, QTextBlockFormat

from gui.visualizer import SoundWaveVisualizer
from core.orchestrator import AmityOrchestrator
from core.config_manager import ConfigManager

try:
    from gui.theme import PRIMARY_ACCENT_COLOR, SECONDARY_ACCENT_COLOR
except ImportError:
    PRIMARY_ACCENT_COLOR = "#a12924"
    SECONDARY_ACCENT_COLOR = "#f7e3a5"


class AgentView(QWidget):
    ui_message_appended = Signal(str, str)
    ui_busy_state_changed = Signal(bool, bool)
    ui_amplitude_emitted = Signal(float)

    def __init__(self, orchestrator: AmityOrchestrator, parent=None):
        super().__init__(parent)
        self.orchestrator = orchestrator
        self.settings_manager = orchestrator.settings_manager

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # Top indicators
        self.loading_bar = QProgressBar()
        self.loading_bar.setMaximumHeight(3)
        self.loading_bar.setTextVisible(False)
        self.loading_bar.setRange(0, 0)
        self.loading_bar.hide()
        self.loading_bar.setStyleSheet(f"""
            QProgressBar {{ border: none; background-color: transparent; }}
            QProgressBar::chunk {{ background-color: {PRIMARY_ACCENT_COLOR}; }}
        """)
        self.main_layout.addWidget(self.loading_bar)

        self.visualizer = SoundWaveVisualizer()
        self.visualizer.hide()
        self.main_layout.addWidget(self.visualizer)

        # Conversation Log
        self.conversation_log = QTextEdit()
        self.conversation_log.setReadOnly(True)
        self.conversation_log.setStyleSheet(
            "background-color: #1a1a1a; color: #FFF; border: none; padding: 20px; font-family: 'Ubuntu Light'; font-weight: 300; font-size: 16px;")
        scroll_bar = self.conversation_log.verticalScrollBar()
        scroll_bar.rangeChanged.connect(
            lambda min, max: scroll_bar.setValue(max))
        self.main_layout.addWidget(self.conversation_log, 1)

        # Console Log
        self.console_log = QTextEdit()
        self.console_log.setReadOnly(True)
        self.console_log.setStyleSheet(
            "background-color: #000; color: #0F0; border: none; font-family: 'Ubuntu Mono'; font-size: 12px; padding: 10px;")
        self.console_log.hide()

        # Footer
        self.footer_widget = QWidget()
        self.footer_widget.setStyleSheet(
            "background-color: #222; border-top: 1px solid #333;")
        self.footer_layout = QHBoxLayout(self.footer_widget)
        self.footer_layout.setContentsMargins(20, 10, 20, 10)
        self.footer_layout.setSpacing(10)

        from gui.main_window import PromptTextEdit
        self.text_input = PromptTextEdit()
        self.text_input.returnPressed.connect(self.send_text_prompt)
        self.text_input.textChanged.connect(self.orchestrator.user_interacted)
        self.footer_layout.addWidget(self.text_input, 1)

        self.btn_send = QPushButton("Send")
        self.btn_send.setMinimumSize(80, 40)
        self.btn_send.clicked.connect(self.send_text_prompt)
        self.btn_send.setStyleSheet(
            "background-color: #333; color: #FFF; border: 1px solid #555; border-radius: 5px;")
        self.footer_layout.addWidget(self.btn_send)

        self.btn_mic = QPushButton("🎤")
        self.btn_mic.setFixedSize(40, 40)
        self.btn_mic.clicked.connect(self.toggle_mic)
        self.btn_mic.setStyleSheet(
            "background-color: #333; color: #FFF; border: 1px solid #555; border-radius: 5px; font-size: 18px; font-family: 'Ubuntu', 'Noto Color Emoji', 'Twemoji Mozilla', emoji;")
        self.footer_layout.addWidget(self.btn_mic)

        self.btn_mute = QPushButton()
        self.btn_mute.setFixedSize(40, 40)
        self.btn_mute.setCheckable(True)
        self.btn_mute.clicked.connect(self.toggle_mute)
        is_muted = self.settings_manager.get("core.mute", False)
        self.btn_mute.setChecked(is_muted)
        if is_muted:
            self.btn_mute.setText("🔇")
            self.btn_mute.setStyleSheet(
                "background-color: #1a1a1a; color: #FFF; border: 1px inset #555; border-radius: 5px; font-size: 18px; font-family: 'Ubuntu', 'Noto Color Emoji', 'Twemoji Mozilla', emoji;")
        else:
            self.btn_mute.setText("🔊")
            self.btn_mute.setStyleSheet(
                "background-color: #333; color: #FFF; border: 1px solid #555; border-radius: 5px; font-size: 18px; font-family: 'Ubuntu', 'Noto Color Emoji', 'Twemoji Mozilla', emoji;")
        self.footer_layout.addWidget(self.btn_mute)

        self.main_layout.addWidget(self.footer_widget)

        # Connect signals through PySide6 thread-safe signals
        self.ui_message_appended.connect(self.append_to_conversation)
        self.orchestrator.on_message_appended.connect(
            lambda s, t: self.ui_message_appended.emit(s, t))

        self.ui_busy_state_changed.connect(self.set_busy_state)
        self.orchestrator.on_busy_state_changed.connect(
            lambda b, s: self.ui_busy_state_changed.emit(b, s))

        self.ui_amplitude_emitted.connect(self.visualizer.set_amplitude)
        self.orchestrator.on_amplitude_emitted.connect(
            self.ui_amplitude_emitted.emit)

    def set_busy_state(self, busy: bool, speaking: bool = False):
        if busy:
            self.btn_mic.setText("🟥")
            self.btn_mic.setStyleSheet(
                "background-color: #AA0000; color: #FFF; border: 1px solid #FF5555; border-radius: 5px; font-size: 18px; font-family: 'Ubuntu', 'Noto Color Emoji', 'Twemoji Mozilla', emoji;")

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
            self.btn_mic.setStyleSheet(
                "background-color: #333; color: #FFF; border: 1px solid #555; border-radius: 5px; font-size: 18px; font-family: 'Ubuntu', 'Noto Color Emoji', 'Twemoji Mozilla', emoji;")
            self.loading_bar.hide()
            self.visualizer.set_active(False)
            self.visualizer.hide()

    def toggle_mic(self):
        self.orchestrator.toggle_mic()

    def toggle_mute(self, checked):
        self.settings_manager.set("core.mute", checked)
        self.settings_manager.save()
        if checked:
            self.btn_mute.setText("🔇")
            self.btn_mute.setStyleSheet(
                "background-color: #1a1a1a; color: #FFF; border: 1px inset #555; border-radius: 5px; font-size: 18px; font-family: 'Ubuntu', 'Noto Color Emoji', 'Twemoji Mozilla', emoji;")
        else:
            self.btn_mute.setText("🔊")
            self.btn_mute.setStyleSheet(
                "background-color: #333; color: #FFF; border: 1px solid #555; border-radius: 5px; font-size: 18px; font-family: 'Ubuntu', 'Noto Color Emoji', 'Twemoji Mozilla', emoji;")

    def send_text_prompt(self):
        text = self.text_input.toPlainText().strip()
        if not text:
            return
        self.text_input.clear()
        self.orchestrator.process_text_input(text)

    def append_to_console(self, text):
        self.console_log.append(text)
        scroll_bar = self.console_log.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.maximum())

    def append_to_conversation(self, sender, text):
        user_name = "User"
        try:
            config = ConfigManager()
            user_name = config.get("user-full-name", "").strip() or "User"
        except Exception:
            pass

        if sender in ["User", user_name]:
            display_name = user_name
            name_color = PRIMARY_ACCENT_COLOR
            text_color = "#808080"
        elif sender.startswith("System"):
            display_name = sender
            name_color = "#808080"
            text_color = "#808080"
        else:
            display_name = self.settings_manager.get(
                "core.agent.name", "Agent")
            name_color = SECONDARY_ACCENT_COLOR
            text_color = "#FFFFFF"
        timestamp = datetime.now().strftime("%H:%M")

        if sender in ["User", user_name]:
            safe_text = text.replace("<", "&lt;").replace(
                ">", "&gt;").replace("\n", "<br>")
            formatted_text = safe_text
        else:
            formatted_text = markdown.markdown(
                text, extensions=['fenced_code', 'tables'])

        html = f"""
        <div style='margin-bottom: 10px; text-align: left;'>
            <span style='color: {name_color}; font-weight: bold;'>{display_name}</span>
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
