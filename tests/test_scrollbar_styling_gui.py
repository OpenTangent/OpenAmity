import pytest
from unittest.mock import MagicMock
from PySide6.QtWidgets import QApplication, QTextBrowser, QScrollArea, QWidget
from PySide6.QtGui import QImage, QColor

from gui.theme import MODERN_SCROLLBAR_STYLE, setup_app_theme
from gui.chat_formatter import setup_chat_browser, insert_message_into_log
from gui.main_window import PromptTextEdit
from gui.settings_panel import SettingsPanelWidget, AgentBackupSelectionDialog
from gui.editable_list_widget import ItemTextEdit
from gui.agent_view import AgentView
from gui.chatroom_view import ChatroomView


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    setup_app_theme(app)
    return app


def test_modern_scrollbar_style_definitions():
    """Verify that MODERN_SCROLLBAR_STYLE defines modern minimal rules for both orientations."""
    style = MODERN_SCROLLBAR_STYLE
    assert "QScrollBar:vertical" in style
    assert "width: 8px;" in style
    assert "background: transparent;" in style

    assert "QScrollBar:horizontal" in style
    assert "height: 8px;" in style

    assert "QScrollBar::handle:vertical" in style
    assert "QScrollBar::handle:horizontal" in style
    assert "border-radius: 3px;" in style
    assert "margin: 1px;" in style

    assert "QScrollBar::add-line:vertical" in style
    assert "QScrollBar::sub-line:vertical" in style
    assert "height: 0px;" in style
    assert "width: 0px;" in style

    assert "QScrollBar::corner" in style


def test_chat_browser_scrollbar(qapp):
    """Verify that QTextBrowser configured via setup_chat_browser has an 8px minimal scrollbar without padding bleed."""
    browser = QTextBrowser()
    setup_chat_browser(browser)

    # Scoped selector check
    assert "QTextBrowser {" in browser.styleSheet()
    assert "width: 8px;" in browser.styleSheet()

    # Populate with enough text to force scrollbar
    for i in range(50):
        insert_message_into_log(browser, f"<p>Test message line {i}</p>")

    browser.resize(500, 300)
    browser.show()

    sb = browser.verticalScrollBar()
    assert sb.width() == 8

    # Verify rendering has transparent background (alpha == 0 for track pixels)
    img = QImage(sb.size(), QImage.Format_ARGB32)
    img.fill(0)
    sb.render(img)

    has_transparent = False
    has_opaque_handle = False
    for y in range(img.height()):
        for x in range(img.width()):
            alpha = img.pixelColor(x, y).alpha()
            if alpha == 0:
                has_transparent = True
            elif alpha > 30:
                has_opaque_handle = True

    assert has_transparent, "Scrollbar track should contain transparent pixels"
    assert has_opaque_handle, "Scrollbar should contain visible handle pixels"
    browser.close()


def test_prompt_text_edit_scrollbar(qapp):
    """Verify that PromptTextEdit has an 8px minimal scrollbar without 10px padding or border bleeding."""
    prompt = PromptTextEdit()
    assert "QTextEdit {" in prompt.styleSheet()
    assert "width: 8px;" in prompt.styleSheet()

    prompt.resize(400, 45)
    prompt.show()
    # Trigger vertical scrollbar
    prompt.setPlainText("\n".join([f"Line {i}" for i in range(15)]))

    sb = prompt.verticalScrollBar()
    assert sb.width() == 8

    img = QImage(sb.size(), QImage.Format_ARGB32)
    img.fill(0)
    sb.render(img)

    has_transparent = False
    has_opaque_handle = False
    for y in range(img.height()):
        for x in range(img.width()):
            alpha = img.pixelColor(x, y).alpha()
            if alpha == 0:
                has_transparent = True
            elif alpha > 30:
                has_opaque_handle = True

    assert has_transparent, "Input box scrollbar track should contain transparent pixels"
    assert has_opaque_handle, "Input box scrollbar should contain visible handle pixels"
    prompt.close()


def test_settings_panel_scrollbars(qapp):
    """Verify that SettingsPanelWidget scroll areas use 8px minimal scrollbars."""
    panel = SettingsPanelWidget()
    panel.resize(800, 600)
    panel.show()

    assert panel.stack.count() > 0

    for idx in range(panel.stack.count()):
        panel.stack.setCurrentIndex(idx)
        qapp.processEvents()
        current_widget = panel.stack.currentWidget()
        scroll_area = current_widget.findChild(QScrollArea)
        if scroll_area:
            sb = scroll_area.verticalScrollBar()
            assert sb.width() == 8

    panel.close()


def test_backup_dialog_scrollbar(qapp):
    """Verify that AgentBackupSelectionDialog scroll area uses 8px minimal scrollbar without border bleed."""
    mgr = MagicMock()
    mgr.get_all_agents.return_value = [f"agent_{i}" for i in range(25)]
    mgr.get_agent_name.side_effect = lambda aid: f"Agent {aid}"
    mgr.get_agent_uid.side_effect = lambda aid: f"+OA-0000-{aid[-4:]:0>4}"

    diag = AgentBackupSelectionDialog(mgr)
    diag.resize(400, 300)
    diag.show()

    sa = diag.findChild(QScrollArea)
    assert sa is not None
    sb = sa.verticalScrollBar()
    assert sb.width() == 8

    diag.close()


def test_item_text_edit_scrollbar_style(qapp):
    """Verify that ItemTextEdit includes modern scrollbar style."""
    item_edit = ItemTextEdit()
    assert "width: 8px;" in item_edit.styleSheet()
    assert "QPlainTextEdit {" in item_edit.styleSheet()


def test_view_footers_and_consoles(qapp):
    """Verify that footer widgets and console logs in AgentView and ChatroomView have proper selectors."""
    agent_mgr = MagicMock()
    agent_mgr.get_all_agents.return_value = ["default"]
    orch = MagicMock()
    orch.user_interacted = MagicMock()

    agent_view = AgentView(orchestrator=orch)
    assert agent_view.footer_widget.objectName() == "footerWidget"
    assert "QWidget#footerWidget" in agent_view.footer_widget.styleSheet()
    assert "QTextEdit {" in agent_view.console_log.styleSheet()
    assert "width: 8px;" in agent_view.console_log.styleSheet()

    chatroom_view = ChatroomView(agent_manager=agent_mgr)
    assert chatroom_view.footer_widget.objectName() == "footerWidget"
    assert "QWidget#footerWidget" in chatroom_view.footer_widget.styleSheet()
    assert "QTextEdit {" in chatroom_view.console_log.styleSheet()
    assert "width: 8px;" in chatroom_view.console_log.styleSheet()
