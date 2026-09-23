import os
import sys
import tempfile
import shutil
import pytest
from unittest.mock import MagicMock, patch

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PySide6.QtWidgets import QApplication
from config.presets import AGENT_PRESETS, get_preset, list_presets
from gui.settings_panel import SettingsPanelWidget
from core.settings_manager import SettingsManager


@pytest.fixture(scope="session")
def qapp():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication.instance()
    if app is None:
        app = QApplication(["-platform", "offscreen"])
    return app


@pytest.fixture
def temp_environment(monkeypatch):
    temp_dir = tempfile.mkdtemp(prefix="openamity_preset_test_")
    monkeypatch.setattr("config.paths.get_app_data_dir", lambda: temp_dir)
    monkeypatch.setattr("config.paths.get_base_dir_for",
                        lambda aid: os.path.join(temp_dir, "agents", aid) if aid else os.path.join(temp_dir, "agents", "default"))
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_all_eight_presets_exist_and_valid():
    """Verify that all 8 required presets exist and adhere to structure contracts."""
    assert len(AGENT_PRESETS) == 8
    expected_ids = ["amy", "alex", "nova", "sage", "rowan", "iris", "marcus", "maya"]
    assert list(AGENT_PRESETS.keys()) == expected_ids

    for pid in expected_ids:
        preset = AGENT_PRESETS[pid]
        assert preset["id"] == pid
        assert isinstance(preset["name"], str) and len(preset["name"]) > 0
        assert isinstance(preset["icon"], str) and len(preset["icon"]) > 0
        assert preset["gender"] in ["Female", "Male", "Nonbinary"]
        assert isinstance(preset["archetype"], str) and len(preset["archetype"]) > 0
        assert isinstance(preset["tagline"], str) and len(preset["tagline"]) > 0
        assert isinstance(preset["focus_badge"], str) and len(preset["focus_badge"]) > 0
        assert isinstance(preset["voice_badge"], str) and len(preset["voice_badge"]) > 0
        assert isinstance(preset["base_personality"], str) and len(preset["base_personality"]) > 50

        assert isinstance(preset["core_values"], list) and len(preset["core_values"]) >= 3
        for val in preset["core_values"]:
            assert "(" in val and ")" in val

        assert isinstance(preset["overarching_goals"], list) and len(preset["overarching_goals"]) >= 3
        for goal in preset["overarching_goals"]:
            assert "(" in goal and ")" in goal

        voice = preset["voice"]
        assert isinstance(voice["piper_model"], str) and len(voice["piper_model"]) > 0
        assert voice["gemini_gender"] in ["Female", "Male", "Non-binary / Neutral"]
        assert any(age in voice["gemini_age"] for age in ["Young Adult", "Adult", "Youthful", "Mature"])
        assert isinstance(voice["gemini_accent"], str) and len(voice["gemini_accent"]) > 0
        assert isinstance(voice["gemini_style"], str) and len(voice["gemini_style"]) > 0
        assert isinstance(voice["gemini_model_name"], str) and len(voice["gemini_model_name"]) > 0
        assert isinstance(voice["gemini_prompt_profile"], str) and len(voice["gemini_prompt_profile"]) > 0

        assert isinstance(preset["cognitive_budget"], int) and preset["cognitive_budget"] >= 10000
        assert isinstance(preset["max_memories"], int) and preset["max_memories"] >= 24

        # Verify no second-person pronouns are used (Soul Jar / Identity must be pure self-notes)
        import re
        forbidden = re.compile(r'\b(you|your|yours|yourself|you\'re)\b', re.IGNORECASE)
        assert not forbidden.search(preset["base_personality"]), f"Preset {pid} base_personality contains second-person pronouns"
        for v in preset["core_values"]:
            assert not forbidden.search(v), f"Preset {pid} core_value '{v}' contains second-person pronouns"
        for g in preset["overarching_goals"]:
            assert not forbidden.search(g), f"Preset {pid} overarching_goal '{g}' contains second-person pronouns"


def test_get_preset_and_list_presets():
    """Verify get_preset and list_presets utility functions."""
    all_presets = list_presets()
    assert len(all_presets) == 8

    # Case insensitivity
    assert get_preset("NOVA")["name"] == "Nova"
    assert get_preset("alex")["name"] == "Alex"
    # Fallback to amy
    assert get_preset("unknown_preset")["name"] == "Amy"


def test_preset_selection_prepopulates_ui(qapp):
    """Verify that selecting presets in SettingsPanelWidget pre-populates all subsequent panels."""
    panel = SettingsPanelWidget()
    assert len(panel.preset_cards) == 8

    # 1. Select Nova (Research Partner)
    panel.select_preset("nova")
    assert panel.selected_preset_id == "nova"
    assert panel.ui_agent_name.text() == "Nova"
    assert panel.ui_archetype.text() == "Autonomous Research Partner"
    assert panel.ui_gender.currentText() == "Female"
    assert "Autonomous Research Lab Partner" in panel.ui_base_personality.toPlainText()
    assert len(panel.ui_core_values.get_items()) == 3
    assert any("Epistemic Rigor" in v for v in panel.ui_core_values.get_items())
    assert any("Advance Working Hypotheses" in g for g in panel.ui_overarching_goals.get_items())
    assert panel.ui_fallback_voice.text() == "en_GB-cori-high"
    assert panel.ui_tts_gender.currentText() == "Female"
    assert panel.ui_tts_accent.currentText() == "British (RP / Standard)"
    assert panel.ui_tts_style.currentText() == "Calm & Analytical"
    assert panel.ui_voice.text() == "Aoede"
    assert panel.ui_agency_limit.value() == 15000
    assert panel.ui_max_memories.value() == 48

    # 2. Select Alex (Executive Chief of Staff)
    panel.select_preset("alex")
    assert panel.selected_preset_id == "alex"
    assert panel.ui_agent_name.text() == "Alex"
    assert panel.ui_archetype.text() == "Executive Chief of Staff"
    assert panel.ui_gender.currentText() == "Nonbinary"
    assert "Executive Chief of Staff" in panel.ui_base_personality.toPlainText()
    assert any("Ruthless Prioritization" in v for v in panel.ui_core_values.get_items())
    assert any("Optimize Executive Bandwidth" in g for g in panel.ui_overarching_goals.get_items())
    assert panel.ui_fallback_voice.text() == "en_US-lessac-high"
    assert panel.ui_tts_gender.currentText() == "Non-binary / Neutral"
    assert panel.ui_tts_accent.currentText() == "American (General)"
    assert panel.ui_tts_style.currentText() == "Professional & Informative"
    assert panel.ui_voice.text() == "Puck"

    # 3. Select Marcus (Systems Architect)
    panel.select_preset("marcus")
    assert panel.selected_preset_id == "marcus"
    assert panel.ui_agent_name.text() == "Marcus"
    assert panel.ui_archetype.text() == "Systems Architect & Strategist"
    assert panel.ui_gender.currentText() == "Male"
    assert panel.ui_fallback_voice.text() == "en_US-lessac-high"
    assert panel.ui_tts_accent.currentText() == "American (General)"
    assert panel.ui_tts_style.currentText() == "Calm & Analytical"
    assert panel.ui_voice.text() == "Fenrir"

    # 4. Select Sage (Socratic Tutor)
    panel.select_preset("sage")
    assert panel.selected_preset_id == "sage"
    assert panel.ui_agent_name.text() == "Sage"
    assert panel.ui_archetype.text() == "Socratic Tutor & Mentor"
    assert panel.ui_gender.currentText() == "Male"
    assert panel.ui_fallback_voice.text() == "en_GB-alan-low"
    assert panel.ui_tts_style.currentText() == "Gentle & Serene"

    # 5. Select Rowan (Creative Co-Author)
    panel.select_preset("rowan")
    assert panel.selected_preset_id == "rowan"
    assert panel.ui_agent_name.text() == "Rowan"
    assert panel.ui_archetype.text() == "Creative Co-Author & Worldbuilder"
    assert panel.ui_tts_accent.currentText() == "Irish"
    assert panel.ui_tts_style.currentText() == "Casual & Playful"
    assert panel.ui_voice.text() == "Kore"

    # 6. Select Iris (Digital Representative)
    panel.select_preset("iris")
    assert panel.selected_preset_id == "iris"
    assert panel.ui_agent_name.text() == "Iris"
    assert panel.ui_archetype.text() == "Digital Representative & Liaison"
    assert panel.ui_tts_accent.currentText() == "British (London / Modern)"
    assert panel.ui_tts_style.currentText() == "Vibrant & Sassy"

    # 7. Select Maya (Health & Habit Coach)
    panel.select_preset("maya")
    assert panel.selected_preset_id == "maya"
    assert panel.ui_agent_name.text() == "Maya"
    assert panel.ui_archetype.text() == "Continuous Health & Habit Coach"
    assert panel.ui_tts_accent.currentText() == "Australian"
    assert panel.ui_tts_style.currentText() == "Cheerful & Energetic"

    # 8. Select Amy (Community Catalyst)
    panel.select_preset("amy")
    assert panel.selected_preset_id == "amy"
    assert panel.ui_agent_name.text() == "Amy"
    assert panel.ui_archetype.text() == "Community Catalyst & Empathetic Companion"
    assert panel.ui_gender.currentText() == "Female"
    assert panel.ui_tts_accent.currentText() == "South African"
    assert panel.ui_tts_style.currentText() == "Warm & Empathetic"
    assert panel.ui_voice.text() == "Sulafat"

    panel.deleteLater()


def test_wizard_navigation_and_back_buttons(qapp):
    """Verify forward (Next/Finish) and backward (Back) traversal through wizard steps."""
    panel = SettingsPanelWidget()
    panel.show()
    panel.set_wizard_mode(True)

    # Initial state: step 0 (Presets)
    panel.set_section(0)
    assert panel.current_section_index == 0
    assert panel.save_buttons[0].text() == "Next"
    assert not panel.back_buttons[0].isVisible()

    # Step 0 -> Step 1 (Basic Settings)
    panel.on_save_clicked()
    assert panel.current_section_index == 1
    assert panel.save_buttons[1].text() == "Next"
    assert panel.back_buttons[1].isVisible()

    # Step 1 -> Step 2 (Values & Goals)
    panel.on_save_clicked()
    assert panel.current_section_index == 2
    assert panel.save_buttons[2].text() == "Next"
    assert panel.back_buttons[2].isVisible()

    # Test Back navigation: Step 2 -> Step 1
    panel.on_back_clicked()
    assert panel.current_section_index == 1
    assert panel.back_buttons[1].isVisible()

    # Step 1 -> Step 0 (Presets)
    panel.on_back_clicked()
    assert panel.current_section_index == 0
    assert not panel.back_buttons[0].isVisible()

    # Forward to step 5 (Social Accounts)
    panel.on_save_clicked()  # to 1
    panel.on_save_clicked()  # to 2
    panel.on_save_clicked()  # to 3 (Voice)
    assert panel.current_section_index == 3
    assert panel.back_buttons[3].isVisible()

    panel.on_save_clicked()  # to 4 (Provider Setup)
    assert panel.current_section_index == 4
    assert panel.back_buttons[4].isVisible()

    panel.on_save_clicked()  # to 5 (Social Accounts)
    assert panel.current_section_index == 5
    assert panel.save_buttons[5].text() == "Finish"
    assert panel.back_buttons[5].isVisible()

    # Verify Finish emits wizard_finished
    wizard_finished_emitted = []
    panel.wizard_finished.connect(lambda: wizard_finished_emitted.append(True))
    panel.on_save_clicked()
    assert len(wizard_finished_emitted) == 1

    panel.deleteLater()


def test_existing_agent_load_settings_preserves_customizations(qapp, temp_environment):
    """Verify that loading an existing agent's settings preserves custom values and highlights preset."""
    agent_id = "test_agent_custom"
    sm = SettingsManager(agent_id=agent_id)
    sm.set("core.agent.name", "CustomBot")
    sm.set("core.agent.archetype", "Autonomous Research Partner")
    sm.set("core.agent.base-personality", "Custom unique personality.")
    sm.set("core.first-run", False)
    sm.save()

    panel = SettingsPanelWidget()
    panel.load_settings(sm)

    # Nova card should be highlighted because archetype matches
    assert panel.selected_preset_id == "nova"
    assert panel.preset_cards["nova"].is_selected is True

    # But customized agent values should NOT be overwritten
    assert panel.ui_agent_name.text() == "CustomBot"
    assert panel.ui_base_personality.toPlainText() == "Custom unique personality."

    panel.deleteLater()


def test_preset_cards_selected_style_and_visibility(qapp):
    """Verify that selected preset card has green border and Selected badge, while all cards remain visible."""
    panel = SettingsPanelWidget()
    panel.set_wizard_mode(True)
    panel.set_section(0)
    panel.show()
    qapp.processEvents()

    assert len(panel.preset_cards) == 8

    # All cards must be visible (no accordion collapsing) and have minimum height 155
    for pid, card in panel.preset_cards.items():
        assert not card.isHidden()
        assert card.minimumHeight() >= 155
        assert hasattr(card, "badge")
        assert "Selected" in card.badge.text()
        assert card.badge.sizePolicy().retainSizeWhenHidden() is True

    # Default selected is amy
    assert panel.selected_preset_id == "amy"
    assert panel.preset_cards["amy"].is_selected is True
    assert panel.preset_cards["amy"].badge.isVisible() is True
    assert "#2e7d32" in panel.preset_cards["amy"].styleSheet()

    # All others must not have badge visible
    for pid in ["alex", "nova", "sage", "rowan", "iris", "marcus", "maya"]:
        card = panel.preset_cards[pid]
        assert card.is_selected is False
        assert card.badge.isVisible() is False
        assert "#2e7d32" not in card.styleSheet()
        assert not card.isHidden()

    # Select Marcus
    panel.select_preset("marcus")
    assert panel.preset_cards["marcus"].is_selected is True
    assert panel.preset_cards["marcus"].badge.isVisible() is True
    assert "#2e7d32" in panel.preset_cards["marcus"].styleSheet()

    # Amy is now deselected
    assert panel.preset_cards["amy"].is_selected is False
    assert panel.preset_cards["amy"].badge.isVisible() is False
    assert "#2e7d32" not in panel.preset_cards["amy"].styleSheet()

    # All 8 cards still remain visible
    for pid, card in panel.preset_cards.items():
        assert not card.isHidden()

    panel.deleteLater()


def test_preset_cards_static_geometry(qapp):
    """Verify that selecting presets causes ZERO size shifts or layout jitter across all cards."""
    panel = SettingsPanelWidget()
    panel.set_wizard_mode(True)
    panel.set_section(0)
    panel.resize(1000, 700)
    panel.show()
    qapp.processEvents()

    initial_size_hints = {pid: card.sizeHint().toTuple() for pid, card in panel.preset_cards.items()}
    initial_geoms = {pid: card.geometry().getRect() for pid, card in panel.preset_cards.items()}

    # Select various presets in sequence and assert geometries remain perfectly static
    for target_pid in ["nova", "marcus", "sage", "maya", "alex", "amy"]:
        panel.select_preset(target_pid)
        qapp.processEvents()

        current_size_hints = {pid: card.sizeHint().toTuple() for pid, card in panel.preset_cards.items()}
        current_geoms = {pid: card.geometry().getRect() for pid, card in panel.preset_cards.items()}

        assert current_size_hints == initial_size_hints, f"SizeHints shifted when selecting {target_pid}"
        assert current_geoms == initial_geoms, f"Geometries shifted when selecting {target_pid}"

    panel.deleteLater()


def test_preset_cards_no_horizontal_scroll_and_full_width(qapp):
    """Verify that preset cards fill available width, stack vertically, and require no horizontal scrollbar."""
    from PySide6.QtWidgets import QScrollArea
    from PySide6.QtCore import Qt

    panel = SettingsPanelWidget()
    panel.set_wizard_mode(True)
    panel.set_section(0)
    panel.resize(1000, 600)
    panel.show()
    qapp.processEvents()

    scroll = panel.stack.widget(0).findChild(QScrollArea)
    assert scroll is not None
    assert scroll.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
    assert scroll.horizontalScrollBar().maximum() == 0
    assert not scroll.horizontalScrollBar().isVisible()

    # Cards must take the full available width of the scroll content (accounting for scroll layout margins)
    scroll_layout = scroll.widget().layout()
    expected_width = scroll.widget().width() - scroll_layout.contentsMargins().left() - scroll_layout.contentsMargins().right()
    for pid, card in panel.preset_cards.items():
        assert card.width() == expected_width
        assert card.width() > 600

    # Resizing the window dynamically updates the card width without horizontal scrolling
    for test_width in [800, 1100, 700]:
        panel.resize(test_width, 600)
        qapp.processEvents()
        assert scroll.horizontalScrollBar().maximum() == 0
        assert not scroll.horizontalScrollBar().isVisible()
        expected_width = scroll.widget().width() - scroll_layout.contentsMargins().left() - scroll_layout.contentsMargins().right()
        for pid, card in panel.preset_cards.items():
            assert card.width() == expected_width

    panel.deleteLater()
