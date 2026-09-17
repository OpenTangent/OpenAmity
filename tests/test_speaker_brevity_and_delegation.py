import os
import sys
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from tools.speaker_tool import SpeakerTool
from core.cerebrum import Cerebrum


def test_speaker_tool_brevity_and_offloading():
    tool = SpeakerTool()
    declarations = tool.get_tool_declarations()
    decl_map = {d["name"]: d for d in declarations}

    # Verify tool description contains rapid back-and-forth and breathing room
    assert "rapid back-and-forth" in tool.description
    assert "breathing room" in tool.description
    assert "xdg-open" in tool.description
    # Ensure no hard limits like '<150 words' are present
    assert "<150 words" not in tool.description

    # Verify Speaker_speak_aloud
    speak_decl = decl_map["Speaker_speak_aloud"]
    assert "breathing room" in speak_decl["description"]
    assert "xdg-open" in speak_decl["description"]
    assert "<150 words" not in speak_decl["description"]
    assert "<150 words" not in speak_decl["parameters"]["properties"]["text"]["description"]

    # Verify Speaker_output_text
    text_decl = decl_map["Speaker_output_text"]
    assert "breathing room" in text_decl["description"]
    assert "xdg-open" in text_decl["description"]
    assert "file location" in text_decl["description"]

    # Verify complementary output reminder
    assert "complement each other, not summarise each other" in tool.description
    assert "complement each other, not summarise each other" in speak_decl["description"]
    assert "complement each other, not summarise each other" in text_decl["description"]


def test_agent_manual_directives():
    manual_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../src/memory/agent_manual.md"))
    with open(manual_path, "r", encoding="utf-8") as f:
        manual_content = f.read()

    # 1. Brevity and rapid pacing
    assert "rapid back-and-forth" in manual_content
    assert "breathing room" in manual_content
    assert "<150 words" not in manual_content

    # 2. Long reports and xdg-open
    assert "xdg-open" in manual_content
    assert "Terminal_run" in manual_content

    # 3. Host command delegation via shell script (.sh)
    assert ".sh" in manual_content
    assert "Single Shell Script" in manual_content
    assert "log file" in manual_content
    assert "copy and paste" in manual_content or "copy/paste" in manual_content


def test_cerebrum_loads_manual_with_directives():
    cerebrum = Cerebrum()
    manual = cerebrum.get_agent_manual()

    assert "breathing room" in manual
    assert "xdg-open" in manual
    assert ".sh" in manual
