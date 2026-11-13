import os
import sys
import pytest
from unittest.mock import MagicMock, patch

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PySide6.QtWidgets import QApplication
from gui.settings_panel import (
    PROVIDER_MODELS,
    THINKING_LEVELS,
    SettingsPanelWidget
)
from core.settings_manager import SettingsManager


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_provider_models_structure():
    """Verify PROVIDER_MODELS structure, keys, and Custom... terminating entry."""
    expected_providers = ["gemini", "claude", "chatgpt", "deepseek", "openrouter", "antigravity"]
    for prov in expected_providers:
        assert prov in PROVIDER_MODELS, f"Provider {prov} missing in PROVIDER_MODELS"
        assert "models" in PROVIDER_MODELS[prov]
        assert "light_models" in PROVIDER_MODELS[prov]
        assert len(PROVIDER_MODELS[prov]["models"]) > 1
        assert len(PROVIDER_MODELS[prov]["light_models"]) > 1

        # The last option must strictly be "Custom..." for future-proofing
        assert PROVIDER_MODELS[prov]["models"][-1] == "Custom..."
        assert PROVIDER_MODELS[prov]["light_models"][-1] == "Custom..."


def test_thinking_levels_structure():
    """Verify thinking levels are exactly low, medium, high, max with low as default."""
    assert THINKING_LEVELS == ["low", "medium", "high", "max"]


def test_provider_gui_controls_exist(qapp, monkeypatch, tmp_path):
    """Verify supported GUI providers have primary model, thinking level, and light model controls with tips."""
    agent_dir = tmp_path / "test_gui_agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(agent_dir))

    sm = SettingsManager(agent_id="test_gui_agent")
    widget = SettingsPanelWidget()
    widget.load_settings(settings_manager=sm)

    expected_providers = ["gemini", "claude", "chatgpt", "deepseek", "openrouter"]
    for prov in expected_providers:
        model_combo = getattr(widget, f"ui_{prov}_model", None)
        model_custom = getattr(widget, f"ui_{prov}_custom_model", None)
        thinking_combo = getattr(widget, f"ui_{prov}_thinking_level", None)
        thinking_tip = getattr(widget, f"ui_{prov}_thinking_tip", None)
        light_combo = getattr(widget, f"ui_{prov}_light_model", None)
        light_custom = getattr(widget, f"ui_{prov}_custom_light_model", None)
        light_tip = getattr(widget, f"ui_{prov}_light_model_tip", None)
        fetch_btn = getattr(widget, f"ui_{prov}_fetch_btn", None)
        fetch_status = getattr(widget, f"ui_{prov}_fetch_status", None)

        assert model_combo is not None, f"Missing model combo for {prov}"
        assert model_custom is not None, f"Missing model custom line edit for {prov}"
        assert fetch_btn is not None, f"Missing fetch button for {prov}"
        assert fetch_status is not None, f"Missing fetch status label for {prov}"
        assert thinking_combo is not None, f"Missing thinking combo for {prov}"
        assert thinking_tip is not None, f"Missing thinking tip for {prov}"
        assert light_combo is not None, f"Missing light model combo for {prov}"
        assert light_custom is not None, f"Missing light model custom line edit for {prov}"
        assert light_tip is not None, f"Missing light model tip for {prov}"

        # Verify tip texts match required guidance
        assert "Low thinking tends to perform better" in thinking_tip.text()
        assert "audit trails" in thinking_tip.text()

        assert "The light model is used for low token mode" in light_tip.text()
        assert "automatic fallback" in light_tip.text()

    # Antigravity is removed from GUI
    assert getattr(widget, "ui_antigravity_model", None) is None
    assert getattr(widget, "ui_antigravity_custom_model", None) is None
    assert getattr(widget, "ui_antigravity_thinking_level", None) is None
    assert getattr(widget, "ui_antigravity_light_model", None) is None


def test_custom_model_visibility_toggle(qapp, monkeypatch, tmp_path):
    """Verify that selecting Custom... reveals the custom QLineEdit and selecting a preset hides it."""
    agent_dir = tmp_path / "test_gui_agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(agent_dir))

    sm = SettingsManager(agent_id="test_gui_agent")
    widget = SettingsPanelWidget()
    widget.load_settings(settings_manager=sm)

    combo = widget.ui_gemini_model
    custom_edit = widget.ui_gemini_custom_model

    # Select a preset
    combo.setCurrentIndex(0)
    assert custom_edit.isHidden()

    # Select "Custom..."
    custom_idx = combo.findText("Custom...")
    assert custom_idx >= 0
    combo.setCurrentIndex(custom_idx)
    assert not custom_edit.isHidden()

    # Switch back to preset
    combo.setCurrentIndex(0)
    assert custom_edit.isHidden()


def test_load_and_save_preset_settings(qapp, monkeypatch, tmp_path):
    """Verify loading and saving preset model selections and thinking levels."""
    agent_dir = tmp_path / "test_gui_agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(agent_dir))

    sm = SettingsManager(agent_id="test_gui_agent")
    sm.set("core.gemini.model", "gemini-2.5-pro")
    sm.set("core.gemini.thinking-level", "high")
    sm.set("core.gemini.light-model", "gemini-2.5-flash")
    sm.save()

    widget = SettingsPanelWidget()
    widget.load_settings(settings_manager=sm)

    assert widget.ui_gemini_model.currentText() == "gemini-2.5-pro"
    assert widget.ui_gemini_custom_model.isHidden()
    assert widget.ui_gemini_thinking_level.currentText() == "high"
    assert widget.ui_gemini_light_model.currentText() == "gemini-2.5-flash"
    assert widget.ui_gemini_custom_light_model.isHidden()

    # Modify through GUI controls and save
    widget.ui_gemini_model.setCurrentText("gemini-3.8-flash")
    widget.ui_gemini_thinking_level.setCurrentText("medium")
    widget.ui_gemini_light_model.setCurrentText("gemini-3.5-flash-lite")
    widget.save_settings()

    # Verify persisted values in SettingsManager
    assert sm.get("core.gemini.model") == "gemini-3.8-flash"
    assert sm.get("core.gemini.thinking-level") == "medium"
    assert sm.get("core.gemini.light-model") == "gemini-3.5-flash-lite"
    # Legacy lists should also be synchronized
    assert sm.get("core.gemini.gemini-models")[0] == "gemini-3.8-flash"
    assert sm.get("core.gemini.light-models")[0] == "gemini-3.5-flash-lite"


def test_load_and_save_custom_model_settings(qapp, monkeypatch, tmp_path):
    """Verify loading and saving custom model IDs not present in presets."""
    agent_dir = tmp_path / "test_gui_agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(agent_dir))

    sm = SettingsManager(agent_id="test_gui_agent")
    sm.set("core.claude.model", "claude-custom-future-99")
    sm.set("core.claude.thinking-level", "max")
    sm.set("core.claude.light-model", "claude-custom-mini-99")
    sm.save()

    widget = SettingsPanelWidget()
    widget.load_settings(settings_manager=sm)

    # Combo box should select "Custom..." and custom input should contain value and be visible
    assert widget.ui_claude_model.currentText() == "Custom..."
    assert widget.ui_claude_custom_model.text() == "claude-custom-future-99"
    assert not widget.ui_claude_custom_model.isHidden()
    assert widget.ui_claude_thinking_level.currentText() == "max"
    assert widget.ui_claude_light_model.currentText() == "Custom..."
    assert widget.ui_claude_custom_light_model.text() == "claude-custom-mini-99"
    assert not widget.ui_claude_custom_light_model.isHidden()

    # Modify custom text and save
    widget.ui_claude_custom_model.setText("claude-custom-v2")
    widget.ui_claude_thinking_level.setCurrentText("low")
    widget.ui_claude_custom_light_model.setText("claude-custom-light-v2")
    widget.save_settings()

    assert sm.get("core.claude.model") == "claude-custom-v2"
    assert sm.get("core.claude.thinking-level") == "low"
    assert sm.get("core.claude.light-model") == "claude-custom-light-v2"


def test_provider_refresh_buttons_styling_and_tooltips(qapp, monkeypatch, tmp_path):
    """Verify refresh button sizing, iconBtn objectName, and standard Open Amity tooltip across all providers."""
    agent_dir = tmp_path / "test_gui_agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(agent_dir))

    sm = SettingsManager(agent_id="test_gui_agent")
    widget = SettingsPanelWidget()
    widget.load_settings(settings_manager=sm)

    expected_providers = {
        "openrouter": "OpenRouter",
        "gemini": "Gemini",
        "claude": "Claude",
        "chatgpt": "OpenAI / ChatGPT",
        "deepseek": "DeepSeek"
    }

    for prov, display_name in expected_providers.items():
        btn = getattr(widget, f"ui_{prov}_fetch_btn", None)
        assert btn is not None, f"Missing fetch button for {prov}"
        assert btn.text() == "🔄", f"Button for {prov} does not have refresh icon"
        assert btn.objectName() == "iconBtn"
        assert btn.width() == 32 and btn.height() == 32
        tooltip = btn.toolTip()
        assert "Fetch and refresh the list of available models from" in tooltip
        assert display_name in tooltip


def test_provider_fetch_missing_api_key(qapp, monkeypatch, tmp_path):
    """Verify that clicking refresh without an API key presents a helpful inline warning."""
    agent_dir = tmp_path / "test_gui_agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(agent_dir))

    sm = SettingsManager(agent_id="test_gui_agent")
    widget = SettingsPanelWidget()
    widget.load_settings(settings_manager=sm)

    # For providers requiring keys, verify missing key message
    for prov in ["gemini", "claude", "chatgpt", "deepseek"]:
        key_input = getattr(widget, f"ui_{prov}_api_key", None)
        if key_input:
            key_input.setText("")
        widget._fetch_provider_models(prov)
        status = getattr(widget, f"ui_{prov}_fetch_status")
        assert "Please enter an API key first" in status.text()
        btn = getattr(widget, f"ui_{prov}_fetch_btn")
        assert btn.isEnabled()
        assert btn.text() == "🔄"


def test_provider_fetch_models_success_and_caching(qapp, monkeypatch, tmp_path):
    """Verify that successfully fetched models update the combobox, preserve selection, and save to cache."""
    agent_dir = tmp_path / "test_gui_agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(agent_dir))
    monkeypatch.setattr("config.paths.get_app_data_dir", lambda: str(tmp_path))

    sm = SettingsManager(agent_id="test_gui_agent")
    widget = SettingsPanelWidget()
    widget.load_settings(settings_manager=sm)

    # Simulate fetched models signal
    new_models = ["gemini-4.0-ultra", "gemini-4.0-flash"]
    widget._on_provider_models_fetched("gemini", new_models, None)

    combo = widget.ui_gemini_model
    items = [combo.itemText(i) for i in range(combo.count())]
    assert "gemini-4.0-ultra" in items
    assert "gemini-4.0-flash" in items
    assert items[-1] == "Custom..."
    assert "✓ Fetched 2 models." in widget.ui_gemini_fetch_status.text()

    # Check cache file written to disk
    import json
    cache_file = tmp_path / "gemini_models.json"
    assert cache_file.exists()
    with open(cache_file, "r") as f:
        cached = json.load(f)
    assert "gemini-4.0-ultra" in cached
    assert "gemini-4.0-flash" in cached


def test_worker_fetch_available_models_methods():
    """Verify that worker classes expose fetch_available_models and parse responses correctly."""
    from core.gemini_worker import GeminiWorker
    from core.claude_worker import ClaudeWorker
    from core.chatgpt_worker import ChatGptWorker
    from core.deepseek_worker import DeepSeekWorker

    # 1. Missing API key raises ValueError
    with pytest.raises(ValueError, match="Gemini API key is required"):
        GeminiWorker.fetch_available_models("")
    with pytest.raises(ValueError, match="Claude API key is required"):
        ClaudeWorker.fetch_available_models("")
    with pytest.raises(ValueError, match="OpenAI API key is required"):
        ChatGptWorker.fetch_available_models("")
    with pytest.raises(ValueError, match="DeepSeek API key is required"):
        DeepSeekWorker.fetch_available_models("")

    # 2. Mocked Gemini fetch
    with patch("google.genai.Client") as mock_client:
        mock_m1 = MagicMock(name="models/gemini-2.5-flash", supported_actions=["generateContent"])
        mock_m1.name = "models/gemini-2.5-flash"
        mock_m2 = MagicMock(name="models/text-embedding-004", supported_actions=["embedContent"])
        mock_m2.name = "models/text-embedding-004"
        mock_client.return_value.models.list.return_value = [mock_m1, mock_m2]

        models = GeminiWorker.fetch_available_models("test-key")
        assert models == ["gemini-2.5-flash"]

    # 3. Mocked Claude fetch
    with patch("anthropic.Anthropic") as mock_client:
        mock_item1 = MagicMock(id="claude-3-7-sonnet-20250219")
        mock_page = MagicMock(data=[mock_item1])
        mock_client.return_value.models.list.return_value = mock_page

        models = ClaudeWorker.fetch_available_models("test-key")
        assert models == ["claude-3-7-sonnet-20250219"]

    # 4. Mocked ChatGPT fetch
    with patch("openai.OpenAI") as mock_client:
        mock_m1 = MagicMock(id="gpt-4o")
        mock_m2 = MagicMock(id="whisper-1")
        mock_m3 = MagicMock(id="text-embedding-3-small")
        mock_m4 = MagicMock(id="o3-mini")
        mock_client.return_value.models.list.return_value = [mock_m1, mock_m2, mock_m3, mock_m4]

        models = ChatGptWorker.fetch_available_models("test-key")
        assert models == ["gpt-4o", "o3-mini"]

    # 5. Mocked DeepSeek fetch
    with patch("openai.OpenAI") as mock_client:
        mock_m1 = MagicMock(id="deepseek-chat")
        mock_m2 = MagicMock(id="deepseek-reasoner")
        mock_client.return_value.models.list.return_value = [mock_m1, mock_m2]

        models = DeepSeekWorker.fetch_available_models("test-key")
        assert models == ["deepseek-chat", "deepseek-reasoner"]
