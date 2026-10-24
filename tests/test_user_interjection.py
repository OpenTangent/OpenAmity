import os
import threading
from unittest.mock import MagicMock, patch
import pytest

from core.orchestrator import AmityOrchestrator
from core.audio_input import AudioService


class MockFunctionCall:
    def __init__(self, name, args=None):
        self.name = name
        self.args = args or {}


def test_process_text_input_interjection_queues_and_stops_tts():
    orch = AmityOrchestrator.__new__(AmityOrchestrator)
    orch.agent_id = "test_agent"
    orch.is_paused = False
    orch.is_busy = True
    orch.is_thinking = True
    orch._is_executing_tools = True
    orch._is_sleep_cycle = False
    orch.speech_queue = ["hello world"]
    orch.event_queue = []
    orch.recent_history = []
    orch._pending_interjections = []
    orch._interjection_lock = threading.Lock()

    mock_tts = MagicMock()
    orch.tts_worker = mock_tts
    orch.gemini_worker = MagicMock()
    orch.gemini_worker.is_processing = True

    appended_messages = []
    orch.on_message_appended = MagicMock()
    orch.on_message_appended.emit = lambda s, t: appended_messages.append((s, t))

    settings_mock = MagicMock()
    settings_mock.get.return_value = False
    orch.settings_manager = settings_mock

    orch.process_text_input("Wait, check the filename first!")

    # TTS stopped and speech queue cleared
    assert mock_tts.stop.called
    assert len(orch.speech_queue) == 0

    # Logged as User (Interjection)
    assert len(appended_messages) == 1
    assert appended_messages[0][0] == "User (Interjection)"
    assert appended_messages[0][1] == "Wait, check the filename first!"

    # Pending interjections contains the text
    pending = orch._get_pending_interjections()
    assert len(pending) == 1
    assert pending[0][0] == "Wait, check the filename first!"
    assert pending[0][1] is None


def test_handle_gemini_thought_pauses_tool_calls_on_interjection():
    orch = AmityOrchestrator.__new__(AmityOrchestrator)
    orch.agent_id = "test_agent"
    orch.is_paused = False
    orch.is_busy = True
    orch.is_thinking = True
    orch._is_executing_tools = False
    orch._is_sleep_cycle = False
    orch.accumulated_thoughts = ""
    orch.speech_queue = []
    orch.event_queue = []
    orch.recent_history = []
    orch._pending_interjections = [("Hold on, what are you doing?", None)]
    orch._interjection_lock = threading.Lock()

    orch.tts_worker = None
    orch.gemini_worker = MagicMock()
    orch.gemini_worker.running = True

    settings_mock = MagicMock()
    settings_mock.get.side_effect = lambda k, default=None: "gemini" if "provider" in k else 100000
    orch.settings_manager = settings_mock

    # Mock tool execution callbacks
    sent_responses = []
    orch.gemini_worker.send_function_responses = lambda r: sent_responses.extend(r)
    orch.cerebrum = MagicMock()

    calls = [MockFunctionCall("Terminal_run", {"command": "rm -rf /tmp/test"})]

    with patch.object(orch, "get_fatigue", return_value=0.1):
        orch.handle_gemini_thought("Thinking about running command...", calls)

    # Tool was NOT executed via cerebrum
    assert not orch.cerebrum.execute_tool_call.called

    # Paused function response was sent
    assert len(sent_responses) == 1
    fn_name, resp = sent_responses[0]
    assert fn_name == "Terminal_run"
    assert "[Tool execution paused for User Interjection]" in resp["result"]
    assert "[USER INTERJECTION]" in resp["result"]
    assert "Hold on, what are you doing?" in resp["result"]
    assert "[CRITICAL CONTINUITY DIRECTIVE" in resp["result"]


def test_mid_batch_tool_execution_pauses_remaining_tools():
    orch = AmityOrchestrator.__new__(AmityOrchestrator)
    orch.agent_id = "test_agent"
    orch.is_paused = False
    orch.is_busy = True
    orch.is_thinking = True
    orch._is_executing_tools = True
    orch._is_sleep_cycle = False
    orch.current_task_weight = 0
    orch.duplicate_command_count = 0
    orch.last_executed_command = None
    orch.session_fatigue_tokens = 0
    orch.current_loop_count = 0
    orch.speech_queue = []
    orch.event_queue = []
    orch._pending_interjections = []
    orch._interjection_lock = threading.Lock()

    orch.on_tool_started = MagicMock()
    orch.on_tool_finished = MagicMock()
    orch.tts_worker = None
    orch.gemini_worker = MagicMock()
    orch.gemini_worker.running = True

    sent_responses = []
    orch.gemini_worker.send_function_responses = lambda r: sent_responses.extend(r)

    orch.settings_manager = MagicMock()
    orch.settings_manager.get.return_value = 100000

    orch.cerebrum = MagicMock()
    orch.cerebrum.tools = {}

    def mock_tool_exec(name, args, call_id=None):
        # When tool 1 executes, simulate an interjection arriving
        with orch._get_interjection_lock():
            orch._get_pending_interjections().append(("Wait, stop after first tool!", None))
        return "Tool 1 Output"

    orch.cerebrum.execute_tool_call = mock_tool_exec

    calls = [
        MockFunctionCall("First_tool", {"step": 1}),
        MockFunctionCall("Second_tool", {"step": 2}),
    ]

    with patch.object(orch, "get_fatigue", return_value=0.1):
        orch._async_tool_execution(calls, 0)

    assert len(sent_responses) == 2
    # Tool 1 succeeded
    assert sent_responses[0][0] == "First_tool"
    assert sent_responses[0][1]["result"] == "Tool 1 Output"

    # Tool 2 was paused
    assert sent_responses[1][0] == "Second_tool"
    assert "[Tool execution paused for User Interjection]" in sent_responses[1][1]["result"]
    assert "Wait, stop after first tool!" in sent_responses[1][1]["result"]


def test_audio_service_direct_multimodal_and_finish_listening():
    service = AudioService(agent_id="test_agent")
    service.settings = MagicMock()
    service.settings.get.side_effect = lambda k, default=None: False if "low-token" in k or "agy-mode" in k else (60 if "max-recording-time" in k else 3.0)

    ready_payloads = []
    service.audio_prompt_ready.connect(lambda text, wav: ready_payloads.append((text, wav)))

    assert hasattr(service, "finish_listening")
    service.finish_listening()
    assert service.finish_flag is True


def test_gui_coupled_controls_and_interjection_badge():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    orch = AmityOrchestrator.__new__(AmityOrchestrator)
    orch.agent_id = "test_gui_agent"
    orch.is_paused = False
    orch.is_busy = False
    orch.is_thinking = False
    orch._is_executing_tools = False
    orch._is_sleep_cycle = False
    orch.settings_manager = MagicMock()
    orch.settings_manager.get.side_effect = lambda k, default=None: False if "mute" in k or "paused" in k else default
    orch.config_manager = MagicMock()
    orch.config_manager.get.return_value = "Alice"

    from core.events import Signal
    orch.on_message_appended = Signal()
    orch.on_busy_state_changed = Signal()
    orch.on_amplitude_emitted = Signal()
    orch.on_paused_state_changed = Signal()
    orch.on_pause_pending = Signal()
    orch.on_tool_started = Signal()
    orch.on_tool_finished = Signal()
    orch.on_listening_state_changed = Signal()

    with patch("gui.agent_view.ConfigManager") as mock_cm_cls:
        mock_cm = MagicMock()
        mock_cm.get.return_value = "Alice"
        mock_cm_cls.return_value = mock_cm
        from gui.agent_view import AgentView
        view = AgentView(orchestrator=orch)

    # 1. Idle state: Pause button is ❚❚, Mic button is ⏺ with "Mic"
    assert view.btn_pause.text() == "❚❚"
    assert view.btn_pause.toolTip() == "Pause Agent"
    assert view.btn_mic.text() == "⏺"
    assert view.btn_mic.toolTip() == "Mic"
    assert view.text_input.placeholderText() == "Type a message..."

    # 2. Busy state: Pause button couples into Stop Agent (⏹) in red
    view.set_busy_state(True)
    assert view.btn_pause.text() == "⏹"
    assert view.btn_pause.toolTip() == "Stop Agent"
    assert "AA0000" in view.btn_pause.styleSheet()
    assert view.btn_mic.toolTip() == "Voice Interject"
    assert view.text_input.placeholderText() == "Interject while agent is working..."
    assert view.btn_send.toolTip() == "Interject"

    # 3. Active mic recording: Mic button becomes Finish Recording (⏹) in red
    view.set_listening_state(True)
    assert view.btn_mic.text() == "⏹"
    assert view.btn_mic.toolTip() == "Finish Recording"
    assert "AA0000" in view.btn_mic.styleSheet()

    view.set_listening_state(False)
    assert view.btn_mic.text() == "⏺"

    # 4. Stop processing on pause button click when busy
    orch.stop_all_processing = MagicMock()
    orch.is_busy = True
    view.on_pause_button_clicked()
    assert orch.stop_all_processing.called

    # 5. Conversation log badge for Interjection
    view.append_to_conversation("User (Interjection)", "Hold on!")
    html = view.conversation_log.toHtml()
    assert "Alice (Interjection)" in html
    assert "Hold on!" in html

    view.cleanup()
