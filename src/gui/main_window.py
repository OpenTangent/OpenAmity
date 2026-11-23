import sys
import logging
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                               QTextEdit, QMenu, QStackedLayout,
                               QStackedWidget, QMessageBox)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent, QAction, QShortcut, QKeySequence

from gui.spellcheck import SpellCheckHighlighter
from gui.logger_formatter import QtLoggingHandler, StreamLogger
from gui.agent_view import AgentView
from gui.chatroom_view import ChatroomView
from gui.chatroom_sidebar import ChatroomSidebar, SidebarAgentButton as AgentTabButton
from gui.chatroom_editor_panel import ChatroomEditorPanel
from core.chatroom_manager import ChatroomManager

try:
    from gui.theme import MODERN_SCROLLBAR_STYLE
except ImportError:
    MODERN_SCROLLBAR_STYLE = ""


class PromptTextEdit(QTextEdit):
    returnPressed = Signal()
    MIN_HEIGHT = 45
    MAX_HEIGHT = 240

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("Type a message...")
        self.setAcceptRichText(False)
        self.setStyleSheet(f"""
            QTextEdit {{
                background-color: #111;
                color: #FFF;
                border: 1px solid #444;
                padding: 10px;
                border-radius: 5px;
                font-size: 14px;
            }}
            {MODERN_SCROLLBAR_STYLE}
        """)
        self.setFixedHeight(self.MIN_HEIGHT)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.textChanged.connect(self.adjust_height)

        self.highlighter = SpellCheckHighlighter(self.document())

    def adjust_height(self):
        doc_height = int(self.document().size().height()) + 20
        max_height = self.MAX_HEIGHT
        new_height = min(doc_height, max_height)
        new_height = max(self.MIN_HEIGHT, new_height)
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
    ui_agent_paused_state_changed = Signal(str, bool)

    def __init__(self, agent_manager):
        super().__init__()
        self.setWindowTitle("Open Amity")
        self.resize(1000, 600)
        self.agent_manager = agent_manager
        self.chatroom_manager = ChatroomManager()
        self.agent_views = {}  # dict of agent_id -> AgentView
        self.current_agent_id = None
        self.current_chatroom_id = None
        self.last_dm_agent_id = None
        self.current_context = ("all", None)  # ("all", None) or ("room", chatroom_id)
        self._shutting_down = False
        self.agents_pending_shutdown = 0

        self.ui_agent_busy_state_changed.connect(
            self._on_agent_busy_state_changed)
        self.ui_agent_paused_state_changed.connect(
            self._on_agent_paused_state_changed)

        # Setup sys.stdout and sys.stderr redirection
        self.stdout_logger = StreamLogger(sys.stdout)
        self.stderr_logger = StreamLogger(sys.stderr)
        sys.stdout = self.stdout_logger
        sys.stderr = self.stderr_logger
        self.stdout_logger.new_message.connect(self.append_to_agent_console)
        self.stderr_logger.new_message.connect(self.append_to_agent_console)

        from core.logger_config import VerbosityFilter, ConsoleLogFilter
        self.qt_logger = QtLoggingHandler()
        self.qt_logger.setLevel(logging.INFO)
        self.qt_logger.addFilter(VerbosityFilter())
        self.qt_logger.addFilter(ConsoleLogFilter())

        self.qt_logger.signals.new_message.connect(
            self.append_to_agent_console)
        logging.getLogger().addHandler(self.qt_logger)

        # Central layout
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)

        self.main_hlayout = QHBoxLayout(self.central_widget)
        self.main_hlayout.setContentsMargins(0, 0, 0, 0)
        self.main_hlayout.setSpacing(0)

        # Unified Navigation Sidebar (runs full height down the left side)
        self.chatroom_sidebar = ChatroomSidebar(self.agent_manager, self)
        self.chatroom_sidebar.agent_selected.connect(self.on_agent_tab_clicked)
        self.chatroom_sidebar.new_agent_requested.connect(self.add_new_agent)
        self.chatroom_sidebar.chatroom_selected.connect(self._on_sidebar_chatroom_selected)
        self.chatroom_sidebar.new_chatroom_requested.connect(self.open_new_chatroom)
        self.chatroom_sidebar.edit_chatroom_requested.connect(self.open_edit_chatroom)
        self.main_hlayout.addWidget(self.chatroom_sidebar)

        self.left_container = QWidget()
        self.main_vlayout = QVBoxLayout(self.left_container)
        self.main_vlayout.setContentsMargins(0, 0, 0, 0)
        self.main_vlayout.setSpacing(0)
        self.main_hlayout.addWidget(self.left_container, 2)

        self.consoles_stack = QStackedWidget()
        self.consoles_stack.hide()
        self.main_hlayout.addWidget(self.consoles_stack, 1)

        # Contextual Hamburger Menu (docked into sidebar bottom settings bar)
        self.hamburger_menu = QMenu(self)
        self.hamburger_menu.setStyleSheet(
            "QMenu { background-color: #222; color: #FFF; border: 1px solid #444; } QMenu::item:selected { background-color: #333; }")

        self.agent_menu_actions = []
        agent_sections = [
            "Cognitive Provider",
            "Basic Agent Settings",
            "Agent Values and Goals",
            "Agent Voice",
            "Media Provider",
            "Social Accounts",
            "Other Agent Settings"
        ]

        for i, title in enumerate(agent_sections):
            action = QAction(title, self)
            action.triggered.connect(
                lambda checked=False, idx=(i + 1): self.show_settings_section(idx))
            self.hamburger_menu.addAction(action)
            self.agent_menu_actions.append(action)

        self.action_delete_agent = QAction("Delete Agent", self)
        self.action_delete_agent.triggered.connect(self.delete_current_agent)
        self.hamburger_menu.addAction(self.action_delete_agent)
        self.agent_menu_actions.append(self.action_delete_agent)

        self.action_edit_chatroom = QAction("Edit Chatroom", self)
        self.action_edit_chatroom.triggered.connect(self._on_menu_edit_chatroom)
        self.hamburger_menu.addAction(self.action_edit_chatroom)

        self.action_delete_chatroom = QAction("Delete Chatroom", self)
        self.action_delete_chatroom.triggered.connect(self.delete_current_chatroom)
        self.hamburger_menu.addAction(self.action_delete_chatroom)

        self.chatroom_menu_actions = [self.action_edit_chatroom, self.action_delete_chatroom]

        self.menu_separator = self.hamburger_menu.addSeparator()

        system_settings_action = QAction("System Settings", self)
        system_settings_action.triggered.connect(
            lambda checked=False: self.show_settings_section(8))
        self.hamburger_menu.addAction(system_settings_action)

        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        self.hamburger_menu.addAction(exit_action)

        self.chatroom_sidebar.set_hamburger_menu(self.hamburger_menu)
        self.hamburger_btn = self.chatroom_sidebar.hamburger_btn
        self.btn_add_agent = self.chatroom_sidebar.btn_add_agent

        self.sidebar_toggle_shortcut = QShortcut(QKeySequence("Ctrl+B"), self)
        self.sidebar_toggle_shortcut.activated.connect(self.chatroom_sidebar.toggle_collapsed)

        # Content Area (Stacked: Views vs Settings vs Chatroom Editor)
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

        self.chatroom_editor_panel = ChatroomEditorPanel(self.agent_manager, self)
        self.chatroom_editor_panel.close_requested.connect(self.hide_chatroom_editor)
        self.chatroom_editor_panel.saved.connect(self.on_chatroom_saved)
        self.chatroom_editor_panel.new_agent_requested.connect(self._on_editor_new_agent_requested)
        self.stacked_layout.addWidget(self.chatroom_editor_panel)

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

        # Start on "All Agents" showing the oldest agent
        agents_by_age = self.agent_manager.get_agents_by_age()
        initial_aid = agents_by_age[0] if agents_by_age else agent_ids[0]
        self.switch_to_all_agents(target_agent_id=initial_aid)

        # Flush early logs
        from core.logger_config import EARLY_LOG_BUFFER, early_buffer_handler
        for record in EARLY_LOG_BUFFER:
            if record.levelno >= self.qt_logger.level:
                self.qt_logger.handle(record)
        logging.getLogger().removeHandler(early_buffer_handler)
        EARLY_LOG_BUFFER.clear()

        # Check first run for current agent
        self.check_first_launch()

    @property
    def tab_buttons(self):
        """Returns mapping of agent_id -> SidebarAgentButton managed by the sidebar."""
        return self.chatroom_sidebar.agent_buttons

    def update_chatroom_tab_visibility(self):
        """Refreshes sidebar agents and rooms."""
        if hasattr(self, 'chatroom_sidebar'):
            self.chatroom_sidebar.refresh_agents()
            self.chatroom_sidebar.refresh_rooms()

    def _on_sidebar_chatroom_selected(self, room_id: int):
        self.switch_to_chatroom(room_id)

    def on_agent_tab_clicked(self, agent_id: str):
        self.switch_to_all_agents(target_agent_id=agent_id)

    def switch_to_agent(self, agent_id: str):
        self.switch_to_all_agents(target_agent_id=agent_id)

    def switch_to_all_agents(self, target_agent_id: str = None):
        if self.stacked_layout.currentIndex() == 1 and not getattr(self.settings_panel, 'wizard_mode', False):
            self.settings_panel.save_settings()
        elif self.stacked_layout.currentIndex() == 2:
            self.hide_chatroom_editor()

        if not target_agent_id:
            target_agent_id = self.last_dm_agent_id
        if not target_agent_id or target_agent_id not in self.agent_views:
            agents_by_age = self.agent_manager.get_agents_by_age()
            target_agent_id = agents_by_age[0] if agents_by_age else (list(self.agent_views.keys())[0] if self.agent_views else None)

        if not target_agent_id or target_agent_id not in self.agent_views:
            return

        self.current_context = ("all", None)
        self.current_chatroom_id = None
        self.current_agent_id = target_agent_id
        self.last_dm_agent_id = target_agent_id

        self.agents_stack.setCurrentWidget(self.agent_views[target_agent_id])
        self.consoles_stack.setCurrentWidget(
            self.agent_views[target_agent_id].console_log)

        # Configure hamburger menu for agent
        for action in self.agent_menu_actions:
            action.setVisible(True)
        for action in self.chatroom_menu_actions:
            action.setVisible(False)
        if hasattr(self, 'menu_separator') and self.menu_separator:
            self.menu_separator.setVisible(True)

        # Update sidebar selection (deselects chatrooms, collapses room member lists, selects agent)
        self.chatroom_sidebar.set_selected_agent(target_agent_id)

        # Update tool pip active states across agent views
        for aid, v in self.agent_views.items():
            if hasattr(v, 'set_active_tab'):
                v.set_active_tab(aid == target_agent_id)

        # Point settings panel to this agent's settings
        orchestrator = self.agent_views[target_agent_id].orchestrator
        self.settings_panel.load_settings(orchestrator.settings_manager)

        if not getattr(self.settings_panel, 'wizard_mode', False):
            self.hide_settings()

    def switch_to_chatroom(self, chatroom_id: int = None):
        if self.stacked_layout.currentIndex() == 1 and not getattr(self.settings_panel, 'wizard_mode', False):
            self.settings_panel.save_settings()
        elif self.stacked_layout.currentIndex() == 2:
            self.hide_chatroom_editor()

        if chatroom_id is None:
            active_rooms = self.chatroom_manager.list_chatrooms(include_deleted=False)
            if active_rooms:
                chatroom_id = active_rooms[0]["id"]
            else:
                chatroom_id = 1

        self.current_context = ("room", chatroom_id)
        self.current_chatroom_id = chatroom_id
        self.current_agent_id = "__chatroom__"

        self.chatroom_view.set_chatroom(chatroom_id)
        self.agents_stack.setCurrentWidget(self.chatroom_view)
        self.consoles_stack.setCurrentWidget(self.chatroom_view.console_log)

        # Configure hamburger menu for chatroom
        for action in self.agent_menu_actions:
            action.setVisible(False)
        for action in self.chatroom_menu_actions:
            action.setVisible(True)
        if hasattr(self, 'menu_separator') and self.menu_separator:
            self.menu_separator.setVisible(True)

        # Update sidebar selection (selects room and expands only its member agents)
        self.chatroom_sidebar.set_selected_chatroom(chatroom_id)

        # Deactivate tool pips on all agent views while viewing chatroom
        for aid, view in self.agent_views.items():
            if hasattr(view, 'set_active_tab'):
                view.set_active_tab(False)

        if not getattr(self.settings_panel, 'wizard_mode', False):
            self.hide_settings()

    def _on_menu_edit_chatroom(self):
        if self.current_context[0] == "room" and self.current_chatroom_id is not None:
            self.open_edit_chatroom(self.current_chatroom_id)

    def open_new_chatroom(self):
        if self.stacked_layout.currentIndex() == 1 and not getattr(self.settings_panel, 'wizard_mode', False):
            self.settings_panel.save_settings()
        self.chatroom_editor_panel.load_new_chatroom()
        self.stacked_layout.setCurrentIndex(2)

    def open_edit_chatroom(self, chatroom_id: int):
        if self.stacked_layout.currentIndex() == 1 and not getattr(self.settings_panel, 'wizard_mode', False):
            self.settings_panel.save_settings()
        self.chatroom_editor_panel.load_edit_chatroom(chatroom_id)
        self.stacked_layout.setCurrentIndex(2)

    def hide_chatroom_editor(self):
        self.stacked_layout.setCurrentIndex(0)
        self.chatroom_sidebar.refresh_selected_room_members()

    def on_chatroom_saved(self, chatroom_id: int):
        self.chatroom_sidebar.refresh_rooms()
        self.switch_to_chatroom(chatroom_id)

    def _on_editor_new_agent_requested(self):
        self.add_new_agent()

    def delete_current_chatroom(self):
        if self.current_context[0] != "room" or self.current_chatroom_id is None:
            return
        room = self.chatroom_manager.get_chatroom(self.current_chatroom_id)
        if not room or room.get("deleted_at") is not None:
            return
        room_name = room["name"]
        reply = QMessageBox.question(
            self,
            "Confirm Chatroom Deletion",
            f"Are you sure you want to delete chatroom '{room_name}'?\nThis will hide the room from all agents.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.chatroom_manager.delete_chatroom(self.current_chatroom_id)
            self.switch_to_all_agents()
            self.chatroom_sidebar.refresh_rooms()

    def _on_agent_busy_state_changed(self, agent_id: str, busy: bool, speaking: bool):
        state = "speaking" if speaking else ("thinking" if busy else "idle")
        self.chatroom_sidebar.set_agent_activity_state(agent_id, state)

    def _on_agent_paused_state_changed(self, agent_id: str, paused: bool):
        self.chatroom_sidebar.set_agent_paused(agent_id, paused)
        if not paused:
            orch = self.agent_manager.get_orchestrator(agent_id)
            if orch and hasattr(orch, 'pulse_engine') and hasattr(orch.pulse_engine, 'notify_unpaused'):
                orch.pulse_engine.notify_unpaused()

    def navigate_to_agent_by_uid_or_id(self, target: str):
        if not target:
            return
        if target in self.agent_views:
            self.chatroom_sidebar.set_agents_collapsed(False)
            self.switch_to_all_agents(target_agent_id=target)
            return
        aid = self.agent_manager.get_agent_id_by_uid(target)
        if aid and aid in self.agent_views:
            self.chatroom_sidebar.set_agents_collapsed(False)
            self.switch_to_all_agents(target_agent_id=aid)

    def add_agent_tab(self, agent_id):
        # Create orchestrator for agent
        orchestrator = self.agent_manager.get_orchestrator(agent_id)
        if not orchestrator:
            orchestrator = self.agent_manager.start_agent(agent_id)

        view = AgentView(orchestrator, self)
        self.agent_views[agent_id] = view
        view.set_active_tab(agent_id == self.current_agent_id)
        self.agents_stack.addWidget(view)
        self.consoles_stack.addWidget(view.console_log)

        orchestrator.on_shutdown_complete.connect(self.check_all_shutdown)
        orchestrator.on_busy_state_changed.connect(
            lambda busy, speaking, aid=agent_id: self.ui_agent_busy_state_changed.emit(aid, busy, speaking)
        )
        orchestrator.on_paused_state_changed.connect(
            lambda paused, aid=agent_id: self.ui_agent_paused_state_changed.emit(aid, paused)
        )
        self.chatroom_sidebar.refresh_agents()
        self.chatroom_sidebar.set_agent_paused(agent_id, orchestrator.is_paused)

    def add_new_agent(self):
        new_id = self.agent_manager.create_new_agent()
        self.add_agent_tab(new_id)
        self.switch_to_all_agents(target_agent_id=new_id)
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
            self.switch_to_all_agents(target_agent_id=other_ids[0])

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
            view.cleanup()
            view.deleteLater()

            # Delete data
            self.agent_manager.delete_agent(aid)
            self.chatroom_sidebar.remove_agent(aid)
            self.chatroom_sidebar.refresh_agents()
            self.chatroom_sidebar.refresh_rooms()

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
        self.chatroom_sidebar.update_agent_names()

    def on_wizard_finished(self):
        self.hide_settings()
        self.update_tab_names()

    def on_settings_saved(self):
        target_aid = getattr(self.settings_panel.settings, 'agent_id', None) or (self.current_agent_id if self.current_agent_id in self.agent_views else (list(self.agent_views.keys())[0] if self.agent_views else None))
        if target_aid and target_aid in self.agent_views:
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
                old_view.cleanup()
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
            new_orch.on_paused_state_changed.connect(
                lambda paused, aid=agent_id: self.ui_agent_paused_state_changed.emit(aid, paused)
            )
            if not getattr(new_orch, 'is_paused', False):
                if hasattr(new_orch, 'pulse_engine') and hasattr(new_orch.pulse_engine, 'notify_unpaused'):
                    new_orch.pulse_engine.notify_unpaused()

            self.chatroom_sidebar.set_agent_paused(agent_id, getattr(new_orch, 'is_paused', False))
            self.update_tab_names()
            self.switch_to_agent(agent_id)
        else:
            self.add_agent_tab(agent_id)
            self.switch_to_agent(agent_id)
            self.update_chatroom_tab_visibility()
            self.update_tab_names()

    def closeEvent(self, event):
        if self._shutting_down:
            if hasattr(self, 'chatroom_sidebar') and self.chatroom_sidebar:
                self.chatroom_sidebar.close()
            event.accept()
            return

        if self.stacked_layout.currentIndex() == 1 and not getattr(self.settings_panel, 'wizard_mode', False):
            self.settings_panel.save_settings()
        elif self.stacked_layout.currentIndex() == 2 and getattr(self.chatroom_editor_panel, 'is_edit_mode', False):
            self.chatroom_editor_panel._save_changes()

        logging.info("System: Shutting down all agents gracefully...")
        self._shutting_down = True
        event.ignore()

        self.agents_pending_shutdown = len(self.agent_views)
        if self.agents_pending_shutdown == 0:
            self.ui_shutdown_complete.emit()
            return

        for aid, view in self.agent_views.items():
            orch = view.orchestrator
            orch.shutdown(force_sleep=False)

    def check_all_shutdown(self):
        if not self._shutting_down:
            return
        self.agents_pending_shutdown -= 1
        if self.agents_pending_shutdown <= 0:
            if hasattr(self.agent_manager, 'stop_hook_server'):
                self.agent_manager.stop_hook_server()
            if hasattr(self.agent_manager, 'stop_chatroom_notifier'):
                self.agent_manager.stop_chatroom_notifier()
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

