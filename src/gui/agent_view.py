from datetime import datetime
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                               QPushButton, QTextEdit, QTextBrowser, QProgressBar)
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QTextCursor, QTextBlockFormat

from gui.visualizer import SoundWaveVisualizer
from gui.chat_formatter import (
    setup_chat_browser,
    render_markdown_to_html,
    render_user_message_content,
    wrap_in_isolated_container,
    insert_message_into_log,
)
from core.orchestrator import AmityOrchestrator
from core.config_manager import ConfigManager
from gui.tool_pips import ToolPipManager

try:
    from gui.theme import PRIMARY_ACCENT_COLOR, SECONDARY_ACCENT_COLOR, MODERN_SCROLLBAR_STYLE
except ImportError:
    PRIMARY_ACCENT_COLOR = "#a12924"
    SECONDARY_ACCENT_COLOR = "#f7e3a5"
    MODERN_SCROLLBAR_STYLE = ""


class AgentView(QWidget):
    ui_message_appended = Signal(str, str)
    ui_busy_state_changed = Signal(bool, bool)
    ui_amplitude_emitted = Signal(float)
    ui_paused_state_changed = Signal(bool)
    ui_pause_pending_changed = Signal(bool)
    ui_tool_started = Signal(str, str, str, str, str, bool)
    ui_tool_finished = Signal(str)

    def __init__(self, orchestrator: AmityOrchestrator, parent=None):
        super().__init__(parent)
        self.orchestrator = orchestrator
        self.settings_manager = orchestrator.settings_manager
        self.config_manager = ConfigManager()

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
        self.conversation_log = QTextBrowser()
        setup_chat_browser(self.conversation_log)
        self.conversation_log.setOpenExternalLinks(True)
        self.main_layout.addWidget(self.conversation_log, 1)

        # Floating Tool-Call Pip Manager
        self.pip_manager = ToolPipManager(self.conversation_log, self)

        # Console Log
        self.console_log = QTextEdit()
        self.console_log.setReadOnly(True)
        self.console_log.setStyleSheet(f"""
            QTextEdit {{
                background-color: #000;
                color: #0F0;
                border: none;
                font-family: 'Ubuntu Mono';
                font-size: 12px;
                padding: 10px;
            }}
            {MODERN_SCROLLBAR_STYLE}
        """)
        self.console_log.hide()

        # Footer
        self.footer_widget = QWidget()
        self.footer_widget.setObjectName("footerWidget")
        self.footer_widget.setStyleSheet(
            "QWidget#footerWidget { background-color: #222; border-top: 1px solid #333; }")
        self.footer_layout = QHBoxLayout(self.footer_widget)
        self.footer_layout.setContentsMargins(20, 10, 20, 10)
        self.footer_layout.setSpacing(10)

        # Pause/Play Button (placed to the left of the user input textbox)
        self.btn_pause = QPushButton()
        self.btn_pause.setFixedSize(40, 40)
        self.btn_pause.setCursor(Qt.PointingHandCursor)
        self.btn_pause.clicked.connect(self.toggle_pause)
        self.footer_layout.addWidget(self.btn_pause)

        from gui.main_window import PromptTextEdit
        self.text_input = PromptTextEdit()
        self.text_input.returnPressed.connect(self.send_text_prompt)
        self.text_input.textChanged.connect(self.orchestrator.user_interacted)
        self.footer_layout.addWidget(self.text_input, 1)

        self.btn_send = QPushButton("⌯⌲")
        self.btn_send.setToolTip("Send")
        self.btn_send.setMinimumSize(80, 40)
        self.btn_send.clicked.connect(self.send_text_prompt)
        self.btn_send.setStyleSheet(
            "QPushButton { background-color: #333; color: #FFF; border: 1px solid #555; border-radius: 5px; font-size: 18px; font-family: 'Ubuntu', 'DejaVu Sans', 'Noto Sans Symbols', 'Noto Sans Symbols2', 'FreeSans', sans-serif; padding-bottom: 4px; } QPushButton:hover { background-color: #444; }")
        self.footer_layout.addWidget(self.btn_send)

        self.btn_mic = QPushButton("⏺")
        self.btn_mic.setToolTip("Mic")
        self.btn_mic.setFixedSize(40, 40)
        self.btn_mic.clicked.connect(self.toggle_mic)
        self.btn_mic.setStyleSheet(
            "QPushButton { background-color: #333; color: #FFF; border: 1px solid #555; border-radius: 5px; font-size: 16px; font-family: 'Ubuntu', 'DejaVu Sans', 'Noto Sans Symbols', 'Noto Sans Symbols2', 'FreeSans', sans-serif; } QPushButton:hover { background-color: #444; }")
        self.footer_layout.addWidget(self.btn_mic)

        self.btn_mute = QPushButton()
        self.btn_mute.setFixedSize(40, 40)
        self.btn_mute.setCheckable(True)
        self.btn_mute.clicked.connect(self.toggle_mute)
        is_muted = self.settings_manager.get("core.mute", False)
        self.btn_mute.setChecked(is_muted)
        if is_muted:
            self.btn_mute.setText("🔇︎")
            self.btn_mute.setToolTip("Unmute Agent")
            self.btn_mute.setStyleSheet(
                "QPushButton { background-color: #1a1a1a; color: #FFF; border: 1px inset #555; border-radius: 5px; font-size: 16px; font-family: 'Ubuntu', 'DejaVu Sans', 'Noto Sans Symbols', 'Noto Sans Symbols2', 'FreeSans', sans-serif; } QPushButton:hover { background-color: #2a2a2a; }")
        else:
            self.btn_mute.setText("🔊︎")
            self.btn_mute.setToolTip("Mute Agent")
            self.btn_mute.setStyleSheet(
                "QPushButton { background-color: #333; color: #FFF; border: 1px solid #555; border-radius: 5px; font-size: 16px; font-family: 'Ubuntu', 'DejaVu Sans', 'Noto Sans Symbols', 'Noto Sans Symbols2', 'FreeSans', sans-serif; } QPushButton:hover { background-color: #444; }")
        self.footer_layout.addWidget(self.btn_mute)

        self.main_layout.addWidget(self.footer_widget)

        # Connect signals through PySide6 thread-safe signals
        self.ui_message_appended.connect(self.append_to_conversation)
        self.orchestrator.on_message_appended.connect(self.ui_message_appended.emit)

        self.ui_busy_state_changed.connect(self.set_busy_state)
        self.orchestrator.on_busy_state_changed.connect(self.ui_busy_state_changed.emit)

        self.ui_amplitude_emitted.connect(self.visualizer.set_amplitude)
        self.orchestrator.on_amplitude_emitted.connect(self.ui_amplitude_emitted.emit)

        self.ui_paused_state_changed.connect(self.set_paused_state)
        self.orchestrator.on_paused_state_changed.connect(self.ui_paused_state_changed.emit)

        self.ui_pause_pending_changed.connect(self.set_pause_pending)
        self.orchestrator.on_pause_pending.connect(self.ui_pause_pending_changed.emit)

        self.ui_tool_started.connect(self.pip_manager.add_pip)
        self.orchestrator.on_tool_started.connect(self.ui_tool_started.emit)

        self.ui_tool_finished.connect(self.pip_manager.complete_async_pip)
        self.orchestrator.on_tool_finished.connect(self.ui_tool_finished.emit)

        self.set_paused_state(self.orchestrator.is_paused)

    def cleanup(self):
        try:
            self.orchestrator.on_message_appended.disconnect(self.ui_message_appended.emit)
            self.orchestrator.on_busy_state_changed.disconnect(self.ui_busy_state_changed.emit)
            self.orchestrator.on_amplitude_emitted.disconnect(self.ui_amplitude_emitted.emit)
            self.orchestrator.on_paused_state_changed.disconnect(self.ui_paused_state_changed.emit)
            self.orchestrator.on_pause_pending.disconnect(self.ui_pause_pending_changed.emit)
            self.orchestrator.on_tool_started.disconnect(self.ui_tool_started.emit)
            self.orchestrator.on_tool_finished.disconnect(self.ui_tool_finished.emit)
        except Exception:
            pass
        if hasattr(self, 'pip_manager') and self.pip_manager:
            self.pip_manager.clear()

    def set_active_tab(self, is_selected: bool):
        """Notifies pip manager of active tab selection state."""
        if hasattr(self, 'pip_manager') and self.pip_manager:
            self.pip_manager.set_active(is_selected)

    def toggle_pause(self):
        was_paused = getattr(self.orchestrator, 'is_paused', False)
        self.orchestrator.toggle_pause()
        if was_paused and hasattr(self.orchestrator, 'pulse_engine') and hasattr(self.orchestrator.pulse_engine, 'notify_unpaused'):
            self.orchestrator.pulse_engine.notify_unpaused()

    def set_pause_pending(self, pending: bool):
        if pending:
            self.btn_pause.setEnabled(False)
            self.text_input.setEnabled(False)
            self.btn_send.setEnabled(False)
            self.btn_mic.setEnabled(False)
            self.text_input.setPlaceholderText("Consolidating memory before pausing...")

    def set_paused_state(self, is_paused: bool):
        self.btn_pause.setEnabled(True)
        if is_paused:
            self.btn_pause.setText("▶")
            self.btn_pause.setToolTip("Resume Agent")
            self.btn_pause.setStyleSheet(
                "QPushButton { background-color: #4a3b10; color: #FFD700; border: 1px solid #AA8800; border-radius: 5px; font-size: 16px; font-family: 'Ubuntu', 'DejaVu Sans', 'Noto Sans Symbols', 'Noto Sans Symbols2', 'FreeSans', sans-serif; } QPushButton:hover { background-color: #5c4914; }"
            )
            self.text_input.setEnabled(False)
            self.text_input.setPlaceholderText("Agent is paused...")
            self.btn_send.setEnabled(False)
            self.btn_mic.setEnabled(False)
            self.loading_bar.hide()
            self.visualizer.set_active(False)
            self.visualizer.hide()
        else:
            self.btn_pause.setText("❚❚")
            self.btn_pause.setToolTip("Pause Agent")
            self.btn_pause.setStyleSheet(
                "QPushButton { background-color: #333; color: #FFF; border: 1px solid #555; border-radius: 5px; font-size: 13px; font-family: 'Ubuntu', 'DejaVu Sans', 'Noto Sans Symbols', 'Noto Sans Symbols2', 'FreeSans', sans-serif; } QPushButton:hover { background-color: #444; }"
            )
            self.text_input.setPlaceholderText("Type a message...")
            if getattr(self.orchestrator, 'is_busy', False) is True or getattr(self.orchestrator, 'is_thinking', False) is True:
                self.btn_mic.setEnabled(True)
                self.set_busy_state(True)
            else:
                self.text_input.setEnabled(True)
                self.btn_send.setEnabled(True)
                self.btn_mic.setEnabled(True)

    def set_busy_state(self, busy: bool, speaking: bool = False):
        if self.orchestrator.is_paused:
            self.btn_mic.setText("⏺")
            self.btn_mic.setToolTip("Mic")
            self.btn_mic.setStyleSheet(
                "QPushButton { background-color: #333; color: #FFF; border: 1px solid #555; border-radius: 5px; font-size: 16px; font-family: 'Ubuntu', 'DejaVu Sans', 'Noto Sans Symbols', 'Noto Sans Symbols2', 'FreeSans', sans-serif; } QPushButton:hover { background-color: #444; }")
            self.loading_bar.hide()
            self.visualizer.set_active(False)
            self.visualizer.hide()
            return

        if busy:
            self.btn_mic.setText("⏹")
            self.btn_mic.setToolTip("Stop Agent")
            self.btn_mic.setStyleSheet(
                "QPushButton { background-color: #AA0000; color: #FFF; border: 1px solid #FF5555; border-radius: 5px; font-size: 16px; font-family: 'Ubuntu', 'DejaVu Sans', 'Noto Sans Symbols', 'Noto Sans Symbols2', 'FreeSans', sans-serif; } QPushButton:hover { background-color: #CC0000; }")

            if speaking:
                self.loading_bar.hide()
                self.visualizer.show()
                self.visualizer.set_active(True)
            else:
                self.visualizer.set_active(False)
                self.visualizer.hide()
                self.loading_bar.show()
        else:
            self.btn_mic.setText("⏺")
            self.btn_mic.setToolTip("Mic")
            self.btn_mic.setStyleSheet(
                "QPushButton { background-color: #333; color: #FFF; border: 1px solid #555; border-radius: 5px; font-size: 16px; font-family: 'Ubuntu', 'DejaVu Sans', 'Noto Sans Symbols', 'Noto Sans Symbols2', 'FreeSans', sans-serif; } QPushButton:hover { background-color: #444; }")
            self.loading_bar.hide()
            self.visualizer.set_active(False)
            self.visualizer.hide()

    def toggle_mic(self):
        if self.orchestrator.is_paused:
            return
        self.orchestrator.toggle_mic()

    def toggle_mute(self, checked):
        self.settings_manager.set("core.mute", checked)
        self.settings_manager.save()
        if checked:
            self.btn_mute.setText("🔇︎")
            self.btn_mute.setToolTip("Unmute Agent")
            self.btn_mute.setStyleSheet(
                "QPushButton { background-color: #1a1a1a; color: #FFF; border: 1px inset #555; border-radius: 5px; font-size: 16px; font-family: 'Ubuntu', 'DejaVu Sans', 'Noto Sans Symbols', 'Noto Sans Symbols2', 'FreeSans', sans-serif; } QPushButton:hover { background-color: #2a2a2a; }")
        else:
            self.btn_mute.setText("🔊︎")
            self.btn_mute.setToolTip("Mute Agent")
            self.btn_mute.setStyleSheet(
                "QPushButton { background-color: #333; color: #FFF; border: 1px solid #555; border-radius: 5px; font-size: 16px; font-family: 'Ubuntu', 'DejaVu Sans', 'Noto Sans Symbols', 'Noto Sans Symbols2', 'FreeSans', sans-serif; } QPushButton:hover { background-color: #444; }")

    def send_text_prompt(self):
        if self.orchestrator.is_paused:
            return
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
            user_name = self.config_manager.get("user-full-name", "").strip() or "User"
        except Exception:
            pass

        if sender in ["User", user_name]:
            display_name = user_name
            name_color = PRIMARY_ACCENT_COLOR
            text_color = "#888888"
            bg_color = "#19191b"
            border_color = "#28282c"
            formatted_text = render_user_message_content(text)
        elif sender.startswith("System"):
            display_name = sender
            name_color = "#666666"
            text_color = "#777777"
            bg_color = "#171718"
            border_color = "#242426"
            formatted_text = render_markdown_to_html(text)
        else:
            display_name = self.settings_manager.get(
                "core.agent.name", "Agent")
            name_color = SECONDARY_ACCENT_COLOR
            text_color = "#FFFFFF"
            bg_color = "#222225"
            border_color = "#36363b"
            formatted_text = render_markdown_to_html(text)

        timestamp = datetime.now().strftime("%H:%M")

        inner_html = f"""
        <div style="margin-bottom: 4px; text-align: left;">
            <span style="color: {name_color}; font-weight: bold; font-size: 14px;">{display_name}</span>
        </div>
        <div style="color: {text_color}; font-size: 18px; line-height: 1.65;">
            {formatted_text}
        </div>
        <div style="text-align: right; color: #555555; font-size: 11px; margin-top: 6px;">
            {timestamp}
        </div>
        """
        isolated_html = wrap_in_isolated_container(
            inner_html,
            margin_bottom=14,
            bg_color=bg_color,
            border_color=border_color,
            border_radius=8,
            padding="12px 18px",
        )
        insert_message_into_log(self.conversation_log, isolated_html)
