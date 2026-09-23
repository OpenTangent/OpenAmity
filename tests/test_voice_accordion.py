import os
import sys
import pytest
from PySide6.QtWidgets import QApplication

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.config_manager import ConfigManager
from core.settings_manager import SettingsManager
from gui.settings_panel import SettingsPanelWidget, AccordionCard, VoiceAccordionCard


@pytest.fixture
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


def test_voice_accordion_initial_state(qapp):
    panel = SettingsPanelWidget()
    assert hasattr(panel, "voice_cards")
    expected_voices = ["piper", "gemini"]
    for vid in expected_voices:
        assert vid in panel.voice_cards
        assert isinstance(panel.voice_cards[vid], VoiceAccordionCard)
        assert isinstance(panel.voice_cards[vid], AccordionCard)

    # Initial default is Piper selected
    assert panel.voice_cards["piper"].is_expanded is True
    assert not panel.voice_cards["piper"].header_widget.badge.isHidden()
    assert not panel.voice_cards["piper"].body_widget.isHidden()
    assert panel.ui_use_piper_tts.isChecked() is True

    # Gemini card must be collapsed down to a single line
    card_gemini = panel.voice_cards["gemini"]
    assert card_gemini.is_expanded is False
    assert card_gemini.header_widget.badge.isHidden()
    assert card_gemini.body_widget.isHidden()
    assert panel.ui_use_gemini_tts.isChecked() is False

    panel.deleteLater()


def test_voice_accordion_click_to_select(qapp):
    panel = SettingsPanelWidget()

    # Click on Gemini card
    panel.voice_cards["gemini"].clicked.emit()

    # Gemini must now be the ONLY uncollapsed card with checkmark badge visible
    assert panel.voice_cards["gemini"].is_expanded is True
    assert not panel.voice_cards["gemini"].header_widget.badge.isHidden()
    assert not panel.voice_cards["gemini"].body_widget.isHidden()
    assert panel.ui_use_gemini_tts.isChecked() is True

    # Piper must have collapsed
    assert panel.voice_cards["piper"].is_expanded is False
    assert panel.voice_cards["piper"].header_widget.badge.isHidden()
    assert panel.voice_cards["piper"].body_widget.isHidden()
    assert panel.ui_use_piper_tts.isChecked() is False

    # Click back to Piper card
    panel.voice_cards["piper"].clicked.emit()

    assert panel.voice_cards["piper"].is_expanded is True
    assert not panel.voice_cards["piper"].header_widget.badge.isHidden()
    assert not panel.voice_cards["piper"].body_widget.isHidden()
    assert panel.ui_use_piper_tts.isChecked() is True

    assert panel.voice_cards["gemini"].is_expanded is False
    assert panel.voice_cards["gemini"].header_widget.badge.isHidden()
    assert panel.voice_cards["gemini"].body_widget.isHidden()
    assert panel.ui_use_gemini_tts.isChecked() is False

    panel.deleteLater()


def test_voice_accordion_load_settings_synchronization(qapp, tmp_path, monkeypatch):
    agent_dir = tmp_path / "test_voice_agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    config_file = str(tmp_path / "config.json")
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(agent_dir))

    cm = ConfigManager(config_file=config_file)
    sm = SettingsManager(agent_id="test_voice_agent")
    sm.set("core.tts.provider", "gemini")
    sm.set("core.tts.piper.prefer-piper", False)
    sm.save()

    panel = SettingsPanelWidget()
    panel.config = cm
    panel.load_settings(settings_manager=sm)

    # Gemini must be expanded, Piper collapsed
    assert panel.voice_cards["gemini"].is_expanded is True
    assert not panel.voice_cards["gemini"].header_widget.badge.isHidden()
    assert panel.ui_use_gemini_tts.isChecked() is True

    assert panel.voice_cards["piper"].is_expanded is False
    assert panel.voice_cards["piper"].header_widget.badge.isHidden()
    assert panel.ui_use_piper_tts.isChecked() is False

    # Switch settings to Piper and reload
    sm.set("core.tts.provider", "piper")
    sm.set("core.tts.piper.prefer-piper", True)
    sm.save()

    panel.load_settings(settings_manager=sm)
    assert panel.voice_cards["piper"].is_expanded is True
    assert not panel.voice_cards["piper"].header_widget.badge.isHidden()
    assert panel.ui_use_piper_tts.isChecked() is True

    assert panel.voice_cards["gemini"].is_expanded is False
    assert panel.voice_cards["gemini"].header_widget.badge.isHidden()
    assert panel.ui_use_gemini_tts.isChecked() is False

    panel.deleteLater()


def test_voice_accordion_radio_toggled(qapp):
    panel = SettingsPanelWidget()

    # Programmatically checking radio button should synchronize accordion state
    panel.ui_use_gemini_tts.setChecked(True)
    assert panel.voice_cards["gemini"].is_expanded is True
    assert not panel.voice_cards["gemini"].header_widget.badge.isHidden()
    assert panel.voice_cards["piper"].is_expanded is False
    assert panel.voice_cards["piper"].header_widget.badge.isHidden()

    panel.ui_use_piper_tts.setChecked(True)
    assert panel.voice_cards["piper"].is_expanded is True
    assert not panel.voice_cards["piper"].header_widget.badge.isHidden()
    assert panel.voice_cards["gemini"].is_expanded is False
    assert panel.voice_cards["gemini"].header_widget.badge.isHidden()

    panel.deleteLater()


def test_voice_accordion_save_settings(qapp, tmp_path, monkeypatch):
    agent_dir = tmp_path / "test_voice_save_agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    config_file = str(tmp_path / "config.json")
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(agent_dir))

    cm = ConfigManager(config_file=config_file)
    sm = SettingsManager(agent_id="test_voice_save_agent")

    panel = SettingsPanelWidget()
    panel.config = cm
    panel.load_settings(settings_manager=sm)

    # Switch to Gemini via accordion click
    panel.voice_cards["gemini"].clicked.emit()
    panel.ui_gemini_tts_api_key.setText("test-gemini-key")
    panel.save_settings()

    assert sm.get("core.tts.provider") == "gemini"
    assert sm.get("core.tts.piper.prefer-piper") is False
    assert sm.get_env("GEMINI_TTS_API_KEY") == "test-gemini-key"

    # Switch to Piper via accordion click
    panel.voice_cards["piper"].clicked.emit()
    panel.save_settings()

    assert sm.get("core.tts.provider") == "piper"
    assert sm.get("core.tts.piper.prefer-piper") is True

    panel.deleteLater()
