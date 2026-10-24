import sys
import os
import json
import shutil
import tempfile
import pytest
from unittest.mock import MagicMock, patch

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.settings_manager import SettingsManager
from core.orchestrator import AmityOrchestrator
from core.claude_worker import ClaudeWorker
from core.chatgpt_worker import ChatGptWorker
from core.deepseek_worker import DeepSeekWorker
from core.openrouter_worker import OpenRouterWorker
from core.agy_worker import AgyWorker
from core.gemini_worker import GeminiWorker


@pytest.fixture
def temp_agent_environment(monkeypatch):
    """Fixture that creates a temporary app data directory for isolated testing."""
    temp_dir = tempfile.mkdtemp(prefix="openamity_graceful_test_")
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


def test_settings_change_while_thinking_defers_reload(temp_agent_environment):
    """Verifies that calling restart_worker() while an agent is thinking does NOT interrupt thoughts or teardown worker."""
    agent_id = "test-agent-graceful-001"

    with patch("core.audio_input.AudioService.start_initialization"):
        orch = AmityOrchestrator(agent_id=agent_id)
        try:
            orch.init_worker()
            original_worker = orch.gemini_worker
            assert original_worker is not None

            # Simulate active thinking turn
            orch.is_thinking = True

            # User changes settings in the background
            orch.settings_manager.set("core.agent.name", "Lyra")
            orch.settings_manager.save()

            # Attempt restart while thinking
            orch.restart_worker()

            # Thought must NOT be broken: worker must remain alive, and reload must be marked pending
            assert orch._pending_settings_reload is True
            assert orch.gemini_worker is original_worker
            assert "Name: Lyra" not in orch.system_prompt  # Not reloaded yet

            # Simulate end of thought process
            orch.finish_thinking()

            # The pending reload should have automatically drained and applied
            assert orch._pending_settings_reload is False
            assert "Name: Lyra" in orch.system_prompt
        finally:
            orch.pulse_engine.stop()
            orch._shutdown_flag = True


def test_session_history_persisted_across_settings_reload(temp_agent_environment):
    """Verifies that conversational context is retained across settings changes."""
    agent_id = "test-agent-graceful-002"

    with patch("core.audio_input.AudioService.start_initialization"):
        orch = AmityOrchestrator(agent_id=agent_id)
        try:
            # Switch to claude provider for clear memory inspection
            orch.settings_manager.set("core.api-provider", "claude")
            orch.settings_manager.set_env("CLAUDE_API_KEY", "sk-mock-key")
            orch.settings_manager.save()
            orch.reload_settings()

            worker = orch.gemini_worker
            assert type(worker).__name__ == "ClaudeWorker"

            # Populate history with conversational exchange
            worker.history = [
                {"role": "user", "content": "What is our mission?"},
                {"role": "assistant", "content": "Our mission is exploring deep neural cosmos."}
            ]

            # User edits settings: change archetype and thinking model
            orch.settings_manager.set("core.agent.archetype", "Starlight Cartographer")
            orch.settings_manager.set("core.claude.model", "claude-fable-5-1")
            orch.settings_manager.save()

            # Restart worker when idle
            orch.restart_worker()

            # Worker should be reconfigured, system prompt updated, but history PRESERVED
            assert "Starlight Cartographer" in orch.system_prompt
            assert len(worker.history) == 2
            assert worker.history[0]["content"] == "What is our mission?"
            assert worker.history[1]["content"] == "Our mission is exploring deep neural cosmos."
        finally:
            orch.pulse_engine.stop()
            orch._shutdown_flag = True


def test_session_history_migrated_on_provider_change(temp_agent_environment):
    """Verifies that changing provider migrates session context from the old worker to the new worker."""
    agent_id = "test-agent-graceful-003"

    with patch("core.audio_input.AudioService.start_initialization"):
        orch = AmityOrchestrator(agent_id=agent_id)
        try:
            # Start with Claude
            orch.settings_manager.set("core.api-provider", "claude")
            orch.settings_manager.set_env("CLAUDE_API_KEY", "sk-mock-key")
            orch.settings_manager.save()
            orch.reload_settings()

            claude_worker = orch.gemini_worker
            assert type(claude_worker).__name__ == "ClaudeWorker"

            claude_worker.history = [
                {"role": "user", "content": "Tell me about Andromeda."},
                {"role": "assistant", "content": "Andromeda is our nearest spiral galactic neighbor."}
            ]

            # Switch provider to ChatGPT
            orch.settings_manager.set("core.api-provider", "chatgpt")
            orch.settings_manager.set_env("OPENAI_API_KEY", "sk-mock-openai-key")
            orch.settings_manager.save()

            orch.reload_settings()

            new_worker = orch.gemini_worker
            assert type(new_worker).__name__ == "ChatGptWorker"

            # Check that conversation history was migrated!
            exported = new_worker.get_session_history()
            assert len(exported) >= 2
            assert any("Tell me about Andromeda." in m["content"] for m in exported)
            assert any("Andromeda is our nearest spiral galactic neighbor." in m["content"] for m in exported)
        finally:
            orch.pulse_engine.stop()
            orch._shutdown_flag = True


def test_worker_swap_from_gemini_to_openrouter_migrates_session_history(temp_agent_environment):
    """Verifies that changing provider from Gemini to OpenRouter migrates session context including tool turns without error."""
    agent_id = "test-agent-graceful-gemini-openrouter"

    with patch("core.audio_input.AudioService.start_initialization"), \
         patch("google.genai.Client"):
        from google.genai import types
        orch = AmityOrchestrator(agent_id=agent_id)
        try:
            # Start with Gemini
            orch.settings_manager.set("core.api-provider", "gemini")
            orch.settings_manager.set_env("GEMINI_API_KEY", "sk-mock-gemini-key")
            orch.settings_manager.save()
            orch.reload_settings()

            gemini_worker = orch.gemini_worker
            assert type(gemini_worker).__name__ == "GeminiWorker"

            user_part = types.Part.from_text(text="Research black holes.")
            turn1 = types.Content(role="user", parts=[user_part])
            fc_part = types.Part.from_function_call(name="Terminal_run_command", args={"command": "echo test"})
            turn2 = types.Content(role="model", parts=[fc_part])
            fr_part = types.Part.from_function_response(name="Terminal_run_command", response={"output": "test"})
            turn3 = types.Content(role="user", parts=[fr_part])
            asst_part = types.Part.from_text(text="Black holes are regions of spacetime where gravity is extremely strong.")
            turn4 = types.Content(role="model", parts=[asst_part])

            gemini_worker.thinker_chat = MagicMock()
            gemini_worker.thinker_chat._curated_history = [turn1, turn2, turn3, turn4]

            # Switch provider to OpenRouter
            orch.settings_manager.set("core.api-provider", "openrouter")
            orch.settings_manager.set_env("OPENROUTER_API_KEY", "sk-mock-openrouter-key")
            orch.settings_manager.save()

            orch.reload_settings()

            new_worker = orch.gemini_worker
            assert type(new_worker).__name__ == "OpenRouterWorker"

            # Check that conversation history was migrated!
            exported = new_worker.get_session_history()
            assert len(exported) >= 4
            assert any("Research black holes." in m["content"] for m in exported)
            assert any("Terminal_run_command" in m["content"] for m in exported)
            assert any("Black holes are regions of spacetime" in m["content"] for m in exported)
        finally:
            orch.pulse_engine.stop()
            orch._shutdown_flag = True


def test_sleep_cycle_clears_session_history(temp_agent_environment):
    """Verifies that Memory Consolidation (Sleep Cycle) cleans active session history as required by the architecture."""
    agent_id = "test-agent-graceful-004"

    with patch("core.audio_input.AudioService.start_initialization"):
        orch = AmityOrchestrator(agent_id=agent_id)
        try:
            orch.settings_manager.set("core.api-provider", "claude")
            orch.settings_manager.set_env("CLAUDE_API_KEY", "sk-mock-key")
            orch.settings_manager.save()
            orch.reload_settings()

            worker = orch.gemini_worker
            worker.history = [
                {"role": "user", "content": "Consolidate this day."},
                {"role": "assistant", "content": "Reflecting on milestones achieved."}
            ]
            assert len(worker.history) == 2

            # Simulate Sleep Cycle completing
            orch._is_sleep_cycle = True
            orch.is_thinking = False
            orch.check_cycle_completion()

            # Upon sleep cycle completion, session history MUST be cleared
            assert len(worker.history) == 0
        finally:
            orch.pulse_engine.stop()
            orch._shutdown_flag = True
