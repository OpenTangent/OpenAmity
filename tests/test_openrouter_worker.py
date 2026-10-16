import os
import sys
import json
import time
import pytest
from unittest.mock import MagicMock, patch

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.openrouter_worker import (
    OpenRouterWorker,
    _convert_schema_types,
    _convert_to_openai_tool,
    _normalize_model_name,
    FunctionCallObject
)
from core.settings_manager import SettingsManager
from PySide6.QtWidgets import QApplication



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


def test_normalize_model_name():
    # If contains slash, returned as is
    assert _normalize_model_name("anthropic/claude-3.7-sonnet") == "anthropic/claude-3.7-sonnet"
    assert _normalize_model_name("openai/gpt-4o") == "openai/gpt-4o"
    assert _normalize_model_name("xiaomi/mimo-v2.6-pro") == "xiaomi/mimo-v2.6-pro"

    # Known prefixes mapped without slash
    assert _normalize_model_name("MiMo-V2.6-Pro") == "xiaomi/mimo-v2.6-pro"
    assert _normalize_model_name("mimo-v2.6-pro") == "xiaomi/mimo-v2.6-pro"
    assert _normalize_model_name("gpt-6-luna") == "openai/gpt-6-luna"
    assert _normalize_model_name("gpt-4o") == "openai/gpt-4o"
    assert _normalize_model_name("claude-3.7-sonnet") == "anthropic/claude-3.7-sonnet"
    assert _normalize_model_name("deepseek-r1") == "deepseek/deepseek-r1"
    assert _normalize_model_name("gemini-2.5-flash") == "google/gemini-2.5-flash"
    assert _normalize_model_name("llama-3.3-70b") == "meta-llama/llama-3.3-70b"
    assert _normalize_model_name("qwen-2.5-72b") == "qwen/qwen-2.5-72b"


def test_openrouter_worker_initialization(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    # Test without API key
    worker = OpenRouterWorker(agent_id="test_openrouter_agent")
    assert worker.available is False

    # Test with API key
    with patch.object(SettingsManager, "get_env", return_value="sk-or-test-key-12345"):
        worker_with_key = OpenRouterWorker(agent_id="test_openrouter_agent")
        assert worker_with_key.available is True
        assert worker_with_key.api_key == "sk-or-test-key-12345"
        assert worker_with_key.client is not None
        assert str(worker_with_key.client.base_url).startswith("https://openrouter.ai/api/v1")


def test_openrouter_worker_start_stop_session(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    with patch.object(SettingsManager, "get_env", return_value="sk-or-test-key-12345"):
        worker = OpenRouterWorker(agent_id="test_openrouter_agent")

        # Test standard model selection (should default to MiMo-V2.6-Pro)
        worker.start_session(
            system_instruction="You are Amy.",
            tools=[{"name": "TestTool_test", "description": "A test tool", "parameters": {}}]
        )
        assert worker.running is True
        assert worker.current_model == "MiMo-V2.6-Pro"
        assert len(worker.openai_tools) == 1
        assert "You are Amy." in worker.sys_instruct
        assert "CRITICAL INSTRUCTION" in worker.sys_instruct
        assert "AUTONOMOUS SPEECH INSTRUCTION" in worker.sys_instruct

        worker.stop_session()
        assert worker.running is False
        assert worker.history == []

        # Test low-token model selection (should default to gpt-6-luna)
        with patch.object(SettingsManager, "get", lambda self, key, default=None: True if key == "core.low-token-mode" else default):
            worker.start_session(system_instruction="Low token session")
            assert worker.current_model == "gpt-6-luna"
            worker.stop_session()


def test_openrouter_worker_streaming_thought_and_tool_calls(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    with patch.object(SettingsManager, "get_env", return_value="sk-or-test-key-12345"):
        worker = OpenRouterWorker(agent_id="test_openrouter_agent")
        worker.start_session(system_instruction="Test system prompt")

        # Mock stream chunks
        chunk1 = MagicMock()
        chunk1.choices = [MagicMock()]
        chunk1.choices[0].delta = MagicMock()
        chunk1.choices[0].delta.reasoning = "I need to check the status."
        chunk1.choices[0].delta.content = None
        chunk1.choices[0].delta.tool_calls = None
        chunk1.usage = None

        chunk2 = MagicMock()
        chunk2.choices = [MagicMock()]
        chunk2.choices[0].delta = MagicMock()
        chunk2.choices[0].delta.reasoning = None
        chunk2.choices[0].delta.content = " Checking now..."
        chunk2.choices[0].delta.tool_calls = None
        chunk2.usage = None

        tc_delta1 = MagicMock()
        tc_delta1.index = 0
        tc_delta1.id = "call_or_123"
        tc_delta1.function = MagicMock()
        tc_delta1.function.name = "Status_check"
        tc_delta1.function.arguments = '{"target":'

        chunk3 = MagicMock()
        chunk3.choices = [MagicMock()]
        chunk3.choices[0].delta = MagicMock()
        chunk3.choices[0].delta.reasoning = None
        chunk3.choices[0].delta.content = None
        chunk3.choices[0].delta.tool_calls = [tc_delta1]
        chunk3.usage = None

        tc_delta2 = MagicMock()
        tc_delta2.index = 0
        tc_delta2.id = None
        tc_delta2.function = MagicMock()
        tc_delta2.function.name = None
        tc_delta2.function.arguments = '"system"}'

        chunk4 = MagicMock()
        chunk4.choices = [MagicMock()]
        chunk4.choices[0].delta = MagicMock()
        chunk4.choices[0].delta.reasoning = None
        chunk4.choices[0].delta.content = None
        chunk4.choices[0].delta.tool_calls = [tc_delta2]
        chunk4.usage = MagicMock()
        chunk4.usage.completion_tokens = 45
        chunk4.usage.total_tokens = 90

        mock_stream = [chunk1, chunk2, chunk3, chunk4]
        worker.client.chat.completions.create = MagicMock(return_value=mock_stream)

        received_thoughts = []
        received_tools = []
        consumed_tokens = []

        worker.thought_received.connect(lambda t, funcs: (received_thoughts.append(t), received_tools.extend(funcs)))
        worker.tokens_consumed.connect(lambda tok: consumed_tokens.append(tok))

        # Directly run _process_thought synchronously
        worker._process_thought(prompt="Please check system status")

        assert len(received_thoughts) == 1
        assert "I need to check the status." in received_thoughts[0]
        assert "Checking now..." in received_thoughts[0]

        assert len(received_tools) == 1
        assert received_tools[0].name == "Status_check"
        assert received_tools[0].args == {"target": "system"}

        assert len(consumed_tokens) == 1
        assert consumed_tokens[0] > 0

        # Check history contains assistant message with tool calls
        assistant_msg = worker.history[-1]
        assert assistant_msg["role"] == "assistant"
        assert assistant_msg["tool_calls"][0]["id"] == "call_or_123"
        assert assistant_msg["tool_calls"][0]["function"]["name"] == "Status_check"

        # Now send function response
        worker.client.chat.completions.create = MagicMock(return_value=[])
        worker.send_function_response("Status_check", {"status": "ok"})
        time.sleep(0.1)

        tool_msg = [m for m in worker.history if m["role"] == "tool"][-1]
        assert tool_msg["tool_call_id"] == "call_or_123"
        assert json.loads(tool_msg["content"]) == {"status": "ok"}


def test_openrouter_worker_reasoning_details_parsing(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    with patch.object(SettingsManager, "get_env", return_value="sk-or-test-key-12345"):
        worker = OpenRouterWorker(agent_id="test_openrouter_agent")
        worker.start_session(system_instruction="Test system prompt")

        chunk = MagicMock()
        chunk.choices = [MagicMock()]
        chunk.choices[0].delta = MagicMock()
        chunk.choices[0].delta.reasoning = None
        chunk.choices[0].delta.reasoning_content = None
        chunk.choices[0].delta.reasoning_details = [
            {"type": "reasoning.text", "text": "Structured thinking step 1."}
        ]
        chunk.choices[0].delta.content = "Answer content"
        chunk.choices[0].delta.tool_calls = None
        chunk.usage = None

        worker.client.chat.completions.create = MagicMock(return_value=[chunk])

        received = []
        worker.thought_received.connect(lambda t, f: received.append(t))
        worker._process_thought(prompt="Hello")

        assert len(received) == 1
        assert "Structured thinking step 1." in received[0]
        assert "Answer content" in received[0]


def test_openrouter_worker_reconcile_tool_calls(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    with patch.object(SettingsManager, "get_env", return_value="sk-or-test-key-12345"):
        worker = OpenRouterWorker(agent_id="test_openrouter_agent")
        worker.start_session(system_instruction="Test system prompt")

        # Manually construct an unfulfilled tool call
        worker.history = [
            {"role": "user", "content": "Do task"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_unfulfilled_1",
                        "type": "function",
                        "function": {"name": "Do_something", "arguments": "{}"}
                    }
                ]
            }
        ]

        worker._reconcile_tool_calls()

        # Must have synthesized a tool response
        assert len(worker.history) == 3
        tool_msg = worker.history[2]
        assert tool_msg["role"] == "tool"
        assert tool_msg["tool_call_id"] == "call_unfulfilled_1"
        assert "[Action cancelled or interrupted by system]" in tool_msg["content"]


def test_openrouter_worker_light_model_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    with patch.object(SettingsManager, "get_env", return_value="sk-or-test-key-12345"):
        worker = OpenRouterWorker(agent_id="test_openrouter_agent")
        worker.start_session(system_instruction="Test system prompt")

        assert worker.current_model == "MiMo-V2.6-Pro"
        assert worker.light_model == "gpt-6-luna"

        attempted_models = []

        def mock_create(**kwargs):
            model = kwargs.get("model")
            attempted_models.append(model)
            if model == "xiaomi/mimo-v2.6-pro":
                raise Exception("503 Service Unavailable on primary model")
            # Success on light model
            chunk = MagicMock()
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta = MagicMock()
            chunk.choices[0].delta.reasoning = None
            chunk.choices[0].delta.content = "Response from fallback"
            chunk.choices[0].delta.tool_calls = None
            chunk.usage = None
            return [chunk]

        worker.client.chat.completions.create = mock_create

        emitted_thoughts = []
        worker.thought_received.connect(lambda t, f: emitted_thoughts.append(t))

        worker._process_thought(prompt="Test fallback")

        assert attempted_models == ["xiaomi/mimo-v2.6-pro", "openai/gpt-6-luna"]
        assert len(emitted_thoughts) == 1
        assert "Response from fallback" in emitted_thoughts[0]
        assert worker.current_model == "gpt-6-luna"


def test_openrouter_worker_reformulate_query(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    with patch.object(SettingsManager, "get_env", return_value="sk-or-test-key-12345"):
        worker = OpenRouterWorker(agent_id="test_openrouter_agent")
        worker.start_session()

        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "What is the capital of France?"

        worker.client.chat.completions.create = MagicMock(return_value=mock_resp)

        history = [("User", "Tell me about France."), ("Amy", "It is a country in Europe.")]
        rewritten = worker.reformulate_query("What is its capital?", history)
        assert rewritten == "What is the capital of France?"


def test_openrouter_settings_panel_load_and_save(monkeypatch, tmp_path):
    agent_dir = tmp_path / "agents" / "test_openrouter_gui_agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(agent_dir))

    from gui.settings_panel import SettingsPanelWidget
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    sm = SettingsManager(agent_id="test_openrouter_gui_agent")
    sm.set("core.api-provider", "openrouter")
    sm.set("core.openrouter.model", "MiMo-V2.6-Pro")
    sm.set("core.openrouter.light-model", "gpt-6-luna")
    sm.set_env("OPENROUTER_API_KEY", "sk-or-ui-test-key")

    panel = SettingsPanelWidget()
    panel.load_settings(settings_manager=sm)

    assert panel.ui_use_openrouter_api.isChecked()
    assert panel.ui_openrouter_api_key.text() == "sk-or-ui-test-key"
    assert panel.ui_openrouter_model.currentText().lower() in ("mimo-v2.6-pro",)
    assert panel.ui_openrouter_light_model.currentText().lower() in ("gpt-6-luna",)
    assert panel._get_openrouter_current_value("model") == "xiaomi/mimo-v2.6-pro"
    assert panel._get_openrouter_current_value("light_model") == "openai/gpt-6-luna"

    # Modify and save
    panel.ui_openrouter_api_key.setText("sk-or-updated-key")
    panel.save_settings()

    assert sm.get("core.api-provider") == "openrouter"
    assert sm.get_env("OPENROUTER_API_KEY") == "sk-or-updated-key"
    panel.deleteLater()


def test_orchestrator_openrouter_worker_init(monkeypatch, tmp_path):
    from core.orchestrator import AmityOrchestrator
    from core.openrouter_worker import OpenRouterWorker

    agent_dir = tmp_path / "agents" / "test_or_orch_agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(agent_dir))

    sm = SettingsManager(agent_id="test_or_orch_agent")
    sm.set("core.api-provider", "openrouter")
    sm.set("core.first-run", False)
    sm.set_env("OPENROUTER_API_KEY", "sk-or-orch-key")

    orch = AmityOrchestrator.__new__(AmityOrchestrator)
    orch.agent_id = "test_or_orch_agent"
    orch.settings_manager = sm
    orch.gemini_worker = None
    orch.is_paused = False
    orch.system_prompt = "Test prompt"

    orch.cerebrum = MagicMock()
    orch.cerebrum.get_all_tool_declarations.return_value = []
    orch.handle_gemini_thought = MagicMock()
    orch.add_fatigue = MagicMock()
    orch.handle_gemini_speech = MagicMock()
    orch.handle_gemini_error = MagicMock()

    orch.init_worker()

    assert isinstance(orch.gemini_worker, OpenRouterWorker)
    assert orch.gemini_worker.api_key == "sk-or-orch-key"
    assert orch.gemini_worker.current_model == "MiMo-V2.6-Pro"


def test_fetch_available_models_mocked():
    fake_json = json.dumps({
        "data": [
            {"id": "openai/gpt-4o"},
            {"id": "anthropic/claude-3.7-sonnet"},
            {"id": "xiaomi/mimo-v2.6-pro"}
        ]
    }).encode("utf-8")

    mock_resp = MagicMock()
    mock_resp.read.return_value = fake_json
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        models = OpenRouterWorker.fetch_available_models()
        assert models == [
            "anthropic/claude-3.7-sonnet",
            "openai/gpt-4o",
            "xiaomi/mimo-v2.6-pro"
        ]


def test_settings_panel_fetch_openrouter_models_success(qapp, monkeypatch, tmp_path):
    from gui.settings_panel import SettingsPanelWidget
    import time
    import json

    test_default_file = tmp_path / "openrouter_models_default.json"
    test_cache_file = tmp_path / "openrouter_models_cache.json"
    monkeypatch.setattr("config.paths.get_default_openrouter_models_file", lambda: str(test_default_file))
    monkeypatch.setattr("config.paths.get_openrouter_models_cache_file", lambda: str(test_cache_file))

    panel = SettingsPanelWidget()
    fake_models = [
        "anthropic/claude-3.7-sonnet",
        "openai/gpt-4o",
        "xiaomi/mimo-v2.6-pro"
    ]

    with patch("core.openrouter_worker.OpenRouterWorker.fetch_available_models", return_value=fake_models):
        t = panel._fetch_openrouter_models()
        assert panel.ui_openrouter_fetch_btn.isEnabled() is False
        assert "Fetching models..." in panel.ui_openrouter_fetch_btn.text()

        if t:
            t.join(timeout=2.0)
        qapp.processEvents()

        assert panel.ui_openrouter_fetch_btn.isEnabled() is True
        assert "Fetch Available Models from OpenRouter" in panel.ui_openrouter_fetch_btn.text()
        assert "✓ Fetched 3 models" in panel.ui_openrouter_fetch_status.text()
        assert panel.ui_openrouter_provider.findText("xiaomi") >= 0
        assert panel.ui_openrouter_provider.findText("anthropic") >= 0
        assert panel.ui_openrouter_provider.findText("openai") >= 0
        assert panel.ui_openrouter_model.findText("mimo-v2.6-pro") >= 0 or panel.ui_openrouter_model.findText("MiMo-V2.6-Pro") >= 0
        assert panel.ui_openrouter_model.findText("Custom...") >= 0

        # Verify permanent caching on successful discovery
        assert test_cache_file.exists()
        assert test_default_file.exists()
        with open(test_cache_file, "r") as f:
            assert json.load(f) == fake_models
        with open(test_default_file, "r") as f:
            assert json.load(f) == fake_models

    panel.deleteLater()


def test_settings_panel_fetch_openrouter_models_error(qapp, monkeypatch, tmp_path):
    from gui.settings_panel import SettingsPanelWidget
    import time

    test_default_file = tmp_path / "openrouter_models_default_err.json"
    test_cache_file = tmp_path / "openrouter_models_cache_err.json"
    monkeypatch.setattr("config.paths.get_default_openrouter_models_file", lambda: str(test_default_file))
    monkeypatch.setattr("config.paths.get_openrouter_models_cache_file", lambda: str(test_cache_file))

    panel = SettingsPanelWidget()

    with patch("core.openrouter_worker.OpenRouterWorker.fetch_available_models", side_effect=RuntimeError("Connection timed out")):
        t = panel._fetch_openrouter_models()
        assert panel.ui_openrouter_fetch_btn.isEnabled() is False

        if t:
            t.join(timeout=2.0)
        qapp.processEvents()

        assert panel.ui_openrouter_fetch_btn.isEnabled() is True
        assert "Fetch Available Models from OpenRouter" in panel.ui_openrouter_fetch_btn.text()
        assert "❌ Error: Connection timed out" in panel.ui_openrouter_fetch_status.text()

        # Verify files were NOT written on error
        assert not test_cache_file.exists()
        assert not test_default_file.exists()

    panel.deleteLater()


def test_openrouter_provider_and_model_dropdown_filter(qapp, monkeypatch, tmp_path):
    from gui.settings_panel import SettingsPanelWidget

    agent_dir = tmp_path / "agents" / "test_filter_agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(agent_dir))

    panel = SettingsPanelWidget()
    sm = SettingsManager(agent_id="test_filter_agent")
    panel.settings = sm

    # 1. Filter primary model by switching provider to google
    panel.ui_openrouter_provider.setCurrentText("google")
    google_models = [panel.ui_openrouter_model.itemText(i) for i in range(panel.ui_openrouter_model.count())]
    assert "gemini-2.5-flash" in google_models
    assert "gemini-2.5-pro" in google_models
    assert "claude-3.7-sonnet" not in google_models
    assert "Custom..." in google_models

    # Select gemini-2.5-flash
    panel.ui_openrouter_model.setCurrentText("gemini-2.5-flash")
    assert panel.ui_openrouter_custom_model.isHidden()

    # 2. Filter light model by switching provider to anthropic
    panel.ui_openrouter_light_provider.setCurrentText("anthropic")
    anthropic_lights = [panel.ui_openrouter_light_model.itemText(i) for i in range(panel.ui_openrouter_light_model.count())]
    assert "claude-haiku-4.5" in anthropic_lights or "claude-3.5-haiku" in anthropic_lights
    assert "gpt-4o-mini" not in anthropic_lights
    assert "Custom..." in anthropic_lights

    # Select anthropic light model
    target_light = "claude-haiku-4.5" if "claude-haiku-4.5" in anthropic_lights else "claude-3.5-haiku"
    panel.ui_openrouter_light_model.setCurrentText(target_light)
    assert panel.ui_openrouter_custom_light_model.isHidden()

    # Save settings and verify full slugs are persisted
    panel._save_openrouter_model_controls()
    assert sm.get("core.openrouter.model") == "google/gemini-2.5-flash"
    assert sm.get("core.openrouter.light-model") == f"anthropic/{target_light}"

    # Reload into a fresh panel and verify both dropdowns restore correctly
    panel2 = SettingsPanelWidget()
    panel2.settings = sm
    panel2._load_openrouter_model_controls()

    assert panel2.ui_openrouter_provider.currentText() == "google"
    assert panel2.ui_openrouter_model.currentText() == "gemini-2.5-flash"
    assert panel2.ui_openrouter_light_provider.currentText() == "anthropic"
    assert panel2.ui_openrouter_light_model.currentText() == target_light

    panel.deleteLater()
    panel2.deleteLater()


def test_openrouter_dropdown_max_visible_items_and_scrollbars(qapp):
    from gui.settings_panel import SettingsPanelWidget
    from gui.theme import MODERN_SCROLLBAR_STYLE

    panel = SettingsPanelWidget()
    combos = [
        panel.ui_openrouter_provider,
        panel.ui_openrouter_model,
        panel.ui_openrouter_thinking_level,
        panel.ui_openrouter_light_provider,
        panel.ui_openrouter_light_model,
    ]

    for cb in combos:
        assert cb.maxVisibleItems() == 25
        assert cb.view().maximumHeight() <= 650
        v_sb = cb.view().verticalScrollBar()
        assert v_sb is not None
        # Verify scrollbar width matches modern 8px minimal scrollbar
        assert v_sb.sizeHint().width() == 8

    panel.deleteLater()


def test_prepopulated_openrouter_models_cache():
    from gui.settings_panel import SettingsPanelWidget
    from config import paths

    # Verify default models file exists and is populated
    default_file = paths.get_default_openrouter_models_file()
    assert os.path.exists(default_file)

    cached_models = SettingsPanelWidget._load_cached_openrouter_models()
    assert isinstance(cached_models, list)
    assert len(cached_models) >= 400
    assert "xiaomi/mimo-v2.6-pro" in cached_models
    assert "openai/gpt-6-luna" in cached_models


def test_openrouter_dropdown_no_scroller_buttons_and_scrollbar_policy(qapp):
    from gui.settings_panel import SettingsPanelWidget
    from PySide6.QtWidgets import QStyle, QStyleOptionComboBox
    from PySide6.QtCore import Qt

    panel = SettingsPanelWidget()
    cb = panel.ui_openrouter_model

    # 1. Verify application proxy style forces SH_ComboBox_Popup to 0
    opt = QStyleOptionComboBox()
    cb.initStyleOption(opt)
    assert cb.style().styleHint(QStyle.SH_ComboBox_Popup, opt, cb) == 0

    # 2. Verify scrollbar policy is ScrollBarAsNeeded (not ScrollBarAlwaysOff)
    assert cb.view().verticalScrollBarPolicy() == Qt.ScrollBarAsNeeded

    # 3. Verify no QComboBoxPrivateScroller widgets exist in the container
    container = cb.view().parent()
    if container:
        scroller_widgets = [
            c for c in container.children()
            if c.isWidgetType() and c is not cb.view() and c.metaObject().className() == "QComboBoxPrivateScroller"
        ]
        assert len(scroller_widgets) == 0

    panel.deleteLater()


def test_openrouter_worker_pdf_media_attachment(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    pdf_file = tmp_path / "manuscript.pdf"
    pdf_file.write_bytes(b"%PDF-1.5 test pdf content")

    with patch.object(SettingsManager, "get_env", return_value="sk-or-test-key-12345"):
        worker = OpenRouterWorker(agent_id="test_openrouter_agent")
        worker.start_session(system_instruction="Test system")

        worker.history.append({
            "role": "assistant",
            "content": "Reading manuscript",
            "tool_calls": [{
                "id": "call_media_read_1",
                "type": "function",
                "function": {"name": "Media_read", "arguments": "{}"}
            }]
        })

        worker.client.chat.completions.create = MagicMock(return_value=[])

        worker.send_function_response("Media_read", {
            "result": "Attached media file",
            "media": [str(pdf_file)]
        })

        assert len(worker.history) >= 3
        media_msg = worker.history[2]
        assert media_msg["role"] == "user"
        content_blocks = media_msg["content"]
        assert len(content_blocks) == 2
        assert content_blocks[0]["type"] == "text"
        assert "[Attached media from tool execution]:" in content_blocks[0]["text"]
        assert content_blocks[1]["type"] == "file"
        assert content_blocks[1]["file"]["filename"] == "manuscript.pdf"
        assert content_blocks[1]["file"]["file_data"].startswith("data:application/pdf;base64,")


def test_openrouter_worker_image_and_audio_media_attachment(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    img_file = tmp_path / "photo.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)

    audio_file = tmp_path / "speech.wav"
    audio_file.write_bytes(b"RIFF" + b"\x00" * 30)

    with patch.object(SettingsManager, "get_env", return_value="sk-or-test-key-12345"):
        worker = OpenRouterWorker(agent_id="test_openrouter_agent")
        worker.start_session(system_instruction="Test system")
        worker.local_stt = MagicMock()
        worker.local_stt.transcribe.return_value = "Transcribed speech content"

        worker.history.append({
            "role": "assistant",
            "content": "Reading media",
            "tool_calls": [{
                "id": "call_media_read_2",
                "type": "function",
                "function": {"name": "Media_read", "arguments": "{}"}
            }]
        })

        worker.client.chat.completions.create = MagicMock(return_value=[])

        worker.send_function_response("Media_read", {
            "result": "Attached media files",
            "media": [str(img_file), str(audio_file)]
        })

        assert len(worker.history) >= 3
        media_msg = worker.history[2]
        content_blocks = media_msg["content"]
        assert len(content_blocks) == 3
        assert content_blocks[1]["type"] == "image_url"
        assert content_blocks[1]["image_url"]["url"].startswith("data:image/png;base64,")
        assert content_blocks[2]["type"] == "input_audio"
        assert content_blocks[2]["input_audio"]["format"] == "wav"
        assert len(content_blocks[2]["input_audio"]["data"]) > 0


def test_openrouter_worker_unsupported_media_error_handling(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    bin_file = tmp_path / "data.xyz_unknown"
    bin_file.write_bytes(b"\x00\x01\x02\x03")

    with patch.object(SettingsManager, "get_env", return_value="sk-or-test-key-12345"):
        worker = OpenRouterWorker(agent_id="test_openrouter_agent")
        worker.start_session(system_instruction="Test system")

        worker.history.append({
            "role": "assistant",
            "content": "Reading data",
            "tool_calls": [{
                "id": "call_media_read_3",
                "type": "function",
                "function": {"name": "Media_read", "arguments": "{}"}
            }]
        })

        worker.client.chat.completions.create = MagicMock(return_value=[])

        worker.send_function_response("Media_read", {
            "result": "Attached unknown file",
            "media": [str(bin_file)]
        })

        media_msg = worker.history[2]
        content_blocks = media_msg["content"]
        assert len(content_blocks) == 2
        assert content_blocks[1]["type"] == "text"
        assert "[Error attaching media" in content_blocks[1]["text"]
        assert "Unsupported media format" in content_blocks[1]["text"]



