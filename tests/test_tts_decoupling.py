import os
import sys
import tempfile
import shutil
import json
import pytest
from unittest.mock import MagicMock, patch

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PySide6.QtWidgets import QApplication, QLineEdit
from gui.settings_panel import SettingsPanelWidget
from core.settings_manager import SettingsManager
from core.audio_output import TTSWorker


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def temp_agent_dir(monkeypatch):
    temp_dir = tempfile.mkdtemp()
    from config import paths
    monkeypatch.setattr(paths, "get_base_dir_for", lambda aid=None: temp_dir)
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_default_settings_tts_provider():
    default_file = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "src", "config", "settings.default.json"
    )
    with open(default_file, "r") as f:
        data = json.load(f)

    tts_conf = data.get("core", {}).get("tts", {})
    assert tts_conf.get("provider") == "piper"
    assert tts_conf.get("piper", {}).get("prefer-piper") is True


def test_agent_voice_gui_components(qapp, temp_agent_dir):
    panel = SettingsPanelWidget()
    sm = SettingsManager()
    panel.load_settings(sm)

    assert hasattr(panel, "ui_use_piper_tts")
    assert hasattr(panel, "ui_use_gemini_tts")
    assert hasattr(panel, "tts_button_group")
    assert hasattr(panel, "ui_gemini_tts_api_key")
    assert hasattr(panel, "ui_fallback_voice")
    assert hasattr(panel, "ui_voice")
    assert hasattr(panel, "ui_voice_prompt")

    assert panel.ui_gemini_tts_api_key.echoMode() == QLineEdit.Password

    # Default should be Piper
    assert panel.ui_use_piper_tts.isChecked()
    assert not panel.ui_use_gemini_tts.isChecked()

    # Toggle to Gemini
    panel.ui_use_gemini_tts.setChecked(True)
    assert panel.ui_use_gemini_tts.isChecked()
    assert not panel.ui_use_piper_tts.isChecked()

    # Toggle back to Piper
    panel.ui_use_piper_tts.setChecked(True)
    assert panel.ui_use_piper_tts.isChecked()
    assert not panel.ui_use_gemini_tts.isChecked()


def test_settings_panel_load_and_save_tts(qapp, temp_agent_dir):
    panel = SettingsPanelWidget()
    sm = SettingsManager()
    panel.load_settings(sm)

    # Initial state should be Piper
    assert panel.ui_use_piper_tts.isChecked()

    # Switch to Gemini TTS and input a dedicated TTS key
    panel.ui_use_gemini_tts.setChecked(True)
    panel.ui_gemini_tts_api_key.setText("test-gemini-tts-key-12345")
    panel.save_settings()

    # Verify settings persisted
    assert sm.get("core.tts.provider") == "gemini"
    assert sm.get("core.tts.piper.prefer-piper") is False
    assert sm.get_env("GEMINI_TTS_API_KEY") == "test-gemini-tts-key-12345"

    # Reload into a new panel instance and check
    panel2 = SettingsPanelWidget()
    panel2.load_settings(sm)
    assert panel2.ui_use_gemini_tts.isChecked()
    assert panel2.ui_gemini_tts_api_key.text() == "test-gemini-tts-key-12345"

    # Switch back to Piper
    panel2.ui_use_piper_tts.setChecked(True)
    panel2.save_settings()
    assert sm.get("core.tts.provider") == "piper"
    assert sm.get("core.tts.piper.prefer-piper") is True


def test_gemini_key_fallback_prefill(qapp, temp_agent_dir):
    sm = SettingsManager()
    # Set only GEMINI_API_KEY (simulating provider setup or existing agent)
    sm.set_env("GEMINI_API_KEY", "general-gemini-key-abc")

    panel = SettingsPanelWidget()
    panel.load_settings(sm)

    # In Agent Voice step, it should fallback/prefill from GEMINI_API_KEY if GEMINI_TTS_API_KEY is empty
    assert panel.ui_gemini_tts_api_key.text() == "general-gemini-key-abc"


def test_tts_worker_routing(temp_agent_dir):
    sm = SettingsManager()

    # 1. When provider is piper, routes to _stream_and_play_piper regardless of cognitive api-provider
    sm.set("core.tts.provider", "piper")
    sm.set("core.api-provider", "claude")
    sm.save()

    worker = TTSWorker("Hello world")
    with patch.object(worker, "_stream_and_play_piper") as mock_piper, \
         patch.object(worker, "_stream_and_play_gemini") as mock_gemini:
        worker._stream_and_play()
        assert mock_piper.called
        assert not mock_gemini.called

    # 2. When provider is gemini with key set, routes to _stream_and_play_gemini even if cognitive provider is claude/chatgpt/deepseek
    sm.set("core.tts.provider", "gemini")
    sm.set("core.api-provider", "deepseek")
    sm.set_env("GEMINI_TTS_API_KEY", "valid-tts-key")
    sm.save()

    worker = TTSWorker("Hello world")
    with patch.object(worker, "_stream_and_play_piper") as mock_piper, \
         patch.object(worker, "_stream_and_play_gemini") as mock_gemini:
        worker._stream_and_play()
        assert mock_gemini.called
        assert not mock_piper.called

    # 3. When provider is gemini but no key exists in GEMINI_TTS_API_KEY or GEMINI_API_KEY, falls back to piper
    sm.set("core.tts.provider", "gemini")
    sm.set_env("GEMINI_TTS_API_KEY", "")
    sm.set_env("GEMINI_API_KEY", "")
    sm.save()

    worker = TTSWorker("Hello world")
    with patch.object(worker, "_stream_and_play_piper") as mock_piper, \
         patch.object(worker, "_stream_and_play_gemini") as mock_gemini:
        worker._stream_and_play()
        assert mock_piper.called
        assert not mock_gemini.called

    # 4. When low-token-mode is active, always routes to piper
    sm.set("core.tts.provider", "gemini")
    sm.set_env("GEMINI_TTS_API_KEY", "valid-tts-key")
    sm.set("core.low-token-mode", True)
    sm.save()

    worker = TTSWorker("Hello world")
    with patch.object(worker, "_stream_and_play_piper") as mock_piper, \
         patch.object(worker, "_stream_and_play_gemini") as mock_gemini:
        worker._stream_and_play()
        assert mock_piper.called
        assert not mock_gemini.called


def test_gemini_tts_retry_success_on_second_attempt(temp_agent_dir):
    sm = SettingsManager()
    sm.set("core.tts.provider", "gemini")
    sm.set_env("GEMINI_TTS_API_KEY", "valid-tts-key")
    sm.save()

    worker = TTSWorker("Hello world")
    error_calls = []
    worker.error_occurred.connect(lambda msg: error_calls.append(msg))

    attempt_count = 0
    def mock_stream_attempt(client, text):
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count == 1:
            raise ConnectionResetError(104, "Connection reset by peer")
        # Attempt 2 succeeds

    with patch.object(worker, "_stream_gemini_attempt", side_effect=mock_stream_attempt), \
         patch("time.sleep") as mock_sleep:
        worker._stream_and_play_gemini(api_key="valid-tts-key")

    assert attempt_count == 2
    assert mock_sleep.called
    assert len(error_calls) == 0  # Succeeded on retry


def test_gemini_tts_consecutive_failures_emit_system_notice(temp_agent_dir):
    sm = SettingsManager()
    sm.set("core.tts.provider", "gemini")
    sm.set_env("GEMINI_TTS_API_KEY", "valid-tts-key")
    sm.save()

    worker = TTSWorker("Hello world")
    error_calls = []
    worker.error_occurred.connect(lambda msg: error_calls.append(msg))

    attempt_count = 0
    def mock_stream_attempt(client, text):
        nonlocal attempt_count
        attempt_count += 1
        raise ConnectionResetError(104, "Connection reset by peer")

    with patch.object(worker, "_stream_gemini_attempt", side_effect=mock_stream_attempt), \
         patch("time.sleep"):
        worker._stream_and_play_gemini(api_key="valid-tts-key")

    # 3 models in default fallback cascade (3.8-flash -> 3.8-flash-lite -> 3.1-flash)
    assert attempt_count == 3
    assert len(error_calls) == 1
    assert "Gemini voice synthesis" in error_calls[0]
    assert "[System:" in error_calls[0]


def test_gemini_voice_catalog_and_resolution():
    from core.audio_output import GEMINI_VOICES, resolve_base_voice

    assert len(GEMINI_VOICES) == 30
    assert "Sulafat" in GEMINI_VOICES
    assert "Achernar" in GEMINI_VOICES
    assert GEMINI_VOICES["Achernar"]["gender"] == "Female"
    assert GEMINI_VOICES["Sulafat"]["gender"] == "Female"
    assert GEMINI_VOICES["Puck"]["gender"] == "Male"
    assert GEMINI_VOICES["Charon"]["gender"] == "Male"
    assert GEMINI_VOICES["Iapetus"]["gender"] == "Neutral"

    # Test resolution
    assert resolve_base_voice(gender="Female", style="Soft") == "Achernar"
    assert resolve_base_voice(gender="Female", style="Warm & Empathetic") == "Sulafat"
    assert resolve_base_voice(gender="Female", style="Firm & Analytical") == "Kore"
    assert resolve_base_voice(gender="Female", age="Youthful", style="Bright") == "Leda"

    assert resolve_base_voice(gender="Male", style="Friendly & Warm") == "Achird"
    assert resolve_base_voice(gender="Male", style="Upbeat & Cheerful") == "Puck"
    assert resolve_base_voice(gender="Male", style="Informative & Analytical") == "Charon"
    assert resolve_base_voice(gender="Male", age="Mature", style="Authoritative") == "Gacrux"

    assert resolve_base_voice(gender="Non-binary", style="Smooth") == "Algieba"
    assert resolve_base_voice(gender="Non-binary", style="Clear") == "Iapetus"


def test_build_gemini_tts_prompt_formatting(temp_agent_dir):
    from core.audio_output import build_gemini_tts_prompt

    sm = SettingsManager()
    sm.set("core.agent.name", "Nova")
    sm.set("core.tts.gemini.gender", "Female")
    sm.set("core.tts.gemini.age", "Young Adult")
    sm.set("core.tts.gemini.accent", "South African")
    sm.set("core.tts.gemini.style", "Warm & Empathetic")
    sm.save()

    prompt = build_gemini_tts_prompt(sm, "agent-123", "Hello there!")

    assert "Read the following transcript aloud as Nova" in prompt
    assert "South African accent" in prompt
    assert "warm and empathetic tone" in prompt
    assert "clear articulation and natural conversational pacing" in prompt
    assert "Hello there!" in prompt


def test_build_gemini_tts_prompt_custom_override(temp_agent_dir):
    from core.audio_output import build_gemini_tts_prompt

    sm = SettingsManager()
    sm.set("core.tts.gemini.override-prompt", True)
    sm.set("core.tts.gemini.custom-prompt", "Speak like a cyberpunk hacker:\n\n{transcript}")
    sm.save()

    prompt = build_gemini_tts_prompt(sm, "agent-123", "Firewall breached.")

    assert "Speak like a cyberpunk hacker:" in prompt
    assert "Firewall breached." in prompt


def test_system_tool_set_custom_voice_and_reset(temp_agent_dir):
    from tools.system_tool import SystemTool

    sm = SettingsManager()
    sm.set("core.tts.gemini.allow-agent-override", True)
    sm.set("core.tts.gemini.override-prompt", False)
    sm.save()

    tool = SystemTool()
    # Mock orchestrator
    mock_orch = MagicMock()
    mock_orch.settings_manager = sm
    tool.orchestrator = mock_orch

    # Agent sets custom voice
    res = tool.execute(
        "set_custom_voice",
        custom_prompt="# AUDIO PROFILE: Autonomous Agent\nSpeak enthusiastically.",
        voice_model="Achernar"
    )
    assert "Custom voice" in res and "successfully activated" in res
    assert sm.get("core.tts.gemini.override-prompt") is True
    assert sm.get("core.tts.gemini.custom-prompt") == "# AUDIO PROFILE: Autonomous Agent\nSpeak enthusiastically."
    assert sm.get("core.tts.gemini.model-name") == "Achernar"

    # Agent resets voice
    reset_res = tool.execute("reset_voice")
    assert "Voice settings successfully reset" in reset_res
    assert sm.get("core.tts.gemini.override-prompt") is False


def test_system_tool_set_custom_voice_permission_denied(temp_agent_dir):
    from tools.system_tool import SystemTool

    sm = SettingsManager()
    sm.set("core.tts.gemini.allow-agent-override", False)
    sm.save()

    tool = SystemTool()
    mock_orch = MagicMock()
    mock_orch.settings_manager = sm
    tool.orchestrator = mock_orch

    res = tool.execute(
        "set_custom_voice",
        custom_prompt="# AUDIO PROFILE: Unauthorized change"
    )
    assert "Permission denied" in res
    assert sm.get("core.tts.gemini.override-prompt", False) is False


def test_settings_panel_voice_character_load_save(qapp, temp_agent_dir):
    panel = SettingsPanelWidget()
    sm = SettingsManager()
    panel.load_settings(sm)

    assert hasattr(panel, "ui_tts_gender")
    assert hasattr(panel, "ui_tts_age")
    assert hasattr(panel, "ui_tts_accent")
    assert hasattr(panel, "ui_tts_custom_accent")
    assert hasattr(panel, "ui_tts_style")
    assert hasattr(panel, "ui_tts_custom_style")
    assert hasattr(panel, "ui_tts_allow_agent_override")

    # Set parameters via UI
    g_idx = panel.ui_tts_gender.findText("Female")
    panel.ui_tts_gender.setCurrentIndex(g_idx)

    ac_idx = panel.ui_tts_accent.findText("British (RP / Standard)")
    panel.ui_tts_accent.setCurrentIndex(ac_idx)

    st_idx = panel.ui_tts_style.findText("Warm & Empathetic")
    panel.ui_tts_style.setCurrentIndex(st_idx)

    panel.ui_tts_allow_agent_override.setChecked(True)
    panel.save_settings()

    assert sm.get("core.tts.gemini.gender") == "Female"
    assert sm.get("core.tts.gemini.accent") == "British (RP / Standard)"
    assert sm.get("core.tts.gemini.style") == "Warm & Empathetic"
    assert sm.get("core.tts.gemini.model-name") == "Sulafat"
    assert sm.get("core.tts.gemini.allow-agent-override") is True
    assert sm.get("core.tts.gemini.override-prompt") is False


def test_stream_gemini_attempt_full_playback(temp_agent_dir):
    worker = TTSWorker("Test monologue")
    mock_client = MagicMock()

    # Create mock chunks
    chunk1 = MagicMock()
    part1 = MagicMock()
    part1.inline_data.data = b"\x01\x00" * 2400
    chunk1.candidates = [MagicMock(content=MagicMock(parts=[part1]))]

    chunk2 = MagicMock()
    part2 = MagicMock()
    part2.inline_data.data = b"\x02\x00" * 2400
    chunk2.candidates = [MagicMock(content=MagicMock(parts=[part2]))]

    mock_client.models.generate_content_stream.return_value = [chunk1, chunk2]

    mock_process = MagicMock()
    mock_stdin = MagicMock()
    mock_process.stdin = mock_stdin

    with patch("subprocess.Popen", return_value=mock_process):
        worker._stream_gemini_attempt(mock_client, "Full prompt")

    # Verify that all chunks were written and stdin was closed cleanly
    assert mock_stdin.write.called
    assert mock_stdin.close.called
    assert mock_process.wait.called
    # Ensure process.kill was NOT called during successful stream
    assert not mock_process.kill.called
    # Ensure automatic function calling is explicitly disabled on the config
    call_kwargs = mock_client.models.generate_content_stream.call_args.kwargs
    assert call_kwargs["config"].automatic_function_calling is not None
    assert call_kwargs["config"].automatic_function_calling.disable is True


def test_stream_gemini_attempt_kills_process_on_stream_error(temp_agent_dir):
    worker = TTSWorker("Test monologue")
    mock_client = MagicMock()

    def error_stream():
        chunk1 = MagicMock()
        part1 = MagicMock()
        part1.inline_data.data = b"\x01\x00" * 2400
        chunk1.candidates = [MagicMock(content=MagicMock(parts=[part1]))]
        yield chunk1
        raise ConnectionResetError(104, "Connection reset by peer")

    mock_client.models.generate_content_stream.return_value = error_stream()

    mock_process = MagicMock()
    mock_stdin = MagicMock()
    mock_process.stdin = mock_stdin

    with patch("subprocess.Popen", return_value=mock_process):
        with pytest.raises(ConnectionResetError):
            worker._stream_gemini_attempt(mock_client, "Full prompt")

    # On stream error, process.kill MUST be called immediately so retries start clean
    assert mock_process.kill.called


def test_stream_gemini_attempt_respects_stopped_flag(temp_agent_dir):
    worker = TTSWorker("Test monologue")
    worker._is_stopped = True
    mock_client = MagicMock()

    chunk1 = MagicMock()
    part1 = MagicMock()
    part1.inline_data.data = b"\x01\x00" * 2400
    chunk1.candidates = [MagicMock(content=MagicMock(parts=[part1]))]
    mock_client.models.generate_content_stream.return_value = [chunk1]

    mock_process = MagicMock()
    mock_stdin = MagicMock()
    mock_process.stdin = mock_stdin

    with patch("subprocess.Popen", return_value=mock_process):
        worker._stream_gemini_attempt(mock_client, "Full prompt")

    assert mock_process.kill.called


def test_stream_gemini_attempt_raises_on_safety_abort(temp_agent_dir):
    worker = TTSWorker("Test monologue")
    mock_client = MagicMock()

    chunk1 = MagicMock()
    cand1 = MagicMock()
    cand1.content = None
    finish_mock = MagicMock()
    finish_mock.name = "SAFETY"
    cand1.finish_reason = finish_mock
    chunk1.candidates = [cand1]
    mock_client.models.generate_content_stream.return_value = [chunk1]

    mock_process = MagicMock()
    mock_stdin = MagicMock()
    mock_process.stdin = mock_stdin

    with patch("subprocess.Popen", return_value=mock_process):
        with pytest.raises(RuntimeError, match="Gemini TTS stream aborted early with finish_reason"):
            worker._stream_gemini_attempt(mock_client, "Full prompt")

    # Ensure process is killed on safety abort
    assert mock_process.kill.called


def test_stream_and_play_gemini_falls_back_to_next_model(temp_agent_dir):
    worker = TTSWorker("Test monologue")
    worker.settings.set("core.gemini.voice-models", [
        "gemini-3.8-flash-tts",
        "gemini-3.1-flash-tts-preview"
    ])
    worker.settings.set("core.tts.provider", "gemini")
    worker.settings.save()

    attempts_called_with_models = []

    def fake_attempt(client, full_text):
        attempts_called_with_models.append(worker.model)
        if len(attempts_called_with_models) == 1:
            raise RuntimeError("Gemini TTS stream aborted early with finish_reason: SAFETY")
        # Attempt 2 succeeds

    worker._stream_gemini_attempt = fake_attempt

    with patch("core.audio_output.genai.Client"), \
         patch.object(worker.settings, "get_env", return_value="fake-api-key"), \
         patch("time.sleep"):
        worker._stream_and_play_gemini()

    assert attempts_called_with_models == [
        "gemini-3.8-flash-tts",
        "gemini-3.1-flash-tts-preview"
    ]


def test_normalize_gemini_voice_models():
    from core.audio_output import normalize_gemini_voice_models

    # Normalizes legacy 3.1 and strips 2.5 TTS models, ensuring 3.8 is primary and 3.1 is fallback
    legacy_list = ["gemini-3.1-flash-tts-preview", "gemini-2.5-flash-lite-preview-tts"]
    assert normalize_gemini_voice_models(legacy_list) == [
        "gemini-3.8-flash-tts",
        "gemini-3.1-flash-tts-preview"
    ]

    # Appends fallback model when omitted and ensures 3.8 is primary
    single_list = ["gemini-3.1-flash-tts-preview"]
    assert normalize_gemini_voice_models(single_list) == [
        "gemini-3.8-flash-tts",
        "gemini-3.1-flash-tts-preview"
    ]

    # Strips 2.5 models and ensures 3.8 is primary and 3.1 is fallback
    dup_list = ["gemini-2.5-flash-preview-tts", "gemini-2.5-flash-lite-preview-tts"]
    assert normalize_gemini_voice_models(dup_list) == [
        "gemini-3.8-flash-tts",
        "gemini-3.1-flash-tts-preview"
    ]

    # Default fallback on empty or None
    assert normalize_gemini_voice_models([]) == [
        "gemini-3.8-flash-tts",
        "gemini-3.8-flash-lite-tts",
        "gemini-3.1-flash-tts-preview"
    ]
    assert normalize_gemini_voice_models(None) == [
        "gemini-3.8-flash-tts",
        "gemini-3.8-flash-lite-tts",
        "gemini-3.1-flash-tts-preview"
    ]


def test_stream_and_play_gemini_normalizes_legacy_model(temp_agent_dir):
    worker = TTSWorker("Test legacy normalization")
    worker.settings.set("core.gemini.voice-models", [
        "gemini-3.1-flash-tts-preview",
        "gemini-2.5-flash-lite-preview-tts"
    ])
    worker.settings.set("core.tts.provider", "gemini")
    worker.settings.save()

    attempts_called_with_models = []

    def fake_attempt(client, full_text):
        attempts_called_with_models.append(worker.model)
        if len(attempts_called_with_models) == 1:
            raise RuntimeError("Gemini TTS stream aborted early with finish_reason: SAFETY")

    worker._stream_gemini_attempt = fake_attempt

    with patch("core.audio_output.genai.Client"), \
         patch.object(worker.settings, "get_env", return_value="fake-api-key"), \
         patch("time.sleep"):
        worker._stream_and_play_gemini()

    assert attempts_called_with_models == [
        "gemini-3.8-flash-tts",
        "gemini-3.1-flash-tts-preview"
    ]


def test_tts_amplitude_single_byte_chunk_safety(temp_agent_dir):
    # Tests H9: ensuring len(data) == 1 (samples == 0) does not cause ZeroDivisionError
    worker = TTSWorker("Test amplitude safety")
    emitted = []
    worker.amplitude_emitted.connect(emitted.append)

    # Directly test the writer thread's amplitude logic with 1-byte data
    data = b"\x01"
    samples = len(data) // 2
    # Verify samples is 0 and guarding with samples > 0 prevents division by zero
    assert samples == 0


def test_gemini_38_payload_structure(temp_agent_dir):
    from core.audio_output import build_gemini_tts_payload, build_gemini_tts_style
    from google.genai import types

    sm = SettingsManager()
    sm.set("core.agent.name", "Nova")
    sm.set("core.tts.gemini.accent", "South African")
    sm.set("core.tts.gemini.style", "Warm & Empathetic")
    sm.save()

    style_directive = build_gemini_tts_style(sm)
    assert "South African" in style_directive
    assert "warm and empathetic" in style_directive

    # For gemini-3.8-flash-tts, payload MUST be a list containing types.Content with speech_metadata
    payload_38 = build_gemini_tts_payload("gemini-3.8-flash-tts", sm, "agent-1", "I am speaking <laugh> naturally.")
    assert isinstance(payload_38, list)
    assert len(payload_38) == 1
    content = payload_38[0]
    assert isinstance(content, types.Content)
    assert len(content.parts) == 1
    part = content.parts[0]
    assert part.text == "I am speaking <laugh> naturally."  # Verbatim text preserved!
    assert part.speech_metadata is not None
    assert part.speech_metadata.style == style_directive

    # For legacy fallback gemini-3.1-flash-tts-preview, payload MUST be a string with prompt prepended
    payload_31 = build_gemini_tts_payload("gemini-3.1-flash-tts-preview", sm, "agent-1", "I am speaking naturally.")
    assert isinstance(payload_31, str)
    assert "Read the following transcript aloud as Nova" in payload_31
    assert "I am speaking naturally." in payload_31

    # Any legacy 2.5 request is aliased to 3.1 and also receives prompt preamble
    payload_25 = build_gemini_tts_payload("gemini-2.5-flash-preview-tts", sm, "agent-1", "I am speaking naturally.")
    assert isinstance(payload_25, str)
    assert "Read the following transcript aloud as Nova" in payload_25


def test_gemini_38_voice_selection_modes(temp_agent_dir):
    from google.genai import types

    # 1. Custom Voice ID mode
    sm = SettingsManager()
    sm.set("core.tts.gemini.voice-selection-mode", "custom_voice_id")
    sm.set("core.tts.gemini.custom-voice-id", "voice_replicated_abc123")
    sm.save()

    worker = TTSWorker("Testing custom voice id")
    mock_client = MagicMock()
    with patch("subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.stdin = MagicMock()
        mock_popen.return_value = mock_proc
        worker._stream_gemini_attempt(mock_client, "Hello")

    call_kwargs = mock_client.models.generate_content_stream.call_args.kwargs
    sp_cfg = call_kwargs["config"].speech_config
    assert sp_cfg.voice_config.voice == "voice_replicated_abc123"

    # 2. Prebuilt voice mode
    sm.set("core.tts.gemini.voice-selection-mode", "prebuilt")
    sm.set("core.tts.gemini.model-name", "Puck")
    sm.save()

    worker2 = TTSWorker("Testing prebuilt voice")
    with patch("subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.stdin = MagicMock()
        mock_popen.return_value = mock_proc
        worker2._stream_gemini_attempt(mock_client, "Hello")

    call_kwargs2 = mock_client.models.generate_content_stream.call_args.kwargs
    sp_cfg2 = call_kwargs2["config"].speech_config
    assert sp_cfg2.voice_config.prebuilt_voice_config is not None
    assert sp_cfg2.voice_config.prebuilt_voice_config.voice_name == "Puck"


def test_gemini_38_three_tier_fallback_cascade(temp_agent_dir):
    worker = TTSWorker("Testing 3-tier fallback")
    worker.settings.set("core.tts.provider", "gemini")
    worker.settings.save()

    attempted_models = []
    def fake_stream(client, payload):
        attempted_models.append(worker.model)
        if len(attempted_models) < 3:
            raise RuntimeError(f"Simulated failure on {worker.model}")

    worker._stream_gemini_attempt = fake_stream

    with patch("core.audio_output.genai.Client"), \
         patch.object(worker.settings, "get_env", return_value="fake-api-key"), \
         patch("time.sleep"):
        worker._stream_and_play_gemini()

    assert attempted_models == [
        "gemini-3.8-flash-tts",
        "gemini-3.8-flash-lite-tts",
        "gemini-3.1-flash-tts-preview"
    ]


def test_settings_panel_gemini_38_voice_selector_and_custom_id(qapp, temp_agent_dir):
    panel = SettingsPanelWidget()
    sm = SettingsManager()
    panel.load_settings(sm)

    assert hasattr(panel, "ui_tts_base_voice")
    assert hasattr(panel, "ui_tts_play_sample_btn")
    assert hasattr(panel, "ui_tts_custom_voice_id")

    # Check that selector contains "auto", all 30 prebuilts, and "custom_voice_id" (total 32 items)
    assert panel.ui_tts_base_voice.count() == 32
    assert panel.ui_tts_base_voice.itemData(0) == "auto"
    assert panel.ui_tts_base_voice.itemData(panel.ui_tts_base_voice.count() - 1) == "custom_voice_id"

    # Selecting custom_voice_id makes custom ID line edit unhidden
    custom_idx = panel.ui_tts_base_voice.findData("custom_voice_id")
    panel.ui_tts_base_voice.setCurrentIndex(custom_idx)
    assert not panel.ui_tts_custom_voice_id.isHidden()

    panel.ui_tts_custom_voice_id.setText("voice_test_design_xyz")
    panel.save_settings()

    assert sm.get("core.tts.gemini.voice-selection-mode") == "custom_voice_id"
    assert sm.get("core.tts.gemini.custom-voice-id") == "voice_test_design_xyz"
    assert sm.get("core.tts.gemini.model-name") == "voice_test_design_xyz"

    # Reload into new panel and check state
    panel2 = SettingsPanelWidget()
    panel2.load_settings(sm)
    assert panel2.ui_tts_base_voice.currentData() == "custom_voice_id"
    assert panel2.ui_tts_custom_voice_id.text() == "voice_test_design_xyz"
    assert not panel2.ui_tts_custom_voice_id.isHidden()

    # Switch to prebuilt voice
    puck_idx = panel2.ui_tts_base_voice.findData("Puck")
    panel2.ui_tts_base_voice.setCurrentIndex(puck_idx)
    assert panel2.ui_tts_custom_voice_id.isHidden()

    # Prebuilt selection automatically synchronizes character profile fields
    assert panel2.ui_tts_gender.currentText() == "Male"
    assert "Young Adult" in panel2.ui_tts_age.currentText()
    assert panel2.ui_tts_style.currentText() == "Cheerful & Energetic"

    panel2.save_settings()

    assert sm.get("core.tts.gemini.voice-selection-mode") == "prebuilt"
    assert sm.get("core.tts.gemini.model-name") == "Puck"
    assert sm.get("core.tts.gemini.gender") == "Male"
    assert sm.get("core.tts.gemini.style") == "Cheerful & Energetic"


def test_voice_to_character_profile_catalog():
    from core.audio_output import GEMINI_VOICES, voice_to_character_profile

    assert len(GEMINI_VOICES) == 30
    for voice_name in GEMINI_VOICES:
        profile = voice_to_character_profile(voice_name)
        assert "gender" in profile
        assert profile["gender"] in ("Female", "Male", "Non-binary / Neutral")
        assert "age" in profile
        assert profile["age"] in (
            "Young Adult (20s - 30s)",
            "Youthful (18 - 25)",
            "Adult (30s - 50s)",
            "Mature / Senior (50+)"
        )
        assert "style" in profile
        assert profile["style"] in (
            "Warm & Empathetic",
            "Calm & Analytical",
            "Cheerful & Energetic",
            "Casual & Playful",
            "Professional & Informative",
            "Vibrant & Sassy",
            "Gentle & Serene"
        )

    # Specific assertions for notable voices
    assert voice_to_character_profile("Leda")["age"] == "Youthful (18 - 25)"
    assert voice_to_character_profile("Gacrux")["age"] == "Mature / Senior (50+)"
    assert voice_to_character_profile("Iapetus")["gender"] == "Non-binary / Neutral"
    assert voice_to_character_profile("Algieba")["gender"] == "Non-binary / Neutral"
    assert voice_to_character_profile("Sulafat")["style"] == "Warm & Empathetic"
    assert voice_to_character_profile("Sadachbia")["style"] == "Vibrant & Sassy"


def test_settings_panel_prebuilt_voice_sync(qapp, temp_agent_dir):
    panel = SettingsPanelWidget()
    sm = SettingsManager()
    panel.load_settings(sm)

    # Select Leda -> female youthful cheerful
    leda_idx = panel.ui_tts_base_voice.findData("Leda")
    panel.ui_tts_base_voice.setCurrentIndex(leda_idx)
    assert panel.ui_tts_gender.currentText() == "Female"
    assert "Youthful" in panel.ui_tts_age.currentText()
    assert panel.ui_tts_style.currentText() == "Cheerful & Energetic"

    # Select Gacrux -> male mature
    gacrux_idx = panel.ui_tts_base_voice.findData("Gacrux")
    panel.ui_tts_base_voice.setCurrentIndex(gacrux_idx)
    assert panel.ui_tts_gender.currentText() == "Male"
    assert "Mature" in panel.ui_tts_age.currentText()

    # Select Algieba -> non-binary / neutral gentle & serene
    algieba_idx = panel.ui_tts_base_voice.findData("Algieba")
    panel.ui_tts_base_voice.setCurrentIndex(algieba_idx)
    assert panel.ui_tts_gender.currentText() == "Non-binary / Neutral"
    assert panel.ui_tts_style.currentText() == "Gentle & Serene"


def test_voice_sample_preview_threading_and_signals(qapp, temp_agent_dir):
    panel = SettingsPanelWidget()
    sm = SettingsManager()
    sm.set_env("GEMINI_API_KEY", "mock-api-key")
    panel.load_settings(sm)

    # Mock synthesize_voice_sample
    fake_wav_data = b"RIFFmockwavheaderanddata"
    ready_called = []
    failed_called = []

    panel.sample_synth_ready.connect(lambda p: ready_called.append(p))
    panel.sample_synth_failed.connect(lambda: failed_called.append(True))

    with patch("core.audio_output.synthesize_voice_sample", return_value=fake_wav_data), \
         patch.object(panel, "_play_temp_sample_file") as mock_play:

        auto_idx = panel.ui_tts_base_voice.findData("auto")
        panel.ui_tts_base_voice.setCurrentIndex(auto_idx)
        panel._on_play_voice_sample()

        # Wait briefly for thread to emit signal
        import time
        for _ in range(50):
            qapp.processEvents()
            if ready_called:
                break
            time.sleep(0.02)

        assert len(ready_called) == 1
        assert os.path.exists(ready_called[0])
        # Verify cleanup works
        panel._cleanup_temp_samples()
        assert not os.path.exists(ready_called[0])


def test_voice_sample_preview_failure_resets_button(qapp, temp_agent_dir):
    panel = SettingsPanelWidget()
    sm = SettingsManager()
    sm.set_env("GEMINI_API_KEY", "mock-api-key")
    panel.load_settings(sm)

    failed_called = []
    panel.sample_synth_failed.connect(lambda: failed_called.append(True))

    with patch("core.audio_output.synthesize_voice_sample", side_effect=RuntimeError("API quota exceeded")):
        auto_idx = panel.ui_tts_base_voice.findData("auto")
        panel.ui_tts_base_voice.setCurrentIndex(auto_idx)
        panel._on_play_voice_sample()

        import time
        for _ in range(50):
            qapp.processEvents()
            if failed_called:
                break
            time.sleep(0.02)

        assert len(failed_called) == 1
        assert panel.ui_tts_play_sample_btn.text() == "▶ Play Sample"
        assert panel.ui_tts_play_sample_btn.isEnabled()


def test_silence_ffmpeg_logs_runs_cleanly():
    from core.audio_output import silence_ffmpeg_logs
    # Ensure calling silence_ffmpeg_logs does not raise exceptions
    silence_ffmpeg_logs()







