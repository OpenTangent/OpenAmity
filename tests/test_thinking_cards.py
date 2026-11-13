import json
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication

# Add src to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.cerebrum import Tool
from core.chat_history_manager import ChatHistoryManager
from core.logger_config import AgentDispatchingHandler, ConsoleLogFilter
from core.orchestrator import AmityOrchestrator, Signal
from gui.agent_view import AgentView
from gui.chat_formatter import render_thought_card_html
from gui.logger_formatter import QtLoggingHandler


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture
def temp_env(monkeypatch):
    ChatHistoryManager.clear_instances()
    temp_dir = tempfile.mkdtemp(prefix="openamity_test_thoughts_")
    monkeypatch.setenv("XDG_DATA_HOME", temp_dir)
    monkeypatch.setattr("config.paths.get_app_data_dir", lambda: temp_dir)
    monkeypatch.setattr("config.paths.get_agent_data_dir", lambda aid: os.path.join(temp_dir, "agents", aid))
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: os.path.join(temp_dir, "agents", aid))
    monkeypatch.setattr(
        "config.paths.get_chat_history_db_path",
        lambda aid: os.path.join(temp_dir, "agents", aid, "chat_history.db"),
    )

    yield temp_dir

    ChatHistoryManager.clear_instances()
    shutil.rmtree(temp_dir, ignore_errors=True)


class DummySettings:
    def __init__(self, overrides=None):
        self.values = overrides or {}

    def get(self, key, default=None):
        return self.values.get(key, default)


def _make_orchestrator(chat_manager, agent_id="+OA-TEST-THOUGHTS"):
    orch = AmityOrchestrator.__new__(AmityOrchestrator)
    orch.agent_id = agent_id
    orch.agent_name = "TestAmity"
    orch.chat_history_manager = chat_manager
    orch.settings_manager = DummySettings()

    # Custom Signal() attributes matching AmityOrchestrator.__init__
    orch.on_message_appended = Signal()
    orch.on_thought_card_updated = Signal()
    orch.on_busy_state_changed = Signal()
    orch.on_amplitude_emitted = Signal()
    orch.on_waveform_emitted = Signal()
    orch.on_paused_state_changed = Signal()
    orch.on_pause_pending = Signal()
    orch.on_tool_started = Signal()
    orch.on_tool_finished = Signal()
    orch.on_listening_state_changed = Signal()
    orch.on_shutdown_complete = Signal()

    orch.is_paused = False
    orch.is_busy = True
    orch.is_thinking = True
    orch._in_flight_prompt = None
    orch._active_thought_card_id = None
    orch._active_thought_entries = []
    orch._fallback_thought_card_seq = -1
    orch._thought_card_lock = threading.RLock()
    orch._speaker_invoked_in_cycle = False
    orch._is_executing_tools = False
    orch._user_turn_nudged = False
    orch._is_sleep_cycle = False
    orch._sleep_cycle_error = False
    orch._hard_fatigue_limit_hit = False
    orch._pending_settings_reload = False
    orch._audio_interjection_in_progress = False
    orch.accumulated_thoughts = ""
    orch.current_task_weight = 1
    orch.session_fatigue_tokens = 0
    orch.current_loop_count = 0
    orch.duplicate_command_count = 0
    orch.last_executed_command = None
    orch._last_notified_fatigue_threshold = 0

    orch._interjection_lock = threading.Lock()
    orch._pending_interjections = []
    orch.recent_history = []
    orch.speech_queue = []
    orch.event_queue = []
    orch.budget_lock = threading.Lock()

    orch.get_fatigue = lambda: 0.0
    orch._write_somatic_state = MagicMock()
    orch.save_session_state = MagicMock()
    orch.clear_session_state = MagicMock()
    orch.build_system_prompt = MagicMock()
    orch.speak = MagicMock()
    orch.user_interacted = MagicMock()
    orch.mempalace_manager = None
    orch.audio_service = None
    orch.tts_worker = None
    orch.gemini_worker = SimpleNamespace(
        running=False,
        is_processing=False,
        send_function_responses=MagicMock(),
        send_prompt=MagicMock(),
        abort=MagicMock(),
    )
    orch.cerebrum = MagicMock()
    orch.cerebrum.tools = {}
    return orch


class DummyOrchestratorForView:
    def __init__(self, agent_id, chat_manager):
        self.agent_id = agent_id
        self.agent_name = "TestAgent"
        self.chat_history_manager = chat_manager
        self.settings_manager = DummySettings()
        self.is_paused = False
        self.is_thinking = False
        self.user_interacted = MagicMock()

        self.on_message_appended = Signal()
        self.on_thought_card_updated = Signal()
        self.on_busy_state_changed = Signal()
        self.on_amplitude_emitted = Signal()
        self.on_waveform_emitted = Signal()
        self.on_paused_state_changed = Signal()
        self.on_pause_pending = Signal()
        self.on_tool_started = Signal()
        self.on_tool_finished = Signal()
        self.on_listening_state_changed = Signal()


def test_chat_history_update_message(temp_env):
    mgr = ChatHistoryManager(agent_id="+OA-DB-01")
    created = mgr.add_message("Thoughts", "Initial thought")
    msg_id = created["id"]

    updated = mgr.update_message(msg_id, "Initial thought\n\nSecond thought")
    assert updated is True
    assert mgr.get_message_count() == 1

    msgs = mgr.get_messages(limit=10)
    assert len(msgs) == 1
    assert msgs[0]["id"] == msg_id
    assert msgs[0]["sender"] == "Thoughts"
    assert msgs[0]["content"] == "Initial thought\n\nSecond thought"

    assert mgr.update_message(999999, "Does not exist") is False


def test_render_thought_card_html_collapsed_and_expanded():
    body = "Checking <files>\nRunning tool_x()"
    collapsed_html = render_thought_card_html(42, body, collapsed=True)
    assert 'href="thought:toggle:42"' in collapsed_html
    assert "▶ Thoughts" in collapsed_html
    assert "Checking" not in collapsed_html
    assert "Running tool_x()" not in collapsed_html

    expanded_html = render_thought_card_html(42, body, collapsed=False)
    assert 'href="thought:toggle:42"' in expanded_html
    assert "▼ Thoughts" in expanded_html
    assert "Checking &lt;files&gt;" in expanded_html
    assert "<br>" in expanded_html
    assert "Running tool_x()" in expanded_html


def test_render_thought_card_html_tool_icons_and_colors():
    body = "\n\n".join(
        [
            "Checking bearings and unread messages.",
            "🧭 Trajectory_get_bearings()",
            "WhatsApp_unread()",
            "💻 Terminal_run: command='ls <tmp>'",
            "MemPalace_search: query='hello'",
            "⚡ Custom_ping()",
            "- Trajectory_get_bearings: not a tool call",
            "Notice WhatsApp_unread() inline in prose.",
            "local_variable: 42",
        ]
    )
    rendered = render_thought_card_html(7, body, collapsed=False, tool_colors={"Custom": "#FF00FF"})

    # Zero-arg calls (with icon already present and legacy unprefixed) render single icon + colored fn()
    assert '🧭 <span style="color: #1ABC9C;">Trajectory_get_bearings()</span>' in rendered
    assert "🧭 🧭" not in rendered
    assert '💬 <span style="color: #25D366;">WhatsApp_unread()</span>' in rendered

    # Arg calls color only the function name while keeping escaped arguments cleanly in body color
    assert '💻 <span style="color: #F39C12;">Terminal_run</span>: command=&#x27;ls &lt;tmp&gt;&#x27;' in rendered
    assert '🧠 <span style="color: #CD853F;">MemPalace_search</span>: query=&#x27;hello&#x27;' in rendered

    # Custom tool color override respected
    assert '⚡ <span style="color: #FF00FF;">Custom_ping()</span>' in rendered

    # Ordinary prose lines (even with bullet or inline mention or snake_case) remain plain text
    assert "- Trajectory_get_bearings: not a tool call" in rendered
    assert '<span style="color: #1ABC9C;">Trajectory_get_bearings</span>: not a tool call' not in rendered
    assert "Notice WhatsApp_unread() inline in prose." in rendered
    assert "local_variable: 42" in rendered


def test_tool_get_tool_class_discovers_after_partial_registration(monkeypatch):
    Tool.discover_tool_classes()
    original_classes = dict(Tool._tool_classes)
    try:
        # Simulate only one tool having been imported before lookup
        monkeypatch.setattr(Tool, "_tool_classes", {"Speaker": original_classes["Speaker"]})
        monkeypatch.setattr(Tool, "_tools_discovered", False)

        # Force re-registration of already-imported modules if discover runs import_module
        def fake_discover():
            Tool._tools_discovered = True
            Tool._tool_classes.update(original_classes)

        monkeypatch.setattr(Tool, "discover_tool_classes", classmethod(lambda cls: fake_discover()))
        assert Tool.get_tool_class("Trajectory") is original_classes["Trajectory"]
        assert Tool.get_tool_color("WhatsApp") == "#25D366"
        assert Tool.get_tool_icon("Trajectory") == "🧭"
    finally:
        Tool._tool_classes = original_classes
        Tool._tools_discovered = True


def test_console_log_filter_and_handlers(temp_env):
    log_filter = ConsoleLogFilter()

    def make_record(name, level, msg, extra_thought=False):
        rec = logging.LogRecord(name, level, __file__, 1, msg, (), None)
        if extra_thought:
            rec.is_thought_or_tool = True
        return rec

    # Filtered out (<= INFO)
    assert not log_filter.filter(make_record("geminiworker.Thoughts", logging.INFO, "Internal thought"))
    assert not log_filter.filter(make_record("openrouterworker.Thoughts", logging.INFO, "OpenRouter thought"))
    assert not log_filter.filter(make_record("tool.Terminal", logging.INFO, "Ran ls"))
    assert not log_filter.filter(make_record("tools.web_search", logging.INFO, "Fetched URL"))
    assert not log_filter.filter(make_record("primp", logging.INFO, "HTTP GET"))
    assert not log_filter.filter(make_record("core.worker", logging.INFO, "Tagged", extra_thought=True))
    assert not log_filter.filter(make_record("subagent.s1", logging.INFO, "[Subagent s1] Thought: planning"))
    assert not log_filter.filter(make_record("subagent.s1", logging.INFO, "[Subagent s1] Function Calls: Foo()"))
    assert not log_filter.filter(make_record("subagent.s1", logging.INFO, "Thought: direct prefix"))
    assert not log_filter.filter(make_record("subagent.s1", logging.INFO, "Function Calls: direct prefix"))

    # Allowed
    assert log_filter.filter(make_record("core.orchestrator", logging.INFO, "Cycle started"))
    assert log_filter.filter(make_record("subagent.s1", logging.INFO, "[Subagent s1] Spawned successfully"))
    assert log_filter.filter(make_record("tool.Terminal", logging.WARNING, "Command timed out"))
    assert log_filter.filter(make_record("tools.web_search", logging.ERROR, "Network failure"))
    assert log_filter.filter(make_record("geminiworker.Thoughts", logging.ERROR, "Thought stream error"))

    # QtLoggingHandler + ConsoleLogFilter integration
    emitted = []
    qt_handler = QtLoggingHandler()
    qt_handler.addFilter(ConsoleLogFilter())
    qt_handler.signals.new_message.connect(lambda aid, html: emitted.append((aid, html)))

    qt_handler.handle(make_record("geminiworker.Thoughts", logging.INFO, "Hidden thought"))
    qt_handler.handle(make_record("tool.Terminal", logging.INFO, "Hidden tool info"))
    assert emitted == []

    qt_handler.handle(make_record("core.orchestrator", logging.INFO, "Visible lifecycle"))
    qt_handler.handle(make_record("tool.Terminal", logging.ERROR, "Visible tool error"))
    assert len(emitted) == 2

    # AgentDispatchingHandler preserves disk logs without ConsoleLogFilter
    agent_id = "+OA-DISK-LOG"
    disk_handler = AgentDispatchingHandler()
    try:
        from core.logger_config import agent_id_var

        token = agent_id_var.set(agent_id)
        try:
            disk_handler.emit(make_record("geminiworker.Thoughts", logging.INFO, "Disk thought entry"))
            disk_handler.emit(make_record("tool.Terminal", logging.INFO, "Disk tool entry"))
        finally:
            agent_id_var.reset(token)
    finally:
        disk_handler.close()

    log_path = os.path.join(temp_env, "agents", agent_id, "logs", "open_amity.log")
    with open(log_path, "r", encoding="utf-8") as f:
        disk_content = f.read()
    assert "Disk thought entry" in disk_content
    assert "Disk tool entry" in disk_content


def test_orchestrator_thought_card_lifecycle_and_speaker_split(temp_env, monkeypatch):
    class ImmediateThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):
            self._target = target
            self._args = args
            self._kwargs = kwargs or {}

        def start(self):
            if self._target:
                self._target(*self._args, **self._kwargs)

    monkeypatch.setattr("core.orchestrator.threading.Thread", ImmediateThread)

    agent_id = "+OA-ORCH-01"
    mgr = ChatHistoryManager(agent_id=agent_id)
    orch = _make_orchestrator(mgr, agent_id=agent_id)

    events = []
    orch.on_thought_card_updated.connect(lambda cid, txt, col: events.append(("card", cid, txt, col)))
    orch.on_message_appended.connect(lambda sender, txt: events.append(("conv", sender, txt)))

    # 1. Turn 1: thought + non-Speaker real tool call accumulate into one growing card with emoji icon
    orch.cerebrum.execute_tool_call.return_value = json.dumps({"status": "success", "content": "ok"})
    orch.handle_gemini_thought(
        "First reasoning step.",
        [SimpleNamespace(name="Trajectory_get_bearings", args={})],
    )

    msgs = mgr.get_messages(limit=20)
    assert len(msgs) == 1
    first_card_id = msgs[0]["id"]
    assert msgs[0]["sender"] == "Thoughts"
    assert "First reasoning step." in msgs[0]["content"]
    assert "🧭 Trajectory_get_bearings()" in msgs[0]["content"]
    assert events[-1] == ("card", first_card_id, msgs[0]["content"], False)

    # 2. Turn 2: Second thought appends to card #1, Speaker_speak_aloud collapses card #1 BEFORE Agent message,
    #    and subsequent WhatsApp_unread in same batch opens a NEW card #2 with 💬 icon
    def fake_execute(name, args, call_id=None):
        if name == "Speaker_speak_aloud":
            return json.dumps({"action": "trigger_speak_aloud", "text": args["text"]})
        if name == "Speaker_output_text":
            return json.dumps({"action": "trigger_output_text", "text": args["text"]})
        return json.dumps({"status": "success", "items": []})

    orch.cerebrum.execute_tool_call.side_effect = fake_execute

    orch.handle_gemini_thought(
        "Second reasoning step.",
        [
            SimpleNamespace(name="Speaker_speak_aloud", args={"text": "Hello human!"}),
            SimpleNamespace(name="WhatsApp_unread", args={}),
        ],
    )

    collapse_idx = next(
        i for i, e in enumerate(events) if e[0] == "card" and e[1] == first_card_id and e[3] is True
    )
    conv_idx = next(i for i, e in enumerate(events) if e[0] == "conv" and e[1] == "Agent")
    new_card_idx = next(
        i for i, e in enumerate(events) if e[0] == "card" and e[1] != first_card_id and e[3] is False
    )
    assert collapse_idx < conv_idx < new_card_idx

    msgs_after = mgr.get_messages(limit=20)
    assert len(msgs_after) == 3
    assert msgs_after[0]["sender"] == "Thoughts"
    assert "Second reasoning step." in msgs_after[0]["content"]
    assert msgs_after[1]["sender"] == "Agent"
    assert msgs_after[1]["content"] == "Hello human!"
    assert msgs_after[2]["sender"] == "Thoughts"
    second_card_id = msgs_after[2]["id"]
    assert second_card_id != first_card_id
    assert "💬 WhatsApp_unread()" in msgs_after[2]["content"]
    assert "Speaker_speak_aloud" not in msgs_after[2]["content"]

    # 3. Turn 3: Speaker_output_text collapses second card and outputs second Agent message
    orch.handle_gemini_thought(
        "Formatting text reply.",
        [SimpleNamespace(name="Speaker_output_text", args={"text": "Plain text reply"})],
    )
    assert any(
        e[0] == "card" and e[1] == second_card_id and e[3] is True for e in events
    )
    assert any(
        e == ("conv", "Agent", "Plain text reply") for e in events
    )

    # 4. Turn 4: Final thought with no function_calls triggers finish_thinking() and collapses third card
    orch.handle_gemini_thought("Final check before finishing.", [])
    msgs_final = mgr.get_messages(limit=20)
    assert msgs_final[-1]["sender"] == "Thoughts"
    third_card_id = msgs_final[-1]["id"]
    assert third_card_id != second_card_id
    assert events[-1] == ("card", third_card_id, "Final check before finishing.", True)

    # 5. Any open thought card is also collapsed by stop_all_processing()
    orch._append_to_thought_card("Interrupted thought.")
    fourth_card_id = mgr.get_messages(limit=20)[-1]["id"]
    orch.stop_all_processing()
    assert any(e[0] == "card" and e[1] == fourth_card_id and e[3] is True for e in events)


def test_agent_view_thought_cards_toggle_reload_and_scroll(qapp, temp_env):
    agent_id = "+OA-VIEW-01"
    mgr = ChatHistoryManager(agent_id=agent_id)
    for i in range(1, 56):
        mgr.add_message("User", f"Historical user msg {i}")
    old_thought = mgr.add_message("Thoughts", "Historical secret thought\n\n🧭 Trajectory_get_bearings()")
    mgr.add_message("Agent", "Historical reply")

    orch = DummyOrchestratorForView(agent_id, mgr)
    view = AgentView(orchestrator=orch)
    try:
        view.resize(800, 600)
        view.show()
        qapp.processEvents()

        # Reloaded Thoughts card must be collapsed by default
        html = view.conversation_log.toHtml()
        assert "▶ Thoughts" in html
        assert "Historical secret thought" not in html
        assert "Historical reply" in html

        # Click toggle link -> expands in place and shows tool icon + tool color
        view.on_anchor_clicked(QUrl(f"thought:toggle:{old_thought['id']}"))
        qapp.processEvents()
        html_expanded = view.conversation_log.toHtml()
        plain_expanded = view.conversation_log.toPlainText()
        assert "▼ Thoughts" in html_expanded
        assert "Historical secret thought" in html_expanded
        assert "🧭 Trajectory_get_bearings()" in plain_expanded
        assert re.search(
            r"color:\s*#1abc9c;[^>]*>Trajectory_get_bearings\(\)<",
            html_expanded,
            re.IGNORECASE,
        )

        # Scroll away from bottom, collapse card -> scroll does not jump unexpectedly
        sb = view.conversation_log.verticalScrollBar()
        sb.setValue(max(10, sb.maximum() // 2))
        qapp.processEvents()
        saved_scroll = sb.value()

        view.on_anchor_clicked(QUrl(f"thought:toggle:{old_thought['id']}"))
        qapp.processEvents()
        assert "▶ Thoughts" in view.conversation_log.toHtml()
        assert "Historical secret thought" not in view.conversation_log.toHtml()
        assert abs(sb.value() - min(saved_scroll, sb.maximum())) <= 5

        # Expand old_thought again, then trigger scrollback pagination (load_older_messages)
        view.on_anchor_clicked(QUrl(f"thought:toggle:{old_thought['id']}"))
        qapp.processEvents()
        assert str(old_thought["id"]) in view._expanded_thought_cards

        view.load_older_messages()
        qapp.processEvents()
        assert "Historical user msg 1" in view.conversation_log.toHtml()
        assert "Historical secret thought" in view.conversation_log.toHtml()

        # Live growing card via orchestrator signal renders icon + color, then collapses cleanly
        live_row = mgr.add_message("Thoughts", "Live thought 1")
        orch.on_thought_card_updated.emit(live_row["id"], "Live thought 1", False)
        qapp.processEvents()
        assert "▼ Thoughts" in view.conversation_log.toHtml()
        assert "Live thought 1" in view.conversation_log.toHtml()

        live_body = "Live thought 1\n\n💬 WhatsApp_unread()"
        orch.on_thought_card_updated.emit(live_row["id"], live_body, False)
        qapp.processEvents()
        live_html = view.conversation_log.toHtml()
        live_plain = view.conversation_log.toPlainText()
        assert "💬 WhatsApp_unread()" in live_plain
        assert re.search(
            r"color:\s*#25d366;[^>]*>WhatsApp_unread\(\)<",
            live_html,
            re.IGNORECASE,
        )

        orch.on_thought_card_updated.emit(live_row["id"], live_body, True)
        qapp.processEvents()
        assert "Live thought 1" not in view.conversation_log.toHtml()
        assert "WhatsApp_unread()" not in view.conversation_log.toHtml()
    finally:
        view.cleanup()
        view.close()
        qapp.processEvents()
