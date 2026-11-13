from datetime import datetime, timezone
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                               QPushButton, QTextEdit, QTextBrowser, QProgressBar)
from PySide6.QtCore import Signal, Qt, QTimer, QUrl
from PySide6.QtGui import QTextCursor, QTextBlockFormat, QDesktopServices

from gui.visualizer import SoundWaveVisualizer
from gui.chat_formatter import (
    setup_chat_browser,
    render_markdown_to_html,
    render_user_message_content,
    render_thought_card_html,
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
    ui_thought_card_updated = Signal(object, str, bool)
    ui_busy_state_changed = Signal(bool, bool)
    ui_amplitude_emitted = Signal(float)
    ui_waveform_emitted = Signal(object)
    ui_paused_state_changed = Signal(bool)
    ui_pause_pending_changed = Signal(bool)
    ui_tool_started = Signal(str, str, str, str, str, bool)
    ui_tool_finished = Signal(str)
    ui_listening_state_changed = Signal(bool)

    def __init__(self, orchestrator: AmityOrchestrator, parent=None):
        super().__init__(parent)
        self.orchestrator = orchestrator
        self.settings_manager = orchestrator.settings_manager
        self.config_manager = ConfigManager()
        self.chat_history_manager = getattr(orchestrator, 'chat_history_manager', None)
        self.BATCH_SIZE = 50
        self.MAX_GUI_MESSAGES = 150
        self._loaded_messages = []
        self._expanded_thought_cards = set()
        self._has_more_history = True
        self._is_loading_history = False
        self._at_bottom = True

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

        # Conversation Log
        self.conversation_log = QTextBrowser()
        self.conversation_log._auto_scroll_on_range_change = False
        setup_chat_browser(self.conversation_log)
        self.conversation_log.setOpenExternalLinks(False)
        self.conversation_log.setOpenLinks(False)
        self.conversation_log.anchorClicked.connect(self.on_anchor_clicked)
        self.conversation_log.verticalScrollBar().valueChanged.connect(self._on_scroll_value_changed)
        self.conversation_log.verticalScrollBar().rangeChanged.connect(self._on_scroll_range_changed)
        self.main_layout.addWidget(self.conversation_log, 1)

        # Oscilloscope Voice Visualizer (positioned at the bottom of the chat log, just above the user input footer)
        self.visualizer = SoundWaveVisualizer()
        self.visualizer.hide()
        self.main_layout.addWidget(self.visualizer)

        # Floating Tool-Call Pip Manager
        self.pip_manager = ToolPipManager(self.conversation_log, self)

        # Console Log (limited to 1,000 blocks for memory conservation)
        self.console_log = QTextEdit()
        self.console_log.setReadOnly(True)
        self.console_log.document().setMaximumBlockCount(1000)
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
        self.btn_pause.clicked.connect(self.on_pause_button_clicked)
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
        is_muted = bool(self.settings_manager.get("core.mute", False))
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

        self.ui_thought_card_updated.connect(self.update_thought_card)
        if hasattr(self.orchestrator, 'on_thought_card_updated'):
            self.orchestrator.on_thought_card_updated.connect(self.ui_thought_card_updated.emit)

        self.ui_busy_state_changed.connect(self.set_busy_state)
        self.orchestrator.on_busy_state_changed.connect(self.ui_busy_state_changed.emit)

        self.ui_amplitude_emitted.connect(self.visualizer.set_amplitude)
        self.orchestrator.on_amplitude_emitted.connect(self.ui_amplitude_emitted.emit)

        self.ui_waveform_emitted.connect(self.visualizer.enqueue_waveform_frames)
        if hasattr(self.orchestrator, 'on_waveform_emitted'):
            self.orchestrator.on_waveform_emitted.connect(self.ui_waveform_emitted.emit)

        self.ui_paused_state_changed.connect(self.set_paused_state)
        self.orchestrator.on_paused_state_changed.connect(self.ui_paused_state_changed.emit)

        self.ui_pause_pending_changed.connect(self.set_pause_pending)
        self.orchestrator.on_pause_pending.connect(self.ui_pause_pending_changed.emit)

        self.ui_tool_started.connect(self.pip_manager.add_pip)
        self.orchestrator.on_tool_started.connect(self.ui_tool_started.emit)

        self.ui_tool_finished.connect(self.pip_manager.complete_async_pip)
        self.orchestrator.on_tool_finished.connect(self.ui_tool_finished.emit)

        self.ui_listening_state_changed.connect(self.set_listening_state)
        if hasattr(self.orchestrator, 'on_listening_state_changed'):
            self.orchestrator.on_listening_state_changed.connect(self.ui_listening_state_changed.emit)

        self.set_paused_state(self.orchestrator.is_paused)
        self.load_history()

    def cleanup(self):
        try:
            self.orchestrator.on_message_appended.disconnect(self.ui_message_appended.emit)
            if hasattr(self.orchestrator, 'on_thought_card_updated'):
                self.orchestrator.on_thought_card_updated.disconnect(self.ui_thought_card_updated.emit)
            self.orchestrator.on_busy_state_changed.disconnect(self.ui_busy_state_changed.emit)
            self.orchestrator.on_amplitude_emitted.disconnect(self.ui_amplitude_emitted.emit)
            if hasattr(self.orchestrator, 'on_waveform_emitted'):
                self.orchestrator.on_waveform_emitted.disconnect(self.ui_waveform_emitted.emit)
            self.orchestrator.on_paused_state_changed.disconnect(self.ui_paused_state_changed.emit)
            self.orchestrator.on_pause_pending.disconnect(self.ui_pause_pending_changed.emit)
            self.orchestrator.on_tool_started.disconnect(self.ui_tool_started.emit)
            self.orchestrator.on_tool_finished.disconnect(self.ui_tool_finished.emit)
            if hasattr(self.orchestrator, 'on_listening_state_changed'):
                self.orchestrator.on_listening_state_changed.disconnect(self.ui_listening_state_changed.emit)
        except Exception:
            pass
        if hasattr(self, 'pip_manager') and self.pip_manager:
            self.pip_manager.clear()

    def set_active_tab(self, is_selected: bool):
        """Notifies pip manager of active tab selection state."""
        if hasattr(self, 'pip_manager') and self.pip_manager:
            self.pip_manager.set_active(is_selected)

    def on_pause_button_clicked(self):
        if getattr(self.orchestrator, 'is_paused', False):
            self.toggle_pause()
        elif getattr(self.orchestrator, 'is_busy', False):
            self.orchestrator.stop_all_processing()
        else:
            self.toggle_pause()

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
            self.btn_send.setToolTip("Send")
            self.btn_mic.setEnabled(False)
            self.loading_bar.hide()
            self.visualizer.set_active(False)
            self.visualizer.hide()
        else:
            self.text_input.setEnabled(True)
            self.btn_send.setEnabled(True)
            self.btn_mic.setEnabled(True)
            if getattr(self.orchestrator, 'is_busy', False) is True or getattr(self.orchestrator, 'is_thinking', False) is True:
                self.set_busy_state(True)
            else:
                self.set_busy_state(False)

    def _scroll_conversation_to_bottom(self):
        self._at_bottom = True
        self.main_layout.activate()
        scroll_bar = self.conversation_log.verticalScrollBar()
        if scroll_bar:
            scroll_bar.setValue(scroll_bar.maximum())

    def set_busy_state(self, busy: bool, speaking: bool = False):
        if self.orchestrator.is_paused:
            self.loading_bar.hide()
            self.visualizer.set_active(False)
            self.visualizer.hide()
            return

        if busy:
            # When busy, pause button becomes Stop Agent (⏹) in red
            self.btn_pause.setText("⏹")
            self.btn_pause.setToolTip("Stop Agent")
            self.btn_pause.setStyleSheet(
                "QPushButton { background-color: #AA0000; color: #FFF; border: 1px solid #FF5555; border-radius: 5px; font-size: 16px; font-family: 'Ubuntu', 'DejaVu Sans', 'Noto Sans Symbols', 'Noto Sans Symbols2', 'FreeSans', sans-serif; } QPushButton:hover { background-color: #CC0000; }"
            )

            self.text_input.setEnabled(True)
            self.text_input.setPlaceholderText("Interject while agent is working...")
            self.btn_send.setEnabled(True)
            self.btn_send.setToolTip("Interject")

            if not getattr(self, '_is_listening', False):
                self.btn_mic.setText("⏺")
                self.btn_mic.setToolTip("Voice Interject")
                self.btn_mic.setStyleSheet(
                    "QPushButton { background-color: #333; color: #FFF; border: 1px solid #555; border-radius: 5px; font-size: 16px; font-family: 'Ubuntu', 'DejaVu Sans', 'Noto Sans Symbols', 'Noto Sans Symbols2', 'FreeSans', sans-serif; } QPushButton:hover { background-color: #444; }"
                )

            if speaking:
                self._at_bottom = True
                self.loading_bar.hide()
                self.visualizer.show()
                self.visualizer.set_active(True)
                self._scroll_conversation_to_bottom()
                QTimer.singleShot(0, self._scroll_conversation_to_bottom)
            else:
                self.visualizer.set_active(False)
                self.visualizer.hide()
                self.loading_bar.show()
        else:
            # When idle, pause button is Pause Agent (❚❚)
            self.btn_pause.setText("❚❚")
            self.btn_pause.setToolTip("Pause Agent")
            self.btn_pause.setStyleSheet(
                "QPushButton { background-color: #333; color: #FFF; border: 1px solid #555; border-radius: 5px; font-size: 13px; font-family: 'Ubuntu', 'DejaVu Sans', 'Noto Sans Symbols', 'Noto Sans Symbols2', 'FreeSans', sans-serif; } QPushButton:hover { background-color: #444; }"
            )

            self.text_input.setEnabled(True)
            self.text_input.setPlaceholderText("Type a message...")
            self.btn_send.setEnabled(True)
            self.btn_send.setToolTip("Send")

            if not getattr(self, '_is_listening', False):
                self.btn_mic.setText("⏺")
                self.btn_mic.setToolTip("Mic")
                self.btn_mic.setStyleSheet(
                    "QPushButton { background-color: #333; color: #FFF; border: 1px solid #555; border-radius: 5px; font-size: 16px; font-family: 'Ubuntu', 'DejaVu Sans', 'Noto Sans Symbols', 'Noto Sans Symbols2', 'FreeSans', sans-serif; } QPushButton:hover { background-color: #444; }"
                )

            self.loading_bar.hide()
            self.visualizer.set_active(False)
            self.visualizer.hide()

    def set_listening_state(self, is_listening: bool):
        self._is_listening = is_listening
        if is_listening:
            self.btn_mic.setText("⏹")
            self.btn_mic.setToolTip("Finish Recording")
            self.btn_mic.setStyleSheet(
                "QPushButton { background-color: #AA0000; color: #FFF; border: 1px solid #FF5555; border-radius: 5px; font-size: 16px; font-family: 'Ubuntu', 'DejaVu Sans', 'Noto Sans Symbols', 'Noto Sans Symbols2', 'FreeSans', sans-serif; } QPushButton:hover { background-color: #CC0000; }"
            )
        else:
            is_busy = getattr(self.orchestrator, 'is_busy', False)
            self.btn_mic.setText("⏺")
            self.btn_mic.setToolTip("Voice Interject" if is_busy else "Mic")
            self.btn_mic.setStyleSheet(
                "QPushButton { background-color: #333; color: #FFF; border: 1px solid #555; border-radius: 5px; font-size: 16px; font-family: 'Ubuntu', 'DejaVu Sans', 'Noto Sans Symbols', 'Noto Sans Symbols2', 'FreeSans', sans-serif; } QPushButton:hover { background-color: #444; }"
            )

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

    def _on_scroll_value_changed(self, value: int):
        scroll_bar = self.conversation_log.verticalScrollBar()
        if scroll_bar and not self._is_loading_history:
            self._at_bottom = (scroll_bar.maximum() - value) <= 60
        if value <= 5 and self._has_more_history and not self._is_loading_history and self._loaded_messages:
            self.load_older_messages()

    def _on_scroll_range_changed(self, min_val: int, max_val: int):
        if not self._is_loading_history and getattr(self, '_at_bottom', True):
            scroll_bar = self.conversation_log.verticalScrollBar()
            if scroll_bar:
                scroll_bar.setValue(max_val)

    def load_history(self):
        if not self.chat_history_manager:
            return
        self._is_loading_history = True
        try:
            self._loaded_messages = []
            self._has_more_history = True
            messages = self.chat_history_manager.get_messages(limit=self.BATCH_SIZE)
            if len(messages) < self.BATCH_SIZE:
                self._has_more_history = False

            self._loaded_messages = messages
            self._render_all_loaded_messages()

            scroll_bar = self.conversation_log.verticalScrollBar()
            if scroll_bar:
                scroll_bar.setValue(scroll_bar.maximum())
            self._at_bottom = True
        finally:
            self._is_loading_history = False

    def load_older_messages(self):
        if not self._loaded_messages or not self._has_more_history or self._is_loading_history or not self.chat_history_manager:
            return
        self._is_loading_history = True
        try:
            oldest_id = None
            for m in self._loaded_messages:
                if m.get("id") is not None:
                    oldest_id = m["id"]
                    break

            if oldest_id is None:
                self._has_more_history = False
                return

            older_msgs = self.chat_history_manager.get_messages(
                limit=self.BATCH_SIZE,
                before_id=oldest_id
            )
            if not older_msgs:
                self._has_more_history = False
                return
            if len(older_msgs) < self.BATCH_SIZE:
                self._has_more_history = False

            scroll_bar = self.conversation_log.verticalScrollBar()
            old_max = scroll_bar.maximum() if scroll_bar else 0
            old_val = scroll_bar.value() if scroll_bar else 0

            self._loaded_messages = older_msgs + self._loaded_messages
            self._render_all_loaded_messages()

            if scroll_bar:
                new_max = scroll_bar.maximum()
                scroll_bar.setValue(old_val + (new_max - old_max))
        finally:
            self._is_loading_history = False

    def _render_all_loaded_messages(self):
        self.conversation_log.clear()
        for m in self._loaded_messages:
            self._render_message(m)

    def _rerender_messages(self, scroll_to_bottom: bool = False):
        scroll_bar = self.conversation_log.verticalScrollBar()
        old_val = scroll_bar.value() if scroll_bar else 0
        prev_loading = self._is_loading_history
        self._is_loading_history = True
        try:
            self._render_all_loaded_messages()
            if scroll_bar:
                if scroll_to_bottom:
                    self._at_bottom = True
                    scroll_bar.setValue(scroll_bar.maximum())
                else:
                    target_val = min(old_val, scroll_bar.maximum())
                    scroll_bar.setValue(target_val)
                    self._at_bottom = (scroll_bar.maximum() - target_val) <= 60
        finally:
            self._is_loading_history = prev_loading

    def on_anchor_clicked(self, url: QUrl):
        url_str = url.toString()
        if url_str.startswith("thought:toggle:"):
            card_id_str = url_str[len("thought:toggle:"):]
            self.toggle_thought_card(card_id_str)
            return
        if url.scheme() in ("http", "https", "mailto", "file"):
            QDesktopServices.openUrl(url)

    def toggle_thought_card(self, card_id_str: str):
        for m in self._loaded_messages:
            if m.get("sender") == "Thoughts" and m.get("id") is not None and str(m.get("id")) == str(card_id_str):
                currently_collapsed = m.get("collapsed", str(m.get("id")) not in self._expanded_thought_cards)
                new_collapsed = not currently_collapsed
                m["collapsed"] = new_collapsed
                key = str(m.get("id"))
                if new_collapsed:
                    self._expanded_thought_cards.discard(key)
                else:
                    self._expanded_thought_cards.add(key)
                self._rerender_messages(scroll_to_bottom=False)
                return

    def update_thought_card(self, card_id, content: str, is_collapsed: bool):
        key = str(card_id)
        if is_collapsed:
            self._expanded_thought_cards.discard(key)
        else:
            self._expanded_thought_cards.add(key)

        scroll_bar = self.conversation_log.verticalScrollBar()
        was_at_bottom = True
        old_val = 0
        if scroll_bar:
            old_val = scroll_bar.value()
            was_at_bottom = (scroll_bar.maximum() - old_val) <= 60
        self._at_bottom = was_at_bottom

        existing = None
        for m in reversed(self._loaded_messages):
            if m.get("sender") == "Thoughts" and m.get("id") is not None and str(m.get("id")) == key:
                existing = m
                break

        if existing is not None:
            existing["content"] = content
            existing["collapsed"] = is_collapsed
            self._rerender_messages(scroll_to_bottom=was_at_bottom)
            return

        if not content:
            return

        msg = {
            "id": card_id,
            "sender": "Thoughts",
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "collapsed": is_collapsed,
        }
        self._loaded_messages.append(msg)
        if len(self._loaded_messages) > self.MAX_GUI_MESSAGES:
            self._loaded_messages = self._loaded_messages[-self.MAX_GUI_MESSAGES:]
            self._has_more_history = True
            self._rerender_messages(scroll_to_bottom=was_at_bottom)
        else:
            self._render_message(msg)
            if scroll_bar:
                if was_at_bottom:
                    self._at_bottom = True
                    scroll_bar.setValue(scroll_bar.maximum())
                else:
                    scroll_bar.setValue(min(old_val, scroll_bar.maximum()))

    def _render_message(self, m: dict):
        sender = m.get("sender", "Agent")
        text = m.get("content", "")
        timestamp_str = m.get("timestamp", "")

        if sender == "Thoughts":
            card_id = m.get("id")
            if "collapsed" in m:
                collapsed = bool(m["collapsed"])
            else:
                collapsed = card_id is None or str(card_id) not in self._expanded_thought_cards
            tools_map = getattr(getattr(self.orchestrator, "cerebrum", None), "tools", None)
            tool_colors = (
                {
                    k: v.color
                    for k, v in tools_map.items()
                    if isinstance(getattr(v, "color", None), str) and v.color
                }
                if isinstance(tools_map, dict)
                else None
            )
            isolated_html = render_thought_card_html(
                card_id=card_id,
                content=text,
                collapsed=collapsed,
                tool_colors=tool_colors,
            )
            insert_message_into_log(self.conversation_log, isolated_html)
            return

        user_name = "User"
        try:
            user_name = self.config_manager.get("user-full-name", "").strip() or "User"
        except Exception:
            pass

        if sender in ["User (Interjection)", f"{user_name} (Interjection)"] or sender.endswith("(Interjection)"):
            display_name = f"{user_name} (Interjection)"
            name_color = PRIMARY_ACCENT_COLOR
            text_color = "#888888"
            bg_color = "#1f1818"
            border_color = "#422424"
            formatted_text = render_user_message_content(text)
        elif sender in ["User", user_name]:
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

        time_display = ""
        if timestamp_str:
            try:
                dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                now_dt = datetime.now().astimezone()
                local_dt = dt.astimezone()
                if local_dt.date() == now_dt.date():
                    time_display = local_dt.strftime("%H:%M")
                else:
                    time_display = local_dt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                time_display = timestamp_str[:16].replace("T", " ")
        else:
            time_display = datetime.now().strftime("%H:%M")

        inner_html = f"""
        <div style="margin-bottom: 4px; text-align: left;">
            <span style="color: {name_color}; font-weight: bold; font-size: 14px;">{display_name}</span>
        </div>
        <div style="color: {text_color}; font-size: 18px; line-height: 1.65;">
            {formatted_text}
        </div>
        <div style="text-align: right; color: #555555; font-size: 11px; margin-top: 6px;">
            {time_display}
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

    def append_to_conversation(self, sender, text):
        msg = {
            "sender": sender,
            "content": text,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        scroll_bar = self.conversation_log.verticalScrollBar()
        was_at_bottom = True
        old_val = 0
        if scroll_bar:
            old_val = scroll_bar.value()
            was_at_bottom = (scroll_bar.maximum() - old_val) <= 60
        self._at_bottom = was_at_bottom

        self._loaded_messages.append(msg)
        if len(self._loaded_messages) > self.MAX_GUI_MESSAGES:
            self._loaded_messages = self._loaded_messages[-self.MAX_GUI_MESSAGES:]
            self._has_more_history = True
            self._rerender_messages(scroll_to_bottom=was_at_bottom)
        else:
            self._render_message(msg)
            if scroll_bar:
                if was_at_bottom:
                    self._at_bottom = True
                    scroll_bar.setValue(scroll_bar.maximum())
                else:
                    scroll_bar.setValue(min(old_val, scroll_bar.maximum()))

