import os
import sys
import json
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from gui.settings_panel import SettingsPanelWidget, MediaAccordionCard
from core.settings_manager import SettingsManager


@pytest.fixture(scope="session")
def qapp():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication.instance()
    if not app:
        app = QApplication(["-platform", "offscreen"])
    return app


def test_media_accordion_card_structure_and_behavior(qapp):
    card = MediaAccordionCard("test_media", "Test Media Setup")
    assert card.card_id == "test_media"
    assert card.is_expanded is False
    assert card.header_widget.badge.isHidden()
    assert card.body_widget.isHidden()

    # Expand
    card.set_expanded(True)
    assert card.is_expanded is True
    assert not card.header_widget.badge.isHidden()
    assert not card.body_widget.isHidden()
    assert card.header_widget.cursor().shape() == Qt.PointingHandCursor
    assert "Click header to deselect" in card.header_widget.toolTip()

    # Collapse
    card.set_expanded(False)
    assert card.is_expanded is False
    assert card.header_widget.badge.isHidden()
    assert card.body_widget.isHidden()
    card.deleteLater()


def test_media_provider_initial_state_in_settings_panel(qapp):
    panel = SettingsPanelWidget()
    assert hasattr(panel, "media_cards")
    assert len(panel.media_cards) == 2
    assert "openrouter" in panel.media_cards
    assert "gemini" in panel.media_cards

    # In stack, Media Provider is index 5
    media_widget = panel.stack.widget(5)
    assert media_widget is not None

    # Initial state: zero provider selected (both cards collapsed)
    assert panel.selected_media_provider == ""
    assert panel.media_cards["openrouter"].is_expanded is False
    assert panel.media_cards["gemini"].is_expanded is False

    panel.deleteLater()


def test_media_provider_accordion_zero_or_one_selection(qapp):
    panel = SettingsPanelWidget()

    # Click Gemini card to select
    panel.media_cards["gemini"].clicked.emit()
    assert panel.selected_media_provider == "gemini"
    assert panel.media_cards["gemini"].is_expanded is True
    assert panel.media_cards["openrouter"].is_expanded is False

    # Click Gemini header again -> should collapse and select NO provider (zero selected)
    panel.media_cards["gemini"].clicked.emit()
    assert panel.selected_media_provider == ""
    assert panel.media_cards["gemini"].is_expanded is False
    assert panel.media_cards["openrouter"].is_expanded is False

    # Click OpenRouter card -> selects and expands OpenRouter
    panel.media_cards["openrouter"].clicked.emit()
    assert panel.selected_media_provider == "openrouter"
    assert panel.media_cards["openrouter"].is_expanded is True
    assert panel.media_cards["gemini"].is_expanded is False

    # Click Gemini card -> switches selection to Gemini, collapsing OpenRouter
    panel.media_cards["gemini"].clicked.emit()
    assert panel.selected_media_provider == "gemini"
    assert panel.media_cards["gemini"].is_expanded is True
    assert panel.media_cards["openrouter"].is_expanded is False

    # Click Gemini header -> collapses back to zero providers
    panel.media_cards["gemini"].clicked.emit()
    assert panel.selected_media_provider == ""
    assert panel.media_cards["gemini"].is_expanded is False
    assert panel.media_cards["openrouter"].is_expanded is False

    panel.deleteLater()


def test_media_provider_api_key_autofill(qapp, tmp_path, monkeypatch):
    agent_dir = tmp_path / "test_media_agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(agent_dir))

    panel = SettingsPanelWidget()

    # Typing into Cognitive Provider Gemini key auto-fills Media Provider and Agent Voice Gemini keys
    panel.ui_gemini_api_key.setText("AIzaSy-TestKey123")
    assert panel.ui_media_gemini_api_key.text() == "AIzaSy-TestKey123"
    assert panel.ui_gemini_tts_api_key.text() == "AIzaSy-TestKey123"

    # Typing into Cognitive Provider OpenRouter key auto-fills Media Provider OpenRouter key
    panel.ui_openrouter_api_key.setText("sk-or-TestKey456")
    assert panel.ui_media_openrouter_api_key.text() == "sk-or-TestKey456"

    # Overriding Media key with custom value works and preserves custom value
    panel.ui_media_gemini_api_key.setText("AIzaSy-CustomMediaKey")
    assert panel.ui_media_gemini_api_key.text() == "AIzaSy-CustomMediaKey"

    panel.deleteLater()


def test_media_provider_step_transition_autofill(qapp):
    panel = SettingsPanelWidget()
    panel.ui_gemini_api_key.setText("AIzaSy-StepKey1")
    panel.ui_openrouter_api_key.setText("sk-or-StepKey2")

    panel.ui_gemini_tts_api_key.clear()
    panel.ui_media_gemini_api_key.clear()
    panel.ui_media_openrouter_api_key.clear()

    # Navigate to Agent Voice (index 4) -> Gemini key copied
    panel.set_section(4)
    assert panel.ui_gemini_tts_api_key.text() == "AIzaSy-StepKey1"

    # Navigate to Media Provider (index 5) -> Gemini & OpenRouter keys copied
    panel.set_section(5)
    assert panel.ui_media_gemini_api_key.text() == "AIzaSy-StepKey1"
    assert panel.ui_media_openrouter_api_key.text() == "sk-or-StepKey2"

    panel.deleteLater()


def test_cognitive_provider_panel_order_and_voice_autofill(qapp, tmp_path, monkeypatch):
    panel = SettingsPanelWidget()

    # Verify panel order:
    # 0: Agent Presets
    # 1: Cognitive Provider
    # 2: Basic Agent Settings
    # 3: Agent Values and Goals
    # 4: Agent Voice
    # 5: Media Provider
    # 6: Social Accounts
    # 7: Other Agent Settings
    # 8: System Settings
    assert panel.stack.count() == 9
    from gui.settings_panel import ProviderAccordionCard
    # Cognitive Provider panel is at index 1
    widget_idx1 = panel.stack.widget(1)
    assert len(widget_idx1.findChildren(ProviderAccordionCard)) == 5

    # Check key copying from settings when opening Agent Voice and Media Provider
    agent_dir = tmp_path / "test_key_copy_agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(agent_dir))

    sm = SettingsManager(agent_id="test_key_copy_agent")
    sm.set_env("GEMINI_API_KEY", "env-gemini-key-999")
    sm.set_env("OPENROUTER_API_KEY", "env-openrouter-key-888")
    sm.save()

    panel.load_settings(sm)

    # Gemini key should be copied to Agent Voice
    assert panel.ui_gemini_tts_api_key.text() == "env-gemini-key-999"
    # Gemini key and OpenRouter key should be copied to Media Provider
    assert panel.ui_media_gemini_api_key.text() == "env-gemini-key-999"
    assert panel.ui_media_openrouter_api_key.text() == "env-openrouter-key-888"

    # Clear and test set_section auto-fill for Voice (4) and Media (5)
    panel.ui_gemini_tts_api_key.clear()
    panel.ui_media_gemini_api_key.clear()
    panel.ui_media_openrouter_api_key.clear()

    panel.set_section(4)
    assert panel.ui_gemini_tts_api_key.text() == "env-gemini-key-999"

    panel.set_section(5)
    assert panel.ui_media_gemini_api_key.text() == "env-gemini-key-999"
    assert panel.ui_media_openrouter_api_key.text() == "env-openrouter-key-888"

    panel.deleteLater()


def test_media_provider_load_and_save_settings(qapp, tmp_path, monkeypatch):
    agent_dir = tmp_path / "test_media_save_agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(agent_dir))

    sm = SettingsManager(agent_id="test_media_save_agent")
    sm.set("core.media.image-provider", "gemini")
    sm.set("core.media.gemini.model", "gemini-3.1-flash-image")
    sm.set_env("GEMINI_IMAGE_API_KEY", "test-gemini-image-key")
    sm.save()

    panel = SettingsPanelWidget()
    panel.load_settings(settings_manager=sm)

    # Gemini card should be expanded
    assert panel.selected_media_provider == "gemini"
    assert panel.media_cards["gemini"].is_expanded is True
    assert panel.media_cards["openrouter"].is_expanded is False
    assert panel.ui_media_gemini_api_key.text() == "test-gemini-image-key"

    # Change to OpenRouter and save
    panel.media_cards["openrouter"].clicked.emit()
    assert panel.selected_media_provider == "openrouter"
    panel.ui_media_openrouter_api_key.setText("test-openrouter-image-key")

    panel.save_settings()

    assert sm.get("core.media.image-provider") == "openrouter"
    assert sm.get_env("OPENROUTER_IMAGE_API_KEY") == "test-openrouter-image-key"

    panel.deleteLater()


def test_openrouter_image_models_fetching_mocked(qapp, tmp_path, monkeypatch):
    cache_file = str(tmp_path / "openrouter_image_models.json")
    default_file = str(tmp_path / "default_openrouter_image_models.json")
    monkeypatch.setattr("config.paths.get_openrouter_image_models_cache_file", lambda: cache_file)
    monkeypatch.setattr("config.paths.get_default_openrouter_image_models_file", lambda: default_file)

    panel = SettingsPanelWidget()
    fake_models = [
        "bytedance-seed/seedream-4.5",
        "black-forest-labs/flux-1.1-pro",
        "stabilityai/stable-diffusion-3.5-large"
    ]

    with patch("core.openrouter_worker.OpenRouterWorker.fetch_available_image_models", return_value=fake_models):
        thread = panel._fetch_openrouter_image_models()
        thread.join(timeout=3)
        qapp.processEvents()

    assert "Fetched 3 image models" in panel.ui_media_openrouter_fetch_status.text()
    assert panel.ui_media_openrouter_fetch_btn.isEnabled() is True
    panel.deleteLater()


def test_wizard_mode_with_seven_steps(qapp):
    panel = SettingsPanelWidget()
    panel.show()
    panel.set_wizard_mode(True)

    # 7 steps: 0, 1, 2, 3, 4, 5, 6
    for i in range(6):
        assert panel.save_buttons[i].text() == "Next"
    assert panel.save_buttons[6].text() == "Finish"

    # Navigation traversal
    panel.set_section(0)
    assert panel.current_section_index == 0
    assert panel.back_buttons[0].isVisible() is False

    for step in range(1, 7):
        panel.on_save_clicked()
        assert panel.current_section_index == step
        assert panel.back_buttons[step].isVisible() is True

    # On step 6 (Social Accounts), clicking save finishes wizard
    wizard_finished_mock = MagicMock()
    panel.wizard_finished.connect(wizard_finished_mock)
    panel.on_save_clicked()
    assert wizard_finished_mock.called is True
    panel.deleteLater()


def test_ensure_openrouter_image_models_cache_seeding(tmp_path, monkeypatch):
    cache_file = str(tmp_path / "data" / "openrouter_image_models.json")
    monkeypatch.setattr("config.paths.get_openrouter_image_models_cache_file", lambda: cache_file)

    assert not os.path.exists(cache_file)
    from config import paths
    seeded_path = paths.ensure_openrouter_image_models_cache()
    assert seeded_path == cache_file
    assert os.path.exists(cache_file)

    with open(cache_file, "r", encoding="utf-8") as f:
        models = json.load(f)

    assert isinstance(models, list)
    assert len(models) >= 50
    assert "bytedance-seed/seedream-4.5" in models
    assert "black-forest-labs/flux-3-image" in models
    assert "google/gemini-3.1-flash-image" in models


def test_load_cached_openrouter_image_models_auto_seeds_cache(tmp_path, monkeypatch):
    cache_file = str(tmp_path / "data" / "openrouter_image_models.json")
    monkeypatch.setattr("config.paths.get_openrouter_image_models_cache_file", lambda: cache_file)

    assert not os.path.exists(cache_file)
    models = SettingsPanelWidget._load_cached_openrouter_image_models()

    assert isinstance(models, list)
    assert len(models) >= 50
    assert "bytedance-seed/seedream-4.5" in models
    # Verify it automatically created and saved to cache_file
    assert os.path.exists(cache_file)


def test_media_provider_autofill_from_tts_key(qapp):
    panel = SettingsPanelWidget()

    # Clear keys first
    panel.ui_gemini_api_key.clear()
    panel.ui_media_gemini_api_key.clear()

    # Typing into Gemini TTS key in voice setup should auto-fill media Gemini key
    panel.ui_gemini_tts_api_key.setText("AIzaSy-VoiceTTSKey")
    assert panel.ui_media_gemini_api_key.text() == "AIzaSy-VoiceTTSKey"

    # Clear media key again and test step transition with empty LLM key but active TTS key
    panel.ui_media_gemini_api_key.clear()
    panel.set_section(5)
    assert panel.ui_media_gemini_api_key.text() == "AIzaSy-VoiceTTSKey"

    panel.deleteLater()


def test_media_provider_load_settings_fallback_to_tts_key(qapp, tmp_path, monkeypatch):
    agent_dir = tmp_path / "test_media_tts_fallback_agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(agent_dir))

    sm = SettingsManager(agent_id="test_media_tts_fallback_agent")
    sm.set("core.media.image-provider", "gemini")
    sm.set_env("GEMINI_TTS_API_KEY", "test-gemini-tts-fallback-key")
    sm.save()

    panel = SettingsPanelWidget()
    panel.load_settings(settings_manager=sm)

    assert panel.ui_media_gemini_api_key.text() == "test-gemini-tts-fallback-key"
    panel.deleteLater()

