import os
import sys
import json
import base64
from io import BytesIO
from unittest.mock import MagicMock, patch
import urllib.error
import pytest
from PIL import Image

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from tools.media_tool import MediaSkill


def create_sample_jpeg_bytes() -> bytes:
    img = Image.new("RGB", (20, 20), color="blue")
    buf = BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def media_tool_fixture(tmp_path):
    agent_dir = str(tmp_path / "agents" / "test_agent")
    os.makedirs(agent_dir, exist_ok=True)

    tool = MediaSkill()

    mock_sm = MagicMock()
    stored_env = {}
    stored_settings = {
        "core.low-token-mode": False,
        "core.media.image-provider": "",
        "core.media.gemini.model": "gemini-3.1-flash-image",
        "core.media.openrouter.model": "bytedance-seed/seedream-4.5"
    }

    mock_sm.get_env.side_effect = lambda k, d="": stored_env.get(k, d)
    mock_sm.get.side_effect = lambda k, d=None: stored_settings.get(k, d)

    mock_orch = MagicMock()
    mock_orch.agent_id = "test_agent"
    mock_orch.settings_manager = mock_sm
    tool.orchestrator = mock_orch

    media_dir = os.path.join(agent_dir, "generated_media")
    with patch("tools.media_tool.get_generated_media_dir", return_value=media_dir), \
         patch("tools.media_tool.get_agent_data_dir", return_value=agent_dir):
        yield tool, stored_env, stored_settings, agent_dir


def test_media_skill_metadata_and_declarations():
    tool = MediaSkill()
    assert tool.name == "Media"
    assert tool.commands == ["read", "generate", "transcribe_audio"]

    declarations = tool.get_tool_declarations()
    decl_names = [d["name"] for d in declarations]
    assert "Media_read" in decl_names
    assert "Media_generate" in decl_names
    assert "Media_transcribe_audio" in decl_names

    gen_decl = next(d for d in declarations if d["name"] == "Media_generate")
    assert "prompt" in gen_decl["parameters"]["properties"]
    assert gen_decl["parameters"]["required"] == ["prompt"]
    assert "24-hour cache" in gen_decl["description"]

    transcribe_decl = next(d for d in declarations if d["name"] == "Media_transcribe_audio")
    assert "file_path" in transcribe_decl["parameters"]["properties"]
    assert transcribe_decl["parameters"]["required"] == ["file_path"]


def test_media_generate_missing_prompt(media_tool_fixture):
    tool, _, _, _ = media_tool_fixture

    res1 = tool.execute("generate")
    assert res1 == {"result": "Error: Missing prompt parameter."}

    res2 = tool.execute("generate", prompt="")
    assert res2 == {"result": "Error: Missing prompt parameter."}


def test_media_generate_low_token_mode(media_tool_fixture):
    tool, _, stored_settings, _ = media_tool_fixture
    stored_settings["core.low-token-mode"] = True

    res = tool.execute("generate", prompt="A neon sunset")
    assert "Error: Media_generate is disabled when Low Token Mode is active." in res["result"]


def test_media_generate_no_provider_configured(media_tool_fixture):
    tool, _, stored_settings, _ = media_tool_fixture
    stored_settings["core.media.image-provider"] = ""

    res = tool.execute("generate", prompt="A cozy cabin in the woods")
    assert res["result"] == (
        "Error: An image generation provider has not been set up. Please select an image provider "
        "(Gemini or OpenRouter) and configure an API key in the Media Provider settings."
    )


def test_media_generate_unsupported_provider(media_tool_fixture):
    tool, _, stored_settings, _ = media_tool_fixture
    stored_settings["core.media.image-provider"] = "dall-e-3"

    res = tool.execute("generate", prompt="A mountain range")
    assert "Error: An image generation provider has not been set up (unsupported provider: dall-e-3)." in res["result"]


def test_media_generate_gemini_missing_api_key(media_tool_fixture):
    tool, stored_env, stored_settings, _ = media_tool_fixture
    stored_settings["core.media.image-provider"] = "gemini"
    stored_env.clear()

    res = tool.execute("generate", prompt="An astronaut riding a horse")
    assert res["result"] == (
        "Error: An image generation provider has not been set up (no API key configured for Gemini). "
        "Please add an API key in the Media Provider settings."
    )


def test_media_generate_openrouter_missing_api_key(media_tool_fixture):
    tool, stored_env, stored_settings, _ = media_tool_fixture
    stored_settings["core.media.image-provider"] = "openrouter"
    stored_env.clear()

    res = tool.execute("generate", prompt="A futuristic city")
    assert res["result"] == (
        "Error: An image generation provider has not been set up (no API key configured for OpenRouter). "
        "Please add an API key in the Media Provider settings."
    )


def test_media_generate_gemini_success(media_tool_fixture):
    tool, stored_env, stored_settings, agent_dir = media_tool_fixture
    stored_settings["core.media.image-provider"] = "gemini"
    stored_env["GEMINI_IMAGE_API_KEY"] = "gemini-img-key"

    sample_bytes = create_sample_jpeg_bytes()

    mock_client = MagicMock()
    mock_part = MagicMock()
    mock_part.inline_data.data = sample_bytes
    mock_candidate = MagicMock()
    mock_candidate.content.parts = [mock_part]
    mock_response = MagicMock()
    mock_response.candidates = [mock_candidate]
    mock_client.models.generate_content.return_value = mock_response

    with patch("tools.media_tool.genai.Client", return_value=mock_client) as mock_client_cls:
        res = tool.execute("generate", prompt="A blue ocean wave")

        mock_client_cls.assert_called_once_with(api_key="gemini-img-key")
        assert "Successfully generated and saved image to" in res["result"]
        assert len(res["media"]) == 1
        saved_path = res["media"][0]
        assert os.path.exists(saved_path)
        assert saved_path.startswith(os.path.join(agent_dir, "generated_media"))


def test_media_generate_gemini_fallback_to_primary_key(media_tool_fixture):
    tool, stored_env, stored_settings, _ = media_tool_fixture
    stored_settings["core.media.image-provider"] = "gemini"
    stored_env.clear()
    stored_env["GEMINI_API_KEY"] = "gemini-primary-key"

    sample_bytes = create_sample_jpeg_bytes()
    mock_client = MagicMock()
    mock_part = MagicMock()
    mock_part.inline_data.data = sample_bytes
    mock_candidate = MagicMock()
    mock_candidate.content.parts = [mock_part]
    mock_response = MagicMock()
    mock_response.candidates = [mock_candidate]
    mock_client.models.generate_content.return_value = mock_response

    with patch("tools.media_tool.genai.Client", return_value=mock_client) as mock_client_cls:
        res = tool.execute("generate", prompt="A green forest")
        mock_client_cls.assert_called_once_with(api_key="gemini-primary-key")
        assert "Successfully generated and saved image to" in res["result"]


def test_media_generate_gemini_all_models_fail(media_tool_fixture):
    tool, stored_env, stored_settings, _ = media_tool_fixture
    stored_settings["core.media.image-provider"] = "gemini"
    stored_env["GEMINI_IMAGE_API_KEY"] = "gemini-img-key"

    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = Exception("Rate limit exceeded")

    with patch("tools.media_tool.genai.Client", return_value=mock_client):
        res = tool.execute("generate", prompt="A red flower")
        assert "Error: All image models failed. Last error: Rate limit exceeded" in res["result"]


def test_media_generate_openrouter_success_b64(media_tool_fixture):
    tool, stored_env, stored_settings, agent_dir = media_tool_fixture
    stored_settings["core.media.image-provider"] = "openrouter"
    stored_settings["core.media.openrouter.model"] = "bytedance-seed/seedream-4.5"
    stored_env["OPENROUTER_IMAGE_API_KEY"] = "openrouter-img-key"

    sample_bytes = create_sample_jpeg_bytes()
    b64_str = base64.b64encode(sample_bytes).decode("utf-8")
    resp_json = json.dumps({"data": [{"b64_json": b64_str}]}).encode("utf-8")

    mock_resp = MagicMock()
    mock_resp.read.return_value = resp_json
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = None

    captured_requests = []

    def mock_urlopen(req, timeout=60):
        captured_requests.append(req)
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        res = tool.execute("generate", prompt="A cyberpunk metropolis")

        assert len(captured_requests) == 1
        req = captured_requests[0]
        assert req.full_url == "https://openrouter.ai/api/v1/images"
        assert req.headers["Authorization"] == "Bearer openrouter-img-key"
        sent_body = json.loads(req.data.decode("utf-8"))
        assert sent_body == {
            "model": "bytedance-seed/seedream-4.5",
            "prompt": "A cyberpunk metropolis"
        }

        assert "Successfully generated and saved image to" in res["result"]
        assert len(res["media"]) == 1
        saved_path = res["media"][0]
        assert os.path.exists(saved_path)
        assert saved_path.startswith(os.path.join(agent_dir, "generated_media"))


def test_media_generate_openrouter_fallback_to_primary_key(media_tool_fixture):
    tool, stored_env, stored_settings, _ = media_tool_fixture
    stored_settings["core.media.image-provider"] = "openrouter"
    stored_env.clear()
    stored_env["OPENROUTER_API_KEY"] = "openrouter-primary-fallback-key"

    sample_bytes = create_sample_jpeg_bytes()
    b64_str = base64.b64encode(sample_bytes).decode("utf-8")
    resp_json = json.dumps({"data": [{"b64_json": b64_str}]}).encode("utf-8")

    mock_resp = MagicMock()
    mock_resp.read.return_value = resp_json
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = None

    captured_requests = []

    def mock_urlopen(req, timeout=60):
        captured_requests.append(req)
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        res = tool.execute("generate", prompt="A magical library")
        assert len(captured_requests) == 1
        assert captured_requests[0].headers["Authorization"] == "Bearer openrouter-primary-fallback-key"
        assert "Successfully generated and saved image to" in res["result"]


def test_media_generate_openrouter_success_url(media_tool_fixture):
    tool, stored_env, stored_settings, agent_dir = media_tool_fixture
    stored_settings["core.media.image-provider"] = "openrouter"
    stored_env["OPENROUTER_IMAGE_API_KEY"] = "openrouter-img-key"

    sample_bytes = create_sample_jpeg_bytes()
    api_resp_json = json.dumps({"data": [{"url": "https://images.example.com/render.jpg"}]}).encode("utf-8")

    mock_api_resp = MagicMock()
    mock_api_resp.read.return_value = api_resp_json
    mock_api_resp.__enter__.return_value = mock_api_resp
    mock_api_resp.__exit__.return_value = None

    mock_img_resp = MagicMock()
    mock_img_resp.read.return_value = sample_bytes
    mock_img_resp.__enter__.return_value = mock_img_resp
    mock_img_resp.__exit__.return_value = None

    def mock_urlopen(req, timeout=60):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "openrouter.ai" in url:
            return mock_api_resp
        elif "images.example.com" in url:
            return mock_img_resp
        raise ValueError(f"Unexpected URL: {url}")

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        res = tool.execute("generate", prompt="A sunset on Mars")
        assert "Successfully generated and saved image to" in res["result"]
        assert len(res["media"]) == 1
        assert os.path.exists(res["media"][0])


def test_media_generate_openrouter_empty_data(media_tool_fixture):
    tool, stored_env, stored_settings, _ = media_tool_fixture
    stored_settings["core.media.image-provider"] = "openrouter"
    stored_env["OPENROUTER_IMAGE_API_KEY"] = "openrouter-img-key"

    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({"data": []}).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = None

    with patch("urllib.request.urlopen", return_value=mock_resp):
        res = tool.execute("generate", prompt="A crystal cave")
        assert "Error: OpenRouter image generation returned no data." in res["result"]


def test_media_generate_openrouter_missing_image_payload(media_tool_fixture):
    tool, stored_env, stored_settings, _ = media_tool_fixture
    stored_settings["core.media.image-provider"] = "openrouter"
    stored_env["OPENROUTER_IMAGE_API_KEY"] = "openrouter-img-key"

    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({"data": [{"revised_prompt": "hello"}]}).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = None

    with patch("urllib.request.urlopen", return_value=mock_resp):
        res = tool.execute("generate", prompt="A crystal cave")
        assert "Error: OpenRouter returned no image data (neither b64_json nor url)." in res["result"]


def test_media_generate_openrouter_network_error(media_tool_fixture):
    tool, stored_env, stored_settings, _ = media_tool_fixture
    stored_settings["core.media.image-provider"] = "openrouter"
    stored_env["OPENROUTER_IMAGE_API_KEY"] = "openrouter-img-key"

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection timed out")):
        res = tool.execute("generate", prompt="A stormy sea")
        assert "Error generating image with OpenRouter" in res["result"]
        assert "Connection timed out" in res["result"]


def test_media_generate_missing_agent_id(media_tool_fixture):
    tool, stored_env, stored_settings, _ = media_tool_fixture
    stored_settings["core.media.image-provider"] = "openrouter"
    stored_env["OPENROUTER_IMAGE_API_KEY"] = "openrouter-img-key"
    tool.orchestrator.agent_id = None

    sample_bytes = create_sample_jpeg_bytes()
    b64_str = base64.b64encode(sample_bytes).decode("utf-8")
    resp_json = json.dumps({"data": [{"b64_json": b64_str}]}).encode("utf-8")

    mock_resp = MagicMock()
    mock_resp.read.return_value = resp_json
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = None

    with patch("urllib.request.urlopen", return_value=mock_resp):
        res = tool.execute("generate", prompt="A starship")
        assert res["result"] == "Error: agent_id is required to generate media."


def test_media_read_command(media_tool_fixture, tmp_path):
    tool, _, _, _ = media_tool_fixture

    # Missing file_path
    res1 = tool.execute("read")
    assert res1 == {"result": "Error: Missing file_path parameter."}

    # Nonexistent file
    non_existent = str(tmp_path / "does_not_exist.png")
    res2 = tool.execute("read", file_path=non_existent)
    assert f"Error: File not found at {non_existent}" in res2["result"]

    # Path is directory, not file
    res3 = tool.execute("read", file_path=str(tmp_path))
    assert f"Error: {tmp_path} is not a file." in res3["result"]

    # Unsupported mime type
    text_file = tmp_path / "note.txt"
    text_file.write_text("hello world")
    res4 = tool.execute("read", file_path=str(text_file))
    assert "does not appear to be a supported media type" in res4["result"]

    # Supported image file
    img_file = tmp_path / "image.jpg"
    img_file.write_bytes(create_sample_jpeg_bytes())
    res5 = tool.execute("read", file_path=str(img_file))
    assert res5["result"] == f"Successfully attached media file: {str(img_file)}"
    assert res5["media"] == [str(img_file)]


def test_media_unknown_command(media_tool_fixture):
    tool, _, _, _ = media_tool_fixture
    res = tool.execute("compress_image")
    assert res == {"result": "Unknown command: compress_image"}


def test_media_generate_gemini_independent_of_llm_provider_claude(media_tool_fixture):
    tool, stored_env, stored_settings, _ = media_tool_fixture
    stored_settings["core.api-provider"] = "claude"
    stored_settings["core.media.image-provider"] = "gemini"
    stored_env["CLAUDE_API_KEY"] = "sk-ant-test"
    stored_env["GEMINI_IMAGE_API_KEY"] = "gemini-media-key-xyz"

    mock_client = MagicMock()
    mock_part = MagicMock()
    mock_part.inline_data.data = create_sample_jpeg_bytes()
    mock_candidate = MagicMock()
    mock_candidate.content.parts = [mock_part]
    mock_response = MagicMock()
    mock_response.candidates = [mock_candidate]
    mock_client.models.generate_content.return_value = mock_response

    with patch("tools.media_tool.genai.Client", return_value=mock_client) as mock_client_cls:
        res = tool.execute("generate", prompt="A majestic mountain landscape")
        mock_client_cls.assert_called_once_with(api_key="gemini-media-key-xyz")
        assert "Successfully generated and saved image to" in res["result"]
        assert len(res["media"]) == 1


def test_media_generate_gemini_fallback_to_tts_key(media_tool_fixture):
    tool, stored_env, stored_settings, _ = media_tool_fixture
    stored_settings["core.api-provider"] = "claude"
    stored_settings["core.media.image-provider"] = "gemini"
    stored_env["CLAUDE_API_KEY"] = "sk-ant-test"
    stored_env.pop("GEMINI_IMAGE_API_KEY", None)
    stored_env.pop("GEMINI_API_KEY", None)
    stored_env["GEMINI_TTS_API_KEY"] = "gemini-tts-fallback-key"

    mock_client = MagicMock()
    mock_part = MagicMock()
    mock_part.inline_data.data = create_sample_jpeg_bytes()
    mock_candidate = MagicMock()
    mock_candidate.content.parts = [mock_part]
    mock_response = MagicMock()
    mock_response.candidates = [mock_candidate]
    mock_client.models.generate_content.return_value = mock_response

    with patch("tools.media_tool.genai.Client", return_value=mock_client) as mock_client_cls:
        res = tool.execute("generate", prompt="A serene forest at dawn")
        mock_client_cls.assert_called_once_with(api_key="gemini-tts-fallback-key")
        assert "Successfully generated and saved image to" in res["result"]


def test_media_generate_openrouter_independent_of_llm_provider_gemini(media_tool_fixture):
    tool, stored_env, stored_settings, _ = media_tool_fixture
    stored_settings["core.api-provider"] = "gemini"
    stored_settings["core.media.image-provider"] = "openrouter"
    stored_env["GEMINI_API_KEY"] = "gemini-llm-key"
    stored_env["OPENROUTER_IMAGE_API_KEY"] = "openrouter-img-dedicated-key"

    sample_bytes = create_sample_jpeg_bytes()
    b64_str = base64.b64encode(sample_bytes).decode("utf-8")
    resp_json = json.dumps({"data": [{"b64_json": b64_str}]}).encode("utf-8")

    mock_resp = MagicMock()
    mock_resp.read.return_value = resp_json
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = None

    captured_requests = []

    def mock_urlopen(req, timeout=60):
        captured_requests.append(req)
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        res = tool.execute("generate", prompt="A cozy winter cottage")
        assert len(captured_requests) == 1
        assert captured_requests[0].headers["Authorization"] == "Bearer openrouter-img-dedicated-key"
        assert "Successfully generated and saved image to" in res["result"]
        assert len(res["media"]) == 1


def test_media_generate_openrouter_independent_of_llm_provider_chatgpt(media_tool_fixture):
    tool, stored_env, stored_settings, _ = media_tool_fixture
    stored_settings["core.api-provider"] = "chatgpt"
    stored_settings["core.media.image-provider"] = "openrouter"
    stored_env["CHATGPT_API_KEY"] = "sk-chatgpt-key"
    stored_env["OPENROUTER_IMAGE_API_KEY"] = "openrouter-img-for-chatgpt"

    sample_bytes = create_sample_jpeg_bytes()
    b64_str = base64.b64encode(sample_bytes).decode("utf-8")
    resp_json = json.dumps({"data": [{"b64_json": b64_str}]}).encode("utf-8")

    mock_resp = MagicMock()
    mock_resp.read.return_value = resp_json
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = None

    captured_requests = []

    def mock_urlopen(req, timeout=60):
        captured_requests.append(req)
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        res = tool.execute("generate", prompt="A futuristic hovercar")
        assert len(captured_requests) == 1
        assert captured_requests[0].headers["Authorization"] == "Bearer openrouter-img-for-chatgpt"
        assert "Successfully generated and saved image to" in res["result"]


def test_paths_get_generated_media_dir(tmp_path):
    from config import paths
    with patch.dict(os.environ, {"XDG_DATA_HOME": str(tmp_path)}):
        agent_media = paths.get_generated_media_dir("test_agent_123")
        assert agent_media == os.path.join(str(tmp_path), "agents", "test_agent_123", "generated_media")

        shared_media = paths.get_generated_media_dir(None)
        assert shared_media == os.path.join(str(tmp_path), "generated_media")


def test_backup_excludes_generated_media():
    from core.backup_manager import EXCLUDED_DIR_NAMES, EXCLUDED_EXTERNAL_DIR_NAMES
    assert "generated_media" in EXCLUDED_DIR_NAMES
    assert "generated_media" in EXCLUDED_EXTERNAL_DIR_NAMES


def test_media_generate_24h_cache_cleanup(media_tool_fixture, tmp_path):
    tool, stored_env, stored_settings, agent_dir = media_tool_fixture
    stored_settings["core.media.image-provider"] = "openrouter"
    stored_env["OPENROUTER_IMAGE_API_KEY"] = "or-key"

    save_dir = os.path.join(agent_dir, "generated_media")
    os.makedirs(save_dir, exist_ok=True)

    import time
    fresh_file = os.path.join(save_dir, "fresh.jpg")
    with open(fresh_file, "wb") as f:
        f.write(create_sample_jpeg_bytes())

    stale_file = os.path.join(save_dir, "stale.jpg")
    with open(stale_file, "wb") as f:
        f.write(create_sample_jpeg_bytes())
    stale_mtime = time.time() - (25 * 3600)  # 25 hours old
    os.utime(stale_file, (stale_mtime, stale_mtime))

    sample_bytes = create_sample_jpeg_bytes()
    b64_str = base64.b64encode(sample_bytes).decode("utf-8")
    resp_json = json.dumps({"data": [{"b64_json": b64_str}]}).encode("utf-8")

    mock_resp = MagicMock()
    mock_resp.read.return_value = resp_json
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = None

    with patch("urllib.request.urlopen", return_value=mock_resp):
        res = tool.execute("generate", prompt="Clean landscape")

        assert "Successfully generated and saved image" in res["result"]
        assert "24 hours" in res["result"]
        # Stale file should be pruned, fresh file preserved
        assert not os.path.exists(stale_file)
        assert os.path.exists(fresh_file)
        # Newly generated file exists
        assert os.path.exists(res["media"][0])


def test_media_generate_result_includes_24h_cache_notice(media_tool_fixture):
    tool, stored_env, stored_settings, _ = media_tool_fixture
    stored_settings["core.media.image-provider"] = "gemini"
    stored_env["GEMINI_IMAGE_API_KEY"] = "gemini-key"

    mock_client = MagicMock()
    mock_part = MagicMock()
    mock_part.inline_data.data = create_sample_jpeg_bytes()
    mock_candidate = MagicMock()
    mock_candidate.content.parts = [mock_part]
    mock_response = MagicMock()
    mock_response.candidates = [mock_candidate]
    mock_client.models.generate_content.return_value = mock_response

    with patch("tools.media_tool.genai.Client", return_value=mock_client):
        res = tool.execute("generate", prompt="A sunset")
        assert "24 hours" in res["result"]
        assert "temporary" in res["result"]
        assert "Be sure to use the image" in res["result"]


def test_media_transcribe_audio_missing_param(media_tool_fixture):
    tool, _, _, _ = media_tool_fixture
    res = tool.execute("transcribe_audio")
    assert res == {"result": "Error: Missing file_path parameter."}


def test_media_transcribe_audio_file_not_found(media_tool_fixture):
    tool, _, _, _ = media_tool_fixture
    res = tool.execute("transcribe_audio", file_path="/nonexistent/path/audio.ogg")
    assert "Error: File not found" in res["result"]


def test_media_transcribe_audio_empty_file(media_tool_fixture, tmp_path):
    tool, _, _, _ = media_tool_fixture
    empty_file = tmp_path / "empty.ogg"
    empty_file.write_bytes(b"")

    res = tool.execute("transcribe_audio", file_path=str(empty_file))
    assert "empty (0 bytes)" in res["result"]


def test_media_transcribe_audio_unsupported_format(media_tool_fixture, tmp_path):
    tool, _, _, _ = media_tool_fixture
    txt_file = tmp_path / "document.txt"
    txt_file.write_text("Hello world")

    res = tool.execute("transcribe_audio", file_path=str(txt_file))
    assert "does not appear to be an audio file" in res["result"]


def test_media_transcribe_audio_success(media_tool_fixture, tmp_path):
    tool, _, _, _ = media_tool_fixture
    audio_file = tmp_path / "voice_note.ogg"
    audio_file.write_bytes(b"OggS" + b"\x00" * 60)

    mock_stt = MagicMock()
    mock_stt.transcribe.return_value = "Meeting scheduled for 3 PM."

    with patch("core.local_stt.LocalSTT", return_value=mock_stt):
        res = tool.execute("transcribe_audio", file_path=str(audio_file))
        assert "Audio Transcription (voice_note.ogg):" in res["result"]
        assert "Meeting scheduled for 3 PM." in res["result"]
        mock_stt.transcribe.assert_called_once_with(str(audio_file))


def test_local_stt_truncated_and_missing_file_safety(tmp_path):
    from core.local_stt import LocalSTT
    stt = LocalSTT()

    res_missing = stt.transcribe("/nonexistent/file.wav")
    assert "Transcription Failed: File not found" in res_missing

    stub_file = tmp_path / "stub.ogg"
    stub_file.write_bytes(b"\x75\xab\x5a\x6a\xe7\x62\xa3\xfa\x20\x81\xca\x1d\x79\xcb")
    res_stub = stt.transcribe(str(stub_file))
    assert "too small or truncated" in res_stub
    assert "14 bytes" in res_stub




