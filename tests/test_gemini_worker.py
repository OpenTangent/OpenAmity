import os
import sys
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.gemini_worker import GeminiWorker
from core.settings_manager import SettingsManager


def test_gemini_worker_init_without_key(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path / (aid or "default")))
    with patch.object(SettingsManager, "get_env", return_value=""):
        worker = GeminiWorker(agent_id="test_gemini_nokey")
        assert worker.available is False
        assert "GEMINI_API_KEY not found" in worker.last_error


def test_gemini_worker_init_with_key(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path / (aid or "default")))
    with patch.object(SettingsManager, "get_env", return_value="test_key_12345"):
        with patch("google.genai.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            worker = GeminiWorker(agent_id="test_gemini_key")
            assert worker.available is True
            assert worker.api_key == "test_key_12345"


def test_gemini_worker_session_lifecycle(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path / (aid or "default")))
    with patch.object(SettingsManager, "get_env", return_value="test_key_12345"):
        with patch("google.genai.Client"):
            worker = GeminiWorker(agent_id="test_gemini_session")
            assert worker.is_running() is False

            worker.start_session("Test instructions", tools=[])
            assert worker.is_running() is True
            assert "Test instructions" in worker.sys_instruct

            worker.stop_session()
            assert worker.is_running() is False


def test_gemini_worker_reconcile_unfulfilled_tool_calls(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path / (aid or "default")))
    with patch.object(SettingsManager, "get_env", return_value="test_key_12345"):
        with patch("google.genai.Client"):
            from google.genai import types

            worker = GeminiWorker(agent_id="test_gemini_reconcile")
            worker.start_session("Test instructions", tools=[])

            # Simulate assistant turn with function call in chat history
            mock_chat = MagicMock()
            fc_part = types.Part.from_function_call(name="Terminal_check_status", args={"task_id": "4"})
            model_turn = types.Content(role="model", parts=[fc_part])
            mock_chat._curated_history = [model_turn]

            captured_messages = []
            def mock_send_stream(message):
                captured_messages.append(message)
                chunk = MagicMock()
                chunk.parts = [MagicMock(text="All good now.")]
                chunk.function_calls = []
                chunk.usage_metadata = None
                return [chunk]

            mock_chat.send_message_stream = mock_send_stream
            worker.thinker_chat = mock_chat

            # Broken loop: user sends next prompt without prior function response
            worker._process_thought(prompt="What is happening now?", image_path=None, yolo=False)

            assert len(captured_messages) == 1
            sent_content = captured_messages[0]
            # Must contain synthesized dummy function response + user prompt
            assert len(sent_content) == 2
            dummy_part = sent_content[0]
            assert dummy_part.function_response is not None
            assert dummy_part.function_response.name == "Terminal_check_status"
            assert "[Action cancelled or interrupted by system]" in dummy_part.function_response.response["result"]
            assert sent_content[1] == "What is happening now?"


def test_gemini_worker_reconcile_partial_tool_calls(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path / (aid or "default")))
    with patch.object(SettingsManager, "get_env", return_value="test_key_12345"):
        with patch("google.genai.Client"):
            from google.genai import types

            worker = GeminiWorker(agent_id="test_gemini_partial")
            worker.start_session("Test instructions", tools=[])

            mock_chat = MagicMock()
            fc1 = types.Part.from_function_call(name="ToolA", args={})
            fc2 = types.Part.from_function_call(name="ToolB", args={})
            model_turn = types.Content(role="model", parts=[fc1, fc2])
            mock_chat._curated_history = [model_turn]

            captured_messages = []
            def mock_send_stream(message):
                captured_messages.append(message)
                chunk = MagicMock()
                chunk.parts = [MagicMock(text="Partial ok.")]
                chunk.function_calls = []
                chunk.usage_metadata = None
                return [chunk]

            mock_chat.send_message_stream = mock_send_stream
            worker.thinker_chat = mock_chat

            def synchronous_thread(target, args=(), kwargs=None, daemon=None):
                m = MagicMock()
                m.start = lambda: target(*args, **(kwargs or {}))
                return m

            # Only respond to ToolA
            with patch("threading.Thread", side_effect=synchronous_thread):
                worker.send_function_responses([("ToolA", {"result": "success"})])

            assert len(captured_messages) == 1
            sent_content = captured_messages[0]
            assert len(sent_content) == 2
            names = [p.function_response.name for p in sent_content if getattr(p, "function_response", None)]
            assert "ToolA" in names
            assert "ToolB" in names


def test_gemini_worker_thinking_budget(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path / (aid or "default")))
    with patch.object(SettingsManager, "get_env", return_value="test_key_12345"):
        with patch("google.genai.Client"):
            worker = GeminiWorker(agent_id="test_gemini_thinking")
            worker.start_session("Test instructions", tools=[])
            assert worker.thinker_config is not None
            assert worker.thinker_config.thinking_config is not None
            # Default thinking level is "low", which maps to 1024 budget
            assert worker.thinker_config.thinking_config.thinking_budget == 1024
            assert worker.thinker_config.automatic_function_calling is not None
            assert worker.thinker_config.automatic_function_calling.disable is True

            # Test other thinking levels
            with patch.object(SettingsManager, "get", lambda self, key, default=None: "medium" if "thinking-level" in key else default):
                w2 = GeminiWorker(agent_id="test_gemini_medium")
                w2.start_session("Instructions")
                assert w2.thinker_config.thinking_config.thinking_budget == 4096

            with patch.object(SettingsManager, "get", lambda self, key, default=None: "max" if "thinking-level" in key else default):
                w3 = GeminiWorker(agent_id="test_gemini_max")
                w3.start_session("Instructions")
                assert w3.thinker_config.thinking_config.thinking_budget == -1


def test_subagent_worker_thinking_budget(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path / (aid or "default")))
    with patch.object(SettingsManager, "get_env", return_value="test_key_12345"):
        with patch("google.genai.Client"):
            from core.subagent_worker import SubagentWorker
            orch = MagicMock()
            orch.agent_id = "test_gemini"
            orch.cerebrum.tools = {}
            worker = SubagentWorker(subagent_id="test_sub_1", orchestrator=orch)
            assert worker.thinker_config is not None
            assert worker.thinker_config.thinking_config is not None
            # Default thinking level is "low" -> 1024
            assert worker.thinker_config.thinking_config.thinking_budget == 1024
            assert worker.thinker_config.automatic_function_calling is not None
            assert worker.thinker_config.automatic_function_calling.disable is True


def test_gemini_worker_light_model_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path / (aid or "default")))
    with patch.object(SettingsManager, "get_env", return_value="test_key_12345"):
        with patch("google.genai.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client

            worker = GeminiWorker(agent_id="test_gemini_fallback")
            worker.start_session("Test instructions", tools=[])
            assert worker.current_model == "gemini-3.8-flash"
            assert worker.light_model == "gemini-3.5-flash-lite"

            primary_chat = MagicMock()
            primary_chat.get_history.return_value = []
            # Primary chat fails with 429 quota error
            def fail_send_message_stream(msg):
                raise Exception("Resource has been exhausted (e.g. check quota, 429).")
            primary_chat.send_message_stream = fail_send_message_stream

            fallback_chat = MagicMock()
            chunk = MagicMock()
            chunk.parts = [MagicMock(text="Response from light model.")]
            chunk.function_calls = []
            chunk.usage_metadata = None
            fallback_chat.send_message_stream.return_value = [chunk]

            chats_created = []
            def mock_create_chat(model, config, history=None):
                if model == worker.light_model:
                    chats_created.append((model, fallback_chat))
                    return fallback_chat
                chats_created.append((model, primary_chat))
                return primary_chat

            mock_client.chats.create.side_effect = mock_create_chat
            worker.thinker_chat = primary_chat

            emitted_thoughts = []
            worker.thought_received.connect(lambda text, tools: emitted_thoughts.append(text))

            worker._process_thought(prompt="Hello", image_path=None, yolo=False)

            # Check that fallback happened to light model
            assert worker.current_model == "gemini-3.5-flash-lite"
            assert len(emitted_thoughts) == 1
            assert emitted_thoughts[0] == "Response from light model."


def test_gemini_worker_reformulator_afc_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path / (aid or "default")))
    with patch.object(SettingsManager, "get_env", return_value="test_key_12345"):
        with patch("google.genai.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            worker = GeminiWorker(agent_id="test_gemini_reform")

            mock_resp = MagicMock()
            mock_part = MagicMock()
            mock_part.text = "Standalone query"
            mock_resp.parts = [mock_part]
            mock_client.models.generate_content.return_value = mock_resp

            res = worker.reformulate_query("what about it?", [("User", "How is the weather?"), ("Assistant", "Sunny")])
            assert res == "Standalone query"
            assert mock_client.models.generate_content.called
            call_kwargs = mock_client.models.generate_content.call_args.kwargs
            config = call_kwargs.get("config")
            assert config is not None
            assert config.automatic_function_calling is not None
            assert config.automatic_function_calling.disable is True



