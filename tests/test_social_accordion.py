import os
import sys
import pytest
from PySide6.QtWidgets import QApplication

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.config_manager import ConfigManager
from core.settings_manager import SettingsManager
from gui.settings_panel import SettingsPanelWidget, AccordionCard, SocialAccordionCard


@pytest.fixture
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


def test_social_accordion_initial_state(qapp):
    panel = SettingsPanelWidget()
    assert hasattr(panel, "social_cards")
    expected_tools = ["email", "whatsapp", "moltbook", "mastodon"]
    for tool_id in expected_tools:
        assert tool_id in panel.social_cards
        card = panel.social_cards[tool_id]
        assert isinstance(card, SocialAccordionCard)
        assert isinstance(card, AccordionCard)
        assert card.header_widget.badge.text() == "✓ Active"

        # By default for new agent: all cards should be deactivated and collapsed
        assert card.is_expanded is False
        assert card.header_widget.badge.isHidden()
        assert card.body_widget.isHidden()
        assert card.checkbox.isChecked() is False

    # Check that panel-level checkbox attributes match
    assert panel.ui_use_email.isChecked() is False
    assert panel.ui_use_whatsapp.isChecked() is False
    assert panel.ui_use_moltbook.isChecked() is False
    assert panel.ui_use_mastodon.isChecked() is False

    panel.deleteLater()


def test_social_accordion_click_to_activate_and_deactivate(qapp):
    panel = SettingsPanelWidget()
    card_email = panel.social_cards["email"]

    # Click collapsed card to activate
    card_email.clicked.emit()
    assert card_email.is_expanded is True
    assert not card_email.header_widget.badge.isHidden()
    assert not card_email.body_widget.isHidden()
    assert card_email.checkbox.isChecked() is True
    assert panel.ui_use_email.isChecked() is True

    # Other cards must remain collapsed
    for tool_id in ["whatsapp", "moltbook", "mastodon"]:
        assert panel.social_cards[tool_id].is_expanded is False
        assert panel.social_cards[tool_id].header_widget.badge.isHidden()

    # Click again to deactivate and collapse
    card_email.clicked.emit()
    assert card_email.is_expanded is False
    assert card_email.header_widget.badge.isHidden()
    assert card_email.body_widget.isHidden()
    assert card_email.checkbox.isChecked() is False
    assert panel.ui_use_email.isChecked() is False

    panel.deleteLater()


def test_social_accordion_multi_card_simultaneous_expansion(qapp):
    panel = SettingsPanelWidget()
    card_email = panel.social_cards["email"]
    card_wa = panel.social_cards["whatsapp"]
    card_mb = panel.social_cards["moltbook"]
    card_mastodon = panel.social_cards["mastodon"]

    # Activate Email and WhatsApp simultaneously
    card_email.clicked.emit()
    card_wa.clicked.emit()

    # Both must be expanded at the same time with "✓ Active" badges
    assert card_email.is_expanded is True
    assert not card_email.header_widget.badge.isHidden()
    assert card_wa.is_expanded is True
    assert not card_wa.header_widget.badge.isHidden()

    # Moltbook and Mastodon remain collapsed
    assert card_mb.is_expanded is False
    assert card_mastodon.is_expanded is False

    # Activate Moltbook
    card_mb.clicked.emit()
    assert card_email.is_expanded is True
    assert card_wa.is_expanded is True
    assert card_mb.is_expanded is True
    assert card_mastodon.is_expanded is False

    # Deactivate Email: WhatsApp and Moltbook remain active
    card_email.clicked.emit()
    assert card_email.is_expanded is False
    assert card_email.header_widget.badge.isHidden()
    assert card_wa.is_expanded is True
    assert card_mb.is_expanded is True

    panel.deleteLater()


def test_social_accordion_checkbox_toggled_synchronization(qapp):
    panel = SettingsPanelWidget()
    card_mastodon = panel.social_cards["mastodon"]

    # Programmatic setChecked(True)
    panel.ui_use_mastodon.setChecked(True)
    assert card_mastodon.is_expanded is True
    assert not card_mastodon.header_widget.badge.isHidden()
    assert not card_mastodon.body_widget.isHidden()

    # Programmatic setChecked(False)
    panel.ui_use_mastodon.setChecked(False)
    assert card_mastodon.is_expanded is False
    assert card_mastodon.header_widget.badge.isHidden()
    assert card_mastodon.body_widget.isHidden()

    panel.deleteLater()


def test_social_accordion_load_and_save_settings(qapp, tmp_path, monkeypatch):
    agent_dir = tmp_path / "test_social_agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    config_file = str(tmp_path / "config.json")
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(agent_dir))

    cm = ConfigManager(config_file=config_file)
    sm = SettingsManager(agent_id="test_social_agent")
    sm.set("core.tools.email", True)
    sm.set("core.tools.whatsapp", True)
    sm.set("core.tools.moltbook", False)
    sm.set("core.tools.mastodon", False)
    sm.save()

    panel = SettingsPanelWidget()
    panel.config = cm
    panel.load_settings(settings_manager=sm)

    # Email and WhatsApp must be expanded simultaneously with "✓ Active"
    assert panel.social_cards["email"].is_expanded is True
    assert not panel.social_cards["email"].header_widget.badge.isHidden()
    assert panel.social_cards["whatsapp"].is_expanded is True
    assert not panel.social_cards["whatsapp"].header_widget.badge.isHidden()

    # Moltbook and Mastodon must be collapsed
    assert panel.social_cards["moltbook"].is_expanded is False
    assert panel.social_cards["mastodon"].is_expanded is False

    # User deactivates Email and activates Mastodon via header clicks
    panel.social_cards["email"].clicked.emit()
    panel.social_cards["mastodon"].clicked.emit()
    panel.save_settings()

    assert sm.get("core.tools.email") is False
    assert sm.get("core.tools.whatsapp") is True
    assert sm.get("core.tools.moltbook") is False
    assert sm.get("core.tools.mastodon") is True

    panel.deleteLater()
