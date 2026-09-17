import os
import sys
import json
import pytest
from unittest.mock import MagicMock, patch

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.claude_worker import (
    ClaudeWorker,
    _convert_schema_types,
    _convert_to_anthropic_tool,
    FunctionCallObject
)
from core.settings_manager import SettingsManager


@pytest.fixture(autouse=True)
def mock_anthropic_if_missing(monkeypatch):
    import core.claude_worker
    if core.claude_worker.anthropic is None:
        mock_mod = MagicMock()
        monkeypatch.setattr(core.claude_worker, "anthropic", mock_mod)


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
    converted = _convert_to_anthropic_tool(genai_tool)
    assert converted["name"] == "Weather_check"
    assert converted["description"] == "Checks the weather"
    assert converted["input_schema"]["type"] == "object"
    assert converted["input_schema"]["properties"]["location"]["type"] == "string"
    assert converted["input_schema"]["properties"]["days"]["type"] == "integer"
    assert converted["input_schema"]["required"] == ["location"]


def test_claude_worker_initialization(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    # Test without API key
    worker = ClaudeWorker(agent_id="test_claude_agent")
    assert worker.available is False

    # Test with API key
    with patch.object(SettingsManager, "get_env", return_value="sk-ant-test-key-12345"):
        worker_with_key = ClaudeWorker(agent_id="test_claude_agent")
        assert worker_with_key.available is True
        assert worker_with_key.api_key == "sk-ant-test-key-12345"
        assert worker_with_key.client is not None
        assert worker_with_key.current_model == "claude-fable-5-1"


def test_claude_worker_start_stop_session(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    with patch.object(SettingsManager, "get_env", return_value="sk-ant-test-key-12345"):
        worker = ClaudeWorker(agent_id="test_claude_agent")

        # Test standard model selection (should default to claude-fable-5-1)
        worker.start_session(
            system_instruction="You are Amity.",
            tools=[{"name": "TestTool_test", "description": "A test tool", "parameters": {}}]
        )
        assert worker.running is True
        assert worker.current_model == "claude-fable-5-1"
        assert len(worker.anthropic_tools) == 1
        assert "You are Amity." in worker.sys_instruct
        assert "CRITICAL INSTRUCTION" in worker.sys_instruct
        assert "AUTONOMOUS SPEECH INSTRUCTION" in worker.sys_instruct

        worker.stop_session()
        assert worker.running is False
        assert worker.history == []
        assert len(worker.consumed_tool_use_ids) == 0

        # Test low-token model selection (should default to claude-haiku-4-5)
        with patch.object(SettingsManager, "get", lambda self, key, default=None: True if key == "core.low-token-mode" else default):
            worker.start_session(system_instruction="Low token session")
            assert worker.current_model == "claude-haiku-4-5"
            worker.stop_session()


def test_claude_worker_streaming_thought_thinking_and_tool_calls(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    with patch.object(SettingsManager, "get_env", return_value="sk-ant-test-key-12345"):
        worker = ClaudeWorker(agent_id="test_claude_agent")
        worker.start_session(
            system_instruction="Test system prompt",
            tools=[
                {"name": "Tool1", "description": "Tool 1", "parameters": {}},
                {"name": "Weather_check", "description": "Check weather", "parameters": {"type": "OBJECT", "properties": {"location": {"type": "STRING"}}}}
            ]
        )

        # Mock stream message
        mock_thinking_block = MagicMock()
        mock_thinking_block.type = "thinking"
        mock_thinking_block.thinking = "Let's analyze the user's location request carefully."

        mock_text_block = MagicMock()
        mock_text_block.type = "text"
        mock_text_block.text = "I will check the weather for Cape Town."

        mock_tool_block = MagicMock()
        mock_tool_block.type = "tool_use"
        mock_tool_block.id = "toolu_01Axyz987"
        mock_tool_block.name = "Weather_check"
        mock_tool_block.input = {"location": "Cape Town"}

        mock_final_msg = MagicMock()
        mock_final_msg.content = [mock_thinking_block, mock_text_block, mock_tool_block]
        mock_final_msg.usage = MagicMock()
        mock_final_msg.usage.input_tokens = 1500
        mock_final_msg.usage.output_tokens = 320
        mock_final_msg.usage.cache_read_input_tokens = 3500
        mock_final_msg.usage.cache_creation_input_tokens = 500

        # Mock stream context manager
        mock_stream = MagicMock()
        mock_stream.text_stream = ["I will check ", "the weather for Cape Town."]
        mock_stream.get_final_message.return_value = mock_final_msg
        mock_stream.__enter__.return_value = mock_stream
        mock_stream.__exit__.return_value = None

        captured_kwargs = {}

        def mock_stream_method(**kwargs):
            captured_kwargs.update(kwargs)
            return mock_stream

        worker.client.messages.stream = MagicMock(side_effect=mock_stream_method)

        received_thought = []
        received_tools = []
        received_tokens = []

        worker.thought_received.connect(lambda t, f: (received_thought.append(t), received_tools.append(f)))
        worker.tokens_consumed.connect(lambda tok: received_tokens.append(tok))

        # Process thought synchronously for test
        worker._process_thought(prompt="What's the weather in Cape Town?", image_path=None, yolo=False)

        # Verify prompt caching in kwargs
        assert captured_kwargs["model"] == "claude-fable-5-1"
        assert captured_kwargs["max_tokens"] == 16384
        assert isinstance(captured_kwargs["system"], list)
        assert captured_kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert "Test system prompt" in captured_kwargs["system"][0]["text"]

        # Verify adaptive thinking effort is passed via output_config
        assert "output_config" in captured_kwargs
        assert captured_kwargs["output_config"]["effort"] == "high"

        # Verify tools payload has ephemeral cache control on the last tool
        assert "tools" in captured_kwargs
        assert len(captured_kwargs["tools"]) == 2
        assert "cache_control" not in captured_kwargs["tools"][0]
        assert captured_kwargs["tools"][1]["cache_control"] == {"type": "ephemeral"}

        # Verify emitted thoughts combine thinking blocks and text blocks
        assert len(received_thought) == 1
        assert "Let's analyze the user's location request carefully." in received_thought[0]
        assert "I will check the weather for Cape Town." in received_thought[0]

        # Verify function call extraction
        assert len(received_tools) == 1
        assert len(received_tools[0]) == 1
        assert received_tools[0][0].name == "Weather_check"
        assert received_tools[0][0].args == {"location": "Cape Town"}

        # Verify token accounting including cache read/write tokens
        # Total tokens = 1500 + 320 + 3500 + 500 = 5820
        assert received_tokens == [5820]

        # Verify assistant message in history contains full content blocks
        assert len(worker.history) == 2  # user + assistant
        assistant_msg = worker.history[1]
        assert assistant_msg["role"] == "assistant"
        assert assistant_msg["content"] == [mock_thinking_block, mock_text_block, mock_tool_block]

        # Now test sending function response
        mock_final_msg_step2 = MagicMock()
        mock_final_msg_step2.content = [MagicMock(type="text", text="The temperature in Cape Town is 21C.")]
        mock_final_msg_step2.usage = MagicMock(input_tokens=200, output_tokens=50, cache_read_input_tokens=5000, cache_creation_input_tokens=0)

        mock_stream_step2 = MagicMock()
        mock_stream_step2.text_stream = ["The temperature in Cape Town is 21C."]
        mock_stream_step2.get_final_message.return_value = mock_final_msg_step2
        mock_stream_step2.__enter__.return_value = mock_stream_step2
        mock_stream_step2.__exit__.return_value = None

        worker.client.messages.stream = MagicMock(return_value=mock_stream_step2)

        def synchronous_thread(target, args=(), kwargs=None, daemon=None):
            m = MagicMock()
            m.start = lambda: target(*args, **(kwargs or {}))
            return m

        with patch("threading.Thread", side_effect=synchronous_thread):
            worker.send_function_response("Weather_check", {"temperature": "21C", "condition": "Sunny", "media": "some_media_data"})

        # Verify tool response added to history
        assert len(worker.history) == 4  # user + assistant + user(tool_result) + assistant
        tool_user_msg = worker.history[2]
        assert tool_user_msg["role"] == "user"
        assert len(tool_user_msg["content"]) == 1
        assert tool_user_msg["content"][0]["type"] == "tool_result"
        assert tool_user_msg["content"][0]["tool_use_id"] == "toolu_01Axyz987"
        
        parsed_res = json.loads(tool_user_msg["content"][0]["content"])
        assert parsed_res["temperature"] == "21C"
        assert "media" not in parsed_res  # media stripped


def test_claude_send_function_responses_multiple(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    with patch.object(SettingsManager, "get_env", return_value="sk-ant-test-key-12345"):
        worker = ClaudeWorker(agent_id="test_claude_agent")
        worker.start_session(system_instruction="Test multiple tools")

        # Mock assistant message with 2 tool calls
        tc1 = MagicMock()
        tc1.type = "tool_use"
        tc1.id = "toolu_01_a"
        tc1.name = "ToolA"
        tc1.input = {"a": 1}

        tc2 = MagicMock()
        tc2.type = "tool_use"
        tc2.id = "toolu_02_b"
        tc2.name = "ToolB"
        tc2.input = {"b": 2}

        worker.history.append({"role": "assistant", "content": [tc1, tc2]})

        mock_stream = MagicMock()
        mock_stream.text_stream = ["All done!"]
        mock_stream.get_final_message.return_value = MagicMock(
            content=[MagicMock(type="text", text="All done!")],
            usage=MagicMock(input_tokens=100, output_tokens=20, cache_read_input_tokens=0, cache_creation_input_tokens=0)
        )
        mock_stream.__enter__.return_value = mock_stream
        mock_stream.__exit__.return_value = None
        worker.client.messages.stream = MagicMock(return_value=mock_stream)

        def synchronous_thread(target, args=(), kwargs=None, daemon=None):
            m = MagicMock()
            m.start = lambda: target(*args, **(kwargs or {}))
            return m

        with patch("threading.Thread", side_effect=synchronous_thread):
            worker.send_function_responses([
                ("ToolA", {"result": "ok_a"}),
                ("ToolB", {"result": "ok_b"})
            ])

        # Verify both tool results are packaged together in the next user message
        assert len(worker.history) == 3
        tool_user_msg = worker.history[1]
        assert tool_user_msg["role"] == "user"
        assert len(tool_user_msg["content"]) == 2
        assert tool_user_msg["content"][0]["tool_use_id"] == "toolu_01_a"
        assert tool_user_msg["content"][1]["tool_use_id"] == "toolu_02_b"


def test_claude_reformulate_query(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    with patch.object(SettingsManager, "get_env", return_value="sk-ant-test-key-12345"):
        worker = ClaudeWorker(agent_id="test_claude_agent")

        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text="What is the weather in Cape Town today?")]

        worker.client.messages.create = MagicMock(return_value=mock_resp)

        history = [("User", "I am in Cape Town."), ("Agent", "Howzit!")]
        result = worker.reformulate_query("What is the weather like here today?", history)
        assert result == "What is the weather in Cape Town today?"
        
        # Verify it used claude-haiku-4-5
        call_kwargs = worker.client.messages.create.call_args[1]
        assert call_kwargs["model"] == "claude-haiku-4-5"


def test_claude_settings_panel_load_and_save(monkeypatch, tmp_path):
    from PySide6.QtWidgets import QApplication
    from gui.settings_panel import SettingsPanelWidget
    from core.config_manager import ConfigManager

    app = QApplication.instance()
    if app is None:
        app = QApplication([])

    agent_dir = tmp_path / "agents" / "test_claude_gui_agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    config_file = str(tmp_path / "config.json")
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(agent_dir))

    cm = ConfigManager(config_file=config_file)
    sm = SettingsManager(agent_id="test_claude_gui_agent")
    sm.set("core.api-provider", "claude")
    sm.save()
    sm.set_env("CLAUDE_API_KEY", "sk-ant-ui-key")

    panel = SettingsPanelWidget()
    panel.config = cm
    panel.load_settings(settings_manager=sm)

    assert panel.ui_use_claude_api.isChecked()
    assert panel.ui_claude_api_key.text() == "sk-ant-ui-key"

    # User modifies key and re-saves
    panel.ui_claude_api_key.setText("sk-ant-ui-key-updated")
    panel.save_settings()

    assert sm.get("core.api-provider") == "claude"
    assert sm.get_env("CLAUDE_API_KEY") == "sk-ant-ui-key-updated"


def test_orchestrator_claude_worker_init(monkeypatch, tmp_path):
    from core.orchestrator import AmityOrchestrator
    from core.claude_worker import ClaudeWorker

    agent_dir = tmp_path / "agents" / "test_claude_orch_agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(agent_dir))

    sm = SettingsManager(agent_id="test_claude_orch_agent")
    sm.set("core.api-provider", "claude")
    sm.save()
    sm.set_env("CLAUDE_API_KEY", "sk-ant-orch-key")

    orch = AmityOrchestrator.__new__(AmityOrchestrator)
    orch.agent_id = "test_claude_orch_agent"
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
    assert isinstance(orch.gemini_worker, ClaudeWorker)
    assert orch.gemini_worker.api_key == "sk-ant-orch-key"
    assert orch.gemini_worker.available is True
    assert orch.gemini_worker.running is True
    assert orch.gemini_worker.current_model == "claude-fable-5-1"


def test_claude_api_error_rolls_back_user_message(monkeypatch, tmp_path):
    """Verify that a failed API call doesn't leave a dangling user message in history."""
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    with patch.object(SettingsManager, "get_env", return_value="sk-ant-test-key-12345"):
        worker = ClaudeWorker(agent_id="test_claude_agent")
        worker.start_session(system_instruction="Test error handling")

        assert len(worker.history) == 0

        # Make the stream call raise an exception
        worker.client.messages.stream = MagicMock(
            side_effect=Exception("Connection timeout"))

        received_errors = []
        worker.error_occurred.connect(lambda err: received_errors.append(err))

        # Process thought synchronously
        worker._process_thought(
            prompt="This should fail", image_path=None, yolo=False)

        # History should be empty — the dangling user message must be rolled back
        assert len(worker.history) == 0
        assert worker.is_processing is False
        assert len(received_errors) == 1
        assert "Connection timeout" in received_errors[0]


def test_claude_configurable_thinking_effort(monkeypatch, tmp_path):
    """Verify that output_config.effort is read from settings."""
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    with patch.object(SettingsManager, "get_env", return_value="sk-ant-test-key-12345"):
        worker = ClaudeWorker(agent_id="test_claude_agent")

        # Configure custom effort level via settings
        def custom_get(key, default=None):
            if key == "core.claude.thinking-effort":
                return "max"
            return default
        with patch.object(SettingsManager, "get", side_effect=custom_get):
            worker.start_session(system_instruction="Max effort test")

            mock_final_msg = MagicMock()
            mock_final_msg.content = [MagicMock(type="text", text="Deep thought.")]
            mock_final_msg.usage = MagicMock(
                input_tokens=100, output_tokens=50,
                cache_read_input_tokens=0, cache_creation_input_tokens=0)

            mock_stream = MagicMock()
            mock_stream.text_stream = ["Deep thought."]
            mock_stream.get_final_message.return_value = mock_final_msg
            mock_stream.__enter__.return_value = mock_stream
            mock_stream.__exit__.return_value = None

            captured_kwargs = {}

            def mock_stream_method(**kwargs):
                captured_kwargs.update(kwargs)
                return mock_stream

            worker.client.messages.stream = MagicMock(side_effect=mock_stream_method)

            worker._process_thought(prompt="Think hard", image_path=None, yolo=False)

            assert captured_kwargs["output_config"]["effort"] == "max"


def test_claude_worker_reconcile_unfulfilled_tool_calls(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    with patch.object(SettingsManager, "get_env", return_value="sk-ant-test-key-12345"):
        worker = ClaudeWorker(agent_id="test_claude_agent")
        worker.start_session(system_instruction="Test prompt")

        # Simulate assistant message with unfulfilled tool_use in history
        tc1 = MagicMock()
        tc1.type = "tool_use"
        tc1.id = "toolu_pending_1"
        tc1.name = "Terminal_check_status"
        tc1.input = {"task_id": "4"}

        worker.history.append({
            "role": "assistant",
            "content": [tc1]
        })

        mock_stream = MagicMock()
        mock_stream.text_stream = ["I am ready."]
        mock_stream.get_final_message.return_value = MagicMock(
            content=[MagicMock(type="text", text="I am ready.")],
            usage=MagicMock(input_tokens=50, output_tokens=10, cache_read_input_tokens=0, cache_creation_input_tokens=0)
        )
        mock_stream.__enter__.return_value = mock_stream
        mock_stream.__exit__.return_value = None

        captured_kwargs = {}
        def mock_stream_method(**kwargs):
            captured_kwargs.update(kwargs)
            return mock_stream

        worker.client.messages.stream = MagicMock(side_effect=mock_stream_method)

        # Send new user prompt (broken loop scenario)
        worker._process_thought(prompt="What is the latest status?", image_path=None, yolo=False)

        # Check consumed IDs
        assert "toolu_pending_1" in worker.consumed_tool_use_ids

        # Verify messages in history / passed to Claude
        messages = captured_kwargs["messages"]
        assert len(messages) == 3  # initial assistant + user (with dummy tool result + text) + response assistant
        user_msg = messages[1]
        assert user_msg["role"] == "user"
        assert len(user_msg["content"]) == 2
        # First block must be tool_result
        assert user_msg["content"][0]["type"] == "tool_result"
        assert user_msg["content"][0]["tool_use_id"] == "toolu_pending_1"
        assert "[Action cancelled or interrupted by system]" in user_msg["content"][0]["content"]
        # Second block is the new prompt text
        assert user_msg["content"][1]["type"] == "text"
        assert user_msg["content"][1]["text"] == "What is the latest status?"


def test_claude_worker_reconcile_partial_tool_calls(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    with patch.object(SettingsManager, "get_env", return_value="sk-ant-test-key-12345"):
        worker = ClaudeWorker(agent_id="test_claude_agent")
        worker.start_session(system_instruction="Test prompt")

        tc1 = MagicMock()
        tc1.type = "tool_use"
        tc1.id = "toolu_part_1"
        tc1.name = "ToolA"
        tc1.input = {}

        tc2 = MagicMock()
        tc2.type = "tool_use"
        tc2.id = "toolu_part_2"
        tc2.name = "ToolB"
        tc2.input = {}

        worker.history.append({
            "role": "assistant",
            "content": [tc1, tc2]
        })

        mock_stream = MagicMock()
        mock_stream.text_stream = ["OK"]
        mock_stream.get_final_message.return_value = MagicMock(
            content=[MagicMock(type="text", text="OK")],
            usage=MagicMock(input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0)
        )
        mock_stream.__enter__.return_value = mock_stream
        mock_stream.__exit__.return_value = None

        captured_kwargs = {}
        def mock_stream_method(**kwargs):
            captured_kwargs.update(kwargs)
            return mock_stream

        worker.client.messages.stream = MagicMock(side_effect=mock_stream_method)

        # Only answer ToolA
        def synchronous_thread(target, args=(), kwargs=None, daemon=None):
            m = MagicMock()
            m.start = lambda: target(*args, **(kwargs or {}))
            return m

        with patch("threading.Thread", side_effect=synchronous_thread):
            worker.send_function_responses([("ToolA", {"result": "success"})])

        assert "toolu_part_1" in worker.consumed_tool_use_ids
        assert "toolu_part_2" in worker.consumed_tool_use_ids

        messages = captured_kwargs["messages"]
        user_msg = messages[1]
        assert user_msg["role"] == "user"
        assert len(user_msg["content"]) == 2
        tool_ids = [block["tool_use_id"] for block in user_msg["content"]]
        assert "toolu_part_1" in tool_ids
        assert "toolu_part_2" in tool_ids

