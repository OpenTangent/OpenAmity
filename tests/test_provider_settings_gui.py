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
    expected_providers = ["gemini", "claude", "chatgpt", "deepseek", "antigravity"]
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

    expected_providers = ["gemini", "claude", "chatgpt", "deepseek"]
    for prov in expected_providers:
        model_combo = getattr(widget, f"ui_{prov}_model", None)
        model_custom = getattr(widget, f"ui_{prov}_custom_model", None)
        thinking_combo = getattr(widget, f"ui_{prov}_thinking_level", None)
        thinking_tip = getattr(widget, f"ui_{prov}_thinking_tip", None)
        light_combo = getattr(widget, f"ui_{prov}_light_model", None)
        light_custom = getattr(widget, f"ui_{prov}_custom_light_model", None)
        light_tip = getattr(widget, f"ui_{prov}_light_model_tip", None)

        assert model_combo is not None, f"Missing model combo for {prov}"
        assert model_custom is not None, f"Missing model custom line edit for {prov}"
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
