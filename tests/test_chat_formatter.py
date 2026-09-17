import os
import sys
import pytest

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PySide6.QtWidgets import QApplication, QTextBrowser
from PySide6.QtGui import QFontDatabase, QTextOption
from gui.chat_formatter import (
    HtmlTagBalancer,
    balance_html,
    render_markdown_to_html,
    render_user_message_content,
    wrap_in_isolated_container,
    insert_message_into_log,
    setup_chat_browser,
)
from gui.theme import CHAT_DOCUMENT_CSS, CHAT_FONT_FAMILY


@pytest.fixture(scope="session")
def qapp():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication.instance()
    if app is None:
        app = QApplication(["-platform", "offscreen"])
    return app


def test_html_tag_balancer():
    # Unclosed tags should be balanced
    assert balance_html("<b><i>Hello") == "<b><i>Hello</i></b>"
    assert balance_html("<div><span>World") == "<div><span>World</span></div>"
    assert balance_html("<ol><li>Item 1<li>Item 2") == "<ol><li>Item 1<li>Item 2</li></li></ol>"

    # Already closed tags should remain unchanged
    assert balance_html("<p><b>Test</b></p>") == "<p><b>Test</b></p>"

    # Void tags should not be closed with end tags
    assert balance_html("<p>Line 1<br>Line 2<hr></p>") == "<p>Line 1<br>Line 2<hr></p>"

    # Empty or None string
    assert balance_html("") == ""
    assert balance_html(None) == ""


def test_markdown_rendering():
    md = """# Header 1
A paragraph with **bold** and *italic* and `inline code`.

```python
def test_fn():
    return 42
```

| Header A | Header B |
| :--- | :--- |
| Val 1 | Val 2 |
"""
    rendered = render_markdown_to_html(md)
    assert "<h1" in rendered
    assert "<strong>bold</strong>" in rendered
    assert "<em>italic</em>" in rendered
    assert "<code>inline code</code>" in rendered
    assert "<pre><code" in rendered
    assert "<table>" in rendered


def test_user_message_content_escaping():
    user_input = "<script>alert('xss')</script> **safe bold** and `code < 3`"
    rendered = render_user_message_content(user_input)

    # Raw script tag must be escaped
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    # Markdown formatting should still be applied
    assert "<strong>safe bold</strong>" in rendered
    assert "<code>code &lt; 3</code>" in rendered


def test_wrap_in_isolated_container():
    inner = "<p>Message body<b>"
    wrapped = wrap_in_isolated_container(
        inner,
        margin_bottom=14,
        bg_color="#222225",
        border_color="#36363b",
        border_radius=8,
        padding="12px 18px",
    )
    assert wrapped.startswith('<table width="100%"')
    assert 'cellpadding="12"' in wrapped
    assert 'bgcolor="#222225"' in wrapped
    assert 'padding: 12px 18px;' in wrapped
    assert 'border-radius: 8px;' in wrapped
    assert "style=\"margin-bottom: 14px; background-color: #222225; border: 1px solid #36363b; border-radius: 8px;\"" in wrapped
    assert "</b>" in wrapped  # Must balance inner tags
    assert wrapped.endswith("</td></tr></table>")


def test_message_isolation_and_list_containment(qapp):
    browser = QTextBrowser()
    setup_chat_browser(browser)

    # Message 1: Has a numbered list and an unclosed bold tag
    msg1_text = "1. First step\n2. Second step\n\n<b>Unclosed styling"
    msg1_html = wrap_in_isolated_container(f"<div>{render_markdown_to_html(msg1_text)}</div>")
    insert_message_into_log(browser, msg1_html)

    # Message 2: Has a new numbered list
    msg2_text = "1. New task 1\n2. New task 2\n\nNormal paragraph"
    msg2_html = wrap_in_isolated_container(f"<div>{render_markdown_to_html(msg2_text)}</div>")
    insert_message_into_log(browser, msg2_html)

    # Verify lists did not merge
    doc = browser.document()
    block = doc.begin()
    list_items = []
    while block.isValid():
        tl = block.textList()
        if tl:
            list_items.append((block.text().strip(), tl.itemNumber(block)))
        block = block.next()

    # We should have 4 list items total:
    # First list: indices 0, 1
    # Second list: indices 0, 1 (MUST NOT continue to 2, 3!)
    assert len(list_items) == 4, f"Expected 4 list items, got {list_items}"
    assert list_items[0] == ("First step", 0)
    assert list_items[1] == ("Second step", 1)
    assert list_items[2] == ("New task 1", 0)
    assert list_items[3] == ("New task 2", 1)


def test_setup_chat_browser(qapp):
    browser = QTextBrowser()
    setup_chat_browser(browser)

    assert browser.isReadOnly() is True
    assert browser.lineWrapMode() == QTextBrowser.LineWrapMode.WidgetWidth
    assert browser.wordWrapMode() == QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere
    assert browser.document().defaultStyleSheet() == CHAT_DOCUMENT_CSS
    assert CHAT_FONT_FAMILY in browser.styleSheet()
    assert "font-size: 18px;" in browser.styleSheet()
    assert CHAT_FONT_FAMILY == "EB Garamond"


def test_eb_garamond_font_registered(qapp):
    font_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "assets", "fonts"))
    font_files = ["EBGaramond-Regular.ttf", "EBGaramond-Italic.ttf"]

    for fname in font_files:
        fpath = os.path.join(font_dir, fname)
        assert os.path.exists(fpath), f"Font file missing: {fpath}"
        font_id = QFontDatabase.addApplicationFont(fpath)
        assert font_id >= 0, f"Failed to register font: {fpath}"
        families = QFontDatabase.applicationFontFamilies(font_id)
        assert any("EB Garamond" in fam for fam in families), f"EB Garamond family not found in {families}"
