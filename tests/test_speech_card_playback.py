import json
import math
import os
import shutil
import sqlite3
import struct
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt, QUrl
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from config import paths
from core.audio_output import (
    SPEECH_CACHE_TTL_SECONDS,
    SpeechPlaybackController,
    TTSWorker,
    build_speech_cache_key,
    encode_pcm_to_opus_atomic,
    get_speech_cache_path,
    get_valid_cached_speech_path,
)
from core.cache_manager import CacheManager
from core.chat_history_manager import ChatHistoryManager
from core.events import Signal as CoreSignal
from core.orchestrator import AmityOrchestrator
from core.settings_manager import SettingsManager
from gui.agent_view import AgentView


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(["-platform", "offscreen"])
    return app


@pytest.fixture
def speech_env(monkeypatch):
    ChatHistoryManager.clear_instances()
    SettingsManager.clear_instances() if hasattr(SettingsManager, "clear_instances") else None
    SpeechPlaybackController.reset_instance()

    temp_dir = tempfile.mkdtemp(prefix="openamity_test_speech_")
    monkeypatch.setenv("XDG_DATA_HOME", temp_dir)
    monkeypatch.setenv("XDG_CACHE_HOME", os.path.join(temp_dir, "cache"))
    monkeypatch.setattr(paths, "get_app_data_dir", lambda: temp_dir)
    monkeypatch.setattr(paths, "get_agent_data_dir", lambda aid: os.path.join(temp_dir, "agents", aid))
    monkeypatch.setattr(paths, "get_base_dir_for", lambda aid: os.path.join(temp_dir, "agents", aid))
    monkeypatch.setattr(
        paths,
        "get_chat_history_db_path",
        lambda aid: os.path.join(temp_dir, "agents", aid, "chat_history.db"),
    )
    monkeypatch.setattr(
        paths,
        "get_audio_cache_dir",
        lambda aid=None: os.path.join(temp_dir, "agents", aid, "audio_cache")
        if aid
        else os.path.join(temp_dir, "audio_cache"),
    )
    monkeypatch.setattr(
        paths,
        "get_speech_transcript_cache_dir",
        lambda aid=None: os.path.join(paths.get_audio_cache_dir(aid), "transcripts"),
    )

    yield temp_dir

    SpeechPlaybackController.reset_instance()
    ChatHistoryManager.clear_instances()
    shutil.rmtree(temp_dir, ignore_errors=True)


def _synthetic_pcm(duration_sec: float = 0.25, sample_rate: int = 24000, freq_hz: float = 440.0) -> bytes:
    n_samples = int(duration_sec * sample_rate)
    samples = [
        int(12000 * math.sin(2.0 * math.pi * freq_hz * (i / sample_rate)))
        for i in range(n_samples)
    ]
    return struct.pack("<" + ("h" * n_samples), *samples)


def _make_orchestrator(agent_id: str, settings_overrides=None):
    os.makedirs(paths.get_base_dir_for(agent_id), exist_ok=True)
    sm = SettingsManager(agent_id=agent_id)
    for k, v in (settings_overrides or {}).items():
        sm.set(k, v)
    sm.save()

    mgr = ChatHistoryManager(agent_id=agent_id)
    orch = AmityOrchestrator.__new__(AmityOrchestrator)
    orch.agent_id = agent_id
    orch.settings_manager = sm
    orch.config_manager = MagicMock()
    orch.config_manager.get.return_value = "Tester"
    orch.chat_history_manager = mgr

    orch.on_message_appended = CoreSignal()
    orch.on_message_record_appended = CoreSignal()
    orch.on_thought_card_updated = CoreSignal()
    orch.on_busy_state_changed = CoreSignal()
    orch.on_amplitude_emitted = CoreSignal()
    orch.on_waveform_emitted = CoreSignal()
    orch.on_paused_state_changed = CoreSignal()
    orch.on_pause_pending = CoreSignal()
    orch.on_tool_started = CoreSignal()
    orch.on_tool_finished = CoreSignal()
    orch.on_listening_state_changed = CoreSignal()
    orch.on_shutdown_complete = CoreSignal()

    orch.is_paused = bool(sm.get("core.paused", False))
    orch.is_busy = False
    orch.is_thinking = False
    orch._is_executing_tools = False
    orch._is_sleep_cycle = False
    orch._pending_settings_reload = False
    orch._shutdown_flag = False
    orch._speaker_invoked_in_cycle = False
    orch._active_thought_card_id = None
    orch._active_thought_entries = []
    orch._thought_card_lock = threading.RLock()
    orch._speech_lock = threading.RLock()
    orch._active_speech_card_key = None
    orch._active_speech_is_replay = False
    orch.recent_history = []
    orch.speech_queue = []
    orch.event_queue = []
    orch.tts_worker = None
    orch.gemini_worker = None
    orch.cerebrum = MagicMock()
    orch.cerebrum.tools = {}

    SpeechPlaybackController.instance().register_orchestrator(orch)
    return orch


# ---------------------------------------------------------------------------
# 1. ChatHistoryManager migration & CacheManager recursive pruning
# ---------------------------------------------------------------------------

def test_chat_history_legacy_migration_and_message_type(speech_env):
    agent_id = "+OA-MIGRATE"
    db_path = paths.get_chat_history_db_path(agent_id)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    # Create legacy schema without message_type column
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            sender TEXT NOT NULL,
            content TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO messages (timestamp, sender, content) VALUES (?, ?, ?)",
        ("2026-01-01T00:00:00+00:00", "Agent", "Legacy spoken message"),
    )
    conn.commit()
    conn.close()

    mgr = ChatHistoryManager(agent_id=agent_id)
    r_speech = mgr.add_message("Agent", "New spoken message", message_type="speech")
    r_text = mgr.add_message("Agent", "```python\nprint(1)\n```", message_type="text")

    assert r_speech["message_type"] == "speech"
    assert r_text["message_type"] == "text"

    msgs = mgr.get_messages(limit=10)
    assert len(msgs) == 3
    assert msgs[0]["content"] == "Legacy spoken message"
    assert msgs[0]["message_type"] == ""
    assert msgs[1]["message_type"] == "speech"
    assert msgs[2]["message_type"] == "text"


def test_cache_manager_recursively_cleans_transcript_speech_cache(speech_env):
    agent_id = "+OA-CACHE-01"
    transcript_dir = paths.get_speech_transcript_cache_dir(agent_id)
    os.makedirs(transcript_dir, exist_ok=True)

    fresh_file = os.path.join(transcript_dir, "msg_10.opus")
    expired_file = os.path.join(transcript_dir, "msg_1.opus")
    with open(fresh_file, "wb") as f:
        f.write(b"fresh")
    with open(expired_file, "wb") as f:
        f.write(b"stale")

    old_ts = time.time() - (SPEECH_CACHE_TTL_SECONDS + 120)
    os.utime(expired_file, (old_ts, old_ts))

    CacheManager.clear_all_agent_expired_caches(agent_id, max_age_days=1.0)
    assert os.path.exists(fresh_file)
    assert not os.path.exists(expired_file)


def test_valid_cached_speech_path_ttl_and_empty_guard(speech_env):
    agent_id = "+OA-TTL-01"
    key = build_speech_cache_key(message_id=42, text="Hello")
    cache_path = get_speech_cache_path(agent_id, key)
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)

    # Empty file is invalid and removed
    with open(cache_path, "wb") as f:
        f.write(b"")
    assert get_valid_cached_speech_path(agent_id, key) is None
    assert not os.path.exists(cache_path)

    # Non-empty fresh file is valid
    with open(cache_path, "wb") as f:
        f.write(b"OggS-valid")
    assert get_valid_cached_speech_path(agent_id, key) == cache_path

    # Expired (>24h) file is invalidated and removed
    old_ts = time.time() - (SPEECH_CACHE_TTL_SECONDS + 60)
    os.utime(cache_path, (old_ts, old_ts))
    assert get_valid_cached_speech_path(agent_id, key) is None
    assert not os.path.exists(cache_path)


# ---------------------------------------------------------------------------
# 2. TTSWorker 24h Opus caching, early stop saving, mid-synthesis discard,
#    cached playback oscilloscope telemetry, and corrupt cache fallback
# ---------------------------------------------------------------------------

def test_encode_and_play_cached_opus_emits_waveform_and_skips_synthesis(speech_env):
    agent_id = "+OA-OPUS-01"
    key = build_speech_cache_key(message_id=7, text="Cached sentence")
    cache_path = get_speech_cache_path(agent_id, key)

    pcm = _synthetic_pcm(duration_sec=0.25, sample_rate=24000)
    assert encode_pcm_to_opus_atomic(pcm, 24000, cache_path) is True
    assert os.path.isfile(cache_path)
    assert os.path.getsize(cache_path) > 0

    worker = TTSWorker("Cached sentence", agent_id=agent_id, cache_path=cache_path, use_cached_file=cache_path)
    started = []
    waves = []
    amps = []
    worker.started_playback.connect(lambda: started.append(True))
    worker.waveform_emitted.connect(waves.extend)
    worker.amplitude_emitted.connect(amps.append)

    mock_ffplay = MagicMock()
    mock_ffplay.stdin = MagicMock()
    mock_ffplay.poll.return_value = 0

    real_popen = TTSWorker._play_cached_opus_file.__globals__["subprocess"].Popen

    def selective_popen(cmd, *args, **kwargs):
        if cmd and cmd[0] == "ffplay":
            return mock_ffplay
        return real_popen(cmd, *args, **kwargs)

    with patch("core.audio_output.subprocess.Popen", side_effect=selective_popen), \
         patch.object(worker, "_stream_and_play_live") as mock_live:
        worker._stream_and_play()

    assert not mock_live.called
    assert len(started) == 1
    assert len(waves) > 0
    assert any(len(frame) > 0 for frame in waves)
    assert len(amps) > 0
    assert mock_ffplay.stdin.write.called
    assert mock_ffplay.wait.called


def test_corrupt_cached_opus_falls_back_to_live_synthesis(speech_env):
    agent_id = "+OA-CORRUPT-01"
    key = build_speech_cache_key(message_id=9, text="Corrupt test")
    cache_path = get_speech_cache_path(agent_id, key)
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "wb") as f:
        f.write(b"not-a-valid-opus-stream")

    worker = TTSWorker("Corrupt test", agent_id=agent_id, cache_path=cache_path, use_cached_file=cache_path)
    with patch.object(worker, "_stream_and_play_live") as mock_live:
        worker._stream_and_play()

    assert mock_live.called
    assert not os.path.exists(cache_path)


def test_gemini_stream_caches_complete_synthesis_even_when_playback_stopped_early(speech_env):
    agent_id = "+OA-GEMINI-CACHE"
    cache_path = get_speech_cache_path(agent_id, "msg_101")
    worker = TTSWorker("Complete synthesis early stop", agent_id=agent_id, cache_path=cache_path)

    pcm_chunk = _synthetic_pcm(duration_sec=0.15, sample_rate=24000)

    def stream_gen():
        part1 = MagicMock()
        part1.inline_data.data = pcm_chunk
        cand1 = MagicMock(content=MagicMock(parts=[part1]), finish_reason=None)
        yield MagicMock(candidates=[cand1])

        part2 = MagicMock()
        part2.inline_data.data = pcm_chunk
        cand2 = MagicMock(content=MagicMock(parts=[part2]), finish_reason="STOP")
        yield MagicMock(candidates=[cand2])

    mock_client = MagicMock()
    mock_client.models.generate_content_stream.return_value = stream_gen()

    mock_ffplay = MagicMock()
    mock_ffplay.stdin = MagicMock()
    # Simulate user clicking Stop while ffplay is still playing AFTER synthesis finished
    def wait_with_stop(*args, **kwargs):
        worker.stop()
        return 0
    mock_ffplay.wait.side_effect = wait_with_stop

    real_popen = TTSWorker._play_cached_opus_file.__globals__["subprocess"].Popen

    def selective_popen(cmd, *args, **kwargs):
        if cmd and cmd[0] == "ffplay":
            return mock_ffplay
        return real_popen(cmd, *args, **kwargs)

    with patch("core.audio_output.subprocess.Popen", side_effect=selective_popen):
        worker._stream_gemini_attempt(mock_client, "Prompt")

    # Complete synthesis must still be saved atomically!
    assert os.path.isfile(cache_path)
    assert os.path.getsize(cache_path) > 0


def test_gemini_mid_synthesis_stop_does_not_write_partial_cache(speech_env):
    agent_id = "+OA-GEMINI-ABORT"
    cache_path = get_speech_cache_path(agent_id, "msg_102")
    worker = TTSWorker("Partial synthesis abort", agent_id=agent_id, cache_path=cache_path)

    pcm_chunk = _synthetic_pcm(duration_sec=0.1, sample_rate=24000)

    def aborting_stream():
        part1 = MagicMock()
        part1.inline_data.data = pcm_chunk
        cand1 = MagicMock(content=MagicMock(parts=[part1]), finish_reason=None)
        yield MagicMock(candidates=[cand1])
        # User clicks Stop mid-synthesis before stream finishes
        worker.stop()
        part2 = MagicMock()
        part2.inline_data.data = pcm_chunk
        cand2 = MagicMock(content=MagicMock(parts=[part2]), finish_reason="STOP")
        yield MagicMock(candidates=[cand2])

    mock_client = MagicMock()
    mock_client.models.generate_content_stream.return_value = aborting_stream()

    mock_ffplay = MagicMock()
    mock_ffplay.stdin = MagicMock()
    mock_ffplay.wait.return_value = 0

    with patch("core.audio_output.subprocess.Popen", return_value=mock_ffplay):
        worker._stream_gemini_attempt(mock_client, "Prompt")

    assert not os.path.exists(cache_path)


# ---------------------------------------------------------------------------
# 3. Orchestrator, SpeechPlaybackController, Queue, Mute Override, Paused
# ---------------------------------------------------------------------------

def test_orchestrator_speaker_payload_sets_message_types(speech_env):
    orch = _make_orchestrator("+OA-PAYLOAD")
    orch._collapse_active_thought_card = MagicMock()
    orch.speak = MagicMock()

    res_speak = orch._dispatch_speaker_payload(
        "Speaker_speak_aloud",
        json.dumps({"action": "trigger_speak_aloud", "text": "Hello aloud"}),
    )
    res_text = orch._dispatch_speaker_payload(
        "Speaker_output_text",
        json.dumps({"action": "trigger_output_text", "text": "https://example.com"}),
    )

    assert res_speak == "Speech queued."
    assert res_text == "Text output to GUI."

    msgs = orch.chat_history_manager.get_messages(limit=10)
    assert len(msgs) == 2
    assert msgs[0]["content"] == "Hello aloud"
    assert msgs[0]["message_type"] == "speech"
    assert msgs[1]["content"] == "https://example.com"
    assert msgs[1]["message_type"] == "text"
    orch.speak.assert_called_once_with("Hello aloud", message_id=msgs[0]["id"])


def test_stop_all_speech_clears_queue_keeps_thinking_and_guards_stale_worker(speech_env):
    orch_a = _make_orchestrator("+OA-AGENT-A")
    orch_b = _make_orchestrator("+OA-AGENT-B")
    orch_a.is_thinking = True
    orch_a.is_busy = True

    busy_events = []
    orch_a.on_busy_state_changed.connect(lambda b, s: busy_events.append((b, s)))

    started_workers = []
    hold_event = threading.Event()

    class FakeWorker:
        def __init__(self, text, **kwargs):
            self.text = text
            self.cache_path = kwargs.get("cache_path")
            self.use_cached_file = kwargs.get("use_cached_file")
            self.card_key = kwargs.get("card_key")
            self._is_stopped = False
            self._alive = False
            self.started_playback = CoreSignal()
            self.amplitude_emitted = CoreSignal()
            self.waveform_emitted = CoreSignal()
            self.error_occurred = CoreSignal()
            self.status_emitted = CoreSignal()
            self.on_finished = CoreSignal()
            started_workers.append(self)

        def is_alive(self):
            return self._alive

        def stop(self):
            self._is_stopped = True
            self._alive = False
            hold_event.set()

        def start(self):
            self._alive = True
            self.started_playback.emit()

    with patch("core.orchestrator.TTSWorker", FakeWorker):
        orch_a.speak("First sentence", message_id=1)
        orch_a.speak("Second queued sentence", message_id=2)
        orch_b.speech_queue.append({"text": "B queued", "message_id": 3, "card_key": "msg_3", "bypass_mute": False, "is_replay": False})

        ctrl = SpeechPlaybackController.instance()
        assert ctrl.is_any_speech_active() is True
        assert len(orch_a.speech_queue) == 1
        assert len(started_workers) == 1

        # Cross-agent replay blocked while speech is active
        assert orch_b.replay_transcript("Blocked", message_id=99) is False

        # Global stop clears both queues, stops worker, keeps orch_a thinking
        ctrl.stop_all_speech()
        assert ctrl.is_any_speech_active() is False
        assert len(orch_a.speech_queue) == 0
        assert len(orch_b.speech_queue) == 0
        assert started_workers[0]._is_stopped is True
        assert orch_a.is_thinking is True
        assert busy_events[-1] == (True, False)

        # Immediately start new replay on B
        assert orch_b.replay_transcript("New replay", message_id=10) is True
        assert len(started_workers) == 2
        w_old = started_workers[0]
        w_new = started_workers[1]

        # Delayed on_finished / started_playback from old stopped worker must NOT clobber w_new!
        orch_a.on_tts_finished(worker=w_old)
        orch_a._on_started_playback(worker=w_old)
        assert ctrl.is_any_speech_active() is True
        assert ctrl.get_state()["agent_id"] == "+OA-AGENT-B"
        assert orch_b.tts_worker is w_new


def test_muted_agent_replay_generates_and_plays_single_transcript_only(speech_env):
    orch = _make_orchestrator("+OA-MUTED", settings_overrides={"core.mute": True})
    orch.is_busy = False
    orch.is_thinking = False

    played_texts = []
    finish_gate = threading.Event()

    class MockSyncWorker:
        def __init__(self, text, **kwargs):
            self.text = text
            self.cache_path = kwargs.get("cache_path")
            self.use_cached_file = kwargs.get("use_cached_file")
            self.card_key = kwargs.get("card_key")
            self._is_stopped = False
            self._alive = False
            self.started_playback = CoreSignal()
            self.amplitude_emitted = CoreSignal()
            self.waveform_emitted = CoreSignal()
            self.error_occurred = CoreSignal()
            self.status_emitted = CoreSignal()
            self.on_finished = CoreSignal()

        def is_alive(self):
            return self._alive

        def stop(self):
            self._is_stopped = True
            self._alive = False

        def start(self):
            self._alive = True
            played_texts.append((self.text, self.use_cached_file, self.cache_path))
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            with open(self.cache_path, "wb") as f:
                f.write(b"OggS-synthesized")
            self.started_playback.emit()
            self._alive = False
            self.on_finished.emit()
            finish_gate.set()

    with patch("core.orchestrator.TTSWorker", MockSyncWorker):
        # Normal speech while muted is skipped and not synthesized
        orch.handle_gemini_speech("Silent muted output")
        assert played_texts == []

        msgs = orch.chat_history_manager.get_messages(limit=10)
        msg_id = msgs[0]["id"]
        expected_cache = get_speech_cache_path(orch.agent_id, build_speech_cache_key(msg_id, "Silent muted output"))
        assert not os.path.exists(expected_cache)

        # Queued non-replay speech remains skipped even when a single replay runs
        orch.speech_queue.append(
            {"text": "Should stay muted", "message_id": 999, "card_key": "msg_999", "bypass_mute": False, "is_replay": False}
        )

        # Clicking Play on the muted card generates + plays THAT transcript only
        assert orch.replay_transcript("Silent muted output", message_id=msg_id) is True
        assert finish_gate.wait(timeout=2.0)
        assert len(played_texts) == 1
        assert played_texts[0][0] == "Silent muted output"
        assert played_texts[0][1] is None  # cache miss triggered fresh synthesis
        assert orch.settings_manager.get("core.mute", False) is True
        assert os.path.isfile(expected_cache)

        # Second click within 24h hits cached file even if voice setting changed
        orch.settings_manager.set("core.tts.gemini.model-name", "Charon")
        orch.settings_manager.save()
        finish_gate.clear()
        assert orch.replay_transcript("Silent muted output", message_id=msg_id) is True
        assert finish_gate.wait(timeout=2.0)
        assert len(played_texts) == 2
        assert played_texts[1][1] == expected_cache

        # Expire cache (>24h) -> next replay regenerates using current voice settings
        old_ts = time.time() - (SPEECH_CACHE_TTL_SECONDS + 120)
        os.utime(expected_cache, (old_ts, old_ts))
        finish_gate.clear()
        assert orch.replay_transcript("Silent muted output", message_id=msg_id) is True
        assert finish_gate.wait(timeout=2.0)
        assert len(played_texts) == 3
        assert played_texts[2][1] is None


def test_paused_agent_allows_replay_with_visualizer_without_resuming(speech_env):
    orch = _make_orchestrator("+OA-PAUSED", settings_overrides={"core.paused": True})
    orch.is_paused = True
    orch.event_queue = [{"type": "input", "text": "Should stay queued"}]

    busy_states = []
    orch.on_busy_state_changed.connect(lambda b, s: busy_states.append((b, s)))

    class PausedReplayWorker:
        def __init__(self, text, **kwargs):
            self.text = text
            self._is_stopped = False
            self._alive = False
            self.started_playback = CoreSignal()
            self.amplitude_emitted = CoreSignal()
            self.waveform_emitted = CoreSignal()
            self.error_occurred = CoreSignal()
            self.status_emitted = CoreSignal()
            self.on_finished = CoreSignal()

        def is_alive(self):
            return self._alive

        def stop(self):
            self._is_stopped = True
            self._alive = False

        def start(self):
            self._alive = True
            self.started_playback.emit()
            self._alive = False
            self.on_finished.emit()

    with patch("core.orchestrator.TTSWorker", PausedReplayWorker):
        assert orch.replay_transcript("Paused replay", message_id=5) is True

    assert orch.is_paused is True
    assert len(orch.event_queue) == 1
    assert (False, True) in busy_states
    assert busy_states[-1] == (False, False)


# ---------------------------------------------------------------------------
# 4. GUI: Card Eligibility, Global Stop Sync, Tooltips, Visualizer, Scroll, Cleanup
# ---------------------------------------------------------------------------

def test_agent_view_speech_buttons_eligibility_global_sync_and_actions(qapp, speech_env):
    orch_a = _make_orchestrator("+OA-GUI-A", settings_overrides={"core.agent.name": "AmityA"})
    orch_b = _make_orchestrator("+OA-GUI-B", settings_overrides={"core.agent.name": "AmityB"})

    # Populate A with mixed message types (legacy Agent, speech, output_text, User, System, Thoughts)
    m_legacy = orch_a.chat_history_manager.add_message("Agent", "Legacy agent speech")
    m_user = orch_a.chat_history_manager.add_message("User", "User prompt")
    m_system = orch_a.chat_history_manager.add_message("System", "System info")
    m_thoughts = orch_a.chat_history_manager.add_message("Thoughts", "Internal thought")
    m_text = orch_a.chat_history_manager.add_message("Agent", "Silent markdown table", message_type="text")
    m_speech = orch_a.chat_history_manager.add_message("Agent", "Spoken response", message_type="speech")

    # Populate B with one speech card
    m_b_speech = orch_b.chat_history_manager.add_message("Agent", "Agent B spoke", message_type="speech")

    view_a = AgentView(orch_a)
    view_b = AgentView(orch_b)
    try:
        view_a.resize(800, 600)
        view_b.resize(800, 600)
        view_a.show()
        view_b.show()
        qapp.processEvents()

        buttons_a = view_a.speech_button_manager.buttons
        buttons_b = view_b.speech_button_manager.buttons

        # Only legacy Agent ("") and "speech" cards get buttons
        expected_a_keys = {
            build_speech_cache_key(m_legacy["id"], "Legacy agent speech"),
            build_speech_cache_key(m_speech["id"], "Spoken response"),
        }
        assert set(buttons_a.keys()) == expected_a_keys
        assert build_speech_cache_key(m_text["id"], "Silent markdown table") not in buttons_a
        assert build_speech_cache_key(m_user["id"], "User prompt") not in buttons_a
        assert build_speech_cache_key(m_system["id"], "System info") not in buttons_a
        assert build_speech_cache_key(m_thoughts["id"], "Internal thought") not in buttons_a

        key_b = build_speech_cache_key(m_b_speech["id"], "Agent B spoke")
        assert set(buttons_b.keys()) == {key_b}

        # Initial idle state -> "play" mode & "Play speech" tooltip
        key_legacy = build_speech_cache_key(m_legacy["id"], "Legacy agent speech")
        key_speech = build_speech_cache_key(m_speech["id"], "Spoken response")
        for btn in list(buttons_a.values()) + list(buttons_b.values()):
            assert btn.mode == "play"
            assert btn.toolTip() == "Play speech"

        # Bottom cards are visible while top off-screen card stays hidden until scrolled into view
        assert buttons_a[key_speech].isVisible() is True
        assert buttons_b[key_b].isVisible() is True
        assert buttons_a[key_legacy].isVisible() is False

        # Trigger generating state on Agent A's spoken card
        ctrl = SpeechPlaybackController.instance()
        ctrl.mark_generating("+OA-GUI-A", key_speech, queued_count=0)
        qapp.processEvents()

        # ALL cards across BOTH Agent A and Agent B switch to stop mode
        assert buttons_a[key_speech].mode == "stop"
        assert buttons_a[key_speech].is_active_card is True
        assert buttons_a[key_speech].is_generating is True
        assert buttons_a[key_speech].toolTip() == "Generating speech"
        assert buttons_a[key_speech]._pulse_timer.isActive() is True

        assert buttons_a[key_legacy].mode == "stop"
        assert buttons_a[key_legacy].is_active_card is False
        assert buttons_a[key_legacy].toolTip() == "Stop speech"

        assert buttons_b[key_b].mode == "stop"
        assert buttons_b[key_b].is_active_card is False
        assert buttons_b[key_b].toolTip() == "Stop speech"

        # Transition to playing state
        ctrl.mark_playing("+OA-GUI-A", key_speech, queued_count=0)
        qapp.processEvents()
        assert buttons_a[key_speech].is_active_card is True
        assert buttons_a[key_speech].is_generating is False
        assert buttons_a[key_speech].toolTip() == "Stop speech"
        assert buttons_a[key_speech]._pulse_timer.isActive() is False

        # Clicking Stop on Agent B's card stops playback globally
        orch_a.tts_worker = MagicMock()
        orch_a.speech_queue.append("Queued item")
        QTest.mouseClick(buttons_b[key_b], Qt.LeftButton)
        qapp.processEvents()

        assert ctrl.is_any_speech_active() is False
        assert len(orch_a.speech_queue) == 0
        for btn in list(buttons_a.values()) + list(buttons_b.values()):
            assert btn.mode == "play"
            assert btn.toolTip() == "Play speech"

        # Scroll to top so legacy card button becomes visible, then click Play
        view_a.conversation_log.verticalScrollBar().setValue(0)
        qapp.processEvents()
        assert buttons_a[key_legacy].isVisible() is True

        orch_a.replay_transcript = MagicMock(return_value=True)
        QTest.mouseClick(buttons_a[key_legacy], Qt.LeftButton)
        qapp.processEvents()
        orch_a.replay_transcript.assert_called_once_with(
            "Legacy agent speech",
            message_id=m_legacy["id"],
            card_key=key_legacy,
        )

        # Live appended Speaker_output_text via orchestrator omits button; Speaker_speak_aloud adds one
        orch_a.append_to_conversation("Agent", "Code block only", message_type="text")
        orch_a.append_to_conversation("Agent", "Live spoken line", message_type="speech")
        qapp.processEvents()
        assert len(view_a.speech_button_manager.buttons) == 3
    finally:
        view_a.cleanup()
        view_b.cleanup()
        view_a.close()
        view_b.close()
        qapp.processEvents()


def test_agent_view_replay_visualizer_and_scroll_preservation(qapp, speech_env):
    orch = _make_orchestrator("+OA-VIS-01")
    for i in range(1, 35):
        orch.chat_history_manager.add_message("User", f"Question {i}")
        orch.chat_history_manager.add_message("Agent", f"Answer number {i} with enough words to fill space", message_type="speech")

    view = AgentView(orch)
    try:
        view.resize(700, 450)
        view.show()
        qapp.processEvents()

        sb = view.conversation_log.verticalScrollBar()
        assert sb.maximum() > 100

        # Scroll up near the top so _at_bottom is False
        sb.setValue(40)
        qapp.processEvents()
        assert view._at_bottom is False
        saved_scroll = sb.value()

        # Standalone replay while idle shows visualizer, keeps Pause button & placeholder unchanged, preserves scroll
        orch.on_busy_state_changed.emit(False, True)
        qapp.processEvents()
        assert view.visualizer.isVisible() is True
        assert view.visualizer.is_active is True
        assert view.btn_pause.text() == "❚❚"
        assert view.text_input.placeholderText() == "Type a message..."
        assert abs(sb.value() - saved_scroll) <= 5

        # Stop replay hides visualizer cleanly
        orch.on_busy_state_changed.emit(False, False)
        qapp.processEvents()
        assert view.visualizer.isVisible() is False
        assert view.visualizer.is_active is False

        # Replay while paused keeps paused footer controls while showing visualizer
        orch.is_paused = True
        orch.on_paused_state_changed.emit(True)
        qapp.processEvents()
        assert view.btn_pause.text() == "▶"
        assert view.text_input.isEnabled() is False

        orch.on_busy_state_changed.emit(False, True)
        qapp.processEvents()
        assert view.visualizer.isVisible() is True
        assert view.visualizer.is_active is True
        assert view.btn_pause.text() == "▶"
        assert view.text_input.isEnabled() is False

        # Scroll to bottom so last_key is visible, trigger generating pulse, then cleanup
        sb.setValue(sb.maximum())
        qapp.processEvents()
        ctrl = SpeechPlaybackController.instance()
        last_key = list(view.speech_button_manager.buttons.keys())[-1]
        ctrl.mark_generating("+OA-VIS-01", last_key, queued_count=0)
        qapp.processEvents()
        btn_ref = view.speech_button_manager.buttons[last_key]
        assert btn_ref.isVisible() is True
        assert btn_ref._pulse_timer.isActive() is True

        view.cleanup()
        qapp.processEvents()
        assert btn_ref._pulse_timer.isActive() is False
        assert view.speech_button_manager.buttons == {}
        assert view.ui_speech_playback_state_changed.emit not in ctrl.playback_state_changed._callbacks
    finally:
        view.cleanup()
        view.close()
        qapp.processEvents()


def test_piper_stream_caches_complete_synthesis_and_discards_mid_abort(speech_env):
    agent_id = "+OA-PIPER-CACHE"
    cache_ok = get_speech_cache_path(agent_id, "msg_201")
    cache_abort = get_speech_cache_path(agent_id, "msg_202")
    pcm_chunk = _synthetic_pcm(duration_sec=0.15, sample_rate=22050)

    fake_voice = MagicMock()
    fake_voice.config.sample_rate = 22050
    fake_voice.synthesize.return_value = [
        SimpleNamespace(audio_int16_bytes=pcm_chunk),
        SimpleNamespace(audio_int16_bytes=pcm_chunk),
    ]

    mock_ffplay = MagicMock()
    mock_ffplay.stdin = MagicMock()
    mock_ffplay.wait.return_value = 0

    real_popen = TTSWorker._play_cached_opus_file.__globals__["subprocess"].Popen

    def selective_popen(cmd, *args, **kwargs):
        if cmd and cmd[0] == "ffplay":
            return mock_ffplay
        return real_popen(cmd, *args, **kwargs)

    w_ok = TTSWorker("Piper complete", agent_id=agent_id, cache_path=cache_ok)
    with patch("core.audio_output.load_piper_voice", return_value=fake_voice), \
         patch("core.audio_output.subprocess.Popen", side_effect=selective_popen):
        w_ok._stream_and_play_piper()

    assert os.path.isfile(cache_ok)
    assert os.path.getsize(cache_ok) > 0

    w_abort = TTSWorker("Piper aborted", agent_id=agent_id, cache_path=cache_abort)

    def abort_chunks(_txt):
        yield SimpleNamespace(audio_int16_bytes=pcm_chunk)
        w_abort.stop()
        yield SimpleNamespace(audio_int16_bytes=pcm_chunk)

    fake_voice.synthesize.side_effect = abort_chunks
    with patch("core.audio_output.load_piper_voice", return_value=fake_voice), \
         patch("core.audio_output.subprocess.Popen", side_effect=selective_popen):
        w_abort._stream_and_play_piper()

    assert not os.path.exists(cache_abort)


def test_overlapping_multi_agent_speech_handoff_and_error_clears_stop_state(speech_env):
    """Regression: Agent B generating while Agent A plays must hand off cleanly and never leave Stop stuck."""
    orch_a = _make_orchestrator("+OA-OVL-A")
    orch_b = _make_orchestrator("+OA-OVL-B")
    ctrl = SpeechPlaybackController.instance()

    class StubWorker:
        def __init__(self, card_key, playing=False):
            self.card_key = card_key
            self._is_stopped = False
            self._alive = True
            self._playback_started_emitted = playing

        def is_alive(self):
            return self._alive

        def stop(self):
            self._is_stopped = True
            self._alive = False

    w_a = StubWorker("msg_1", playing=True)
    w_b = StubWorker("msg_2", playing=False)

    orch_a.tts_worker = w_a
    orch_a._active_speech_card_key = "msg_1"
    ctrl.mark_playing(orch_a.agent_id, "msg_1", worker=w_a)

    # Agent B starts generating while Agent A is still playing
    orch_b.tts_worker = w_b
    orch_b._active_speech_card_key = "msg_2"
    ctrl.mark_generating(orch_b.agent_id, "msg_2", worker=w_b)
    assert ctrl.get_state()["agent_id"] == orch_a.agent_id

    # Agent A finishes -> controller hands off active tracking to live Agent B
    w_a._alive = False
    orch_a.tts_worker = None
    orch_a._active_speech_card_key = None
    ctrl.mark_idle(worker=w_a)
    state_after_a = ctrl.get_state()
    assert state_after_a["is_active"] is True
    assert state_after_a["agent_id"] == orch_b.agent_id
    assert state_after_a["card_key"] == "msg_2"
    assert state_after_a["phase"] == "generating"

    # Agent B fails before ever emitting started_playback -> controller must return to idle
    w_b._alive = False
    orch_b.tts_worker = None
    orch_b._active_speech_card_key = None
    ctrl.mark_idle(worker=w_b)
    final_state = ctrl.get_state()
    assert final_state["is_active"] is False
    assert final_state["phase"] == "idle"
    assert ctrl.is_any_speech_active() is False


def test_agent_view_cleanup_disconnects_global_controller_on_earlier_error(qapp, speech_env):
    """Regression: an exception disconnecting an orchestrator signal must not leak SpeechPlaybackController slot."""
    orch = _make_orchestrator("+OA-CLEANUP-ERR")
    view = AgentView(orch)
    ctrl = SpeechPlaybackController.instance()
    try:
        assert view.ui_speech_playback_state_changed.emit in ctrl.playback_state_changed._callbacks

        broken_sig = CoreSignal()
        broken_sig.disconnect = MagicMock(side_effect=RuntimeError("boom"))
        orch.on_message_record_appended = broken_sig

        view.cleanup()
        assert view.ui_speech_playback_state_changed.emit not in ctrl.playback_state_changed._callbacks
    finally:
        view.close()
        qapp.processEvents()
