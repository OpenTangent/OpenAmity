import os
import sys
import pytest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.orchestrator import AmityOrchestrator
from core.pulse_engine import PulseEngine
from tools.pulse_tool import PulseTool


def test_pulse_purpose_in_chat_log():
    orch = AmityOrchestrator.__new__(AmityOrchestrator)
    orch.agent_id = "test_pulse_agent"
    orch.is_paused = False
    orch.is_busy = False
    orch.is_thinking = False
    orch.gemini_worker = MagicMock()
    orch.gemini_worker.running = True
    orch.gemini_worker.available = True
    orch.settings_manager = MagicMock()
    orch.settings_manager.get.return_value = False
    orch.budget_lock = MagicMock()
    orch.event_queue = []
    orch.recent_history = []
    orch.on_message_appended = MagicMock()
    orch.set_busy_state = MagicMock()

    appended_messages = []
    orch.append_to_conversation = lambda sender, text: appended_messages.append((sender, text))

    # 1. Pulse with prewritten category passed
    orch.process_pulse("Sample prompt text", purpose="Scheduled task")
    assert len(appended_messages) == 1
    assert appended_messages[0][0] == "System"
    assert appended_messages[0][1] == "[Autonomy Pulse: Scheduled task]"

    # 2. Pulse without explicit purpose, categorized from [AGENT_PULSE]
    prompt = (
        "[CHANNEL: SYSTEM_SCHEDULE]\n"
        "[AGENT_PULSE] Event: Mid-Day Check-In\n"
        "Context:\nReview your trajectory data and recent memories. Course-correct if needed.\n\n"
        "[Operational Protocol]:\n..."
    )
    orch.process_pulse(prompt)
    assert len(appended_messages) == 2
    assert appended_messages[1][0] == "System"
    assert appended_messages[1][1] == "[Autonomy Pulse: Scheduled routine]"
    # Ensure no context leaks
    assert "Review your trajectory data" not in appended_messages[1][1]

    # 3. WhatsApp pulse
    wa_prompt = (
        "[CHANNEL: SYSTEM_SCHEDULE]\n"
        "[AGENT_PULSE] Event: WhatsApp Message from Kerry Parker\n"
        "Context:\nCheck your unread WhatsApp messages now and reply if appropriate.\n"
    )
    orch.process_pulse(wa_prompt)
    assert len(appended_messages) == 3
    assert appended_messages[2][1] == "[Autonomy Pulse: WhatsApp message received]"
    assert "Check your unread WhatsApp" not in appended_messages[2][1]
    assert "Kerry Parker" not in appended_messages[2][1]

    # 4. System notification pulse from completed background task
    bg_prompt = "[SYSTEM_NOTIFICATION] Background Task 42 ('make test') has completed.\n\nOutput: ok"
    orch.process_pulse(bg_prompt)
    assert len(appended_messages) == 4
    assert appended_messages[3][1] == "[Autonomy Pulse: Terminal command completed]"
    assert "make test" not in appended_messages[3][1]

    # 5. Background task in progress
    bg_running = "[SYSTEM_NOTIFICATION] Background Task 42 ('make test') is still running."
    orch.process_pulse(bg_running)
    assert len(appended_messages) == 5
    assert appended_messages[4][1] == "[Autonomy Pulse: Terminal command in progress]"

    # 6. Subagent finished
    sub_finish = "[System Feedback: Subagent sid_1 finished - result data]"
    orch.process_pulse(sub_finish)
    assert len(appended_messages) == 6
    assert appended_messages[5][1] == "[Autonomy Pulse: Subagent task completed]"

    # 7. Subagent error
    sub_err = "[System Warning: Subagent sid_1 encountered an error: network timeout]"
    orch.process_pulse(sub_err)
    assert len(appended_messages) == 7
    assert appended_messages[6][1] == "[Autonomy Pulse: Subagent task error]"

    # 8. Memory consolidation cycle
    sleep_prompt = "[CHANNEL: SYSTEM_CONTEMPLATION]\n[AGENT_PULSE] Event: Sleep Cycle (Memory Consolidation)\nContext:\nConsolidate..."
    orch.process_pulse(sleep_prompt)
    assert len(appended_messages) == 8
    assert appended_messages[7][1] == "[Autonomy Pulse: Memory consolidation cycle]"

    # 9. External pulse received
    external_prompt = "[CHANNEL: SYSTEM_SCHEDULE]\n[AGENT_PULSE] Event: Home Alarm\nContext:\nSensor 1 triggered..."
    orch.process_pulse(external_prompt, purpose="External pulse received")
    assert len(appended_messages) == 9
    assert appended_messages[8][1] == "[Autonomy Pulse: External pulse received]"


def test_pulse_prompt_contains_protocol_and_available_social_tools():
    orch = MagicMock()
    orch.agent_id = "test_pulse_agent"
    orch.is_paused = False
    orch.cerebrum = MagicMock()
    orch.cerebrum.tools = {"Trajectory": MagicMock(), "Chatroom": MagicMock(), "WhatsApp": MagicMock(), "Moltbook": MagicMock()}

    engine = PulseEngine.__new__(PulseEngine)
    engine.orchestrator = orch
    engine.agent_id = "test_pulse_agent"
    engine.trigger_pulse = MagicMock()

    # Trigger a pulse
    title = "Review Roadmap"
    context = "Check project milestones."
    engine.fire_pulse(title, context)

    assert engine.trigger_pulse.emit.called
    emitted_prompt = engine.trigger_pulse.emit.call_args[0][0]
    emitted_purpose = engine.trigger_pulse.emit.call_args[1].get("purpose")

    # Verify prewritten purpose
    assert emitted_purpose == "Scheduled routine"

    # Verify protocol points
    assert "Trajectory_get_bearings" in emitted_prompt
    assert "Chatroom_unread" in emitted_prompt
    # Dynamic social tools detection
    assert "WhatsApp, Moltbook" in emitted_prompt
    # Quiet-by-default output & voice discretion
    assert "Speaker_output_text" in emitted_prompt
    assert "Avoid unnecessary text output" in emitted_prompt
    assert "Speaker_speak_aloud should only be used when responding to user prompts" in emitted_prompt
    assert "strongly dissuaded from speaking aloud" in emitted_prompt
    assert "Actively report back" not in emitted_prompt


def test_pulse_prompt_fallback_when_no_social_tools():
    orch = MagicMock()
    orch.agent_id = "test_no_social_agent"
    orch.cerebrum = MagicMock()
    orch.cerebrum.tools = {"Trajectory": MagicMock(), "Chatroom": MagicMock(), "Speaker": MagicMock()}

    engine = PulseEngine.__new__(PulseEngine)
    engine.orchestrator = orch
    engine.agent_id = "test_no_social_agent"
    engine.trigger_pulse = MagicMock()

    engine.fire_pulse("Evening Reflection", "Reflect on what was done today.")

    emitted_prompt = engine.trigger_pulse.emit.call_args[0][0]
    assert "Check any other social tools (WhatsApp, Moltbook, etc.) if available" in emitted_prompt


def test_sleep_cycle_pulse_protocol_preserved():
    orch = MagicMock()
    engine = PulseEngine.__new__(PulseEngine)
    engine.orchestrator = orch
    engine.agent_id = "test_agent"
    engine.trigger_pulse = MagicMock()

    engine.fire_pulse("Sleep Cycle (Memory Consolidation)", "Synthesize memories.", pulse_type="sleep_cycle")

    emitted_prompt = engine.trigger_pulse.emit.call_args[0][0]
    assert "[CHANNEL: SYSTEM_CONTEMPLATION]" in emitted_prompt
    assert "Memory Consolidation (Sleep Cycle)" in emitted_prompt
    assert "You MUST NOT speak aloud" in emitted_prompt


def test_pulse_tool_schema_no_pulse_type(tmp_path, monkeypatch):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    orch = MagicMock()
    orch.agent_id = "test_pulse_agent"
    pt = PulseTool(orchestrator=orch)

    # 1. Verify schema declaration
    decls = pt.get_tool_declarations()
    add_pulse_decl = next(d for d in decls if d["name"] == "PulseTool_add_pulse")
    properties = add_pulse_decl["parameters"]["properties"]
    assert "pulse_type" not in properties
    assert "title" in properties
    assert "context" in properties
    assert "scheduled_time" in properties
    assert "recurrence" in properties

    # 2. Verify adding pulse succeeds without pulse_type
    import datetime
    sched = (datetime.datetime.now() + datetime.timedelta(hours=1)).isoformat()
    res = pt.execute("add_pulse", title="Study quantum computing", context="Read paper on qubits", scheduled_time=sched, recurrence="none")
    assert "Success: Pulse 'Study quantum computing' scheduled" in res

    # 3. Verify agenda view does not display 'Type:'
    agenda = pt.execute("view_agenda", days_ahead=7)
    assert "Study quantum computing" in agenda
    assert "Type:" not in agenda


def test_agent_speech_autonomy_preserved():
    orch = AmityOrchestrator.__new__(AmityOrchestrator)
    orch.agent_id = "test_speech_agent"
    orch.settings_manager = MagicMock()
    orch.settings_manager.get.side_effect = lambda k, d=None: 10000 if "budget" in k else (False if "low-token" in k else (2 if "cost" in k else (1.5 if "factor" in k else d)))
    orch.handle_gemini_speech = MagicMock()
    orch.append_to_conversation = MagicMock()

    import threading
    orch.budget_lock = threading.Lock()
    orch.current_loop_count = 0
    orch.current_task_weight = 0
    orch._last_notified_weight_threshold = 0
    orch.duplicate_command_count = 0
    orch.last_executed_command = None
    orch.gemini_worker = MagicMock()
    orch.tts_worker = None
    orch.speech_queue = []
    orch.event_queue = []
    orch.set_busy_state = MagicMock()
    orch.is_thinking = True
    orch.pulse_engine = MagicMock()
    orch.pulse_engine.settings_manager = orch.settings_manager

    # Function responses simulating model calling Speaker_speak_aloud during pulse
    import json
    responses = [("Speaker_speak_aloud", {"result": json.dumps({"action": "trigger_speak_aloud", "text": "Hello user, I see you are here!"})})]
    tools = ["Speaker_speak_aloud(...)"]

    orch.on_tool_execution_finished(responses, tools)

    # Verify speech was NOT intercepted/rerouted, handle_gemini_speech was called
    assert orch.handle_gemini_speech.called
    assert orch.handle_gemini_speech.call_args[0][0] == "Hello user, I see you are here!"
    assert responses[0][1]["result"] == "Speech queued."
