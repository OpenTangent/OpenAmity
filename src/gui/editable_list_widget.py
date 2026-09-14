import os
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QPlainTextEdit, QFrame
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent

try:
    from gui.theme import (
        PRIMARY_ACCENT_COLOR, BG_INPUT, BG_INPUT_FOCUS,
        BORDER_INPUT, BG_BUTTON, BG_BUTTON_HOVER,
        BORDER_BUTTON, TEXT_PRIMARY, TEXT_MUTED
    )
except ImportError:
    PRIMARY_ACCENT_COLOR = "#a12924"
    BG_INPUT = "#383838"
    BG_INPUT_FOCUS = "#404040"
    BORDER_INPUT = "#585858"
    BG_BUTTON = "#484848"
    BG_BUTTON_HOVER = "#585858"
    BORDER_BUTTON = "#666666"
    TEXT_PRIMARY = "#eeeeee"
    TEXT_MUTED = "#aaaaaa"


class ItemTextEdit(QPlainTextEdit):
    editing_finished = Signal()
    return_pressed = Signal()
    text_changed_signal = Signal()

    def __init__(self, placeholder="", parent=None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setTabChangesFocus(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.setFixedHeight(52)
        self._changed_since_focus = False
        self.textChanged.connect(self._on_text_changed)

        self.setStyleSheet(f"""
            QPlainTextEdit {{
                background-color: {BG_INPUT};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER_INPUT};
                border-radius: 5px;
                padding: 6px 8px;
                font-size: 13px;
                selection-background-color: {PRIMARY_ACCENT_COLOR};
            }}
            QPlainTextEdit:focus {{
                border: 1px solid {PRIMARY_ACCENT_COLOR};
                background-color: {BG_INPUT_FOCUS};
            }}
        """)

    def _on_text_changed(self):
        self._changed_since_focus = True
        self.text_changed_signal.emit()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        if self._changed_since_focus:
            self._changed_since_focus = False
            self.editing_finished.emit()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if event.modifiers() & Qt.ShiftModifier:
                super().keyPressEvent(event)
            else:
                self.return_pressed.emit()
                event.accept()
                return
        super().keyPressEvent(event)


class EditableItemRow(QWidget):
    move_up_requested = Signal(object)
    move_down_requested = Signal(object)
    delete_requested = Signal(object)
    return_pressed = Signal(object)
    editing_finished = Signal(object)
    text_changed = Signal(object)

    def __init__(self, text="", placeholder="", parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(6)

        # Reorder Buttons (Vertical Stack)
        reorder_widget = QWidget()
        reorder_layout = QVBoxLayout(reorder_widget)
        reorder_layout.setContentsMargins(0, 0, 0, 0)
        reorder_layout.setSpacing(2)

        self.btn_up = QPushButton("▲")
        self.btn_up.setFixedSize(26, 24)
        self.btn_up.setToolTip("Move up")
        self.btn_up.setCursor(Qt.PointingHandCursor)
        self.btn_up.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_BUTTON};
                color: {TEXT_MUTED};
                border: 1px solid {BORDER_BUTTON};
                border-radius: 3px;
                font-size: 10px;
                padding: 0px;
            }}
            QPushButton:hover {{
                background-color: {BG_BUTTON_HOVER};
                color: #FFF;
                border-color: #777;
            }}
            QPushButton:disabled {{
                background-color: #222;
                color: #444;
                border-color: #333;
            }}
        """)
        self.btn_up.clicked.connect(lambda: self.move_up_requested.emit(self))
        reorder_layout.addWidget(self.btn_up)

        self.btn_down = QPushButton("▼")
        self.btn_down.setFixedSize(26, 24)
        self.btn_down.setToolTip("Move down")
        self.btn_down.setCursor(Qt.PointingHandCursor)
        self.btn_down.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_BUTTON};
                color: {TEXT_MUTED};
                border: 1px solid {BORDER_BUTTON};
                border-radius: 3px;
                font-size: 10px;
                padding: 0px;
            }}
            QPushButton:hover {{
                background-color: {BG_BUTTON_HOVER};
                color: #FFF;
                border-color: #777;
            }}
            QPushButton:disabled {{
                background-color: #222;
                color: #444;
                border-color: #333;
            }}
        """)
        self.btn_down.clicked.connect(lambda: self.move_down_requested.emit(self))
        reorder_layout.addWidget(self.btn_down)

        layout.addWidget(reorder_widget, 0, Qt.AlignVCenter)

        # Text input
        self.text_edit = ItemTextEdit(placeholder=placeholder)
        if text:
            self.text_edit.setPlainText(text)
        self.text_edit.return_pressed.connect(lambda: self.return_pressed.emit(self))
        self.text_edit.editing_finished.connect(lambda: self.editing_finished.emit(self))
        self.text_edit.text_changed_signal.connect(lambda: self.text_changed.emit(self))
        layout.addWidget(self.text_edit, 1)

        # Delete Button
        self.btn_delete = QPushButton("✕")
        self.btn_delete.setFixedSize(28, 28)
        self.btn_delete.setToolTip("Delete item")
        self.btn_delete.setCursor(Qt.PointingHandCursor)
        self.btn_delete.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: #888;
                border: 1px solid #444;
                border-radius: 4px;
                font-size: 13px;
                font-weight: bold;
                padding: 0px;
            }}
            QPushButton:hover {{
                background-color: {PRIMARY_ACCENT_COLOR};
                color: #FFF;
                border-color: {PRIMARY_ACCENT_COLOR};
            }}
        """)
        self.btn_delete.clicked.connect(lambda: self.delete_requested.emit(self))
        layout.addWidget(self.btn_delete, 0, Qt.AlignVCenter)

    def get_text(self) -> str:
        return self.text_edit.toPlainText()

    def set_text(self, text: str):
        self.text_edit.setPlainText(text)

    def set_up_enabled(self, enabled: bool):
        self.btn_up.setEnabled(enabled)

    def set_down_enabled(self, enabled: bool):
        self.btn_down.setEnabled(enabled)

    def focus_input(self):
        self.text_edit.setFocus()
        cursor = self.text_edit.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.text_edit.setTextCursor(cursor)


class EditableItemListWidget(QWidget):
    items_changed = Signal()
    count_changed = Signal(int)

    def __init__(self, add_button_text="+ Add Item", placeholder_text="", empty_message="No items defined yet.", parent=None):
        super().__init__(parent)
        self.add_button_text = add_button_text
        self.placeholder_text = placeholder_text
        self.empty_message = empty_message
        self.rows = []

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(8)

        # Empty state label
        self.empty_label = QLabel(self.empty_message)
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet("""
            QLabel {
                color: #888;
                font-style: italic;
                padding: 14px;
                border: 1px dashed #444;
                border-radius: 6px;
                background-color: rgba(255, 255, 255, 0.02);
            }
        """)
        main_layout.addWidget(self.empty_label)

        # Container for rows
        self.rows_layout = QVBoxLayout()
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(6)
        main_layout.addLayout(self.rows_layout)

        # Add Button
        self.btn_add = QPushButton(self.add_button_text)
        self.btn_add.setCursor(Qt.PointingHandCursor)
        self.btn_add.setStyleSheet(f"""
            QPushButton {{
                background-color: #242424;
                color: #ccc;
                border: 1px dashed #555;
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: 500;
                font-size: 13px;
            }}
            QPushButton:hover {{
                background-color: #333333;
                color: #FFF;
                border-color: {PRIMARY_ACCENT_COLOR};
            }}
            QPushButton:pressed {{
                background-color: #1e1e1e;
            }}
        """)
        self.btn_add.clicked.connect(lambda: self.add_item("", focus=True))
        main_layout.addWidget(self.btn_add)

        self._update_states()

    def add_item(self, text: str = "", focus: bool = False) -> EditableItemRow:
        row = EditableItemRow(text=text, placeholder=self.placeholder_text, parent=self)
        row.move_up_requested.connect(self._on_move_up)
        row.move_down_requested.connect(self._on_move_down)
        row.delete_requested.connect(self._on_delete_row)
        row.return_pressed.connect(self._on_return_pressed)
        row.editing_finished.connect(lambda _: self.items_changed.emit())
        row.text_changed.connect(lambda _: self.items_changed.emit())

        self.rows.append(row)
        self.rows_layout.addWidget(row)
        self._update_states()

        if focus:
            row.focus_input()

        self.items_changed.emit()
        return row

    def _on_delete_row(self, row: EditableItemRow):
        if row in self.rows:
            self.rows.remove(row)
            self.rows_layout.removeWidget(row)
            row.deleteLater()
            self._update_states()
            self.items_changed.emit()

    def _on_move_up(self, row: EditableItemRow):
        if row in self.rows:
            idx = self.rows.index(row)
            if idx > 0:
                self.rows.pop(idx)
                self.rows.insert(idx - 1, row)
                self.rows_layout.removeWidget(row)
                self.rows_layout.insertWidget(idx - 1, row)
                self._update_states()
                row.focus_input()
                self.items_changed.emit()

    def _on_move_down(self, row: EditableItemRow):
        if row in self.rows:
            idx = self.rows.index(row)
            if idx < len(self.rows) - 1:
                self.rows.pop(idx)
                self.rows.insert(idx + 1, row)
                self.rows_layout.removeWidget(row)
                self.rows_layout.insertWidget(idx + 1, row)
                self._update_states()
                row.focus_input()
                self.items_changed.emit()

    def _on_return_pressed(self, row: EditableItemRow):
        if row in self.rows:
            idx = self.rows.index(row)
            if idx == len(self.rows) - 1:
                self.add_item("", focus=True)
            else:
                self.rows[idx + 1].focus_input()

    def _update_states(self):
        n = len(self.rows)
        self.empty_label.setVisible(n == 0)
        for i, row in enumerate(self.rows):
            row.set_up_enabled(i > 0)
            row.set_down_enabled(i < n - 1)
        self.count_changed.emit(n)

    def set_items(self, items: list[str]):
        for row in list(self.rows):
            self.rows_layout.removeWidget(row)
            row.deleteLater()
        self.rows.clear()

        for item_text in items:
            if item_text and str(item_text).strip():
                row = EditableItemRow(text=str(item_text), placeholder=self.placeholder_text, parent=self)
                row.move_up_requested.connect(self._on_move_up)
                row.move_down_requested.connect(self._on_move_down)
                row.delete_requested.connect(self._on_delete_row)
                row.return_pressed.connect(self._on_return_pressed)
                row.editing_finished.connect(lambda _: self.items_changed.emit())
                row.text_changed.connect(lambda _: self.items_changed.emit())
                self.rows.append(row)
                self.rows_layout.addWidget(row)

        self._update_states()
        self.items_changed.emit()

    def get_items(self) -> list[str]:
        result = []
        for row in self.rows:
            txt = row.get_text().strip()
            if txt:
                result.append(txt)
        return result

    def clear(self):
        self.set_items([])

    def count(self) -> int:
        return len(self.rows)

    def addItems(self, items: list[str]):
        for item in items:
            self.add_item(str(item), focus=False)

    class _ItemWrapper:
        def __init__(self, text):
            self._text = text

        def text(self):
            return self._text

    def item(self, index: int):
        if 0 <= index < len(self.rows):
            return self._ItemWrapper(self.rows[index].get_text())
        return self._ItemWrapper("")
