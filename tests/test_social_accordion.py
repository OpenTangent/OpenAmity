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


def test_whatsapp_advanced_settings_accordion_toggle(qapp):
    panel = SettingsPanelWidget()
    wa_card = panel.social_cards["whatsapp"]

    assert hasattr(panel, "ui_whatsapp_adv_toggle_btn")
    assert hasattr(panel, "ui_whatsapp_adv_container")
    assert hasattr(panel, "ui_wa_buffer")
    assert hasattr(panel, "ui_whatsapp_whitelist")
    assert hasattr(panel, "ui_wa_pulse_all_dms")
    assert hasattr(panel, "ui_wa_pulse_all_groups")
    assert hasattr(panel, "ui_wa_pulse_on_mentions")
    assert hasattr(panel, "ui_whatsapp_group_whitelist")
    assert hasattr(panel, "ui_wa_number_whitelist_container")
    assert hasattr(panel, "ui_wa_group_whitelist_container")

    # Header is at the bottom of the WhatsApp card
    assert panel.ui_whatsapp_adv_toggle_btn.parent() == wa_card.body_widget
    assert panel.ui_whatsapp_adv_container.parent() == wa_card.body_widget

    # Buffer and whitelist containers are inside the advanced container
    assert panel.ui_wa_buffer.parent() == panel.ui_whatsapp_adv_container
    assert panel.ui_wa_number_whitelist_container.parent() == panel.ui_whatsapp_adv_container
    assert panel.ui_whatsapp_whitelist.parent() == panel.ui_wa_number_whitelist_container
    assert panel.ui_wa_group_whitelist_container.parent() == panel.ui_whatsapp_adv_container
    assert panel.ui_whatsapp_group_whitelist.parent() == panel.ui_wa_group_whitelist_container

    # Checkboxes are unchecked by default
    assert not panel.ui_wa_pulse_all_dms.isChecked()
    assert not panel.ui_wa_pulse_all_groups.isChecked()
    assert not panel.ui_wa_pulse_on_mentions.isChecked()

    # Dynamic visibility when checkboxes are toggled
    assert not panel.ui_wa_number_whitelist_container.isHidden()
    panel.ui_wa_pulse_all_dms.setChecked(True)
    assert panel.ui_wa_number_whitelist_container.isHidden()
    panel.ui_wa_pulse_all_dms.setChecked(False)
    assert not panel.ui_wa_number_whitelist_container.isHidden()

    assert not panel.ui_wa_group_whitelist_container.isHidden()
    panel.ui_wa_pulse_all_groups.setChecked(True)
    assert panel.ui_wa_group_whitelist_container.isHidden()
    panel.ui_wa_pulse_all_groups.setChecked(False)
    assert not panel.ui_wa_group_whitelist_container.isHidden()

    # Collapsed by default
    assert panel.ui_whatsapp_adv_container.isHidden()
    assert panel.ui_whatsapp_adv_toggle_btn.text() == "▶ Advanced Whatsapp Settings"

    # Clean, neat styling conforming to the panel (no floating borders or background colors)
    assert panel.ui_whatsapp_adv_container.objectName() == "settingsSubContainer"
    btn_style = panel.ui_whatsapp_adv_toggle_btn.styleSheet()
    assert "background-color: transparent" in btn_style
    assert "border: none" in btn_style

    # Click to expand
    panel.ui_whatsapp_adv_toggle_btn.click()
    assert not panel.ui_whatsapp_adv_container.isHidden()
    assert panel.ui_whatsapp_adv_toggle_btn.text() == "▼ Advanced Whatsapp Settings"
    assert "background-color: transparent" in panel.ui_whatsapp_adv_toggle_btn.styleSheet()

    # Click to collapse
    panel.ui_whatsapp_adv_toggle_btn.click()
    assert panel.ui_whatsapp_adv_container.isHidden()
    assert panel.ui_whatsapp_adv_toggle_btn.text() == "▶ Advanced Whatsapp Settings"

    panel.deleteLater()


def test_whatsapp_settings_load_and_save_in_social_accounts(qapp, tmp_path, monkeypatch):
    agent_dir = tmp_path / "test_wa_settings_agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    config_file = str(tmp_path / "config.json")
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(agent_dir))

    cm = ConfigManager(config_file=config_file)
    sm = SettingsManager(agent_id="test_wa_settings_agent")
    sm.set("core.tools.whatsapp", True)
    sm.set("core.auto-pulse.whitelist", ["+1234567890", "+9876543210"])
    sm.set("core.auto-pulse.group-whitelist", ["Project Amity", "Dev Team"])
    sm.set("core.auto-pulse.pulse-all-dms", False)
    sm.set("core.auto-pulse.pulse-all-groups", True)
    sm.set("core.auto-pulse.pulse-on-mentions", True)
    sm.set("core.auto-pulse.buffer-seconds", 45)
    sm.save()

    panel = SettingsPanelWidget()
    panel.config = cm
    panel.load_settings(settings_manager=sm)

    # Verify loaded values in UI controls
    assert panel.ui_wa_buffer.value() == 45
    items = [panel.ui_whatsapp_whitelist.item(i).text() for i in range(panel.ui_whatsapp_whitelist.count())]
    assert items == ["+1234567890", "+9876543210"]
    group_items = [panel.ui_whatsapp_group_whitelist.item(i).text() for i in range(panel.ui_whatsapp_group_whitelist.count())]
    assert group_items == ["Project Amity", "Dev Team"]
    assert panel.ui_wa_pulse_all_dms.isChecked() is False
    assert panel.ui_wa_pulse_all_groups.isChecked() is True
    assert panel.ui_wa_pulse_on_mentions.isChecked() is True
    assert panel.ui_wa_group_whitelist_container.isHidden() is True
    assert panel.ui_wa_number_whitelist_container.isHidden() is False

    # Modify values
    panel.ui_wa_buffer.setValue(60)
    panel.ui_whatsapp_whitelist.addItem("+1122334455")
    panel.ui_whatsapp_group_whitelist.addItem("General")
    panel.ui_wa_pulse_all_dms.setChecked(True)
    panel.ui_wa_pulse_all_groups.setChecked(False)
    panel.ui_wa_pulse_on_mentions.setChecked(False)
    panel.save_settings()

    # Verify persisted values in SettingsManager
    assert sm.get("core.auto-pulse.buffer-seconds") == 60
    saved_whitelist = sm.get("core.auto-pulse.whitelist")
    assert saved_whitelist == ["+1234567890", "+9876543210", "+1122334455"]
    saved_group_whitelist = sm.get("core.auto-pulse.group-whitelist")
    assert saved_group_whitelist == ["Project Amity", "Dev Team", "General"]
    assert sm.get("core.auto-pulse.pulse-all-dms") is True
    assert sm.get("core.auto-pulse.pulse-all-groups") is False
    assert sm.get("core.auto-pulse.pulse-on-mentions") is False

    panel.deleteLater()


def test_other_agent_settings_panel_without_whatsapp(qapp):
    panel = SettingsPanelWidget()
    # Panel 7 is "Other Agent Settings"
    other_panel = panel.stack.widget(7)
    assert other_panel is not None

    # Agent Setup controls exist
    assert hasattr(panel, "ui_low_token_mode")
    assert hasattr(panel, "ui_max_memories")
    assert hasattr(panel, "ui_agency_limit")

    # WhatsApp controls are NOT inside the Other Agent Settings panel
    assert panel.ui_wa_buffer.parent() != other_panel
    assert panel.ui_whatsapp_whitelist.parent() != other_panel
    assert panel.ui_whatsapp_adv_container.parent() != other_panel

    panel.deleteLater()

