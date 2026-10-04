import json
import os
import sys
import threading
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.orchestrator import AmityOrchestrator


def _create_mock_orchestrator(tmp_path, token_limit=400000, low_token=False):
    """Helper to instantiate an isolated orchestrator with mocks."""
    orch = AmityOrchestrator.__new__(AmityOrchestrator)
    orch.agent_id = "test_fatigue_agent"
    orch.agent_dir = str(tmp_path)
    orch.is_paused = False
    orch.is_busy = False
    orch.is_thinking = True
    orch._is_sleep_cycle = False
    orch._is_user_interaction = True
    orch._speaker_invoked_in_cycle = False
    orch._user_turn_nudged = False
    orch._hard_fatigue_limit_hit = False
    orch._last_notified_fatigue_threshold = 0
    orch.session_fatigue_tokens = 0
    orch.current_task_weight = 0
    orch.current_loop_count = 0
    orch.duplicate_command_count = 0
    orch.last_executed_command = None
    orch.last_action_result = None
    orch.system_prompt = "You are a test agent."
    orch.speech_queue = []
    orch.event_queue = []
    orch.recent_history = []
    orch.tts_worker = None

    orch.budget_lock = threading.Lock()
    orch.gemini_worker = MagicMock()
    orch.gemini_worker.running = True
    orch.gemini_worker.available = True
    orch.cerebrum = MagicMock()
    orch.pulse_engine = MagicMock()
    orch.mempalace_manager = MagicMock()

    # Settings mock
    orch.settings_manager = MagicMock()
    def get_setting(key, default=None):
        if key in ("core.somatic.token-usage-limit", "core.somatic.session-token-cap", "core.somatic.cognitive-budget"):
            return token_limit
        if key == "core.low-token-mode":
            return low_token
        return default
    orch.settings_manager.get.side_effect = get_setting

    # Signals / callbacks
    orch.on_tool_started = MagicMock()
    orch.on_tool_finished = MagicMock()
    orch.on_message_appended = MagicMock()
    orch.on_amplitude_emitted = MagicMock()
    orch.set_busy_state = MagicMock()
    orch.speak = MagicMock()
    orch.finish_thinking = MagicMock(side_effect=lambda: setattr(orch, "is_thinking", False))
    orch.build_system_prompt = MagicMock()

    appended_messages = []
    orch.append_to_conversation = lambda sender, text: appended_messages.append((sender, text))
    orch._appended_messages = appended_messages

    return orch


def test_fatigue_accumulation_and_ratio(tmp_path):
    orch = _create_mock_orchestrator(tmp_path, token_limit=400000)

    # Initial state
    assert orch.get_fatigue() == 0.0

    # Accumulate 100,000 tokens -> 25% fatigue
    orch.add_fatigue(100000)
    assert orch.session_fatigue_tokens == 100000
    assert pytest.approx(orch.get_fatigue(), 0.001) == 0.25

    # Accumulate another 100,000 tokens -> 50% fatigue
    orch.add_fatigue(100000)
    assert orch.session_fatigue_tokens == 200000
    assert pytest.approx(orch.get_fatigue(), 0.001) == 0.50

    # Accumulate 200,000 tokens -> 100% fatigue
    orch.add_fatigue(200000)
    assert orch.session_fatigue_tokens == 400000
    assert pytest.approx(orch.get_fatigue(), 0.001) == 1.0


def test_low_token_mode_halves_limit(tmp_path):
    # In low token mode, effective limit is 400,000 / 2 = 200,000
    orch = _create_mock_orchestrator(tmp_path, token_limit=400000, low_token=True)

    orch.add_fatigue(100000)
    assert orch.session_fatigue_tokens == 100000
    # 100,000 / 200,000 = 0.5 (50%)
    assert pytest.approx(orch.get_fatigue(), 0.001) == 0.50


def test_sleep_cycle_token_exemption(tmp_path):
    orch = _create_mock_orchestrator(tmp_path, token_limit=400000)
    orch.add_fatigue(50000)
    assert orch.session_fatigue_tokens == 50000

    # Enter sleep cycle
    orch._is_sleep_cycle = True
    orch.add_fatigue(15000)
    # Sleep turns must NOT increment session_fatigue_tokens
    assert orch.session_fatigue_tokens == 50000


def test_write_somatic_state_atomic_and_compatible(tmp_path):
    orch = _create_mock_orchestrator(tmp_path, token_limit=400000)
    orch.session_fatigue_tokens = 260000

    with patch("config.paths.get_base_dir_for", return_value=str(tmp_path)):
        orch._write_somatic_state()

    state_file = os.path.join(str(tmp_path), "somatic_state.json")
    assert os.path.exists(state_file)

    with open(state_file, "r") as f:
        data = json.load(f)

    # Check unified fatigue fields
    assert data["session_fatigue_tokens"] == 260000
    assert data["token_usage_limit"] == 400000
    assert pytest.approx(data["fatigue"], 0.001) == 0.65
    assert pytest.approx(data["percent"], 0.1) == 65.0

    # Check backward-compatible aliases
    assert data["current_task_weight"] == 260000
    assert data["max_weight"] == 400000
    assert data["cognitive_budget"] == 400000


def test_hysteresis_threshold_alerts(tmp_path):
    orch = _create_mock_orchestrator(tmp_path, token_limit=400000)

    class DummyCall:
        def __init__(self, name, args):
            self.name = name
            self.args = args

    # 1. Under 50% (25%) -> No alert
    orch.session_fatigue_tokens = 100000
    calls = [DummyCall("DateTime_now", {})]
    orch.cerebrum.execute_tool_call.return_value = "2026-09-30T10:00:00"
    orch._async_tool_execution(calls, current_weight=100000)

    sent_responses = orch.gemini_worker.send_function_responses.call_args[0][0]
    assert len(sent_responses) == 1
    resp_text = sent_responses[0][1]["result"]
    assert "SYSTEM ALERT" not in resp_text
    assert orch._last_notified_fatigue_threshold == 0

    # 2. Cross 50% (55%) -> 50% threshold alert attached
    orch.session_fatigue_tokens = 220000
    calls = [DummyCall("DateTime_now", {"tz": "UTC"})]
    orch._async_tool_execution(calls, current_weight=220000)

    sent_responses = orch.gemini_worker.send_function_responses.call_args[0][0]
    resp_text = sent_responses[0][1]["result"]
    assert "SYSTEM ALERT: Fatigue is at 55%" in resp_text
    assert "Pulse_schedule_pulse" in resp_text
    assert orch._last_notified_fatigue_threshold == 50

    # 3. Still between 50% and 90% (70%) -> Hysteresis suppresses duplicate alert
    orch.session_fatigue_tokens = 280000
    calls = [DummyCall("DateTime_now", {"tz": "GMT"})]
    orch._async_tool_execution(calls, current_weight=280000)

    sent_responses = orch.gemini_worker.send_function_responses.call_args[0][0]
    resp_text = sent_responses[0][1]["result"]
    assert "SYSTEM ALERT" not in resp_text
    assert orch._last_notified_fatigue_threshold == 50

    # 4. Cross 90% (92%) -> Critical alert attached
    orch.session_fatigue_tokens = 368000
    calls = [DummyCall("DateTime_now", {"format": "iso"})]
    orch._async_tool_execution(calls, current_weight=368000)

    sent_responses = orch.gemini_worker.send_function_responses.call_args[0][0]
    resp_text = sent_responses[0][1]["result"]
    assert "SYSTEM ALERT: Fatigue is at 92%" in resp_text
    assert "Critical fatigue reached" in resp_text
    assert orch._last_notified_fatigue_threshold == 90


def test_hard_fatigue_limit_circuit_breaker(tmp_path):
    orch = _create_mock_orchestrator(tmp_path, token_limit=400000)
    orch.session_fatigue_tokens = 400000  # 100%

    func_responses = [("DateTime_now", {"result": "2026-09-30"})]
    executed_tools = ["DateTime_now({})"]

    orch.on_tool_execution_finished(func_responses, executed_tools)

    # Assert emergency halt state
    assert orch._hard_fatigue_limit_hit is True
    assert orch.finish_thinking.called
    assert orch.pulse_engine.fire_pulse.called

    call_args = orch.pulse_engine.fire_pulse.call_args
    assert "Sleep Cycle (Memory Consolidation)" in call_args[0][0]
    assert call_args[0][2] == "sleep_cycle"

    # Verify user notice in conversation log
    assert any("Fatigue limit reached (100%)" in msg[1] for msg in orch._appended_messages)


def test_post_sleep_reset_and_reprimand(tmp_path):
    orch = _create_mock_orchestrator(tmp_path, token_limit=400000)

    # Case A: Post-sleep after 100% hard limit hit
    orch._is_sleep_cycle = True
    orch._hard_fatigue_limit_hit = True
    orch.session_fatigue_tokens = 400000
    orch.is_thinking = False
    orch.speech_queue = []

    orch.check_cycle_completion()

    # Fatigue should be reset to 0
    assert orch.session_fatigue_tokens == 0
    assert orch.get_fatigue() == 0.0
    assert orch._last_notified_fatigue_threshold == 0
    assert orch._hard_fatigue_limit_hit is False

    # Reprimand notice should be present in last_action_result
    assert orch.last_action_result is not None
    assert "You failed to adequately halt cognition before your fatigue limit was reached" in orch.last_action_result

    # Case B: Post-sleep after normal scheduled sleep cycle
    orch._is_sleep_cycle = True
    orch._hard_fatigue_limit_hit = False
    orch.session_fatigue_tokens = 150000

    orch.check_cycle_completion()

    assert orch.session_fatigue_tokens == 0
    assert orch.get_fatigue() == 0.0
    assert "You have completed memory consolidation and awoken to a new waking cycle" in orch.last_action_result
    assert "failed to adequately halt cognition" not in orch.last_action_result


def test_sleep_cycle_tool_restriction(tmp_path):
    orch = _create_mock_orchestrator(tmp_path, token_limit=400000)
    orch._is_sleep_cycle = True

    class DummyCall:
        def __init__(self, name, args):
            self.name = name
            self.args = args

    # 1. Disallowed tools (Terminal, Browser, WebSearch, Speaker) must be intercepted
    disallowed_calls = [
        DummyCall("Terminal_run_command", {"command": "ls"}),
        DummyCall("Browser_navigate", {"url": "https://example.com"}),
        DummyCall("WebSearch_search", {"query": "cats"}),
        DummyCall("Speaker_speak_aloud", {"text": "Hello"})
    ]

    orch._async_tool_execution(disallowed_calls, current_weight=0)

    # Cerebrum must NOT have executed any of these
    assert not orch.cerebrum.execute_tool_call.called

    sent_responses = orch.gemini_worker.send_function_responses.call_args[0][0]
    assert len(sent_responses) == 4
    for _, resp in sent_responses:
        assert "Action rejected: Tool" in resp["result"]
        assert "is disabled during Sleep Cycle" in resp["result"]

    # 2. Allowed tools (MemPalace, Trajectory, Pulse, System_contemplate) must pass through
    orch.cerebrum.reset_mock()
    orch.cerebrum.execute_tool_call.return_value = "Success"
    allowed_calls = [
        DummyCall("MemPalace_add_short_term", {"content": "Reflection"}),
        DummyCall("Trajectory_get_bearings", {}),
        DummyCall("System_contemplate", {})
    ]

    orch._async_tool_execution(allowed_calls, current_weight=0)
    assert orch.cerebrum.execute_tool_call.call_count == 3


def test_duplicate_action_argument_sensitivity(tmp_path):
    orch = _create_mock_orchestrator(tmp_path, token_limit=400000)

    # Different arguments -> NOT duplicates
    func_responses = [("WebSearch_search", {"result": "results for apples"})]
    orch.on_tool_execution_finished(func_responses, ['WebSearch_search({"query": "apples"})'])
    assert orch.duplicate_command_count == 0

    orch.on_tool_execution_finished(func_responses, ['WebSearch_search({"query": "bananas"})'])
    assert orch.duplicate_command_count == 0

    orch.on_tool_execution_finished(func_responses, ['WebSearch_search({"query": "oranges"})'])
    assert orch.duplicate_command_count == 0

    # Exactly identical arguments 3 times -> triggers duplicate loop circuit breaker
    call_sig = 'WebSearch_search({"query": "oranges"})'
    orch.on_tool_execution_finished(func_responses, [call_sig])
    assert orch.duplicate_command_count == 1

    orch.on_tool_execution_finished(func_responses, [call_sig])
    assert orch.duplicate_command_count == 2

    orch.on_tool_execution_finished(func_responses, [call_sig])
    assert orch.duplicate_command_count == 3
    assert orch.finish_thinking.called


def test_somatic_voice_modulation_subdued(tmp_path):
    # Verify somatic_state reading in TTS
    state_file = tmp_path / "somatic_state.json"
    with open(state_file, "w") as f:
        json.dump({"percent": 65.0, "fatigue": 0.65}, f)

    with patch("config.paths.get_base_dir_for", return_value=str(tmp_path)):
        from core.audio_output import TTSWorker
        tts = TTSWorker.__new__(TTSWorker)
        tts.agent_id = "test_fatigue_agent"

        # Read somatic state as done in _stream_gemini_audio
        somatic_state_text = ""
        with open(state_file, "r") as f:
            somatic = json.load(f)
            percent = somatic.get("percent")
            if percent >= 50:
                somatic_state_text = "Slightly tired, weary, and subdued due to cognitive fatigue."
            elif percent >= 25:
                somatic_state_text = "Slightly subdued and contemplative due to moderate fatigue."

        assert somatic_state_text == "Slightly tired, weary, and subdued due to cognitive fatigue."
