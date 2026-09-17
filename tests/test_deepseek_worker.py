import os
import sys
import json
import pytest
from unittest.mock import MagicMock, patch

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.deepseek_worker import (
    DeepSeekWorker,
    _convert_schema_types,
    _convert_to_openai_tool,
    FunctionCallObject
)
from core.settings_manager import SettingsManager


def test_convert_schema_types_and_tool():
    genai_tool = {
        "name": "Weather_check",
        "description": "Checks the weather",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "location": {
                    "type": "STRING",
                    "description": "City name"
                },
                "days": {
                    "type": "INTEGER"
                }
            },
            "required": ["location"]
        }
    }
    converted = _convert_to_openai_tool(genai_tool)
    assert converted["type"] == "function"
    assert converted["function"]["name"] == "Weather_check"
    assert converted["function"]["description"] == "Checks the weather"
    assert converted["function"]["parameters"]["type"] == "object"
    assert converted["function"]["parameters"]["properties"]["location"]["type"] == "string"
    assert converted["function"]["parameters"]["properties"]["days"]["type"] == "integer"
    assert converted["function"]["parameters"]["required"] == ["location"]


def test_deepseek_worker_initialization(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    # Test without API key
    worker = DeepSeekWorker(agent_id="test_deepseek_agent")
    assert worker.available is False

    # Test with API key
    with patch.object(SettingsManager, "get_env", return_value="sk-deepseek-test-key-12345"):
        worker_with_key = DeepSeekWorker(agent_id="test_deepseek_agent")
        assert worker_with_key.available is True
        assert worker_with_key.api_key == "sk-deepseek-test-key-12345"
        assert worker_with_key.client is not None
        assert str(worker_with_key.client.base_url).startswith("https://api.deepseek.com")


def test_deepseek_worker_start_stop_session(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    with patch.object(SettingsManager, "get_env", return_value="sk-deepseek-test-key-12345"):
        worker = DeepSeekWorker(agent_id="test_deepseek_agent")
        
        # Test standard model selection (should default to deepseek-flash)
        worker.start_session(
            system_instruction="You are Amy.",
            tools=[{"name": "TestTool_test", "description": "A test tool", "parameters": {}}]
        )
        assert worker.running is True
        assert worker.current_model == "deepseek-flash"
        assert len(worker.openai_tools) == 1
        assert "You are Amy." in worker.sys_instruct
        assert "CRITICAL INSTRUCTION" in worker.sys_instruct
        assert "AUTONOMOUS SPEECH INSTRUCTION" in worker.sys_instruct

        worker.stop_session()
        assert worker.running is False
        assert worker.history == []

        # Test low-token model selection
        with patch.object(SettingsManager, "get", lambda self, key, default=None: True if key == "core.low-token-mode" else default):
            worker.start_session(system_instruction="Low token session")
            assert worker.current_model == "deepseek-flash"
            worker.stop_session()


def test_deepseek_worker_streaming_thought_and_tool_calls(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    with patch.object(SettingsManager, "get_env", return_value="sk-deepseek-test-key-12345"):
        worker = DeepSeekWorker(agent_id="test_deepseek_agent")
        worker.start_session(system_instruction="Test system prompt")

        # Create mock stream chunks with reasoning_content and content
        # Chunk 1: reasoning delta
        chunk1 = MagicMock()
        chunk1.choices = [MagicMock()]
        chunk1.choices[0].delta = MagicMock()
        chunk1.choices[0].delta.reasoning_content = "Let me think about weather checking."
        chunk1.choices[0].delta.content = None
        chunk1.choices[0].delta.tool_calls = None
        chunk1.usage = None

        # Chunk 2: text content delta
        chunk2 = MagicMock()
        chunk2.choices = [MagicMock()]
        chunk2.choices[0].delta = MagicMock()
        chunk2.choices[0].delta.reasoning_content = None
        chunk2.choices[0].delta.content = "I will check the weather now."
        chunk2.choices[0].delta.tool_calls = None
        chunk2.usage = None

        # Chunk 3: tool call delta part 1
        tc_delta1 = MagicMock()
        tc_delta1.index = 0
        tc_delta1.id = "call_deepseek_123"
        tc_delta1.function.name = "Weather_check"
        tc_delta1.function.arguments = '{"loc'

        chunk3 = MagicMock()
        chunk3.choices = [MagicMock()]
        chunk3.choices[0].delta = MagicMock()
        chunk3.choices[0].delta.reasoning_content = None
        chunk3.choices[0].delta.content = None
        chunk3.choices[0].delta.tool_calls = [tc_delta1]
        chunk3.usage = None

        # Chunk 4: tool call delta part 2
        tc_delta2 = MagicMock()
        tc_delta2.index = 0
        tc_delta2.id = None
        tc_delta2.function.name = None
        tc_delta2.function.arguments = 'ation": "Tokyo"}'

        chunk4 = MagicMock()
        chunk4.choices = [MagicMock()]
        chunk4.choices[0].delta = MagicMock()
        chunk4.choices[0].delta.reasoning_content = None
        chunk4.choices[0].delta.content = None
        chunk4.choices[0].delta.tool_calls = [tc_delta2]
        chunk4.usage = None

        # Chunk 5: usage
        chunk5 = MagicMock()
        chunk5.choices = []
        chunk5.usage = MagicMock()
        chunk5.usage.total_tokens = 50

        mock_stream = [chunk1, chunk2, chunk3, chunk4, chunk5]
        worker.client.chat.completions.create = MagicMock(return_value=mock_stream)

        received_thought = []
        received_tools = []
        received_tokens = []

        worker.thought_received.connect(lambda t, f: (received_thought.append(t), received_tools.append(f)))
        worker.tokens_consumed.connect(lambda tok: received_tokens.append(tok))

        # Process thought synchronously for test
        worker._process_thought(prompt="What's the weather in Tokyo?")

        assert len(received_thought) == 1
        assert "Let me think about weather checking." in received_thought[0]
        assert "I will check the weather now." in received_thought[0]
        assert len(received_tools) == 1
        assert len(received_tools[0]) == 1
        assert received_tools[0][0].name == "Weather_check"
        assert received_tools[0][0].args == {"location": "Tokyo"}
        assert received_tokens == [50]

        # Verify assistant message in history retains reasoning_content
        assert len(worker.history) == 2  # user + assistant
        assistant_msg = worker.history[1]
        assert assistant_msg["role"] == "assistant"
        assert assistant_msg["content"] == "I will check the weather now."
        assert assistant_msg["reasoning_content"] == "Let me think about weather checking."
        assert len(assistant_msg["tool_calls"]) == 1
        assert assistant_msg["tool_calls"][0]["id"] == "call_deepseek_123"

        # Now test sending function responses back
        mock_stream_step2 = [chunk2]
        worker.client.chat.completions.create = MagicMock(return_value=mock_stream_step2)

        worker.send_function_response("Weather_check", {"temp": 18, "condition": "Clear"})
        
        # Verify tool response added to history
        tool_msg = worker.history[2]
        assert tool_msg["role"] == "tool"
        assert tool_msg["tool_call_id"] == "call_deepseek_123"
        assert json.loads(tool_msg["content"]) == {"temp": 18, "condition": "Clear"}


def test_deepseek_reformulate_query(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    with patch.object(SettingsManager, "get_env", return_value="sk-deepseek-test-key-12345"):
        worker = DeepSeekWorker(agent_id="test_deepseek_agent")

        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "What is the weather in Tokyo today?"

        worker.client.chat.completions.create = MagicMock(return_value=mock_resp)

        history = [("User", "I am in Tokyo."), ("Agent", "Konichiwa!")]
        result = worker.reformulate_query("What is the weather like here today?", history)
        assert result == "What is the weather in Tokyo today?"


def test_deepseek_settings_panel_load_and_save(monkeypatch, tmp_path):
    from PySide6.QtWidgets import QApplication
    from gui.settings_panel import SettingsPanelWidget
    from core.config_manager import ConfigManager

    app = QApplication.instance()
    if app is None:
        app = QApplication([])

    agent_dir = tmp_path / "agents" / "test_deepseek_gui_agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    config_file = str(tmp_path / "config.json")
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(agent_dir))

    cm = ConfigManager(config_file=config_file)
    sm = SettingsManager(agent_id="test_deepseek_gui_agent")
    sm.set("core.api-provider", "deepseek")
    sm.save()
    sm.set_env("DEEPSEEK_API_KEY", "sk-deepseek-ui-key")

    panel = SettingsPanelWidget()
    panel.config = cm
    panel.load_settings(settings_manager=sm)

    assert panel.ui_use_deepseek_api.isChecked()
    assert panel.ui_deepseek_api_key.text() == "sk-deepseek-ui-key"

    # User modifies key and re-saves
    panel.ui_deepseek_api_key.setText("sk-deepseek-ui-key-updated")
    panel.save_settings()

    assert sm.get("core.api-provider") == "deepseek"
    assert sm.get_env("DEEPSEEK_API_KEY") == "sk-deepseek-ui-key-updated"


def test_orchestrator_deepseek_worker_init(monkeypatch, tmp_path):
    from core.orchestrator import AmityOrchestrator
    from core.deepseek_worker import DeepSeekWorker

    agent_dir = tmp_path / "agents" / "test_deepseek_orch_agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(agent_dir))

    sm = SettingsManager(agent_id="test_deepseek_orch_agent")
    sm.set("core.api-provider", "deepseek")
    sm.save()
    sm.set_env("DEEPSEEK_API_KEY", "sk-deepseek-orch-key")

    orch = AmityOrchestrator.__new__(AmityOrchestrator)
    orch.agent_id = "test_deepseek_orch_agent"
    orch.settings_manager = sm
    orch.gemini_worker = None
    orch.handle_gemini_thought = MagicMock()
    orch.add_fatigue = MagicMock()
    orch.handle_gemini_speech = MagicMock()
    orch.handle_gemini_error = MagicMock()
    orch.cerebrum = MagicMock()
    orch.cerebrum.get_all_tool_declarations.return_value = []
    orch.system_prompt = "Test system prompt"

    orch.init_worker()
    assert isinstance(orch.gemini_worker, DeepSeekWorker)
    assert orch.gemini_worker.api_key == "sk-deepseek-orch-key"
    assert orch.gemini_worker.available is True
    assert orch.gemini_worker.running is True
    assert orch.gemini_worker.current_model == "deepseek-flash"


def test_deepseek_worker_local_image_handling(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    # Create dummy PNG file
    img_file = tmp_path / "test_image.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)

    with patch.object(SettingsManager, "get_env", return_value="sk-deepseek-test-key-12345"):
        worker = DeepSeekWorker(agent_id="test_deepseek_agent")
        worker.start_session(system_instruction="Test prompt")

        mock_stream = []
        worker.client.chat.completions.create = MagicMock(return_value=mock_stream)

        worker._process_thought(prompt="Analyze this chart", image_path=str(img_file))

        assert len(worker.history) >= 1
        user_msg = worker.history[0]
        assert user_msg["role"] == "user"
        assert isinstance(user_msg["content"], list)
        assert len(user_msg["content"]) == 2
        assert user_msg["content"][0]["type"] == "text"
        assert user_msg["content"][0]["text"] == "Analyze this chart"
        assert user_msg["content"][1]["type"] == "image_url"
        assert user_msg["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
        assert user_msg["content"][1]["image_url"]["detail"] == "auto"


def test_deepseek_worker_external_image_url(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    with patch.object(SettingsManager, "get_env", return_value="sk-deepseek-test-key-12345"):
        worker = DeepSeekWorker(agent_id="test_deepseek_agent")
        worker.start_session(system_instruction="Test prompt")

        mock_stream = []
        worker.client.chat.completions.create = MagicMock(return_value=mock_stream)

        worker._process_thought(
            prompt="Look at this web image",
            image_path="https://example.com/sample.jpg"
        )

        user_msg = worker.history[0]
        assert user_msg["role"] == "user"
        assert isinstance(user_msg["content"], list)
        img_part = user_msg["content"][1]
        assert img_part["type"] == "image_url"
        assert img_part["image_url"]["url"] == "https://example.com/sample.jpg"
        assert img_part["image_url"]["detail"] == "auto"


def test_deepseek_worker_intelligent_media_culling(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    with patch.object(SettingsManager, "get_env", return_value="sk-deepseek-test-key-12345"):
        worker = DeepSeekWorker(agent_id="test_deepseek_agent")
        worker.start_session(system_instruction="Test prompt")

        # Populate history with 6 turns (first turn has heavy multimodal image)
        worker.history = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Turn 1: here is an image"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,ABCD1234"}}
                ]
            },
            {"role": "assistant", "content": "Turn 1 response"},
            {"role": "user", "content": "Turn 2: question"},
            {"role": "assistant", "content": "Turn 2 response"},
            {"role": "user", "content": "Turn 3: question"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Turn 4 (recent): recent image"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,RECENTIMAGE"}}
                ]
            }
        ]

        worker._cull_media_from_history()

        # Turn 1 should have been culled (older than last 4 turns)
        culled_turn1 = worker.history[0]
        assert culled_turn1["content"][0]["text"] == "Turn 1: here is an image"
        assert culled_turn1["content"][1]["type"] == "text"
        assert "[Media attachment automatically culled to save tokens/memory]" in culled_turn1["content"][1]["text"]

        # Turn 4 is in the recent 4 turns and must NOT be culled
        recent_turn = worker.history[5]
        assert recent_turn["content"][1]["type"] == "image_url"
        assert recent_turn["content"][1]["image_url"]["url"] == "data:image/png;base64,RECENTIMAGE"


def test_deepseek_worker_tool_media_feedback(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    img_file = tmp_path / "tool_output.jpg"
    img_file.write_bytes(b"\xff\xd8\xff" + b"\x00" * 20)

    with patch.object(SettingsManager, "get_env", return_value="sk-deepseek-test-key-12345"):
        worker = DeepSeekWorker(agent_id="test_deepseek_agent")
        worker.start_session(system_instruction="Test prompt")

        # Fake assistant message with tool call
        worker.history.append({
            "role": "assistant",
            "content": "Reading media file",
            "tool_calls": [{
                "id": "call_media_123",
                "type": "function",
                "function": {"name": "Media_read", "arguments": "{}"}
            }]
        })

        worker.client.chat.completions.create = MagicMock(return_value=[])

        # Send tool response containing media
        worker.send_function_response("Media_read", {
            "result": "Attached image successfully",
            "media": [str(img_file)]
        })

        # History should have tool result (without media) and follow-up user message with image
        tool_msg = worker.history[1]
        assert tool_msg["role"] == "tool"
        assert tool_msg["tool_call_id"] == "call_media_123"
        tool_content = json.loads(tool_msg["content"])
        assert "media" not in tool_content
        assert tool_content["result"] == "Attached image successfully"

        user_media_msg = worker.history[2]
        assert user_media_msg["role"] == "user"
        assert isinstance(user_media_msg["content"], list)
        assert "[Attached media from tool execution]:" in user_media_msg["content"][0]["text"]
        assert user_media_msg["content"][1]["type"] == "image_url"
        assert user_media_msg["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_deepseek_worker_thinking_effort_kwargs(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    with patch.object(SettingsManager, "get_env", return_value="sk-deepseek-test-key-12345"):
        worker = DeepSeekWorker(agent_id="test_deepseek_agent")
        worker.start_session(system_instruction="Test prompt")

        captured_kwargs = {}
        def mock_create(**kwargs):
            captured_kwargs.update(kwargs)
            return []

        worker.client.chat.completions.create = mock_create

        # 1. Standard mode: extra_body={"thinking": {"type": "enabled"}}, reasoning_effort omitted (Finding H3)
        worker._process_thought(prompt="Standard mode test")
        assert captured_kwargs["model"] == "deepseek-flash"
        assert "reasoning_effort" not in captured_kwargs
        assert captured_kwargs["extra_body"] == {"thinking": {"type": "enabled"}}

        # 2. Low token mode: reasoning_effort still omitted
        with patch.object(SettingsManager, "get", lambda self, key, default=None: True if key == "core.low-token-mode" else default):
            worker._process_thought(prompt="Low token test")
            assert "reasoning_effort" not in captured_kwargs


def test_deepseek_worker_reconcile_unfulfilled_tool_calls(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    with patch.object(SettingsManager, "get_env", return_value="sk-deepseek-test-key-12345"):
        worker = DeepSeekWorker(agent_id="test_deepseek_agent")
        worker.start_session(system_instruction="Test prompt")

        # Simulate assistant message with unfulfilled tool calls in history
        worker.history.append({
            "role": "assistant",
            "content": "Running task...",
            "tool_calls": [
                {
                    "id": "call_pending_1",
                    "type": "function",
                    "function": {"name": "Terminal_check_status", "arguments": '{"task_id": "4"}'}
                },
                {
                    "id": "call_pending_2",
                    "type": "function",
                    "function": {"name": "Terminal_wait", "arguments": "{}"}
                }
            ]
        })

        # Partially fulfill call_pending_1
        worker.history.append({
            "role": "tool",
            "tool_call_id": "call_pending_1",
            "content": json.dumps({"status": "running"})
        })
        worker.consumed_tool_call_ids.add("call_pending_1")

        captured_kwargs = {}
        def mock_create(**kwargs):
            captured_kwargs.update(kwargs)
            return []

        worker.client.chat.completions.create = mock_create

        # Next prompt comes in (e.g. after loop break)
        worker._process_thought(prompt="What is happening now?")

        # Check that call_pending_2 was synthesized
        assert "call_pending_2" in worker.consumed_tool_call_ids

        messages = captured_kwargs["messages"]
        # System prompt + assistant + tool(call_pending_1) + tool(call_pending_2 dummy) + user("What is happening now?")
        tool_messages = [m for m in messages if m.get("role") == "tool"]
        assert len(tool_messages) == 2
        assert tool_messages[0]["tool_call_id"] == "call_pending_1"
        assert tool_messages[1]["tool_call_id"] == "call_pending_2"
        assert "[Action cancelled or interrupted by system]" in tool_messages[1]["content"]

        # Ensure tool message comes BEFORE user message
        assert messages[-1]["role"] == "user"
        assert messages[-1]["content"] == "What is happening now?"
        assert messages[-2]["role"] == "tool"
        assert messages[-2]["tool_call_id"] == "call_pending_2"


def test_deepseek_worker_user_prompt_rollback_on_error(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    with patch.object(SettingsManager, "get_env", return_value="sk-deepseek-test-key-12345"):
        worker = DeepSeekWorker(agent_id="test_deepseek_agent")
        worker.start_session(system_instruction="Test prompt")

        def mock_error(**kwargs):
            raise RuntimeError("API connection timeout")

        worker.client.chat.completions.create = mock_error

        errors = []
        worker.error_occurred.connect(lambda e: errors.append(e))

        worker._process_thought(prompt="Will fail")
        assert len(errors) == 1
        assert "API connection timeout" in errors[0]
        # History should NOT retain the failed user message
        assert len(worker.history) == 0


def test_deepseek_worker_reasoning_only_empty_content(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    with patch.object(SettingsManager, "get_env", return_value="sk-deepseek-test-key-12345"):
        worker = DeepSeekWorker(agent_id="test_deepseek_agent")
        worker.start_session(system_instruction="Test system prompt")

        # Chunk with reasoning only, no content, no tool calls
        chunk = MagicMock()
        chunk.choices = [MagicMock()]
        chunk.choices[0].delta = MagicMock()
        chunk.choices[0].delta.reasoning_content = "Thinking through a difficult puzzle..."
        chunk.choices[0].delta.content = None
        chunk.choices[0].delta.tool_calls = None
        chunk.usage = None

        worker.client.chat.completions.create = MagicMock(return_value=[chunk])

        worker._process_thought(prompt="Solve this")

        assert len(worker.history) == 2
        assistant_msg = worker.history[1]
        assert assistant_msg["role"] == "assistant"
        assert assistant_msg["content"] == ""  # Must not be None
        assert "tool_calls" not in assistant_msg
        assert assistant_msg["reasoning_content"] == "Thinking through a difficult puzzle..."


def test_deepseek_worker_reconcile_sanitizes_none_content(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    with patch.object(SettingsManager, "get_env", return_value="sk-deepseek-test-key-12345"):
        worker = DeepSeekWorker(agent_id="test_deepseek_agent")
        worker.start_session(system_instruction="Test system prompt")

        # Inject legacy corrupted assistant message
        worker.history.append({
            "role": "assistant",
            "content": None,
            "reasoning_content": "Prior thought"
        })

        worker._reconcile_tool_calls()

        assert worker.history[0]["content"] == ""






