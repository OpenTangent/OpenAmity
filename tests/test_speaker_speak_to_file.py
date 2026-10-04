import os
import sys
import json
import time
import shutil
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from tools.speaker_tool import SpeakerTool
from core.audio_output import synthesize_speech_to_file, load_piper_voice
from core.cache_manager import CacheManager
from core.backup_manager import EXCLUDED_DIR_NAMES, EXCLUDED_EXTERNAL_DIR_NAMES
from config import paths


def test_speaker_speak_to_file_declaration():
    tool = SpeakerTool()
    declarations = tool.get_tool_declarations()
    decl_map = {d["name"]: d for d in declarations}

    assert "Speaker_speak_to_file" in decl_map
    decl = decl_map["Speaker_speak_to_file"]
    assert "24 hours" in decl["description"]
    assert ".opus" in decl["description"]
    assert "xdg-open" in decl["description"]

    props = decl["parameters"]["properties"]
    assert "text" in props
    assert "filename" in props
    assert "text" in decl["parameters"]["required"]
    assert "filename" not in decl["parameters"]["required"]


def test_speaker_commands_list():
    tool = SpeakerTool()
    matching_cmds = [cmd for cmd in tool.commands if cmd.startswith("speak_to_file")]
    assert len(matching_cmds) == 1
    assert "24 hours" in matching_cmds[0]
    assert ".opus" in matching_cmds[0]


def test_paths_get_audio_cache_dir(tmp_path):
    with patch.dict(os.environ, {"XDG_DATA_HOME": str(tmp_path)}):
        agent_dir = paths.get_audio_cache_dir("test_agent_123")
        assert agent_dir == os.path.join(str(tmp_path), "agents", "test_agent_123", "audio_cache")

        shared_dir = paths.get_audio_cache_dir(None)
        assert shared_dir == os.path.join(str(tmp_path), "audio_cache")


def test_backup_excludes_audio_cache():
    assert "audio_cache" in EXCLUDED_DIR_NAMES
    assert "audio_cache" in EXCLUDED_EXTERNAL_DIR_NAMES


def test_speak_to_file_empty_text():
    tool = SpeakerTool()
    res = tool.execute("speak_to_file", text="")
    data = json.loads(res)
    assert data["status"] == "error"
    assert "required" in data["error"]

    res_spaces = tool.execute("speak_to_file", text="   ")
    data_spaces = json.loads(res_spaces)
    assert data_spaces["status"] == "error"


def test_speak_to_file_execution_and_24h_cleanup(tmp_path):
    mock_orch = MagicMock()
    mock_orch.agent_id = "test_agent_456"

    # Setup isolated audio cache directory
    audio_cache = tmp_path / "agents" / "test_agent_456" / "audio_cache"
    audio_cache.mkdir(parents=True, exist_ok=True)

    # Create a fresh file (mtime = now) and a stale file (mtime = 25h ago)
    fresh_file = audio_cache / "fresh.opus"
    fresh_file.write_bytes(b"fresh")

    stale_file = audio_cache / "stale.opus"
    stale_file.write_bytes(b"stale")
    stale_time = time.time() - (25 * 3600)
    os.utime(stale_file, (stale_time, stale_time))

    tool = SpeakerTool(orchestrator=mock_orch)

    with patch("tools.speaker_tool.get_audio_cache_dir", return_value=str(audio_cache)):
        with patch("tools.speaker_tool.synthesize_speech_to_file") as mock_synth:
            mock_synth.side_effect = lambda text, output_path, **kwargs: (
                Path(output_path).write_bytes(b"fake_opus_content") and output_path
                if hasattr(output_path, "write_bytes")
                else (open(output_path, "wb").write(b"fake_opus_content") and output_path)
            )

            res = tool.execute(
                "speak_to_file",
                transcript="Hello from Open Amity!",
                filename="../../my_voice_memo"
            )
            data = json.loads(res)

            assert data["status"] == "success"
            assert data["file_path"].endswith("my_voice_memo.opus")
            # Verify path traversal was sanitized (no ../../)
            assert os.path.dirname(data["file_path"]) == str(audio_cache)
            assert os.path.exists(data["file_path"])

            # Verify 24h cache cleanup: stale_file deleted, fresh_file kept
            assert not stale_file.exists()
            assert fresh_file.exists()


def test_synthesize_speech_to_file_validation():
    with pytest.raises(ValueError, match="Transcript text is required"):
        synthesize_speech_to_file("", "/tmp/dummy.opus")

    with pytest.raises(ValueError, match="Transcript text is required"):
        synthesize_speech_to_file("   ", "/tmp/dummy.opus")


def test_synthesize_speech_to_file_piper_offline(tmp_path):
    output_opus = tmp_path / "piper_test.opus"

    # Mock PiperVoice and synthesize
    mock_voice = MagicMock()
    mock_voice.config.sample_rate = 22050

    mock_chunk = MagicMock()
    # 0.5s of silence at 22050Hz (22050 * 2 bytes = 44100 bytes / 2)
    mock_chunk.audio_int16_bytes = b"\x00\x00" * 11025
    mock_voice.synthesize.return_value = [mock_chunk]

    mock_settings = MagicMock()
    mock_settings.get.side_effect = lambda key, default=None: {
        "core.low-token-mode": False,
        "core.tts.provider": "piper"
    }.get(key, default)

    with patch("core.audio_output.load_piper_voice", return_value=mock_voice):
        result_path = synthesize_speech_to_file(
            text="Hello world <laugh> test </laugh>",
            output_path=str(output_opus),
            settings=mock_settings
        )

        assert os.path.exists(result_path)
        assert os.path.getsize(result_path) > 0
        assert result_path.endswith(".opus")

        # Verify angle-bracket expressive tags were stripped for Piper
        mock_voice.synthesize.assert_called_once()
        synthesized_text = mock_voice.synthesize.call_args[0][0]
        assert "<laugh>" not in synthesized_text
        assert "</laugh>" not in synthesized_text
        assert "Hello world test" in synthesized_text


def test_synthesize_speech_to_file_gemini_success_and_fallback(tmp_path):
    output_opus = tmp_path / "gemini_test.opus"

    mock_settings = MagicMock()
    mock_settings.get.side_effect = lambda key, default=None: {
        "core.low-token-mode": False,
        "core.tts.provider": "gemini",
        "core.gemini.voice-models": ["gemini-3.8-flash-tts", "gemini-3.1-flash-tts-preview"]
    }.get(key, default)
    mock_settings.get_env.return_value = "fake_gemini_api_key"

    # Test 1: Gemini succeeds
    mock_part = MagicMock()
    mock_part.inline_data.data = b"\x00\x00" * 12000  # 0.5s at 24000Hz
    mock_candidate = MagicMock()
    mock_candidate.finish_reason = None
    mock_candidate.content.parts = [mock_part]
    mock_chunk = MagicMock()
    mock_chunk.candidates = [mock_candidate]

    mock_genai_client = MagicMock()
    mock_genai_client.models.generate_content_stream.return_value = [mock_chunk]

    with patch("google.genai.Client", return_value=mock_genai_client):
        result_path = synthesize_speech_to_file(
            text="Direct Gemini synthesis test",
            output_path=str(output_opus),
            settings=mock_settings
        )
        assert os.path.exists(result_path)
        assert os.path.getsize(result_path) > 0

    # Test 2: Gemini fails on all attempts, falls back to Piper
    output_fallback = tmp_path / "fallback_test.opus"
    mock_genai_client.models.generate_content_stream.side_effect = RuntimeError("API Quota Exceeded")

    mock_piper = MagicMock()
    mock_piper.config.sample_rate = 22050
    mock_piper_chunk = MagicMock()
    mock_piper_chunk.audio_int16_bytes = b"\x00\x00" * 11025
    mock_piper.synthesize.return_value = [mock_piper_chunk]

    with patch("google.genai.Client", return_value=mock_genai_client):
        with patch("core.audio_output.load_piper_voice", return_value=mock_piper):
            fallback_path = synthesize_speech_to_file(
                text="Fallback text",
                output_path=str(output_fallback),
                settings=mock_settings
            )
            assert os.path.exists(fallback_path)
            assert os.path.getsize(fallback_path) > 0
            mock_piper.synthesize.assert_called_once()
