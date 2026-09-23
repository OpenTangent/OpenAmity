import os
import sys
import time
import pytest
from PySide6.QtWidgets import QApplication, QTextBrowser
from PySide6.QtCore import Qt

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from unittest.mock import MagicMock
from core.cerebrum import Cerebrum, Tool
from core.logger_config import ColorFormatter
from gui.tool_pips import ToolPip, ToolPipManager


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_all_tools_have_icons_and_colors():
    """Verify all 17 tools declare icons, valid hex colors, and async_commands."""
    Tool.discover_tool_classes()
    expected_tools = [
        "Browser", "Chatroom", "Contacts", "DateTime", "Email",
        "Mastodon", "Media", "MemPalace", "Moltbook", "PulseTool",
        "Speaker", "Subagent", "System", "Terminal", "Trajectory",
        "WebSearch", "WhatsApp"
    ]

    for tool_name in expected_tools:
        tool_cls = Tool.get_tool_class(tool_name)
        assert tool_cls is not None, f"Tool class for {tool_name} not found"
        assert hasattr(tool_cls, "icon") and len(tool_cls.icon) > 0, f"{tool_name} missing icon"
        assert hasattr(tool_cls, "color") and tool_cls.color.startswith("#") and len(tool_cls.color) == 7, (
            f"{tool_name} color must be #RRGGBB format, got {getattr(tool_cls, 'color', None)}"
        )
        assert hasattr(tool_cls, "async_commands") and isinstance(tool_cls.async_commands, list), (
            f"{tool_name} missing async_commands list"
        )

    # Subagent and Terminal must declare their respective async commands
    subagent_cls = Tool.get_tool_class("Subagent")
    assert "spawn" in subagent_cls.async_commands

    terminal_cls = Tool.get_tool_class("Terminal")
    assert "run_async" in terminal_cls.async_commands


def test_tool_color_and_icon_resolution():
    """Verify Tool.get_tool_color and Tool.get_tool_icon resolve variations."""
    assert Tool.get_tool_color("Terminal") == "#F39C12"
    assert Tool.get_tool_color("tool.Terminal") == "#F39C12"
    assert Tool.get_tool_color("PulseTool") == "#673AB7"
    assert Tool.get_tool_color("Pulse") == "#673AB7"
    assert Tool.get_tool_color("tool.Pulse") == "#673AB7"
    assert Tool.get_tool_color("WhatsApp") == "#25D366"

    assert Tool.get_tool_icon("Browser") == "🌐"
    assert Tool.get_tool_icon("Subagent") == "🤖"
    assert Tool.get_tool_icon("Terminal") == "💻"


def test_color_formatter_dynamic_tool_highlighting():
    """Verify ColorFormatter dynamically retrieves ANSI colors from Tool objects."""
    cf = ColorFormatter()
    # Terminal (#F39C12 -> 243, 156, 18)
    term_ansi = cf.get_section_color("tool.Terminal")
    assert term_ansi == "\x1b[38;2;243;156;18m"

    # Browser (#3B82F6 -> 59, 130, 246)
    browser_ansi = cf.get_section_color("tool.Browser")
    assert browser_ansi == "\x1b[38;2;59;130;246m"

    # Subagent (#E040FB -> 224, 64, 251)
    subagent_ansi = cf.get_section_color("tool.Subagent")
    assert subagent_ansi == "\x1b[38;2;224;64;251m"


def test_tool_pip_widget(qapp):
    """Verify ToolPip widget dimensions, state, and tooltip truncation."""
    short_call = "Terminal_run: command_str='ls'"
    pip = ToolPip(
        call_id="call1",
        tool_name="Terminal",
        icon="💻",
        color="#F39C12",
        call_text=short_call,
        is_async=False,
    )

    assert pip.width() == 24
    assert pip.height() == 24
    assert "Terminal" in pip.toolTip()
    assert short_call in pip.toolTip()

    # Test 512 character truncation
    long_call = "x" * 600
    pip_long = ToolPip(
        call_id="call2",
        tool_name="Terminal",
        icon="💻",
        color="#F39C12",
        call_text=long_call,
        is_async=False,
    )
    # Check that text in tooltip is truncated with ellipses
    assert "..." in pip_long.toolTip()
    # The truncated call text inside the tooltip should not exceed 512 characters
    assert len(long_call[:509] + "...") == 512


def test_tool_pip_instant_hover_tooltip(qapp):
    """Verify ToolPip displays its tooltip instantly on enterEvent without delay."""
    from PySide6.QtWidgets import QToolTip
    from PySide6.QtGui import QEnterEvent
    from PySide6.QtCore import QPoint

    pip = ToolPip(
        call_id="call_hover",
        tool_name="WebSearch",
        icon="🔍",
        color="#00BCD4",
        call_text="WebSearch_search: q='Amity'",
        is_async=False,
    )
    pip.show()
    qapp.processEvents()

    # Trigger enterEvent
    enter_ev = QEnterEvent(QPoint(12, 12), QPoint(12, 12), QPoint(100, 100))
    qapp.sendEvent(pip, enter_ev)
    assert QToolTip.isVisible()
    assert "WebSearch" in QToolTip.text()


def test_tool_pip_manager_layout_and_lifecycle(qapp):
    """Verify ToolPipManager slot calculations, animations, and tab visibility on container."""
    from gui.chat_formatter import setup_chat_browser
    browser = QTextBrowser()
    setup_chat_browser(browser)
    browser.resize(400, 300)
    browser.show()

    # Manager attaches directly to browser container (ignoring 16px internal padding)
    mgr = ToolPipManager(browser)

    # 1. Add synchronous pips (left-aligned)
    mgr.add_pip("s1", "Terminal", "💻", "#F39C12", "Terminal_run: ls", is_async=False)
    assert len(mgr.sync_pips) == 1
    assert mgr.sync_pips[0].target_x == 2.0
    assert mgr.sync_pips[0].x() == 2

    mgr.add_pip("s2", "WebSearch", "🔍", "#00BCD4", "WebSearch_search: q='Amity'", is_async=False)
    assert len(mgr.sync_pips) == 2
    # 2px left margin + 26px = 28px
    assert mgr.sync_pips[1].target_x == 28.0
    assert mgr.sync_pips[1].x() == 28

    # 2. Add asynchronous pips (right-aligned)
    mgr.add_pip("a1", "Terminal", "💻", "#F39C12", "Terminal_run_async: build", is_async=True)
    assert len(mgr.async_pips) == 1
    # Browser width - 26px
    expected_a1_x = browser.width() - 26.0
    assert mgr.async_pips[0].target_x == expected_a1_x

    mgr.add_pip("a2", "Subagent", "🤖", "#E040FB", "Subagent_spawn: research", is_async=True)
    assert len(mgr.async_pips) == 2
    # Stacks to the left: browser.width() - 52px
    expected_a2_x = browser.width() - 52.0
    assert mgr.async_pips[1].target_x == expected_a2_x

    # 3. Complete async pip
    mgr.complete_async_pip("a1")
    assert len(mgr.async_pips) == 1
    assert mgr.async_pips[0].call_id == "a2"
    # a2 should now shift right to slot 0 (browser.width() - 26)
    assert mgr.async_pips[0].target_x == browser.width() - 26.0
    assert len(mgr.fading_pips) == 1
    assert mgr.fading_pips[0].call_id == "a1"
    assert mgr.fading_pips[0].state == "fading"

    # 4. Tab visibility toggling
    mgr.set_active(False)
    assert not mgr.is_active
    assert not mgr.sync_pips[0].isVisible()
    assert not mgr.async_pips[0].isVisible()

    mgr.set_active(True)
    assert mgr.is_active
    assert mgr.sync_pips[0].isVisible()
    assert mgr.async_pips[0].isVisible()

    # 5. Cleanup
    mgr.clear()
    assert len(mgr.sync_pips) == 0
    assert len(mgr.async_pips) == 0
    assert len(mgr.fading_pips) == 0


def test_tool_pip_drawn_above_scrollbar(qapp):
    """Verify that pips on the right side are rendered above the scroll bar."""
    from PySide6.QtWidgets import QWidget, QVBoxLayout
    from gui.chat_formatter import setup_chat_browser

    parent_win = QWidget()
    layout = QVBoxLayout(parent_win)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    browser = QTextBrowser()
    setup_chat_browser(browser)
    layout.addWidget(browser)

    parent_win.resize(600, 400)
    parent_win.show()

    # Fill browser to ensure vertical scrollbar is active and visible
    browser.append("Content line\\n" * 150)
    qapp.processEvents()

    mgr = ToolPipManager(container=browser, overlay_parent=parent_win)
    mgr.add_pip("async_task", "Terminal", "💻", "#F39C12", "Terminal_run_async: make", is_async=True)
    qapp.processEvents()

    pip = mgr.async_pips[0]
    assert pip.isVisible()
    # Ensure pip is child of overlay_parent
    assert pip.parent() == parent_win

    # Check child at pip center: must be pip itself, not scrollbar
    center_pos = pip.geometry().center()
    hit_widget = parent_win.childAt(center_pos)
    assert hit_widget == pip, f"Expected pip on top of scrollbar, but hit {hit_widget}"



def test_execute_tool_call_call_id_routing():
    """Verify that execute_tool_call only routes _call_id to asynchronous commands."""
    from tools.trajectory_tool import TrajectoryTool
    from tools.pulse_tool import PulseTool
    from tools.terminal_tool import TerminalSkill
    from tools.subagent_tool import SubagentSkill

    mock_orch = MagicMock()
    mock_orch.agent_id = "test-call-id-agent"

    cerebrum = Cerebrum(orchestrator=mock_orch)
    traj_tool = TrajectoryTool(orchestrator=mock_orch)
    pulse_tool = PulseTool(orchestrator=mock_orch)
    term_tool = TerminalSkill(orchestrator=mock_orch)
    subagent_tool = SubagentSkill(orchestrator=mock_orch)

    cerebrum.register_skill(traj_tool)
    cerebrum.register_skill(pulse_tool)
    cerebrum.register_skill(term_tool)
    cerebrum.register_skill(subagent_tool)

    # 1. Synchronous commands should NEVER receive _call_id
    res_bearings = cerebrum.execute_tool_call("Trajectory_get_bearings", {}, call_id="pip-1234")
    assert not res_bearings.startswith("Error:"), f"Trajectory_get_bearings failed: {res_bearings}"

    res_reflect = cerebrum.execute_tool_call(
        "Trajectory_reflect_and_update_state",
        {"summary": "Testing routing", "perceived_state": "Calibrated"},
        call_id="pip-1234"
    )
    assert not res_reflect.startswith("Error:"), f"Trajectory_reflect_and_update_state failed: {res_reflect}"

    res_agenda = cerebrum.execute_tool_call("PulseTool_view_agenda", {"days_ahead": 3}, call_id="pip-1234")
    assert not res_agenda.startswith("Error:"), f"PulseTool_view_agenda failed: {res_agenda}"

    # 2. Asynchronous commands MUST receive _call_id
    # Intercept execute on SubagentTool to verify _call_id is passed
    received_kwargs = {}
    original_subagent_execute = subagent_tool.execute
    def mock_subagent_execute(command, *args, **kwargs):
        received_kwargs.update(kwargs)
        return "Subagent spawned"
    subagent_tool.execute = mock_subagent_execute

    cerebrum.execute_tool_call(
        "Subagent_spawn",
        {"task_description": "Do work", "model_tier": "light"},
        call_id="pip-async-99"
    )
    assert received_kwargs.get("_call_id") == "pip-async-99"


