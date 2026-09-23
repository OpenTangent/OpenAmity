import os
import sys
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.config_manager import ConfigManager
from core.settings_manager import SettingsManager
from gui.settings_panel import SettingsPanelWidget, ProviderAccordionCard


@pytest.fixture
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


def test_provider_accordion_initial_state(qapp):
    panel = SettingsPanelWidget()
    assert hasattr(panel, "provider_cards")
    expected_providers = ["gemini", "claude", "chatgpt", "deepseek"]
    assert "antigravity" not in panel.provider_cards
    for pid in expected_providers:
        assert pid in panel.provider_cards
        assert isinstance(panel.provider_cards[pid], ProviderAccordionCard)

    # Initial default is Gemini selected
    assert panel.provider_cards["gemini"].is_expanded is True
    assert not panel.provider_cards["gemini"].header_widget.badge.isHidden()
    assert not panel.provider_cards["gemini"].body_widget.isHidden()
    assert panel.ui_use_gemini_api.isChecked() is True

    # Other cards must be collapsed down to a single line
    for pid in ["claude", "chatgpt", "deepseek"]:
        card = panel.provider_cards[pid]
        assert card.is_expanded is False
        assert card.header_widget.badge.isHidden()
        assert card.body_widget.isHidden()

    panel.deleteLater()


def test_provider_accordion_click_to_select(qapp):
    panel = SettingsPanelWidget()

    # Click on Claude card
    panel.provider_cards["claude"].clicked.emit()

    # Claude must now be the ONLY uncollapsed card with checkmark badge visible
    assert panel.provider_cards["claude"].is_expanded is True
    assert not panel.provider_cards["claude"].header_widget.badge.isHidden()
    assert not panel.provider_cards["claude"].body_widget.isHidden()
    assert panel.ui_use_claude_api.isChecked() is True

    # Gemini must have collapsed
    assert panel.provider_cards["gemini"].is_expanded is False
    assert panel.provider_cards["gemini"].header_widget.badge.isHidden()
    assert panel.provider_cards["gemini"].body_widget.isHidden()
    assert panel.ui_use_gemini_api.isChecked() is False

    # Click on DeepSeek card
    panel.provider_cards["deepseek"].clicked.emit()

    assert panel.provider_cards["deepseek"].is_expanded is True
    assert not panel.provider_cards["deepseek"].header_widget.badge.isHidden()
    assert not panel.provider_cards["deepseek"].body_widget.isHidden()
    assert panel.ui_use_deepseek_api.isChecked() is True

    assert panel.provider_cards["claude"].is_expanded is False
    assert panel.provider_cards["claude"].header_widget.badge.isHidden()
    assert panel.provider_cards["claude"].body_widget.isHidden()

    panel.deleteLater()


def test_provider_accordion_load_settings_synchronization(qapp, tmp_path, monkeypatch):
    agent_dir = tmp_path / "test_accordion_agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    config_file = str(tmp_path / "config.json")
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(agent_dir))

    cm = ConfigManager(config_file=config_file)
    sm = SettingsManager(agent_id="test_accordion_agent")
    sm.set("core.api-provider", "chatgpt")
    sm.save()

    panel = SettingsPanelWidget()
    panel.config = cm
    panel.load_settings(settings_manager=sm)

    # ChatGPT must be expanded, all others collapsed
    assert panel.provider_cards["chatgpt"].is_expanded is True
    assert not panel.provider_cards["chatgpt"].header_widget.badge.isHidden()
    assert panel.ui_use_chatgpt_api.isChecked() is True

    for pid in ["gemini", "claude", "deepseek"]:
        assert panel.provider_cards[pid].is_expanded is False
        assert panel.provider_cards[pid].header_widget.badge.isHidden()

    # Now load settings with Antigravity mode (configured in settings file)
    sm.set("core.antigravity.agy-mode", True)
    sm.save()
    panel.load_settings(settings_manager=sm)

    # Antigravity card is removed from GUI, but hidden radio state is preserved
    assert "antigravity" not in panel.provider_cards
    assert panel.ui_agy_mode.isChecked() is True
    assert panel.provider_cards["chatgpt"].is_expanded is False
    for pid in ["gemini", "claude", "chatgpt", "deepseek"]:
        assert panel.provider_cards[pid].is_expanded is False

    panel.deleteLater()


def test_provider_accordion_save_settings(qapp, tmp_path, monkeypatch):
    agent_dir = tmp_path / "test_accordion_save_agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    config_file = str(tmp_path / "config.json")
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(agent_dir))

    cm = ConfigManager(config_file=config_file)
    sm = SettingsManager(agent_id="test_accordion_save_agent")
    sm.set("core.api-provider", "gemini")
    sm.save()

    panel = SettingsPanelWidget()
    panel.config = cm
    panel.load_settings(settings_manager=sm)

    # Switch to Claude via accordion
    panel.provider_cards["claude"].clicked.emit()
    panel.ui_claude_api_key.setText("sk-ant-accordion-key")
    panel.save_settings()

    assert sm.get("core.api-provider") == "claude"
    assert sm.get_env("CLAUDE_API_KEY") == "sk-ant-accordion-key"

    panel.deleteLater()
