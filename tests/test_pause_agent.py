import os
import sys
import tempfile
import shutil
import time
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PySide6.QtGui import QColor, QFontMetrics
from PySide6.QtWidgets import QApplication
from core.settings_manager import SettingsManager
from core.orchestrator import AmityOrchestrator
from core.pulse_engine import PulseEngine
from core.agent_manager import AgentManager
from gui.main_window import MainWindow, AgentTabButton
from gui.agent_view import AgentView
from gui.chatroom_view import ChatroomView


@pytest.fixture(scope="session")
def qapp():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication.instance()
    if app is None:
        app = QApplication(["-platform", "offscreen"])
    return app


@pytest.fixture
def temp_agent_dir(monkeypatch):
    temp_dir = tempfile.mkdtemp(prefix="openamity_pause_test_")
    monkeypatch.setattr("config.paths.get_app_data_dir", lambda: temp_dir)
    monkeypatch.setattr("config.paths.get_base_dir_for",
                        lambda aid: os.path.join(temp_dir, "agents", aid) if aid else os.path.join(temp_dir, "agents", "default"))
    monkeypatch.setattr("config.paths.get_mempalace_dir",
                        lambda aid: os.path.join(temp_dir, "agents", aid, "mempalace") if aid else os.path.join(temp_dir, "agents", "default", "mempalace"))
    monkeypatch.setattr("config.paths.get_chatroom_dir", lambda: os.path.join(temp_dir, "chatroom"))

    SettingsManager.clear_instances()
    yield temp_dir
    SettingsManager.clear_instances()
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_settings_pause_default_and_persistence(temp_agent_dir):
    sm = SettingsManager(agent_id="test_agent")
    assert sm.get("core.paused") is False

    sm.set("core.paused", True)
    sm.save()

    sm2 = SettingsManager(agent_id="test_agent")
    assert sm2.get("core.paused") is True


def test_orchestrator_pause_and_resume(temp_agent_dir):
    orch = AmityOrchestrator.__new__(AmityOrchestrator)
    orch.agent_id = "test_agent"
    orch.settings_manager = SettingsManager(agent_id="test_agent")
    orch.is_paused = False
    orch._pausing_in_progress = False
    orch.is_busy = False
    orch.is_thinking = False
    orch.active_subagents = {}
    orch.recent_history = []
    orch.speech_queue = []
    orch.event_queue = []
    orch.session_fatigue_tokens = 0
    orch.on_paused_state_changed = MagicMock()
    orch.on_pause_pending = MagicMock()
    orch.set_busy_state = MagicMock()
    orch.stop_all_processing = MagicMock()
    orch.append_to_conversation = MagicMock()
    orch.build_system_prompt = MagicMock()
    orch.system_prompt = "Test System Prompt"
    orch.cerebrum = MagicMock()
    orch.cerebrum.get_all_tool_declarations.return_value = ["tool_a"]
    orch.pulse_engine = MagicMock()
    orch.gemini_worker = MagicMock()
    orch.get_fatigue = MagicMock(return_value=0.0)

    # 1. Pause agent (low fatigue -> immediate finalize)
    orch.set_paused(True)
    assert orch.is_paused is True
    assert orch.settings_manager.get("core.paused") is True
    assert orch.stop_all_processing.called
    assert orch.gemini_worker.stop_session.called
    orch.on_paused_state_changed.emit.assert_called_with(True)

    # 2. Resume agent
    orch.set_paused(False)
    assert orch.is_paused is False
    assert orch.settings_manager.get("core.paused") is False
    assert orch.build_system_prompt.called
    assert orch.gemini_worker.start_session.called
    orch.on_paused_state_changed.emit.assert_called_with(False)


def test_orchestrator_pause_with_fatigue_consolidation(temp_agent_dir):
    orch = AmityOrchestrator.__new__(AmityOrchestrator)
    orch.agent_id = "test_agent"
    orch.settings_manager = SettingsManager(agent_id="test_agent")
    orch.is_paused = False
    orch._pausing_in_progress = False
    orch.is_busy = True
    orch.is_thinking = True
    orch.active_subagents = {}
    orch.recent_history = []
    orch.speech_queue = []
    orch.event_queue = []
    orch.session_fatigue_tokens = 50000
    orch.on_paused_state_changed = MagicMock()
    orch.on_pause_pending = MagicMock()
    orch.set_busy_state = MagicMock()
    orch.stop_all_processing = MagicMock()
    orch.append_to_conversation = MagicMock()
    orch.pulse_engine = MagicMock()
    orch.pulse_engine.settings_manager = orch.settings_manager
    orch.gemini_worker = MagicMock()
    orch.get_fatigue = MagicMock(return_value=0.10)  # >= 0.02 threshold

    with patch("threading.Timer") as mock_timer:
        orch.set_paused(True)
        assert orch._pausing_in_progress is True
        assert orch.on_pause_pending.emit.called
        assert orch.pulse_engine.fire_pulse.called
        assert mock_timer.called


def test_orchestrator_guards_when_paused(temp_agent_dir):
    orch = AmityOrchestrator.__new__(AmityOrchestrator)
    orch.agent_id = "test_agent"
    orch.settings_manager = SettingsManager(agent_id="test_agent")
    orch.is_paused = True
    orch.is_busy = False
    orch.is_thinking = False
    orch.gemini_worker = MagicMock()
    orch.audio_service = MagicMock()
    orch.event_queue = []
    orch._finalize_shutdown = MagicMock()

    # Pulse ignored
    orch.process_pulse("Test Pulse")
    assert not orch.gemini_worker.send_prompt.called

    # Input ignored
    orch.process_input("Hello")
    assert not orch.gemini_worker.send_prompt.called
    orch.process_text_input("Hello")
    assert len(orch.event_queue) == 0

    # Mic ignored
    orch.toggle_mic()
    assert not orch.audio_service.start_listening.called

    # Subagent rejected
    res = orch.spawn_subagent("Do something")
    assert "Error: Agent is currently paused." in res

    # Shutdown bypasses consolidation
    orch.get_fatigue = MagicMock(return_value=0.50)
    orch.shutdown(force_sleep=True)
    assert orch._finalize_shutdown.called


def test_pulse_engine_suspended_when_paused(temp_agent_dir):
    orch = MagicMock()
    orch.agent_id = "test_agent"
    orch.is_paused = True

    pe = PulseEngine.__new__(PulseEngine)
    pe.orchestrator = orch
    pe.agent_id = "test_agent"
    pe.trigger_pulse = MagicMock()
    pe.get_db_connection = MagicMock()

    # check_pulses must return early
    pe.check_pulses()
    assert not pe.get_db_connection.called

    # handle_whatsapp_message must return early
    pe.handle_whatsapp_message("12345@c.us", "Alice")
    assert not pe.trigger_pulse.emit.called


def test_gui_pause_button_and_layout(qapp, temp_agent_dir):
    orch = MagicMock()
    orch.agent_id = "test_agent"
    orch.is_paused = False
    orch.settings_manager = SettingsManager(agent_id="test_agent")
    from core.events import Signal
    orch.on_message_appended = Signal()
    orch.on_busy_state_changed = Signal()
    orch.on_amplitude_emitted = Signal()
    orch.on_paused_state_changed = Signal()
    orch.on_pause_pending = Signal()

    with patch("gui.main_window.SpellCheckHighlighter"):
        view = AgentView(orch)

    # 1. Verify btn_pause is to the left of text_input in footer_layout
    footer_items = [view.footer_layout.itemAt(i).widget() for i in range(view.footer_layout.count())]
    pause_idx = footer_items.index(view.btn_pause)
    input_idx = footer_items.index(view.text_input)
    assert pause_idx < input_idx
    assert pause_idx == 0  # First item in footer

    # 2. Verify initial state (playing/active)
    assert view.btn_pause.text() == "❚❚"
    idle_pause_css = view.btn_pause.styleSheet()
    assert "font-size: 10px" in idle_pause_css
    assert "padding-bottom" not in idle_pause_css
    assert "padding-top: 4px" in view.btn_mic.styleSheet()
    assert "padding-top: 5px" in view.btn_mute.styleSheet()
    assert view.text_input.isEnabled() is True

    # Verify mute toggle keeps vertical centering padding
    view.toggle_mute(True)
    assert "padding-top: 5px" in view.btn_mute.styleSheet()
    view.toggle_mute(False)
    assert "padding-top: 5px" in view.btn_mute.styleSheet()

    # 3. Simulate pausing
    orch.is_paused = True
    orch.on_paused_state_changed.emit(True)
    assert view.btn_pause.text() == "▶"
    pause_css = view.btn_pause.styleSheet()
    assert "#1b3820" in pause_css
    assert "#2e7d32" in pause_css
    assert "#81c784" in pause_css
    assert "padding-bottom: 4px" in pause_css
    assert "#4a3b10" not in pause_css
    assert "#FFD700" not in pause_css
    assert "#AA8800" not in pause_css
    assert view.text_input.isEnabled() is False
    assert "Agent is paused" in view.text_input.placeholderText()
    assert view.btn_send.isEnabled() is False
    assert view.btn_mic.isEnabled() is False

    # 4. Simulate unpausing
    orch.is_paused = False
    orch.on_paused_state_changed.emit(False)
    assert view.btn_pause.text() == "❚❚"
    unpaused_css = view.btn_pause.styleSheet()
    assert "font-size: 10px" in unpaused_css
    assert "padding-bottom" not in unpaused_css
    assert view.text_input.isEnabled() is True
    assert view.btn_send.isEnabled() is True
    assert view.btn_mic.isEnabled() is True


def test_chatroom_view_does_not_have_pause_button(qapp, temp_agent_dir):
    agent_manager = AgentManager()
    with patch("gui.main_window.SpellCheckHighlighter"):
        chatroom = ChatroomView(agent_manager)

    # Verify no btn_pause in chatroom view
    assert not hasattr(chatroom, "btn_pause")
    for i in range(chatroom.footer_layout.count()):
        widget = chatroom.footer_layout.itemAt(i).widget()
        if widget and hasattr(widget, "text"):
            assert widget.text() != "❚❚" and widget.text() != "▶"


def test_agent_tab_button_paused_indicator(qapp):
    btn = AgentTabButton("agent_1", "Nova")
    assert btn.is_paused is False
    assert "⏸" not in btn.sizeHint().width().__str__()

    btn.set_paused(True)
    assert btn.is_paused is True
    assert btn.activity_state == "idle"

    # Thinking state should be rejected while paused
    btn.set_activity_state("thinking")
    assert btn.activity_state == "idle"

    btn.set_paused(False)
    assert btn.is_paused is False


def test_agent_tab_button_paused_layout_and_neutral_colors(qapp):
    # 1. Main tab: idle unpaused vs paused
    main_btn = AgentTabButton("agent_1", "Nova")
    main_btn.setFixedWidth(180)
    main_draw_rect = main_btn.rect().adjusted(4, 2, -4, -2)
    main_fm = QFontMetrics(main_btn.font())

    watch_center, pause_rect, text_rect = main_btn._compute_content_layout(main_draw_rect, main_fm)
    assert watch_center is None
    assert pause_rect is None

    main_btn.set_paused(True)
    watch_center, pause_rect, text_rect = main_btn._compute_content_layout(main_draw_rect, main_fm)
    assert watch_center is None
    assert pause_rect is not None
    assert text_rect.right() < pause_rect.left()
    assert pause_rect.right() <= main_draw_rect.right() - 5

    # 2. Chatroom sub-item: watching unpaused vs watching + paused
    sub_btn = AgentTabButton("agent_1", "Nova", is_sub_item=True, is_watching=True)
    sub_btn.setFixedWidth(180)
    sub_draw_rect = sub_btn.rect().adjusted(18, 1, -4, -1)
    sub_fm = QFontMetrics(sub_btn.font())

    watch_center, pause_rect, text_rect = sub_btn._compute_content_layout(sub_draw_rect, sub_fm)
    assert watch_center is not None
    assert pause_rect is None
    assert text_rect.right() < watch_center.x() - 3

    sub_btn.set_paused(True)
    watch_center, pause_rect, text_rect = sub_btn._compute_content_layout(sub_draw_rect, sub_fm)
    assert watch_center is not None
    assert pause_rect is not None
    assert text_rect.right() < pause_rect.left()
    assert pause_rect.right() < watch_center.x() - 3

    # 3. Rendered pixel colors match normal unpaused idle/selected tabs (no amber tint or border)
    idle_main = AgentTabButton("agent_idle", "Nova")
    idle_main.setFixedWidth(180)
    paused_main = AgentTabButton("agent_paused", "Nova")
    paused_main.setFixedWidth(180)
    paused_main.set_paused(True)

    idle_img = idle_main.grab().toImage()
    paused_img = paused_main.grab().toImage()
    assert paused_img.pixelColor(90, 5) == QColor("#292929")
    assert paused_img.pixelColor(90, 5) == idle_img.pixelColor(90, 5)
    assert paused_img.pixelColor(90, 2) == idle_img.pixelColor(90, 2)

    idle_main.set_selected(True)
    paused_main.set_selected(True)
    idle_sel_img = idle_main.grab().toImage()
    paused_sel_img = paused_main.grab().toImage()
    assert paused_sel_img.pixelColor(90, 5) == QColor("#3a3a3a")
    assert paused_sel_img.pixelColor(90, 5) == idle_sel_img.pixelColor(90, 5)
    assert paused_sel_img.pixelColor(90, 2) == idle_sel_img.pixelColor(90, 2)

    idle_sub = AgentTabButton("agent_sub_idle", "Nova", is_sub_item=True, is_watching=True)
    idle_sub.setFixedWidth(180)
    paused_sub = AgentTabButton("agent_sub_paused", "Nova", is_sub_item=True, is_watching=True)
    paused_sub.setFixedWidth(180)
    paused_sub.set_paused(True)

    idle_sub_img = idle_sub.grab().toImage()
    paused_sub_img = paused_sub.grab().toImage()
    assert paused_sub_img.pixelColor(90, 5) == QColor("#262626")
    assert paused_sub_img.pixelColor(90, 5) == idle_sub_img.pixelColor(90, 5)
    assert paused_sub_img.pixelColor(90, 1) == idle_sub_img.pixelColor(90, 1)


def test_pulse_engine_shares_orchestrator_settings_manager(temp_agent_dir):
    orch = AmityOrchestrator.__new__(AmityOrchestrator)
    orch.agent_id = "test_shared_sm"
    orch.settings_manager = SettingsManager(agent_id="test_shared_sm")
    orch.is_paused = False
    orch.on_paused_state_changed = MagicMock()

    pe = PulseEngine(orch)
    assert pe.settings_manager is orch.settings_manager

    # Changing paused on orchestrator settings reflects immediately in pulse_engine
    orch.settings_manager.set("core.paused", True)
    assert pe.settings_manager.get("core.paused") is True

    orch.settings_manager.set("core.paused", False)
    assert pe.settings_manager.get("core.paused") is False


def test_unpaused_agent_persisted_across_shutdown_and_restart(temp_agent_dir):
    """
    Ensures that when a previously paused agent is unpaused and active for hours
    (accumulating contextual fatigue), shutdown consolidation does not revert
    core.paused back to True, and restarting the agent keeps it unpaused.
    """
    # 1. Agent starts paused in settings
    sm = SettingsManager(agent_id="agent_persist_test")
    sm.set("core.paused", True)
    sm.save()

    # 2. Start orchestrator
    orch = AmityOrchestrator.__new__(AmityOrchestrator)
    orch.agent_id = "agent_persist_test"
    orch.settings_manager = sm
    orch.is_paused = sm.get("core.paused", False)
    assert orch.is_paused is True

    orch._pausing_in_progress = False
    orch.is_busy = False
    orch.is_thinking = False
    orch.active_subagents = {}
    orch.recent_history = []
    orch.speech_queue = []
    orch.event_queue = []
    orch.session_fatigue_tokens = 0
    orch.on_paused_state_changed = MagicMock()
    orch.on_pause_pending = MagicMock()
    orch.set_busy_state = MagicMock()
    orch.stop_all_processing = MagicMock()
    orch.append_to_conversation = MagicMock()
    orch.build_system_prompt = MagicMock()
    orch.system_prompt = "Test System Prompt"
    orch.cerebrum = MagicMock()
    orch.cerebrum.get_all_tool_declarations.return_value = []
    orch.gemini_worker = MagicMock()
    orch.pulse_engine = PulseEngine(orch)

    # 3. Unpause agent
    orch.set_paused(False)
    assert orch.is_paused is False
    assert sm.get("core.paused") is False

    # 4. Agent is active for hours -> fatigue builds up
    orch.session_fatigue_tokens = 60000
    orch.get_fatigue = MagicMock(return_value=0.25)  # >= 0.02

    # 5. Shutdown with force_sleep=True (graceful consolidation on shutdown)
    orch.shutdown(force_sleep=True)

    # 6. Verify settings.json on disk has core.paused == False
    assert sm.get("core.paused") is False

    # 7. Start a fresh orchestrator instance as on app restart
    SettingsManager.clear_instances()
    sm_reloaded = SettingsManager(agent_id="agent_persist_test")
    assert sm_reloaded.get("core.paused") is False

    orch_restart = AmityOrchestrator.__new__(AmityOrchestrator)
    orch_restart.agent_id = "agent_persist_test"
    orch_restart.settings_manager = sm_reloaded
    orch_restart.is_paused = sm_reloaded.get("core.paused", False)

    assert orch_restart.is_paused is False


def test_paused_agent_persisted_across_shutdown_and_restart(temp_agent_dir):
    """
    Ensures that when an agent is paused, shutting down and restarting keeps it paused.
    """
    sm = SettingsManager(agent_id="agent_paused_persist_test")
    sm.set("core.paused", False)
    sm.save()

    orch = AmityOrchestrator.__new__(AmityOrchestrator)
    orch.agent_id = "agent_paused_persist_test"
    orch.settings_manager = sm
    orch.is_paused = False
    orch._pausing_in_progress = False
    orch.is_busy = False
    orch.is_thinking = False
    orch.active_subagents = {}
    orch.recent_history = []
    orch.speech_queue = []
    orch.event_queue = []
    orch.session_fatigue_tokens = 0
    orch.on_paused_state_changed = MagicMock()
    orch.on_pause_pending = MagicMock()
    orch.set_busy_state = MagicMock()
    orch.stop_all_processing = MagicMock()
    orch.append_to_conversation = MagicMock()
    orch.build_system_prompt = MagicMock()
    orch.cerebrum = MagicMock()
    orch.gemini_worker = MagicMock()
    orch.get_fatigue = MagicMock(return_value=0.0)
    orch.pulse_engine = PulseEngine(orch)

    # Pause agent
    orch.set_paused(True)
    assert orch.is_paused is True
    assert sm.get("core.paused") is True

    # Shutdown
    orch.shutdown(force_sleep=False)
    assert sm.get("core.paused") is True

    # Restart
    SettingsManager.clear_instances()
    sm_reloaded = SettingsManager(agent_id="agent_paused_persist_test")
    assert sm_reloaded.get("core.paused") is True

    orch_restart = AmityOrchestrator.__new__(AmityOrchestrator)
    orch_restart.agent_id = "agent_paused_persist_test"
    orch_restart.settings_manager = sm_reloaded
    orch_restart.is_paused = sm_reloaded.get("core.paused", False)

    assert orch_restart.is_paused is True


def test_restored_agent_backup_and_pause_persistence(temp_agent_dir):
    """
    Verifies that when a previously paused agent is backed up, restored, and unpaused,
    and active for hours, shutting down and restarting preserves the unpaused state.
    """
    from core.backup_manager import create_agent_backup, restore_agent_backup
    from core.agent_manager import AgentManager

    agent_id = "test_restore_agent"
    am = AgentManager()
    am.create_agent(agent_id)

    sm = SettingsManager(agent_id=agent_id)
    sm.set("core.paused", True)
    sm.save()

    # Create backup while paused
    backup_file = create_agent_backup(agent_id, agent_manager=am)
    assert os.path.exists(backup_file)

    # Restore backup
    success, msg, details = restore_agent_backup(backup_file, agent_manager=am)
    assert success is True

    # Start restored orchestrator
    restored_id = details["agent_id"]
    sm_restored = SettingsManager(agent_id=restored_id)
    assert sm_restored.get("core.paused") is True

    orch = AmityOrchestrator.__new__(AmityOrchestrator)
    orch.agent_id = restored_id
    orch.settings_manager = sm_restored
    orch.is_paused = sm_restored.get("core.paused", False)
    orch._pausing_in_progress = False
    orch.is_busy = False
    orch.is_thinking = False
    orch.active_subagents = {}
    orch.recent_history = []
    orch.speech_queue = []
    orch.event_queue = []
    orch.session_fatigue_tokens = 0
    orch.on_paused_state_changed = MagicMock()
    orch.on_pause_pending = MagicMock()
    orch.set_busy_state = MagicMock()
    orch.stop_all_processing = MagicMock()
    orch.append_to_conversation = MagicMock()
    orch.build_system_prompt = MagicMock()
    orch.system_prompt = "Restored Prompt"
    orch.cerebrum = MagicMock()
    orch.cerebrum.get_all_tool_declarations.return_value = []
    orch.gemini_worker = MagicMock()
    orch.pulse_engine = PulseEngine(orch)

    # Agent is unpaused / restored to online
    orch.set_paused(False)
    assert orch.is_paused is False
    assert sm_restored.get("core.paused") is False

    # Active for hours -> fatigue
    orch.session_fatigue_tokens = 80000
    orch.get_fatigue = MagicMock(return_value=0.30)

    # Shutdown with memory consolidation
    orch.shutdown(force_sleep=True)

    # Verify settings on disk preserved False
    assert sm_restored.get("core.paused") is False

    # Simulate app restart
    SettingsManager.clear_instances()
    sm_restart = SettingsManager(agent_id=restored_id)
    assert sm_restart.get("core.paused") is False

    orch_restarted = AmityOrchestrator.__new__(AmityOrchestrator)
    orch_restarted.agent_id = restored_id
    orch_restarted.settings_manager = sm_restart
    orch_restarted.is_paused = sm_restart.get("core.paused", False)

    assert orch_restarted.is_paused is False


