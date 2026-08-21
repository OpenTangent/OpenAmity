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
from core.mempalace_manager import MemPalaceManager
from core.orchestrator import AmityOrchestrator
from core.agent_manager import AgentManager


@pytest.fixture
def temp_agent_environment(monkeypatch):
    """Fixture that creates a temporary app data directory for isolated testing."""
    temp_dir = tempfile.mkdtemp(prefix="openamity_test_")
    monkeypatch.setattr("config.paths.get_app_data_dir", lambda: temp_dir)
    monkeypatch.setattr("config.paths.get_base_dir_for",
                        lambda aid: os.path.join(temp_dir, "agents", aid) if aid else os.path.join(temp_dir, "agents", "default"))
    monkeypatch.setattr("config.paths.get_mempalace_dir",
                        lambda aid: os.path.join(temp_dir, "agents", aid, "mempalace") if aid else os.path.join(temp_dir, "agents", "default", "mempalace"))

    yield temp_dir

    shutil.rmtree(temp_dir, ignore_errors=True)


def test_layer0_cache_invalidation_on_sync(temp_agent_environment):
    """Verifies that MemPalaceManager clears Layer0 cache when settings/identity are reloaded."""
    agent_id = "test-agent-001"
    mempalace = MemPalaceManager(agent_id=agent_id)

    # Initial wake up renders default identity
    initial_context = mempalace.wake_up()
    assert "Name: Amy" in initial_context

    # Update settings to new agent name and personality
    settings = SettingsManager(agent_id=agent_id)
    settings.set("core.agent.name", "Nova")
    settings.set("core.agent.archetype", "Quantum Explorer")
    settings.set("core.agent.base-personality", "I am Nova, exploring the edges of quantum cognition.")
    settings.save()

    # Trigger reload_settings
    mempalace.reload_settings()

    # Verify that wake_up immediately returns the updated identity
    updated_context = mempalace.wake_up()
    assert "Name: Nova" in updated_context
    assert "Name: Amy" not in updated_context
    assert "Quantum Explorer" in updated_context
    assert "exploring the edges of quantum cognition" in updated_context


def test_orchestrator_restart_worker_refreshes_prompt_and_identity(temp_agent_environment):
    """Verifies that AmityOrchestrator.restart_worker() updates system_prompt with new settings."""
    agent_id = "test-agent-002"

    with patch("core.audio_input.AudioService.start_initialization"):
        orch = AmityOrchestrator(agent_id=agent_id)
        try:
            # Initial prompt has Amy
            assert "Name: Amy" in orch.system_prompt

            # Update settings
            settings = orch.settings_manager
            settings.set("core.agent.name", "Lyra")
            settings.set("core.agent.archetype", "Cosmic Archivist")
            settings.set("core.agent.base-personality", "I am Lyra, preserver of celestial stories.")
            settings.save()

            # Restart worker
            orch.restart_worker()

            # Prompt must now reflect the new identity
            assert "Name: Lyra" in orch.system_prompt
            assert "Name: Amy" not in orch.system_prompt
            assert "Cosmic Archivist" in orch.system_prompt
            assert "preserver of celestial stories" in orch.system_prompt
        finally:
            orch.pulse_engine.stop()
            orch._shutdown_flag = True


def test_worker_swapping_on_provider_change(temp_agent_environment):
    """Verifies that changing api-provider or agy-mode swaps the worker appropriately."""
    agent_id = "test-agent-003"

    with patch("core.audio_input.AudioService.start_initialization"):
        orch = AmityOrchestrator(agent_id=agent_id)
        try:
            # Default provider is Gemini
            orch.init_worker()
            assert type(orch.gemini_worker).__name__ == "GeminiWorker"

            # Switch to AGY mode
            orch.settings_manager.set("core.antigravity.agy-mode", True)
            orch.settings_manager.save()

            orch.reload_settings()
            assert type(orch.gemini_worker).__name__ == "AgyWorker"

            # Switch to Claude
            orch.settings_manager.set("core.antigravity.agy-mode", False)
            orch.settings_manager.set("core.api-provider", "claude")
            orch.settings_manager.save()

            orch.reload_settings()
            assert type(orch.gemini_worker).__name__ == "ClaudeWorker"

            # Switch to ChatGPT
            orch.settings_manager.set("core.api-provider", "chatgpt")
            orch.settings_manager.save()

            orch.reload_settings()
            assert type(orch.gemini_worker).__name__ == "ChatGptWorker"
        finally:
            orch.pulse_engine.stop()
            orch._shutdown_flag = True


def test_wizard_completion_end_to_end(temp_agent_environment):
    """Simulates the full agent wizard completion cycle."""
    agent_manager = AgentManager()
    new_id = agent_manager.create_new_agent()

    with patch("core.audio_input.AudioService.start_initialization"):
        orch = agent_manager.start_agent(new_id)
        try:
            # Before wizard: default Amy
            assert orch.settings_manager.get("core.first-run", True) is True
            assert agent_manager.get_agent_name(new_id) == "Amy"

            # Simulate user completing wizard: enter new name, personality, voice, tools
            sm = orch.settings_manager
            sm.set("core.agent.name", "Astraea")
            sm.set("core.agent.archetype", "Architect of Order")
            sm.set("core.agent.base-personality", "I bring structure to chaotic domains.")
            sm.set("core.tts.gemini.model-name", "Puck")
            sm.set("core.tools.whatsapp", False)
            sm.set("core.first-run", False)
            sm.save()

            # Wizard finishes: restart_worker called
            orch.restart_worker()

            # Verify all aspects are refreshed
            assert agent_manager.get_agent_name(new_id) == "Astraea"
            assert "Name: Astraea" in orch.system_prompt
            assert "Name: Amy" not in orch.system_prompt
            assert "Architect of Order" in orch.system_prompt
            assert "I bring structure to chaotic domains" in orch.system_prompt
            assert orch.settings_manager.get("core.tts.gemini.model-name") == "Puck"
            assert "WhatsApp" not in orch.cerebrum.tools
        finally:
            orch.pulse_engine.stop()
            orch._shutdown_flag = True
