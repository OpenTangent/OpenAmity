import os
import sys
import tempfile
import shutil
import time
import json
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PySide6.QtWidgets import QApplication
from config import paths
from core.settings_manager import SettingsManager
from core.orchestrator import AmityOrchestrator
from core.session_cache import (
    normalize_session_messages,
    save_session_cache,
    load_session_cache,
    clear_session_cache
)
from core.gemini_worker import GeminiWorker
from core.claude_worker import ClaudeWorker
from core.chatgpt_worker import ChatGptWorker
from core.deepseek_worker import DeepSeekWorker
from core.openrouter_worker import OpenRouterWorker
from core.agy_worker import AgyWorker
from core.backup_manager import EXCLUDED_FILE_NAMES


@pytest.fixture(scope="session")
def qapp():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication.instance()
    if app is None:
        app = QApplication(["-platform", "offscreen"])
    return app


@pytest.fixture
def temp_agent_dir(monkeypatch):
    temp_dir = tempfile.mkdtemp(prefix="openamity_session_cache_test_")
    monkeypatch.setattr("config.paths.get_app_data_dir", lambda: temp_dir)
    monkeypatch.setattr(
        "config.paths.get_base_dir_for",
        lambda aid: os.path.join(temp_dir, "agents", aid) if aid else os.path.join(temp_dir, "agents", "default")
    )
    monkeypatch.setattr(
        "config.paths.get_mempalace_dir",
        lambda aid: os.path.join(temp_dir, "agents", aid, "mempalace") if aid else os.path.join(temp_dir, "agents", "default", "mempalace")
    )
    monkeypatch.setattr("config.paths.get_chatroom_dir", lambda: os.path.join(temp_dir, "chatroom"))

    SettingsManager.clear_instances()
    yield temp_dir
    SettingsManager.clear_instances()
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_session_cache_roundtrip_and_normalization():
    raw_messages = [
        {"role": "user", "content": "Hello agent!"},
        {"role": "model", "content": "Hello user!"},
        {"role": "tool", "content": '{"result": "weather is sunny"}'},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Look at this:"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="}}
            ]
        },
        {"role": "assistant", "content": "I see the image."},
        {"role": "user", "content": "Now run a command"}
    ]

    # Without close_trailing_user
    norm1 = normalize_session_messages(raw_messages, close_trailing_user=False)
    assert len(norm1) == 5
    assert norm1[0]["role"] == "user"
    assert norm1[0]["content"] == "Hello agent!"
    assert norm1[1]["role"] == "assistant"
    assert norm1[1]["content"] == "Hello user!"
    assert norm1[2]["role"] == "user"
    # tool and next user turns merged
    assert "[Media attachment from earlier in session]" in norm1[2]["content"]
    assert norm1[3]["role"] == "assistant"
    assert norm1[3]["content"] == "I see the image."
    assert norm1[4]["role"] == "user"
    assert norm1[4]["content"] == "Now run a command"

    # With close_trailing_user=True
    norm2 = normalize_session_messages(raw_messages, close_trailing_user=True)
    assert len(norm2) == 6
    assert norm2[-1]["role"] == "assistant"
    assert "[Turn interrupted by application shutdown or restart]" in norm2[-1]["content"]


def test_corrupt_session_cache_quarantine(temp_agent_dir):
    agent_id = "test_corrupt_agent"
    cache_file = paths.get_session_cache_file(agent_id)
    os.makedirs(os.path.dirname(cache_file), exist_ok=True)

    # 1. Non-JSON corrupted data
    with open(cache_file, "w", encoding="utf-8") as f:
        f.write("{invalid_json: true, broken")

    loaded = load_session_cache(agent_id)
    assert loaded is None
    assert not os.path.exists(cache_file)  # original removed/quarantined

    # Verify quarantine file exists
    agent_dir = paths.get_base_dir_for(agent_id)
    corrupt_files = [f for f in os.listdir(agent_dir) if f.startswith("session_cache.json.corrupt.")]
    assert len(corrupt_files) == 1

    # 2. Non-dict root object
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(["not", "a", "dict"], f)

    loaded2 = load_session_cache(agent_id)
    assert loaded2 is None
    corrupt_files2 = [f for f in os.listdir(agent_dir) if f.startswith("session_cache.json.corrupt.")]
    assert len(corrupt_files2) == 2


def test_orchestrator_saves_and_restores_session_across_restart(temp_agent_dir):
    agent_id = "test_persist_agent"
    sm = SettingsManager(agent_id=agent_id)
    sm.set("core.paused", False)
    sm.set("core.first-run", False)
    sm.save()

    # Create mock orchestrator
    orch = AmityOrchestrator.__new__(AmityOrchestrator)
    orch.agent_id = agent_id
    orch.settings_manager = sm
    orch.is_paused = False
    orch._pausing_in_progress = False
    orch.is_busy = False
    orch.is_thinking = False
    orch._is_sleep_cycle = False
    orch._sleep_cycle_error = False
    orch._in_flight_prompt = None
    orch.active_subagents = {}
    orch.recent_history = [("User", "What is the capital of France?"), ("Agent", "Paris.")]
    orch.speech_queue = []
    orch.event_queue = []
    orch.session_fatigue_tokens = 15000
    orch._last_notified_fatigue_threshold = 50
    orch._hard_fatigue_limit_hit = False
    orch.current_task_weight = 15000
    orch.on_shutdown_complete = MagicMock()
    orch.on_paused_state_changed = MagicMock()
    orch.set_busy_state = MagicMock()
    orch.stop_all_processing = MagicMock()
    orch.append_to_conversation = MagicMock()
    orch.cerebrum = MagicMock()
    orch.pulse_engine = MagicMock()
    orch.pulse_engine.last_interaction_time = 1700000000.0

    # Mock cognitive worker with history
    worker = MagicMock()
    worker.get_session_history.return_value = [
        {"role": "user", "content": "What is the capital of France?"},
        {"role": "assistant", "content": "Paris."}
    ]
    orch.gemini_worker = worker

    # 1. Save session state
    orch.save_session_state()

    cache_file = paths.get_session_cache_file(agent_id)
    assert os.path.exists(cache_file)
    with open(cache_file, "r") as f:
        cache_data = json.load(f)
    assert cache_data["session_fatigue_tokens"] == 15000
    assert cache_data["last_notified_fatigue_threshold"] == 50
    assert len(cache_data["messages"]) == 2
    assert cache_data["pulse_state"]["last_interaction_time"] == 1700000000.0

    # 2. Simulate shutdown
    orch.shutdown(force_sleep=False)
    assert os.path.exists(cache_file)  # Cache preserved on shutdown

    # 3. Simulate App Restart: initialize fresh AmityOrchestrator
    SettingsManager.clear_instances()
    with patch("core.audio_input.AudioService.start_initialization"):
        orch_restored = AmityOrchestrator(agent_id=agent_id)
        try:
            assert orch_restored.session_fatigue_tokens == 15000
            assert orch_restored._last_notified_fatigue_threshold == 50
            assert len(orch_restored.recent_history) == 2
            assert orch_restored.pulse_engine.last_interaction_time == 1700000000.0
            assert "seamlessly restored from disk cache" in orch_restored.last_action_result
        finally:
            orch_restored.pulse_engine.stop()
            orch_restored._shutdown_flag = True


def test_crash_mid_turn_recovery(temp_agent_dir):
    agent_id = "test_crash_recovery"
    sm = SettingsManager(agent_id=agent_id)
    sm.set("core.paused", False)
    sm.set("core.first-run", False)
    sm.save()

    # Pre-populate session_cache as if saved mid-turn with in-flight prompt
    data = {
        "messages": [
            {"role": "user", "content": "Analyze system load and generate a report."},
            {"role": "assistant", "content": "[Tool invocation: Terminal_run(command='uptime')]"},
            {"role": "user", "content": "[Tool Result: Terminal_run -> load average: 0.12]"}
        ],
        "session_fatigue_tokens": 8500,
        "last_notified_fatigue_threshold": 0,
        "hard_fatigue_limit_hit": False,
        "current_task_weight": 8500,
        "recent_history": [("User", "Analyze system load and generate a report.")],
        "pulse_state": {"last_interaction_time": 1700000000.0}
    }
    save_session_cache(agent_id, data)

    # Restore session
    SettingsManager.clear_instances()
    with patch("core.audio_input.AudioService.start_initialization"):
        orch = AmityOrchestrator(agent_id=agent_id)
        try:
            # Trailing user/tool turn must have been cleanly closed for role alternation
            worker_history = orch._restored_session_messages
            # When worker set_session_history is invoked, trailing turn is closed with assistant closure
            norm = normalize_session_messages(worker_history, close_trailing_user=True)
            assert norm[-1]["role"] == "assistant"
            assert "[Turn interrupted by application shutdown or restart]" in norm[-1]["content"]
        finally:
            orch.pulse_engine.stop()
            orch._shutdown_flag = True


def test_sleep_cycle_clears_session_cache_on_success(temp_agent_dir):
    agent_id = "test_sleep_clear"
    sm = SettingsManager(agent_id=agent_id)
    sm.set("core.paused", False)
    sm.set("core.first-run", False)
    sm.save()

    orch = AmityOrchestrator.__new__(AmityOrchestrator)
    orch.agent_id = agent_id
    orch.settings_manager = sm
    orch.is_paused = False
    orch._pausing_in_progress = False
    orch.is_busy = False
    orch.is_thinking = False
    orch._is_sleep_cycle = True
    orch._sleep_cycle_error = False
    orch._in_flight_prompt = None
    orch.active_subagents = {}
    orch.speech_queue = []
    orch.event_queue = []
    orch.recent_history = []
    orch.session_fatigue_tokens = 95000
    orch._last_notified_fatigue_threshold = 90
    orch._hard_fatigue_limit_hit = False
    orch.current_task_weight = 95000
    orch.on_busy_state_changed = MagicMock()
    orch.tts_worker = None
    orch.gemini_worker = MagicMock()
    orch.build_system_prompt = MagicMock()
    orch.system_prompt = "Fresh System Prompt"
    orch.cerebrum = MagicMock()
    orch.cerebrum.get_all_tool_declarations.return_value = []

    # Write initial session cache
    save_session_cache(agent_id, {"messages": [{"role": "user", "content": "Hi"}], "session_fatigue_tokens": 95000})
    cache_file = paths.get_session_cache_file(agent_id)
    assert os.path.exists(cache_file)

    # Call check_cycle_completion when sleep cycle succeeded
    orch.check_cycle_completion()

    # Verify session fatigue is 0 and session cache file is deleted
    assert orch.session_fatigue_tokens == 0
    assert not os.path.exists(cache_file)
    assert orch.gemini_worker.start_session.called


def test_failed_sleep_cycle_preserves_session_cache(temp_agent_dir):
    agent_id = "test_failed_sleep"
    sm = SettingsManager(agent_id=agent_id)
    sm.set("core.paused", False)
    sm.set("core.first-run", False)
    sm.save()

    orch = AmityOrchestrator.__new__(AmityOrchestrator)
    orch.agent_id = agent_id
    orch.settings_manager = sm
    orch.is_paused = False
    orch._pausing_in_progress = False
    orch.is_busy = False
    orch.is_thinking = False
    orch._is_sleep_cycle = True
    orch._sleep_cycle_error = False
    orch._in_flight_prompt = None
    orch.active_subagents = {}
    orch.speech_queue = []
    orch.event_queue = []
    orch.recent_history = []
    orch.session_fatigue_tokens = 95000
    orch._last_notified_fatigue_threshold = 90
    orch._hard_fatigue_limit_hit = False
    orch.current_task_weight = 95000
    orch.on_busy_state_changed = MagicMock()
    orch.tts_worker = None
    orch.gemini_worker = MagicMock()
    orch.build_system_prompt = MagicMock()
    orch.append_to_conversation = MagicMock()

    # Pre-save session cache
    initial_messages = [{"role": "user", "content": "Pre-sleep work turn"}]
    save_session_cache(agent_id, {"messages": initial_messages, "session_fatigue_tokens": 95000})
    cache_file = paths.get_session_cache_file(agent_id)
    assert os.path.exists(cache_file)

    # Simulate error during sleep cycle (which automatically finishes thinking and executes fail-closed check)
    orch.handle_gemini_error("API Error 503: Service Unavailable")

    # Verify fatigue was NOT reset and cache was NOT deleted
    assert orch.session_fatigue_tokens == 95000
    assert os.path.exists(cache_file)
    # Verify worker had cached session history restored for retry
    assert orch.gemini_worker.set_session_history.called


def test_worker_history_export_import_all_providers(temp_agent_dir):
    test_turns = [
        {"role": "user", "content": "How are you?"},
        {"role": "assistant", "content": "I am doing well, thank you."}
    ]

    # 1. GeminiWorker
    with patch("google.genai.Client"):
        gw = GeminiWorker.__new__(GeminiWorker)
        gw.agent_id = "test_gemini"
        gw.settings = SettingsManager(agent_id="test_gemini")
        gw._chat_lock = MagicMock()
        gw._chat_lock.__enter__ = MagicMock()
        gw._chat_lock.__exit__ = MagicMock()
        mock_part1 = MagicMock()
        mock_part1.text = "How are you?"
        mock_part1.function_call = None
        mock_part1.function_response = None
        mock_part1.inline_data = None
        mock_part1.file_data = None
        mock_content1 = MagicMock()
        mock_content1.role = "user"
        mock_content1.parts = [mock_part1]
        gw._get_chat_history = MagicMock(return_value=[mock_content1])
        hist = gw.get_session_history()
        assert len(hist) == 1
        assert hist[0]["role"] == "user"
        assert hist[0]["content"] == "How are you?"

    # 2. ClaudeWorker
    cw = ClaudeWorker.__new__(ClaudeWorker)
    cw.agent_id = "test_claude"
    cw.settings = SettingsManager(agent_id="test_claude")
    cw._history_lock = MagicMock()
    cw._history_lock.__enter__ = MagicMock()
    cw._history_lock.__exit__ = MagicMock()
    cw.history = []
    cw.set_session_history(test_turns)
    assert len(cw.history) == 2
    assert cw.history[0]["role"] == "user"
    assert cw.history[1]["role"] == "assistant"
    hist_c = cw.get_session_history()
    assert len(hist_c) == 2

    # 3. ChatGptWorker
    cgw = ChatGptWorker.__new__(ChatGptWorker)
    cgw.agent_id = "test_chatgpt"
    cgw.settings = SettingsManager(agent_id="test_chatgpt")
    cgw.history = []
    cgw.set_session_history(test_turns)
    assert len(cgw.history) == 2
    hist_cg = cgw.get_session_history()
    assert len(hist_cg) == 2

    # 4. DeepSeekWorker
    dsw = DeepSeekWorker.__new__(DeepSeekWorker)
    dsw.agent_id = "test_deepseek"
    dsw.settings = SettingsManager(agent_id="test_deepseek")
    dsw.history = []
    dsw.set_session_history(test_turns)
    assert len(dsw.history) == 2
    hist_ds = dsw.get_session_history()
    assert len(hist_ds) == 2

    # 5. OpenRouterWorker
    orw = OpenRouterWorker.__new__(OpenRouterWorker)
    orw.agent_id = "test_openrouter"
    orw.settings = SettingsManager(agent_id="test_openrouter")
    orw.history = []
    orw.set_session_history(test_turns)
    assert len(orw.history) == 2
    hist_or = orw.get_session_history()
    assert len(hist_or) == 2

    # 6. AgyWorker
    aw = AgyWorker.__new__(AgyWorker)
    aw.agent_id = "test_agy"
    aw.settings = SettingsManager(agent_id="test_agy")
    aw.history = []
    aw.set_session_history(test_turns)
    assert len(aw.history) == 2
    hist_a = aw.get_session_history()
    assert len(hist_a) == 2


def test_main_window_close_event_skips_sleep_cycle(qapp, temp_agent_dir):
    from gui.main_window import MainWindow

    orch = MagicMock()
    orch.agent_id = "test_mw_agent"
    orch.is_paused = False
    orch.get_fatigue.return_value = 0.50  # High fatigue that previously triggered sleep on close

    view = MagicMock()
    view.orchestrator = orch

    with patch("gui.main_window.SpellCheckHighlighter"):
        mw = MainWindow.__new__(MainWindow)
        mw._shutting_down = False
        mw.stacked_layout = MagicMock()
        mw.stacked_layout.currentIndex.return_value = 0
        mw.agent_views = {"test_mw_agent": view}
        mw.ui_shutdown_complete = MagicMock()

        event = MagicMock()
        mw.closeEvent(event)

        # Orch shutdown must be called immediately with force_sleep=False
        orch.shutdown.assert_called_once_with(force_sleep=False)
        assert event.ignore.called


def test_backup_excludes_session_cache():
    assert "session_cache.json" in EXCLUDED_FILE_NAMES
    assert "somatic_state.json" in EXCLUDED_FILE_NAMES
