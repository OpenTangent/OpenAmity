import os
import sys
import pytest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.orchestrator import AmityOrchestrator


def test_post_sleep_prompt_rebuilt_and_lifecycle_feedback():
    orch = AmityOrchestrator.__new__(AmityOrchestrator)
    orch.agent_id = "test_sleep_agent"
    orch.is_thinking = False
    orch.tts_worker = None
    orch.speech_queue = []
    orch.event_queue = []
    orch._is_sleep_cycle = True
    orch.last_action_result = None

    orch.build_system_prompt = MagicMock()
    orch.cerebrum = MagicMock()
    orch.cerebrum.get_all_tool_declarations.return_value = ["mock_tool"]
    orch.system_prompt = "Freshly rebuilt post-sleep prompt"
    orch.set_busy_state = MagicMock()

    mock_worker = MagicMock()
    orch.gemini_worker = mock_worker

    # Execute check_cycle_completion
    orch.check_cycle_completion()

    # 1. Verify sleep cycle flag reset
    assert orch._is_sleep_cycle is False

    # 2. Verify system prompt was rebuilt (E3)
    assert orch.build_system_prompt.called

    # 3. Verify lifecycle event feedback set for next prompt
    assert orch.last_action_result is not None
    assert "[LIFECYCLE EVENT:" in orch.last_action_result
    assert "memory consolidation" in orch.last_action_result

    # 4. Verify worker session was stopped and restarted with fresh prompt
    assert mock_worker.stop_session.called
    assert mock_worker.start_session.called
    assert mock_worker.start_session.call_args[0][0] == "Freshly rebuilt post-sleep prompt"
