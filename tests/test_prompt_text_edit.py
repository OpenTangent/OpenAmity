import os
import sys
import pytest

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from gui.main_window import PromptTextEdit


@pytest.fixture(scope="session")
def qapp():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication.instance()
    if app is None:
        app = QApplication(["-platform", "offscreen"])
    return app


def test_prompt_text_edit_height_constants():
    assert PromptTextEdit.MIN_HEIGHT == 45
    assert PromptTextEdit.MAX_HEIGHT == 240


def test_prompt_text_edit_dynamic_height_growth(qapp):
    prompt = PromptTextEdit()
    prompt.resize(400, 45)
    prompt.show()

    # Initial state (empty)
    assert prompt.height() == 45
    assert prompt.verticalScrollBarPolicy() == Qt.ScrollBarAlwaysOff

    # Single line
    prompt.setPlainText("Hello world")
    qapp.processEvents()
    assert 45 <= prompt.height() <= 60

    # Multiple lines - should grow beyond 45 and beyond previous 120 limit
    multi_line_text = "\n".join([f"Line {i}" for i in range(10)])
    prompt.setPlainText(multi_line_text)
    qapp.processEvents()

    # Height should have grown past 120
    assert prompt.height() > 120
    assert prompt.height() <= 240

    # Many lines - should cap at 240
    many_lines_text = "\n".join([f"Long prompt line {i}" for i in range(30)])
    prompt.setPlainText(many_lines_text)
    qapp.processEvents()

    assert prompt.height() == 240
    assert prompt.verticalScrollBarPolicy() == Qt.ScrollBarAsNeeded

    # Clearing text shrinks it back to single line height
    prompt.clear()
    qapp.processEvents()

    assert prompt.height() <= 60
    assert prompt.verticalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
    prompt.close()
