import sys
import math
import logging
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                               QPushButton, QTextEdit, QMenu, QStackedLayout,
                               QStackedWidget, QMessageBox)
from PySide6.QtCore import Qt, Signal, QTimer, QSize
from PySide6.QtGui import QKeyEvent, QAction, QPainter, QColor, QPen, QBrush

from gui.spellcheck import SpellCheckHighlighter
from gui.logger_formatter import QtLoggingHandler, StreamLogger
from gui.agent_view import AgentView
from gui.chatroom_view import ChatroomView

try:
    from gui.theme import PRIMARY_ACCENT_COLOR, SECONDARY_ACCENT_COLOR
except ImportError:
    PRIMARY_ACCENT_COLOR = "#a12924"
    SECONDARY_ACCENT_COLOR = "#f7e3a5"


class AgentTabButton(QPushButton):
    def __init__(self, agent_id: str, agent_name: str, parent=None):
        super().__init__(agent_name, parent)
        self.agent_id = agent_id
        self.setFixedHeight(30)
        self.setCursor(Qt.PointingHandCursor)
        self.is_selected = False
        self.activity_state = "idle"  # "idle", "thinking", "speaking"
        self._is_hovered = False

        font = self.font()
        font.setPointSize(10)
        self.setFont(font)

        # Pulse animation
        self.pulse_phase = 0.0
        self.pulse_timer = QTimer(self)
        self.pulse_timer.setInterval(30)  # ~33 FPS
        self.pulse_timer.timeout.connect(self._on_pulse_tick)

        # Base and dark/light color limits for accents
        self.primary_base = QColor(PRIMARY_ACCENT_COLOR)
        self.primary_dark = QColor("#4e1412")
        self.primary_light = QColor("#cf3832")

        self.secondary_base = QColor(SECONDARY_ACCENT_COLOR)
        self.secondary_dark = QColor("#705e2c")
        self.secondary_light = QColor("#faecc2")

    def _on_pulse_tick(self):
        # A slow pulse: full cycle in ~2.0 seconds (frequency = 0.5 Hz)
        self.pulse_phase += 0.0943
        if self.pulse_phase >= 2 * math.pi:
            self.pulse_phase -= 2 * math.pi
        self.update()

    def set_activity_state(self, state: str):
        if self.activity_state == state:
            return
        self.activity_state = state
        if state in ("thinking", "speaking"):
            if not self.pulse_timer.isActive():
                self.pulse_phase = 0.0
                self.pulse_timer.start()
        else:
            self.pulse_timer.stop()
            self.pulse_phase = 0.0
        self.update()

    def set_selected(self, selected: bool):
        if self.is_selected != selected:
            self.is_selected = selected
            self.update()

    def enterEvent(self, event):
        self._is_hovered = True
        if self.activity_state == "idle":
            self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._is_hovered = False
        if self.activity_state == "idle":
            self.update()
        super().leaveEvent(event)

    @staticmethod
    def _interpolate_color(c1: QColor, c2: QColor, factor: float) -> QColor:
        factor = max(0.0, min(1.0, factor))
        r = int(c1.red() + (c2.red() - c1.red()) * factor)
        g = int(c1.green() + (c2.green() - c1.green()) * factor)
        b = int(c1.blue() + (c2.blue() - c1.blue()) * factor)
        return QColor(r, g, b)

    def sizeHint(self):
        fm = self.fontMetrics()
        text_width = fm.horizontalAdvance(self.text())
        return QSize(max(80, text_width + 30), 30)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        rect = self.rect()
        draw_rect = rect.adjusted(0, 0, -1, -1)

        if self.activity_state == "speaking":
            factor = (math.sin(self.pulse_phase) + 1.0) / 2.0
            bg_color = self._interpolate_color(self.secondary_dark, self.secondary_light, factor)
            border_color = self._interpolate_color(self.secondary_base, self.secondary_light, factor)
            text_color = QColor("#1a1a1a")
        elif self.activity_state == "thinking":
            factor = (math.sin(self.pulse_phase) + 1.0) / 2.0
            bg_color = self._interpolate_color(self.primary_dark, self.primary_light, factor)
            border_color = self._interpolate_color(self.primary_base, self.primary_light, factor)
            text_color = QColor("#FFFFFF")
        else:
            # Idle
            if self.is_selected:
                bg_color = QColor("#444444")
                border_color = QColor("#555555")
                text_color = QColor("#FFFFFF")
            elif self._is_hovered:
                bg_color = QColor("#333333")
                border_color = QColor("#444444")
                text_color = QColor("#FFFFFF")
            else:
                bg_color = QColor("#222222")
                border_color = QColor("#222222")
                text_color = QColor("#AAAAAA")

        painter.setPen(QPen(border_color, 1))
        painter.setBrush(QBrush(bg_color))
        painter.drawRoundedRect(draw_rect, 4, 4)

        font = self.font()
        font.setPointSize(10)
        painter.setFont(font)
        painter.setPen(text_color)

        text_rect = rect.adjusted(15, 0, -15, 0)
        elided_text = painter.fontMetrics().elidedText(
            self.text(), Qt.TextElideMode.ElideRight, text_rect.width())
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, elided_text)


class PromptTextEdit(QTextEdit):
    returnPressed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("Type a message...")
        self.setAcceptRichText(False)
        self.setStyleSheet(
            "background-color: #111; color: #FFF; border: 1px solid #444; padding: 10px; border-radius: 5px; font-size: 14px;")
        self.setFixedHeight(45)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.textChanged.connect(self.adjust_height)

        self.highlighter = SpellCheckHighlighter(self.document())

    def adjust_height(self):
        doc_height = int(self.document().size().height()) + 20
        max_height = 120
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
    ui_shutdown_complete = Signal()
    ui_agent_busy_state_changed = Signal(str, bool, bool)

    def __init__(self, agent_manager):
        super().__init__()
        self.setWindowTitle("Open Amity")
        self.resize(1000, 600)
        self.agent_manager = agent_manager
        self.agent_views = {}  # dict of agent_id -> AgentView
        self.tab_buttons = {}  # dict of agent_id -> AgentTabButton
        self.current_agent_id = None
        self._shutting_down = False
        self.agents_pending_shutdown = 0

        self.ui_agent_busy_state_changed.connect(
            self._on_agent_busy_state_changed)

        # Setup sys.stdout and sys.stderr redirection
        self.stdout_logger = StreamLogger(sys.stdout)
        self.stderr_logger = StreamLogger(sys.stderr)
        sys.stdout = self.stdout_logger
        sys.stderr = self.stderr_logger
        self.stdout_logger.new_message.connect(self.append_to_agent_console)
        self.stderr_logger.new_message.connect(self.append_to_agent_console)

        self.qt_logger = QtLoggingHandler()
        self.qt_logger.setLevel(logging.INFO)

        self.qt_logger.signals.new_message.connect(
            self.append_to_agent_console)
        logging.getLogger().addHandler(self.qt_logger)

        # Central layout
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)

        self.main_hlayout = QHBoxLayout(self.central_widget)
        self.main_hlayout.setContentsMargins(0, 0, 0, 0)
        self.main_hlayout.setSpacing(0)

        self.left_container = QWidget()
        self.main_vlayout = QVBoxLayout(self.left_container)
        self.main_vlayout.setContentsMargins(0, 0, 0, 0)
        self.main_vlayout.setSpacing(0)
        self.main_hlayout.addWidget(self.left_container, 2)

        self.consoles_stack = QStackedWidget()
        self.consoles_stack.hide()
        self.main_hlayout.addWidget(self.consoles_stack, 1)

        # Header Bar
        self.header_widget = QWidget()
        self.header_widget.setStyleSheet("background-color: #1a1a1a;")
        self.header_layout = QHBoxLayout(self.header_widget)
        self.header_layout.setContentsMargins(10, 5, 10, 5)
        self.header_layout.setSpacing(5)

        self.tabs_layout = QHBoxLayout()
        self.tabs_layout.setSpacing(2)
        self.header_layout.addLayout(self.tabs_layout)

        # Persistent Chatroom Tab Button (Hidden if <= 1 agent)
        self.btn_chatroom = QPushButton("👥 Chatroom")
        self.btn_chatroom.setFixedHeight(30)
        self.btn_chatroom.setStyleSheet(
            "QPushButton { background-color: #222; color: #AAA; border: none; font-size: 14px; padding: 0 15px; border-radius: 4px; } QPushButton:hover { background-color: #333; color: #FFF; }")
        self.btn_chatroom.clicked.connect(self.switch_to_chatroom)
        self.tabs_layout.addWidget(self.btn_chatroom)

        self.btn_add_agent = QPushButton("+")
        self.btn_add_agent.setFixedSize(30, 30)
        self.btn_add_agent.setStyleSheet(
            "QPushButton { background-color: #333; color: #FFF; border: none; font-size: 18px; border-radius: 4px; } QPushButton:hover { background-color: #444; }")
        self.btn_add_agent.clicked.connect(self.add_new_agent)
        self.header_layout.addWidget(self.btn_add_agent)

        self.header_layout.addStretch()

        self.hamburger_btn = QPushButton("☰")
        self.hamburger_btn.setFixedSize(30, 30)
        self.hamburger_btn.setStyleSheet(
            "QPushButton { background-color: transparent; color: #FFF; border: none; font-size: 20px; } QPushButton::menu-indicator { image: none; }")

        self.hamburger_menu = QMenu(self)
        self.hamburger_menu.setStyleSheet(
            "QMenu { background-color: #222; color: #FFF; border: 1px solid #444; } QMenu::item:selected { background-color: #333; }")

        self.agent_menu_actions = []
        agent_sections = [
            "Basic Agent Settings",
            "Agent Values and Goals",
            "Agent Voice",
            "Provider Setup",
            "Social Accounts",
            "Other Agent Settings"
        ]

        for i, title in enumerate(agent_sections):
            action = QAction(title, self)
            action.triggered.connect(
                lambda checked=False, idx=i: self.show_settings_section(idx))
            self.hamburger_menu.addAction(action)
            self.agent_menu_actions.append(action)

        self.action_delete_agent = QAction("Delete Agent", self)
        self.action_delete_agent.triggered.connect(self.delete_current_agent)
        self.hamburger_menu.addAction(self.action_delete_agent)
        self.agent_menu_actions.append(self.action_delete_agent)

        self.menu_separator = self.hamburger_menu.addSeparator()

        system_settings_action = QAction("System Settings", self)
        system_settings_action.triggered.connect(
            lambda checked=False: self.show_settings_section(6))
        self.hamburger_menu.addAction(system_settings_action)

        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        self.hamburger_menu.addAction(exit_action)

        self.hamburger_btn.setMenu(self.hamburger_menu)
        self.header_layout.addWidget(self.hamburger_btn)

        self.main_vlayout.addWidget(self.header_widget)

        # Content Area (Stacked: Views vs Settings)
        self.content_container = QWidget()
        self.stacked_layout = QStackedLayout(self.content_container)
        self.stacked_layout.setContentsMargins(0, 0, 0, 0)
        self.main_vlayout.addWidget(self.content_container, 1)

        self.agents_stack = QStackedWidget()
        self.stacked_layout.addWidget(self.agents_stack)

        # Persistent Chatroom View
        self.chatroom_view = ChatroomView(self.agent_manager, self)
        self.chatroom_view.agent_tab_requested.connect(
            self.navigate_to_agent_by_uid_or_id)
        self.agents_stack.addWidget(self.chatroom_view)
        self.consoles_stack.addWidget(self.chatroom_view.console_log)

        from gui.settings_panel import SettingsPanelWidget
        self.settings_panel = SettingsPanelWidget()
        self.settings_panel.agent_manager = self.agent_manager
        self.settings_panel.close_requested.connect(self.hide_settings)
        self.settings_panel.wizard_finished.connect(self.on_wizard_finished)
        self.settings_panel.settings_saved.connect(self.on_settings_saved)
        self.settings_panel.agent_restored.connect(self.on_agent_restored)
        self.stacked_layout.addWidget(self.settings_panel)

        self.ui_shutdown_complete.connect(self.close)

        self.setStyleSheet("""
            QMainWindow { background-color: #1a1a1a; color: #FFF; }
        """)

        # Initialize existing agents
        agent_ids = self.agent_manager.get_all_agents()
        if not agent_ids:
            self.agent_manager.create_agent("default")
            agent_ids = ["default"]

        for aid in agent_ids:
            self.add_agent_tab(aid)

        self.update_chatroom_tab_visibility()

        if len(self.agent_views) > 1:
            self.switch_to_chatroom()
        elif agent_ids:
            self.switch_to_agent(agent_ids[0])

        # Flush early logs
        from core.logger_config import EARLY_LOG_BUFFER, early_buffer_handler
        for record in EARLY_LOG_BUFFER:
            if record.levelno >= self.qt_logger.level:
                self.qt_logger.handle(record)
        logging.getLogger().removeHandler(early_buffer_handler)
        EARLY_LOG_BUFFER.clear()

        # Check first run for current agent
        self.check_first_launch()

    def update_chatroom_tab_visibility(self):
        """Shows the chatroom tab if there is more than one agent, hides it if there is only one."""
        if len(self.agent_views) > 1:
            self.btn_chatroom.show()
        else:
            self.btn_chatroom.hide()
            if self.current_agent_id == "__chatroom__" and self.agent_views:
                first_aid = list(self.agent_views.keys())[0]
                self.switch_to_agent(first_aid)

    def switch_to_chatroom(self):
        self.current_agent_id = "__chatroom__"
        self.agents_stack.setCurrentWidget(self.chatroom_view)
        self.consoles_stack.setCurrentWidget(self.chatroom_view.console_log)
        self.hamburger_btn.show()

        for action in self.agent_menu_actions:
            action.setVisible(False)
        if hasattr(self, 'menu_separator') and self.menu_separator:
            self.menu_separator.setVisible(False)

        # Highlight chatroom button
        self.btn_chatroom.setStyleSheet(
            "QPushButton { background-color: #444; color: #FFF; border: none; font-size: 14px; padding: 0 15px; border-radius: 4px; }")

        # Unhighlight agent tab buttons
        for aid, btn in self.tab_buttons.items():
            btn.set_selected(False)

        if not getattr(self.settings_panel, 'wizard_mode', False):
            self.hide_settings()

    def _on_agent_busy_state_changed(self, agent_id: str, busy: bool, speaking: bool):
        if agent_id not in self.tab_buttons:
            return
        btn = self.tab_buttons[agent_id]
        if speaking:
            btn.set_activity_state("speaking")
        elif busy:
            btn.set_activity_state("thinking")
        else:
            btn.set_activity_state("idle")

    def navigate_to_agent_by_uid_or_id(self, target: str):
        if not target:
            return
        if target in self.agent_views:
            self.switch_to_agent(target)
            return
        aid = self.agent_manager.get_agent_id_by_uid(target)
        if aid and aid in self.agent_views:
            self.switch_to_agent(aid)

    def add_agent_tab(self, agent_id):
        # Create orchestrator for agent
        orchestrator = self.agent_manager.get_orchestrator(agent_id)
        if not orchestrator:
            orchestrator = self.agent_manager.start_agent(agent_id)

        view = AgentView(orchestrator, self)
        self.agent_views[agent_id] = view
        self.agents_stack.addWidget(view)
        self.consoles_stack.addWidget(view.console_log)

        agent_name = self.agent_manager.get_agent_name(agent_id)
        btn = AgentTabButton(agent_id, agent_name, self)
        btn.clicked.connect(lambda checked=False,
                            a=agent_id: self.switch_to_agent(a))
        self.tabs_layout.addWidget(btn)
        self.tab_buttons[agent_id] = btn

        orchestrator.on_shutdown_complete.connect(self.check_all_shutdown)
        orchestrator.on_busy_state_changed.connect(
            lambda busy, speaking, aid=agent_id: self.ui_agent_busy_state_changed.emit(aid, busy, speaking)
        )
        self.update_chatroom_tab_visibility()

    def switch_to_agent(self, agent_id):
        if agent_id not in self.agent_views:
            return
        self.current_agent_id = agent_id
        self.agents_stack.setCurrentWidget(self.agent_views[agent_id])
        self.consoles_stack.setCurrentWidget(
            self.agent_views[agent_id].console_log)
        self.hamburger_btn.show()

        for action in self.agent_menu_actions:
            action.setVisible(True)
        if hasattr(self, 'menu_separator') and self.menu_separator:
            self.menu_separator.setVisible(True)

        # Update tab styles
        self.btn_chatroom.setStyleSheet(
            "QPushButton { background-color: #222; color: #AAA; border: none; font-size: 14px; padding: 0 15px; border-radius: 4px; } QPushButton:hover { background-color: #333; color: #FFF; }")

        for aid, btn in self.tab_buttons.items():
            btn.set_selected(aid == agent_id)

        # Point settings panel to this agent's settings
        orchestrator = self.agent_views[agent_id].orchestrator
        self.settings_panel.load_settings(orchestrator.settings_manager)

        if not getattr(self.settings_panel, 'wizard_mode', False):
            self.hide_settings()

    def add_new_agent(self):
        new_id = self.agent_manager.create_new_agent()
        self.add_agent_tab(new_id)
        self.switch_to_agent(new_id)
        self.update_chatroom_tab_visibility()
        # Start wizard
        self.settings_panel.settings.set("core.first-run", True)
        self.settings_panel.settings.save()
        self.check_first_launch()

    def delete_current_agent(self):
        if len(self.agent_views) <= 1:
            QMessageBox.warning(self, "Cannot Delete",
                                "You cannot delete the last remaining agent.")
            return

        agent_name = self.agent_manager.get_agent_name(self.current_agent_id)
        reply = QMessageBox.question(self, 'Confirm Deletion',
                                     f"Are you sure you want to permanently delete agent '{agent_name}'?\nThis will remove all their memory and state.",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)

        if reply == QMessageBox.Yes:
            aid = self.current_agent_id

            # Switch away first
            other_ids = [k for k in self.agent_views.keys() if k != aid]
            self.switch_to_agent(other_ids[0])

            # Disconnect shutdown signal so deleting an individual agent does not trigger app shutdown
            orch = self.agent_manager.get_orchestrator(aid)
            if orch and hasattr(orch, 'on_shutdown_complete'):
                try:
                    orch.on_shutdown_complete.disconnect(self.check_all_shutdown)
                except Exception:
                    pass

            # Remove UI elements
            view = self.agent_views.pop(aid)
            self.agents_stack.removeWidget(view)
            self.consoles_stack.removeWidget(view.console_log)
            view.deleteLater()

            btn = self.tab_buttons.pop(aid)
            btn.set_activity_state("idle")
            self.tabs_layout.removeWidget(btn)
            btn.deleteLater()

            # Delete data
            self.agent_manager.delete_agent(aid)
            self.update_chatroom_tab_visibility()

    def append_to_agent_console(self, agent_id, text):
        if agent_id == "global":
            if hasattr(self, 'chatroom_view'):
                self.chatroom_view.append_to_console(text)
            for view in self.agent_views.values():
                view.append_to_console(text)
        elif agent_id in self.agent_views:
            self.agent_views[agent_id].append_to_console(text)

    def check_first_launch(self):
        target_aid = self.current_agent_id if self.current_agent_id in self.agent_views else (list(self.agent_views.keys())[0] if self.agent_views else None)
        if not target_aid:
            return
        settings = self.agent_views[target_aid].settings_manager
        if settings.get("core.first-run", False):
            self.settings_panel.load_settings(settings)
            self.settings_panel.set_wizard_mode(True)
            self.settings_panel.set_section(0)
            self.stacked_layout.setCurrentIndex(1)

    def show_settings_section(self, index):
        target_aid = self.current_agent_id if self.current_agent_id in self.agent_views else (list(self.agent_views.keys())[0] if self.agent_views else None)
        if target_aid and target_aid in self.agent_views:
            settings = self.agent_views[target_aid].settings_manager
            self.settings_panel.load_settings(settings)
        else:
            self.settings_panel.load_system_config()
        self.settings_panel.set_wizard_mode(False)
        self.settings_panel.set_section(index)
        self.stacked_layout.setCurrentIndex(1)

    def hide_settings(self):
        self.stacked_layout.setCurrentIndex(0)
        self.update_tab_names()

    def update_tab_names(self):
        for aid, btn in self.tab_buttons.items():
            btn.setText(self.agent_manager.get_agent_name(aid))

    def on_wizard_finished(self):
        self.hide_settings()
        target_aid = self.current_agent_id if self.current_agent_id in self.agent_views else (list(self.agent_views.keys())[0] if self.agent_views else None)
        if target_aid:
            orch = self.agent_views[target_aid].orchestrator
            orch.restart_worker()
        self.update_tab_names()

    def on_settings_saved(self):
        target_aid = self.current_agent_id if self.current_agent_id in self.agent_views else (list(self.agent_views.keys())[0] if self.agent_views else None)
        if target_aid:
            orch = self.agent_views[target_aid].orchestrator
            orch.restart_worker()
        self.update_tab_names()

    def on_agent_restored(self, agent_id: str, is_rollback: bool):
        self.hide_settings()
        if is_rollback:
            if agent_id in self.agent_views:
                old_view = self.agent_views.pop(agent_id)
                self.agents_stack.removeWidget(old_view)
                self.consoles_stack.removeWidget(old_view.console_log)
                old_view.deleteLater()

            # Start fresh orchestrator for restored agent
            new_orch = self.agent_manager.start_agent(agent_id)
            view = AgentView(new_orch, self)
            self.agent_views[agent_id] = view
            self.agents_stack.addWidget(view)
            self.consoles_stack.addWidget(view.console_log)
            new_orch.on_shutdown_complete.connect(self.check_all_shutdown)
            new_orch.on_busy_state_changed.connect(
                lambda busy, speaking, aid=agent_id: self.ui_agent_busy_state_changed.emit(aid, busy, speaking)
            )

            self.update_tab_names()
            self.switch_to_agent(agent_id)
        else:
            self.add_agent_tab(agent_id)
            self.switch_to_agent(agent_id)
            self.update_chatroom_tab_visibility()
            self.update_tab_names()

    def closeEvent(self, event):
        if self._shutting_down:
            event.accept()
            return

        logging.info("System: Shutting down all agents gracefully...")
        self._shutting_down = True
        event.ignore()

        self.agents_pending_shutdown = len(self.agent_views)
        if self.agents_pending_shutdown == 0:
            self.ui_shutdown_complete.emit()
            return

        for aid, view in self.agent_views.items():
            orch = view.orchestrator
            fatigue = orch.get_fatigue() if hasattr(orch, 'get_fatigue') else 0.0
            if fatigue >= 0.05:
                view.append_to_conversation(
                    "System", "Consolidating memories for graceful shutdown... please wait.")
                orch.shutdown(force_sleep=True)
            else:
                orch.shutdown()

    def check_all_shutdown(self):
        if not self._shutting_down:
            return
        self.agents_pending_shutdown -= 1
        if self.agents_pending_shutdown <= 0:
            self.ui_shutdown_complete.emit()

    def keyPressEvent(self, event: QKeyEvent):
        if self.current_agent_id and self.current_agent_id in self.agent_views:
            self.agent_views[self.current_agent_id].orchestrator.user_interacted()

        if event.key() == Qt.Key_QuoteLeft or event.key() == Qt.Key_AsciiTilde:
            if self.current_agent_id and self.current_agent_id in self.agent_views:
                view = self.agent_views[self.current_agent_id]
                if view.text_input.hasFocus():
                    super().keyPressEvent(event)
                    return
            elif self.current_agent_id == "__chatroom__":
                if self.chatroom_view.text_input.hasFocus():
                    super().keyPressEvent(event)
                    return
            self.consoles_stack.setVisible(
                not self.consoles_stack.isVisible())
        else:
            super().keyPressEvent(event)

