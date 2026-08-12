import sys
import logging
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                               QPushButton, QTextEdit, QMenu, QStackedLayout,
                               QStackedWidget, QMessageBox)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent, QAction

from gui.spellcheck import SpellCheckHighlighter
from gui.logger_formatter import QtLoggingHandler, StreamLogger
from gui.agent_view import AgentView


class PromptTextEdit(QTextEdit):
    returnPressed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("Type a message...")
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

    def __init__(self, agent_manager):
        super().__init__()
        self.setWindowTitle("Open Amity")
        self.resize(1000, 600)
        self.agent_manager = agent_manager
        self.agent_views = {}  # dict of agent_id -> AgentView
        self.tab_buttons = {}  # dict of agent_id -> QPushButton
        self.current_agent_id = None
        self._shutting_down = False

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

        sections = [
            "Basic Agent Settings",
            "Agent Values and Goals",
            "Agent Voice",
            "Provider Setup",
            "Social Accounts",
            "System Settings"
        ]

        for i, title in enumerate(sections):
            action = QAction(title, self)
            action.triggered.connect(
                lambda checked=False, idx=i: self.show_settings_section(idx))
            self.hamburger_menu.addAction(action)

        self.hamburger_menu.addSeparator()

        self.action_delete_agent = QAction("Delete Agent", self)
        self.action_delete_agent.triggered.connect(self.delete_current_agent)
        self.hamburger_menu.addAction(self.action_delete_agent)

        self.hamburger_menu.addSeparator()

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

        from gui.settings_panel import SettingsPanelWidget
        self.settings_panel = SettingsPanelWidget()
        self.settings_panel.close_requested.connect(self.hide_settings)
        self.settings_panel.wizard_finished.connect(self.on_wizard_finished)
        self.settings_panel.settings_saved.connect(self.on_settings_saved)
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

        if agent_ids:
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
        btn = QPushButton(agent_name)
        btn.setFixedHeight(30)
        btn.clicked.connect(lambda checked=False,
                            a=agent_id: self.switch_to_agent(a))
        self.tabs_layout.addWidget(btn)
        self.tab_buttons[agent_id] = btn

        orchestrator.on_shutdown_complete.connect(self.check_all_shutdown)

    def switch_to_agent(self, agent_id):
        if agent_id not in self.agent_views:
            return
        self.current_agent_id = agent_id
        self.agents_stack.setCurrentWidget(self.agent_views[agent_id])
        self.consoles_stack.setCurrentWidget(
            self.agent_views[agent_id].console_log)

        # Update tab styles
        for aid, btn in self.tab_buttons.items():
            if aid == agent_id:
                btn.setStyleSheet(
                    "QPushButton { background-color: #444; color: #FFF; border: none; font-size: 14px; padding: 0 15px; border-radius: 4px; font-weight: bold; }")
            else:
                btn.setStyleSheet(
                    "QPushButton { background-color: #222; color: #AAA; border: none; font-size: 14px; padding: 0 15px; border-radius: 4px; } QPushButton:hover { background-color: #333; color: #FFF; }")

        # Point settings panel to this agent's settings
        orchestrator = self.agent_views[agent_id].orchestrator
        self.settings_panel.load_settings(orchestrator.settings_manager)

    def add_new_agent(self):
        new_id = self.agent_manager.create_new_agent()
        self.add_agent_tab(new_id)
        self.switch_to_agent(new_id)
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

            # Remove UI elements
            view = self.agent_views.pop(aid)
            self.agents_stack.removeWidget(view)
            self.consoles_stack.removeWidget(view.console_log)
            view.deleteLater()

            btn = self.tab_buttons.pop(aid)
            self.tabs_layout.removeWidget(btn)
            btn.deleteLater()

            # Delete data
            self.agent_manager.delete_agent(aid)

    def append_to_agent_console(self, agent_id, text):
        if agent_id == "global":
            for view in self.agent_views.values():
                view.append_to_console(text)
        elif agent_id in self.agent_views:
            self.agent_views[agent_id].append_to_console(text)

    def check_first_launch(self):
        if not self.current_agent_id:
            return
        settings = self.agent_views[self.current_agent_id].settings_manager
        if settings.get("core.first-run", False):
            self.settings_panel.load_settings(settings)
            self.settings_panel.set_wizard_mode(True)
            self.settings_panel.set_section(0)
            self.stacked_layout.setCurrentIndex(1)

    def show_settings_section(self, index):
        if not self.current_agent_id:
            return
        settings = self.agent_views[self.current_agent_id].settings_manager
        self.settings_panel.load_settings(settings)
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
        if self.current_agent_id:
            orch = self.agent_views[self.current_agent_id].orchestrator
            orch.restart_worker()
        self.update_tab_names()

    def on_settings_saved(self):
        if self.current_agent_id:
            orch = self.agent_views[self.current_agent_id].orchestrator
            orch.reload_settings()
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
            if getattr(orch, 'session_fatigue_tokens', 0) > 10000:
                view.append_to_conversation(
                    "System", "Consolidating memories for graceful shutdown... please wait.")
                orch.shutdown(force_sleep=True)
            else:
                orch.shutdown()

    def check_all_shutdown(self):
        self.agents_pending_shutdown -= 1
        if self.agents_pending_shutdown <= 0:
            self.ui_shutdown_complete.emit()

    def keyPressEvent(self, event: QKeyEvent):
        if self.current_agent_id:
            self.agent_views[self.current_agent_id].orchestrator.user_interacted()

        if event.key() == Qt.Key_QuoteLeft or event.key() == Qt.Key_AsciiTilde:
            if self.current_agent_id:
                view = self.agent_views[self.current_agent_id]
                if view.text_input.hasFocus():
                    super().keyPressEvent(event)
                else:
                    self.consoles_stack.setVisible(
                        not self.consoles_stack.isVisible())
        else:
            super().keyPressEvent(event)
