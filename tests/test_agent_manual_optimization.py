import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from core.cerebrum import Cerebrum, MANUAL_TOPICS
from core.prompt_context import (
    MEMORY_BEGIN,
    MEMORY_END,
    WORKER_AUTONOMOUS_SPEECH_INSTRUCTION,
    WORKER_CRITICAL_INSTRUCTION,
    inject_static_worker_instructions,
    split_system_prompt_for_caching,
)
from tools.system_tool import SystemTool


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MANUAL_INDEX_PATH = os.path.join(REPO_ROOT, "src", "memory", "agent_manual.md")
MANUAL_DIR = os.path.join(REPO_ROOT, "src", "memory", "manual")
SOUL_JAR_PATH = os.path.join(REPO_ROOT, "src", "memory", "soul_jar.json")


class _DictSettings:
    def __init__(self, initial=None):
        self.data = dict(initial or {})

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value

    def save(self):
        return None


def test_agent_manual_index_compact_and_topics_complete():
    with open(MANUAL_INDEX_PATH, "r", encoding="utf-8") as f:
        index_text = f.read()

    assert len(index_text) < 6200, f"Core index grew too large: {len(index_text)} chars"
    assert "Use your tools natively" in index_text
    assert "Speaker_speak_aloud" in index_text
    assert "Speaker_output_text" in index_text
    assert "already automatically captioned" in index_text
    assert "MemPalace_apply_identity_delta" in index_text
    assert "never fabricate user consent" in index_text
    assert "Contacts_lookup_contact" in index_text
    assert "Contacts_add_contact" in index_text
    assert "24 hours" in index_text
    assert ".sh" in index_text
    assert "never ask them to copy-paste output" in index_text
    for topic, meta in MANUAL_TOPICS.items():
        assert f"`{topic}`" in index_text
        topic_file = os.path.join(MANUAL_DIR, meta["file"])
        assert os.path.isfile(topic_file)
        with open(topic_file, "r", encoding="utf-8") as tf:
            content = tf.read().strip()
        assert len(content) > 200

    with open(os.path.join(MANUAL_DIR, "memory.md"), "r", encoding="utf-8") as f:
        assert "MemPalace_update_mirror" in f.read()
    with open(os.path.join(MANUAL_DIR, "browser.md"), "r", encoding="utf-8") as f:
        assert "screenshot=False" in f.read()
    with open(os.path.join(MANUAL_DIR, "terminal_files.md"), "r", encoding="utf-8") as f:
        assert "Terminal_read_file" in f.read()
    with open(os.path.join(MANUAL_DIR, "channels.md"), "r", encoding="utf-8") as f:
        assert "Speaker_output_text" in f.read()
    with open(os.path.join(MANUAL_DIR, "agency_pulses.md"), "r", encoding="utf-8") as f:
        assert "[AGENT_PULSE]" in f.read()
    with open(os.path.join(MANUAL_DIR, "backups_api.md"), "r", encoding="utf-8") as f:
        assert "System_api_docs" in f.read()


def test_soul_jar_operational_protocols_valid_and_complete():
    with open(SOUL_JAR_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    protocols = data["operational_protocols"]
    expected_keys = {
        "short_term_memory",
        "character_defining_memories",
        "social_records",
        "theory_of_mind",
        "trajectory",
        "momentum",
    }
    assert set(protocols.keys()) == expected_keys
    rendered = json.dumps(protocols)
    for token in (
        "MemPalace_add_short_term",
        "MemPalace_update_mirror",
        "S1..S10",
        "Trajectory_get_bearings",
        "Trajectory_manage_aspirations",
        "Sphere 1 Horizon Guard",
    ):
        assert token in rendered


def test_system_read_manual_declaration_and_execution_no_alias():
    tool = SystemTool(orchestrator=None)
    decls = {d["name"]: d for d in tool.get_tool_declarations()}
    assert "System_read_manual" in decls
    assert "System_ReadManual" not in decls

    topic_schema = decls["System_read_manual"]["parameters"]["properties"]["topic"]
    assert topic_schema["enum"] == [
        "index",
        "memory",
        "browser",
        "terminal_files",
        "channels",
        "agency_pulses",
        "backups_api",
    ]
    assert "all" not in topic_schema["enum"]

    # Explicit decision: no ReadManual alias
    assert tool.execute("ReadManual", topic="browser") == "Unknown command: ReadManual"

    # Index lookup & empty topic fallback
    idx_out = tool.execute("read_manual", topic="index")
    assert "Available Agent Manual Topics" in idx_out
    assert "`browser`" in idx_out
    assert tool.execute("read_manual") == idx_out

    # Direct & alias topic lookup in standalone mode (orchestrator=None)
    assert "Two-Tier Web Strategy" in tool.execute("read_manual", topic="browser")
    assert "Two-Tier Web Strategy" in tool.execute("read_manual", topic="web")
    assert "Bidirectional Empathy Cone" in tool.execute("read_manual", topic="mempalace")
    assert "Host Command Delegation" in tool.execute("read_manual", topic="terminal")
    assert "Gemini 3.8 Flash TTS" in tool.execute("read_manual", topic="speaker")
    assert "Sphere 1 Horizon Guard" in tool.execute("read_manual", topic="pulses")
    assert "System_api_docs" in tool.execute("read_manual", topic="backup")

    # Unknown topic reports cleanly
    err = tool.execute("read_manual", topic="nonexistent_topic")
    assert err.startswith("Error: Unknown manual topic")
    assert "index" in err


def test_cerebrum_cwd_independent_resolution_and_tool_filtering(monkeypatch, tmp_path):
    empty_tools = tmp_path / "empty_tools"
    empty_tools.mkdir()
    monkeypatch.chdir(tmp_path)
    cerebrum = Cerebrum(
        orchestrator=None,
        settings_manager=_DictSettings(),
        skills_dir=str(empty_tools),
    )
    # Register loaded tools without Browser/WebSearch
    cerebrum.register_skill(SimpleNamespace(name="System"))
    cerebrum.register_skill(SimpleNamespace(name="MemPalace"))

    assert os.path.isfile(cerebrum.manual_path)
    assert os.path.isdir(cerebrum.manual_dir)
    assert "browser" not in cerebrum.list_manual_topics(enabled_only=True)
    assert "memory" in cerebrum.list_manual_topics(enabled_only=True)

    filtered_manual = cerebrum.get_agent_manual()
    assert "# The Agent Manual (Core Index)" in filtered_manual
    assert "| `browser` |" not in filtered_manual
    assert "| `memory` |" in filtered_manual

    # Explicit topic reads still work via Cerebrum and SystemTool delegation
    orch = SimpleNamespace(cerebrum=cerebrum)
    sys_tool = SystemTool(orchestrator=orch)
    assert "Browse -> Observe -> Act" in sys_tool.execute("read_manual", topic="browser")


def test_sleep_cycle_allows_system_read_manual():
    from core.orchestrator import AmityOrchestrator

    orch = AmityOrchestrator.__new__(AmityOrchestrator)
    orch.agent_id = "test_sleep_agent"
    orch._is_sleep_cycle = True
    orch._is_aborted = MagicMock(return_value=False)
    orch.get_fatigue = lambda: 0.0
    orch.is_paused = False
    orch.on_tool_started = MagicMock()
    orch.on_tool_finished = MagicMock()
    orch.on_tool_execution_finished = MagicMock()

    fake_tool = SimpleNamespace(icon="⚙️", color="#78909C", is_command_async=lambda *a: False)
    cerebrum = SimpleNamespace(
        tools={"System": fake_tool},
        execute_tool_call=MagicMock(return_value="Available Agent Manual Topics:\n- `memory`"),
    )
    orch.cerebrum = cerebrum

    orch._async_tool_execution([SimpleNamespace(name="System_read_manual", args={"topic": "memory"})], 0.0)

    cerebrum.execute_tool_call.assert_called_once()
    assert orch.on_tool_execution_finished.called
    args, _ = orch.on_tool_execution_finished.call_args
    responses = args[0]
    assert responses[0][0] == "System_read_manual"
    assert "Available Agent Manual Topics" in responses[0][1]["result"]


def test_static_first_prompt_order_conditional_low_token_and_refresh_stability():
    from core.orchestrator import AmityOrchestrator
    from core.openrouter_worker import OpenRouterWorker

    orch = AmityOrchestrator.__new__(AmityOrchestrator)
    orch.agent_id = "test_agent"
    orch.cerebrum = SimpleNamespace(
        get_agent_manual=lambda: "# The Agent Manual (Core Index)\nAlways check memory.\n",
        get_all_tool_declarations=lambda: [],
    )
    orch.mempalace_manager = SimpleNamespace(
        get_l0_identity=lambda: "# Layer 0 Identity\nName: Test",
        wake_up_dynamic=lambda: (
            f"{MEMORY_BEGIN}\n\n[Wake Context: Current Date & Time is 2026-11-13 10:00:00]\n\n"
            f"--- Morphological Memory (Prospective Attractors [Rev: 1]) ---\nold attractor\n{MEMORY_END}"
        ),
        refresh_prospective_anchors=lambda: "[Rev: 2] fresh attractor",
    )

    # Normal mode omits low-token block
    orch.settings_manager = _DictSettings({"core.agent.uid": "+OA-2345-6789", "core.low-token-mode": False})
    orch.build_system_prompt()
    assert "[SYSTEM STATE: LOW TOKEN MODE IS ACTIVE]" not in orch.system_prompt

    # Static sections must precede MEMORY_BEGIN
    idx_l0 = orch.system_prompt.find("# Layer 0 Identity")
    idx_uid = orch.system_prompt.find("+OA-2345-6789")
    idx_manual = orch.system_prompt.find("# The Agent Manual (Core Index)")
    idx_mem_begin = orch.system_prompt.find(MEMORY_BEGIN)
    idx_mem_end = orch.system_prompt.find(MEMORY_END)
    assert 0 <= idx_l0 < idx_uid < idx_manual < idx_mem_begin < idx_mem_end

    # Worker start_session injects static worker rules BEFORE MEMORY_BEGIN
    worker = OpenRouterWorker.__new__(OpenRouterWorker)
    worker.available = True
    worker.settings = orch.settings_manager
    worker.history = []
    worker.consumed_tool_call_ids = set()
    worker.multimodal_failed = False
    worker.start_session(orch.system_prompt, tools=[], clear_history=True)
    orch.gemini_worker = worker

    static_before, dynamic_before = split_system_prompt_for_caching(worker.sys_instruct)
    assert WORKER_CRITICAL_INSTRUCTION in static_before
    assert WORKER_AUTONOMOUS_SPEECH_INSTRUCTION in static_before
    assert MEMORY_BEGIN not in static_before
    assert dynamic_before.startswith(MEMORY_BEGIN)

    # deliver_memory_refresh keeps static prefix byte-identical
    orch.deliver_memory_refresh("[Rev: 2] fresh attractor")
    static_after, dynamic_after = split_system_prompt_for_caching(worker.sys_instruct)
    assert static_after == static_before
    assert "fresh attractor" in dynamic_after
    assert "old attractor" not in dynamic_after

    # Low token mode injects compact directive before MEMORY_BEGIN
    orch.settings_manager = _DictSettings({"core.agent.uid": "+OA-2345-6789", "core.low-token-mode": True})
    orch.build_system_prompt()
    idx_ltm = orch.system_prompt.find("[SYSTEM STATE: LOW TOKEN MODE IS ACTIVE]")
    idx_mb = orch.system_prompt.find(MEMORY_BEGIN)
    assert 0 < idx_ltm < idx_mb

    # Idempotent worker instruction helper
    once = inject_static_worker_instructions(orch.system_prompt)
    twice = inject_static_worker_instructions(once)
    assert once == twice


def test_claude_worker_splits_static_cached_and_dynamic_uncached_system_blocks():
    from core.claude_worker import ClaudeWorker

    worker = ClaudeWorker.__new__(ClaudeWorker)
    worker.available = True
    worker.settings = _DictSettings({"core.claude.thinking-effort": "low"})
    worker._history_lock = MagicMock()
    worker._history_lock.__enter__ = MagicMock(return_value=None)
    worker._history_lock.__exit__ = MagicMock(return_value=None)
    worker.history = []
    worker.consumed_tool_use_ids = set()
    worker._abort_flag = False
    worker.is_processing = False
    worker.thought_received = MagicMock()
    worker.error_occurred = MagicMock()
    worker.tokens_consumed = MagicMock()

    stream_ctx = MagicMock()
    stream_ctx.text_stream = []
    stream_ctx.get_final_message.return_value = SimpleNamespace(content=[], stop_reason="end_turn")
    stream_enter = MagicMock()
    stream_enter.__enter__ = MagicMock(return_value=stream_ctx)
    stream_enter.__exit__ = MagicMock(return_value=False)

    worker.client = SimpleNamespace(messages=SimpleNamespace(stream=MagicMock(return_value=stream_enter)))

    prompt_with_memory = (
        f"# Layer 0 Identity\nName: Amity\n\n# The Agent Manual (Core Index)\nCore rules.\n\n"
        f"{MEMORY_BEGIN}\n\n[Wake Context: Current Date & Time is 2026-11-13 10:00:00]\n{MEMORY_END}"
    )
    worker.start_session(prompt_with_memory, tools=[], clear_history=True)
    worker._process_thought_inner("hello", None, False)

    system_arg = worker.client.messages.stream.call_args.kwargs["system"]
    assert isinstance(system_arg, list)
    assert len(system_arg) == 2
    assert system_arg[0]["cache_control"] == {"type": "ephemeral"}
    assert "# The Agent Manual (Core Index)" in system_arg[0]["text"]
    assert WORKER_CRITICAL_INSTRUCTION in system_arg[0]["text"]
    assert MEMORY_BEGIN not in system_arg[0]["text"]
    assert system_arg[1]["text"].startswith(MEMORY_BEGIN)
    assert "cache_control" not in system_arg[1]
