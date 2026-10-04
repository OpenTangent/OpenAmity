import os
import sys
import tempfile
import shutil
from datetime import datetime
from unittest.mock import MagicMock, patch
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.mempalace_manager import MemPalaceManager
from core.orchestrator import AmityOrchestrator
from core.settings_manager import SettingsManager


@pytest.fixture
def temp_agent_env(monkeypatch):
    temp_dir = tempfile.mkdtemp(prefix="openamity_test_wake_")
    monkeypatch.setattr("config.paths.get_app_data_dir", lambda: temp_dir)
    monkeypatch.setattr("config.paths.get_config_file", lambda: os.path.join(temp_dir, "config.json"))
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: os.path.join(temp_dir, "agents", aid or "default"))
    monkeypatch.setattr("config.paths.get_mempalace_dir", lambda aid: os.path.join(temp_dir, "agents", aid or "default", "mempalace"))
    monkeypatch.setattr("config.paths.get_settings_file", lambda aid=None: os.path.join(temp_dir, "agents", aid or "default", "settings.json"))

    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_mempalace_wake_up_includes_current_datetime(temp_agent_env):
    """Verify that MemPalaceManager.wake_up includes the temporal wake context."""
    mp = MemPalaceManager(agent_id="test-wake-agent")
    context = mp.wake_up()

    assert "[Wake Context: Current Date & Time is " in context
    now = datetime.now()
    assert str(now.year) in context


def test_orchestrator_startup_lifecycle_event_has_datetime(temp_agent_env):
    """Verify that Orchestrator initializes with a startup lifecycle event containing the timestamp."""
    with patch("core.orchestrator.AudioService"), \
         patch("core.orchestrator.PulseEngine"):
        orch = AmityOrchestrator(agent_id="test-orch-startup")
        assert orch.last_action_result is not None
        assert "[LIFECYCLE EVENT: Current time:" in orch.last_action_result
        assert "Open Amity initialized and ready" in orch.last_action_result
        now = datetime.now()
        assert str(now.year) in orch.last_action_result


def test_orchestrator_unpause_sets_lifecycle_datetime(temp_agent_env):
    """Verify that resuming/unpausing an agent sets last_action_result with current time."""
    with patch("core.orchestrator.AudioService"), \
         patch("core.orchestrator.PulseEngine"):
        orch = AmityOrchestrator(agent_id="test-orch-unpause")
        orch.is_paused = True
        orch.last_action_result = None

        orch._resume_agent()

        assert orch.is_paused is False
        assert orch.last_action_result is not None
        assert "[LIFECYCLE EVENT: Current time:" in orch.last_action_result
        assert "You have been unpaused and resumed operations" in orch.last_action_result
        now = datetime.now()
        assert str(now.year) in orch.last_action_result


def test_orchestrator_post_sleep_cycle_has_datetime(temp_agent_env):
    """Verify that completing a sleep cycle adds current time to the waking lifecycle event."""
    with patch("core.orchestrator.AudioService"), \
         patch("core.orchestrator.PulseEngine"):
        orch = AmityOrchestrator(agent_id="test-orch-sleep")
        orch._is_sleep_cycle = True
        orch.is_thinking = False
        orch.tts_worker = None
        orch.speech_queue = []

        orch.check_cycle_completion()

        assert orch._is_sleep_cycle is False
        assert orch.last_action_result is not None
        assert "[LIFECYCLE EVENT: Current time:" in orch.last_action_result
        assert "awoken to a new waking cycle" in orch.last_action_result
        now = datetime.now()
        assert str(now.year) in orch.last_action_result


def test_process_pulse_forwards_last_action_result(temp_agent_env):
    """Verify that process_pulse forwards last_action_result when present into worker send_prompt."""
    with patch("core.orchestrator.AudioService"), \
         patch("core.orchestrator.PulseEngine"):
        orch = AmityOrchestrator(agent_id="test-orch-pulse-fwd")
        orch.gemini_worker = MagicMock()
        orch.gemini_worker.available = True
        orch.gemini_worker.running = True

        orch.last_action_result = "[LIFECYCLE EVENT: Current time: 2026-10-02 10:44:34]"

        orch.process_pulse(text="Scheduled routine", purpose="Scheduled routine")

        assert orch.gemini_worker.send_prompt.called
        sent_prompt = orch.gemini_worker.send_prompt.call_args[0][0]
        assert "[System Feedback from previous turn]: [LIFECYCLE EVENT: Current time: 2026-10-02 10:44:34]" in sent_prompt
        assert "Scheduled routine" in sent_prompt
        # Ensure last_action_result was cleared
        assert orch.last_action_result is None
