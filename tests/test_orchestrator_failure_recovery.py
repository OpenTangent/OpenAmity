import os
import sys
import tempfile
import shutil
import pytest
from unittest.mock import MagicMock, patch

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.settings_manager import SettingsManager
from core.orchestrator import AmityOrchestrator


@pytest.fixture
def temp_orchestrator_env(monkeypatch):
    temp_dir = tempfile.mkdtemp(prefix="openamity_orch_recovery_")
    monkeypatch.setattr("config.paths.get_app_data_dir", lambda: temp_dir)
    monkeypatch.setattr(
        "config.paths.get_base_dir_for",
        lambda aid: os.path.join(temp_dir, "agents", aid) if aid else os.path.join(temp_dir, "agents", "default")
    )
    monkeypatch.setattr(
        "config.paths.get_mempalace_dir",
        lambda aid: os.path.join(temp_dir, "agents", aid, "mempalace") if aid else os.path.join(temp_dir, "agents", "default", "mempalace")
    )

    yield temp_dir

    SettingsManager.clear_instances()
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_process_pulse_heals_stale_busy_state(temp_orchestrator_env):
    """Verifies that if an agent was stranded in a stale busy state, process_pulse
    self-heals the state and continues executing the pulse normally."""
    agent_id = "test-agent-recovery-001"

    with patch("core.audio_input.AudioService.start_initialization"):
        orch = AmityOrchestrator(agent_id=agent_id)
        orch.init_worker()
        assert orch.gemini_worker is not None

        # Mock the worker
        orch.gemini_worker.is_processing = False
        orch.gemini_worker.running = True
        orch.gemini_worker.available = True
        orch.gemini_worker.send_prompt = MagicMock()

        # Simulate stale busy state (e.g. from a prior crashed thread)
        orch.is_busy = True
        orch.is_thinking = True
        orch.event_queue = [{"type": "pulse", "text": "Old stale pulse", "purpose": "Scheduled routine"}]

        assert orch._is_truly_busy() is False

        orch.last_action_result = None

        # Fire a new autonomy pulse
        orch.process_pulse("Autonomy Pulse: Check tasks", purpose="Scheduled routine")

        # The stale state should have been cleared, stale queue flushed, and new pulse sent
        assert orch.is_thinking is True
        assert orch.is_busy is True
        orch.gemini_worker.send_prompt.assert_called_once_with("Autonomy Pulse: Check tasks")


def test_handle_gemini_error_cleans_up_busy_and_thinking_state(temp_orchestrator_env):
    """Verifies that when an API error occurs, handle_gemini_error resets thinking and busy flags."""
    agent_id = "test-agent-recovery-002"

    with patch("core.audio_input.AudioService.start_initialization"):
        orch = AmityOrchestrator(agent_id=agent_id)
        orch.init_worker()

        orch.is_thinking = True
        orch.set_busy_state(True)

        orch.handle_gemini_error("OpenRouter API Error: Error code: 404")

        assert orch.is_thinking is False
        assert orch.is_busy is False
        # Verify warning was appended to conversation
        assert any("404" in text for sender, text in orch.recent_history if sender == "System Warning")


def test_async_query_prep_exception_recovers_state(temp_orchestrator_env):
    """Verifies that an unhandled exception in _async_query_prep does not strand the agent."""
    agent_id = "test-agent-recovery-003"

    with patch("core.audio_input.AudioService.start_initialization"):
        orch = AmityOrchestrator(agent_id=agent_id)
        orch.init_worker()

        orch.is_thinking = True
        orch.set_busy_state(True)
        orch.current_user_prompt = "Hello"

        # Force send_prompt to raise
        orch.gemini_worker.send_prompt = MagicMock(side_effect=RuntimeError("Worker socket died"))

        orch._async_query_prep("Hello")

        # State must be safely recovered to idle
        assert orch.is_thinking is False
        assert orch.is_busy is False
        assert any("Query preparation failed" in text for sender, text in orch.recent_history)


def test_async_tool_execution_exception_recovers_state(temp_orchestrator_env):
    """Verifies that an unhandled exception during tool execution resets _is_executing_tools and recovers state."""
    agent_id = "test-agent-recovery-004"

    with patch("core.audio_input.AudioService.start_initialization"):
        orch = AmityOrchestrator(agent_id=agent_id)
        orch.init_worker()

        orch.is_thinking = True
        orch.set_busy_state(True)

        call = MagicMock()
        call.name = "Corrupted_tool"
        call.args = {}

        # Force cerebrum to raise an unexpected error
        orch.cerebrum.execute_tool_call = MagicMock(side_effect=RuntimeError("Cerebrum explosion"))
        orch.on_tool_execution_finished = MagicMock(side_effect=RuntimeError("Post-execution failure"))

        orch._async_tool_execution([call], 0)

        assert getattr(orch, "_is_executing_tools", False) is False
        assert orch.is_thinking is False
        assert orch.is_busy is False
        assert any("Tool execution failed" in text for sender, text in orch.recent_history)


def test_tts_error_does_not_leave_agent_speaking_or_busy(temp_orchestrator_env):
    """Verifies that a TTS error finishes the playback cycle cleanly."""
    agent_id = "test-agent-recovery-005"

    with patch("core.audio_input.AudioService.start_initialization"):
        orch = AmityOrchestrator(agent_id=agent_id)
        orch.tts_worker = MagicMock()
        orch.tts_worker.is_alive.return_value = False

        orch._on_tts_error("TTS API error: quota exhausted")

        assert orch.tts_worker is None
        assert orch.is_busy is False


def test_process_pulse_prompt_dispatch_exception_recovers_state(temp_orchestrator_env):
    """Verifies that if send_prompt raises an exception synchronously during pulse dispatch,
    the orchestrator recovers immediately and does not remain stuck."""
    agent_id = "test-agent-recovery-006"

    with patch("core.audio_input.AudioService.start_initialization"):
        orch = AmityOrchestrator(agent_id=agent_id)
        orch.init_worker()

        orch.gemini_worker.running = True
        orch.gemini_worker.available = True
        orch.gemini_worker.send_prompt = MagicMock(side_effect=RuntimeError("Worker offline"))

        orch.process_pulse("Autonomy Pulse", purpose="Scheduled routine")

        # Must have caught the exception, logged warning, and returned to idle
        assert orch.is_thinking is False
        assert orch.is_busy is False
        assert any("Pulse prompt dispatch failed" in text for sender, text in orch.recent_history)
