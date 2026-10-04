import os
import sys
import tempfile
import shutil
import time
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

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
    assert view.text_input.isEnabled() is True

    # 3. Simulate pausing
    orch.is_paused = True
    orch.on_paused_state_changed.emit(True)
    assert view.btn_pause.text() == "▶"
    assert view.text_input.isEnabled() is False
    assert "Agent is paused" in view.text_input.placeholderText()
    assert view.btn_send.isEnabled() is False
    assert view.btn_mic.isEnabled() is False

    # 4. Simulate unpausing
    orch.is_paused = False
    orch.on_paused_state_changed.emit(False)
    assert view.btn_pause.text() == "❚❚"
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


