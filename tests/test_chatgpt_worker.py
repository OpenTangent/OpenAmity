import os
import sys
import json
import pytest
from unittest.mock import MagicMock, patch

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.chatgpt_worker import (
    ChatGptWorker,
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


def test_chatgpt_worker_initialization(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    # Test without API key
    worker = ChatGptWorker(agent_id="test_chatgpt_agent")
    assert worker.available is False

    # Test with API key
    with patch.object(SettingsManager, "get_env", return_value="sk-test-key-12345"):
        worker_with_key = ChatGptWorker(agent_id="test_chatgpt_agent")
        assert worker_with_key.available is True
        assert worker_with_key.api_key == "sk-test-key-12345"
        assert worker_with_key.client is not None


def test_chatgpt_worker_start_stop_session(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    with patch.object(SettingsManager, "get_env", return_value="sk-test-key-12345"):
        worker = ChatGptWorker(agent_id="test_chatgpt_agent")
        
        # Test standard model selection
        worker.start_session(
            system_instruction="You are Amy.",
            tools=[{"name": "TestTool_test", "description": "A test tool", "parameters": {}}]
        )
        assert worker.running is True
        assert worker.current_model == "gpt-5.6-terra"
        assert len(worker.openai_tools) == 1
        assert "You are Amy." in worker.sys_instruct
        assert "CRITICAL INSTRUCTION" in worker.sys_instruct

        worker.stop_session()
        assert worker.running is False
        assert worker.history == []

        # Test low-token model selection
        with patch.object(SettingsManager, "get", lambda self, key, default=None: True if key == "core.low-token-mode" else default):
            worker.start_session(system_instruction="Low token session")
            assert worker.current_model == "gpt-5.6-luna"
            worker.stop_session()


def test_chatgpt_worker_streaming_thought_and_tool_calls(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    with patch.object(SettingsManager, "get_env", return_value="sk-test-key-12345"):
        worker = ChatGptWorker(agent_id="test_chatgpt_agent")
        worker.start_session(system_instruction="Test system prompt")

        # Create mock stream chunks
        # Chunk 1: text delta
        chunk1 = MagicMock()
        chunk1.choices = [MagicMock()]
        chunk1.choices[0].delta = MagicMock()
        chunk1.choices[0].delta.content = "I will check the weather."
        chunk1.choices[0].delta.tool_calls = None
        chunk1.usage = None

        # Chunk 2: tool call delta part 1
        tc_delta1 = MagicMock()
        tc_delta1.index = 0
        tc_delta1.id = "call_abc123"
        tc_delta1.function.name = "Weather_check"
        tc_delta1.function.arguments = '{"loc'

        chunk2 = MagicMock()
        chunk2.choices = [MagicMock()]
        chunk2.choices[0].delta = MagicMock()
        chunk2.choices[0].delta.content = None
        chunk2.choices[0].delta.tool_calls = [tc_delta1]
        chunk2.usage = None

        # Chunk 3: tool call delta part 2
        tc_delta2 = MagicMock()
        tc_delta2.index = 0
        tc_delta2.id = None
        tc_delta2.function.name = None
        tc_delta2.function.arguments = 'ation": "London"}'

        chunk3 = MagicMock()
        chunk3.choices = [MagicMock()]
        chunk3.choices[0].delta = MagicMock()
        chunk3.choices[0].delta.content = None
        chunk3.choices[0].delta.tool_calls = [tc_delta2]
        chunk3.usage = None

        # Chunk 4: usage
        chunk4 = MagicMock()
        chunk4.choices = []
        chunk4.usage = MagicMock()
        chunk4.usage.total_tokens = 42

        mock_stream = [chunk1, chunk2, chunk3, chunk4]
        worker.client.chat.completions.create = MagicMock(return_value=mock_stream)

        received_thought = []
        received_tools = []
        received_tokens = []

        worker.thought_received.connect(lambda t, f: (received_thought.append(t), received_tools.append(f)))
        worker.tokens_consumed.connect(lambda tok: received_tokens.append(tok))

        # Process thought synchronously for test
        worker._process_thought(prompt="What's the weather?")

        assert len(received_thought) == 1
        assert received_thought[0] == "I will check the weather."
        assert len(received_tools) == 1
        assert len(received_tools[0]) == 1
        assert received_tools[0][0].name == "Weather_check"
        assert received_tools[0][0].args == {"location": "London"}
        assert received_tokens == [42]

        # Verify assistant message in history
        assert len(worker.history) == 2  # user + assistant
        assistant_msg = worker.history[1]
        assert assistant_msg["role"] == "assistant"
        assert assistant_msg["content"] == "I will check the weather."
        assert len(assistant_msg["tool_calls"]) == 1
        assert assistant_msg["tool_calls"][0]["id"] == "call_abc123"

        # Now test sending function responses back
        mock_stream_step2 = [chunk1]
        worker.client.chat.completions.create = MagicMock(return_value=mock_stream_step2)

        worker.send_function_response("Weather_check", {"temp": 22, "condition": "Sunny"})
        
        # Verify tool response added to history
        tool_msg = worker.history[2]
        assert tool_msg["role"] == "tool"
        assert tool_msg["tool_call_id"] == "call_abc123"
        assert json.loads(tool_msg["content"]) == {"temp": 22, "condition": "Sunny"}


def test_chatgpt_reformulate_query(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    with patch.object(SettingsManager, "get_env", return_value="sk-test-key-12345"):
        worker = ChatGptWorker(agent_id="test_chatgpt_agent")

        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "What is the weather in London today?"

        worker.client.chat.completions.create = MagicMock(return_value=mock_resp)

        history = [("User", "I am in London."), ("Agent", "Nice!")]
        result = worker.reformulate_query("What is the weather like here today?", history)
        assert result == "What is the weather in London today?"


def test_chatgpt_worker_reconcile_unfulfilled_tool_calls(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    with patch.object(SettingsManager, "get_env", return_value="sk-test-key-12345"):
        worker = ChatGptWorker(agent_id="test_chatgpt_agent")
        worker.start_session(system_instruction="Test prompt")

        # Simulate assistant message with unfulfilled tool calls in history
        worker.history.append({
            "role": "assistant",
            "content": "Checking terminal...",
            "tool_calls": [
                {
                    "id": "call_gpt_1",
                    "type": "function",
                    "function": {"name": "Terminal_run", "arguments": "{}"}
                }
            ]
        })

        captured_kwargs = {}
        def mock_create(**kwargs):
            captured_kwargs.update(kwargs)
            return []

        worker.client.chat.completions.create = mock_create

        worker._process_thought(prompt="Continue please")

        assert "call_gpt_1" in worker.consumed_tool_call_ids
        messages = captured_kwargs["messages"]
        tool_messages = [m for m in messages if m.get("role") == "tool"]
        assert len(tool_messages) == 1
        assert tool_messages[0]["tool_call_id"] == "call_gpt_1"
        assert "[Action cancelled or interrupted by system]" in tool_messages[0]["content"]

        assert messages[-1]["role"] == "user"
        assert messages[-1]["content"] == "Continue please"
        assert messages[-2]["role"] == "tool"


def test_chatgpt_worker_user_prompt_rollback_on_error(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    with patch.object(SettingsManager, "get_env", return_value="sk-test-key-12345"):
        worker = ChatGptWorker(agent_id="test_chatgpt_agent")
        worker.start_session(system_instruction="Test prompt")

        def mock_error(**kwargs):
            raise RuntimeError("OpenAI 500 error")

        worker.client.chat.completions.create = mock_error

        errors = []
        worker.error_occurred.connect(lambda e: errors.append(e))

        worker._process_thought(prompt="Will fail")
        assert len(errors) == 1
        assert "OpenAI 500 error" in errors[0]
        assert len(worker.history) == 0


def test_chatgpt_worker_empty_content_without_tool_calls(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    with patch.object(SettingsManager, "get_env", return_value="sk-test-key-12345"):
        worker = ChatGptWorker(agent_id="test_chatgpt_agent")
        worker.start_session(system_instruction="Test prompt")

        # Mock chunk with no content and no tool calls
        chunk = MagicMock()
        chunk.choices = [MagicMock()]
        chunk.choices[0].delta = MagicMock()
        chunk.choices[0].delta.content = None
        chunk.choices[0].delta.tool_calls = None
        chunk.usage = None

        worker.client.chat.completions.create = MagicMock(return_value=[chunk])

        worker._process_thought(prompt="Say something")

        assert len(worker.history) == 2
        assistant_msg = worker.history[1]
        assert assistant_msg["role"] == "assistant"
        assert assistant_msg["content"] == ""  # Must not be None
        assert "tool_calls" not in assistant_msg


def test_chatgpt_worker_reconcile_sanitizes_none_content(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    with patch.object(SettingsManager, "get_env", return_value="sk-test-key-12345"):
        worker = ChatGptWorker(agent_id="test_chatgpt_agent")
        worker.start_session(system_instruction="Test prompt")

        # Inject legacy corrupted assistant message
        worker.history.append({
            "role": "assistant",
            "content": None
        })

        worker._reconcile_tool_calls()

        assert worker.history[0]["content"] == ""


