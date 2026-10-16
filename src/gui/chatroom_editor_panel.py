from typing import Optional, Dict, Any, List
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QCheckBox, QScrollArea, QFrame, QMessageBox,
    QSizePolicy
)
from PySide6.QtCore import Qt, Signal, QEvent

from core.chatroom_manager import ChatroomManager

try:
    from gui.theme import (
        BG_DARK, BG_CARD, BORDER_COLOR, PRIMARY_ACCENT_COLOR,
        TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED, MODERN_SCROLLBAR_STYLE
    )
except ImportError:
    BG_DARK = "#1a1a1a"
    BG_CARD = "#242424"
    BORDER_COLOR = "#3c3c3c"
    PRIMARY_ACCENT_COLOR = "#a12924"
    TEXT_PRIMARY = "#eeeeee"
    TEXT_SECONDARY = "#cccccc"
    TEXT_MUTED = "#aaaaaa"
    MODERN_SCROLLBAR_STYLE = ""


class AgentRosterItem(QFrame):
    """
    Encapsulated selectable item for an agent in the chatroom editor roster.
    Clicking the item toggles agent participation (access).
    Clicking the Watch button toggles Watch notifications for the agent.
    """
    toggled = Signal(bool)
    watch_toggled = Signal(bool)

    def __init__(
        self,
        uid: str,
        name: str,
        is_paused: bool = False,
        initial_access: bool = False,
        initial_watch: bool = False,
        parent: Optional[QWidget] = None
    ):
        super().__init__(parent)
        self.uid = uid
        self.name = name
        self.is_paused = is_paused
        self.setObjectName("agentRosterItem")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumHeight(44)

        # Headless proxies maintaining full backwards-compatibility with existing callers and tests
        self.cb_access = QCheckBox(self)
        self.cb_access.hide()
        self.cb_access.setChecked(initial_access)

        self.cb_watch = QCheckBox("Watch", self)
        self.cb_watch.hide()
        self.cb_watch.setChecked(bool(initial_watch and initial_access))
        self.cb_watch.setEnabled(initial_access)
        self.cb_watch.installEventFilter(self)

        self._build_ui()

        # Connect signals
        self.cb_access.toggled.connect(self._on_access_toggled)
        self.cb_watch.toggled.connect(self._on_watch_toggled)

        self._update_appearance()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(10)

        pause_str = " <span style='color: #ffb74d;'>⏸ (Paused)</span>" if self.is_paused else ""
        self.lbl_agent = QLabel(
            f"<span style='font-size: 14px; font-weight: bold; color: #FFFFFF;'>{self.name}</span> "
            f"<span style='font-size: 12px; color: #888888;'>({self.uid})</span>{pause_str}"
        )
        self.lbl_agent.setTextFormat(Qt.RichText)
        self.lbl_agent.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.lbl_agent.setStyleSheet("background-color: transparent;")
        layout.addWidget(self.lbl_agent)

        # Settings panel style green "✓ Selected" badge
        self.badge_selected = QLabel("✓ Selected")
        self.badge_selected.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.badge_selected.setStyleSheet(
            "background-color: #1b3820; "
            "border: 1px solid #2e7d32; "
            "color: #81c784; "
            "border-radius: 4px; "
            "padding: 2px 8px; "
            "font-weight: bold; "
            "font-size: 11px;"
        )
        layout.addWidget(self.badge_selected)

        layout.addStretch(1)

        # Encapsulated Watch button
        self.btn_watch = QPushButton("Watch")
        self.btn_watch.setObjectName("agentWatchBtn")
        self.btn_watch.setMinimumWidth(72)
        self.btn_watch.setFixedHeight(28)
        self.btn_watch.setFocusPolicy(Qt.TabFocus)
        self.btn_watch.clicked.connect(self._on_watch_clicked)
        layout.addWidget(self.btn_watch)

    def click(self):
        """Programmatically clicks the item to toggle participation."""
        self.cb_access.setChecked(not self.cb_access.isChecked())

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.click()
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.click()
            event.accept()
            return
        super().keyPressEvent(event)

    def eventFilter(self, watched, event):
        if watched == self.cb_watch and event.type() == QEvent.EnabledChange:
            self._update_watch_button()
        return super().eventFilter(watched, event)

    def _on_watch_clicked(self):
        if not self.cb_access.isChecked():
            return
        self.cb_watch.setChecked(not self.cb_watch.isChecked())

    def _on_access_toggled(self, checked: bool):
        self.cb_watch.setEnabled(checked)
        if not checked:
            self.cb_watch.setChecked(False)
        self._update_appearance()
        self.toggled.emit(checked)

    def _on_watch_toggled(self, checked: bool):
        self._update_watch_button()
        self.watch_toggled.emit(checked)

    def _update_appearance(self):
        is_selected = self.cb_access.isChecked()
        self.badge_selected.setVisible(is_selected)

        if is_selected:
            self.setStyleSheet("""
                QFrame#agentRosterItem {
                    background-color: #1b281d;
                    border: 1px solid #2e7d32;
                    border-radius: 6px;
                }
                QFrame#agentRosterItem:hover {
                    background-color: #223325;
                    border: 1px solid #388e3c;
                }
            """)
        else:
            self.setStyleSheet("""
                QFrame#agentRosterItem {
                    background-color: #1e1e1e;
                    border: 1px solid #383838;
                    border-radius: 6px;
                }
                QFrame#agentRosterItem:hover {
                    background-color: #272727;
                    border: 1px solid #4a4a4a;
                }
            """)

        self._update_watch_button()

    def _update_watch_button(self):
        is_access = self.cb_access.isChecked()
        is_watch = self.cb_watch.isChecked()
        is_enabled = self.cb_watch.isEnabled()

        if not is_access or not is_enabled:
            self.btn_watch.setEnabled(False)
            self.btn_watch.setCursor(Qt.ArrowCursor)
            self.btn_watch.setText("Watch")
            self.btn_watch.setToolTip("Enable agent participation first to configure Watch notifications.")
            self.btn_watch.setStyleSheet("""
                QPushButton#agentWatchBtn {
                    background-color: #1a1a1a;
                    border: 1px solid #2b2b2b;
                    color: #555555;
                    border-radius: 4px;
                    padding: 4px 12px;
                    font-size: 12px;
                    font-weight: normal;
                }
            """)
        elif is_watch:
            self.btn_watch.setEnabled(True)
            self.btn_watch.setCursor(Qt.PointingHandCursor)
            self.btn_watch.setText("✓ Watch")
            self.btn_watch.setToolTip("Watch enabled: this agent will receive notifications when new messages are posted.")
            self.btn_watch.setStyleSheet("""
                QPushButton#agentWatchBtn {
                    background-color: #1b3820;
                    border: 1px solid #2e7d32;
                    color: #81c784;
                    border-radius: 4px;
                    padding: 4px 12px;
                    font-size: 12px;
                    font-weight: bold;
                }
                QPushButton#agentWatchBtn:hover {
                    background-color: #244b2a;
                    border: 1px solid #388e3c;
                    color: #a5d6a7;
                }
            """)
        else:
            self.btn_watch.setEnabled(True)
            self.btn_watch.setCursor(Qt.PointingHandCursor)
            self.btn_watch.setText("Watch")
            self.btn_watch.setToolTip("Watch disabled: click to notify this agent when new messages are posted.")
            self.btn_watch.setStyleSheet("""
                QPushButton#agentWatchBtn {
                    background-color: #2a2a2a;
                    border: 1px solid #484848;
                    color: #999999;
                    border-radius: 4px;
                    padding: 4px 12px;
                    font-size: 12px;
                    font-weight: normal;
                }
                QPushButton#agentWatchBtn:hover {
                    background-color: #363636;
                    border: 1px solid #666666;
                    color: #FFFFFF;
                }
            """)


class ChatroomEditorPanel(QWidget):
    close_requested = Signal()
    saved = Signal(int)  # Emits chatroom_id on save
    new_agent_requested = Signal()

    def __init__(self, agent_manager, parent=None):
        super().__init__(parent)
        self.setObjectName("chatroomEditorPanel")
        self.agent_manager = agent_manager
        self.manager = ChatroomManager()
        self.is_edit_mode = False
        self.current_chatroom_id: Optional[int] = None

        # Agent rows mapping: agent_uid -> {"item": AgentRosterItem, "access_cb": QCheckBox, "watch_cb": QCheckBox, "watch_btn": QPushButton}
        self.agent_rows: Dict[str, Dict[str, Any]] = {}

        self.init_ui()


    def init_ui(self):
        self.setStyleSheet(f"""
            ChatroomEditorPanel, QWidget#chatroomEditorPanel {{
                background-color: {BG_DARK};
                color: {TEXT_PRIMARY};
                font-size: 14px;
            }}
            QFrame#editorCard {{
                background-color: {BG_CARD};
                border: 1px solid {BORDER_COLOR};
                border-radius: 8px;
            }}
            QLabel {{
                background-color: transparent;
                color: {TEXT_PRIMARY};
            }}
            QLineEdit {{
                background-color: #383838;
                color: #FFFFFF;
                border: 1px solid #585858;
                border-radius: 5px;
                padding: 7px 10px;
            }}
            QLineEdit:focus {{
                border: 1px solid {PRIMARY_ACCENT_COLOR};
                background-color: #404040;
            }}
            QPushButton {{
                background-color: #484848;
                color: #FFFFFF;
                border: 1px solid #666666;
                border-radius: 5px;
                padding: 8px 16px;
            }}
            QPushButton:hover {{
                background-color: #585858;
                border-color: #888888;
            }}
            QPushButton#panelCloseBtn {{
                background-color: transparent;
                color: #AAAAAA;
                border: none;
                font-size: 18px;
                font-weight: bold;
                padding: 0px;
                margin: 0px;
                border-radius: 15px;
                font-family: 'Ubuntu', 'DejaVu Sans', 'Noto Sans Symbols', 'Noto Sans Symbols2', 'FreeSans', sans-serif;
            }}
            QPushButton#panelCloseBtn:hover {{
                color: #FFFFFF;
                background-color: #383838;
            }}
            QPushButton#agentWatchBtn {{
                margin: 0px;
                padding: 4px 12px;
                border-radius: 4px;
                font-size: 12px;
            }}
            QCheckBox {{
                spacing: 8px;
                background-color: transparent;
                color: {TEXT_PRIMARY};
            }}
            QScrollArea, QScrollArea > QWidget > QWidget {{
                background-color: transparent;
                border: none;
            }}
            {MODERN_SCROLLBAR_STYLE}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(15)

        # Header Row
        header_layout = QHBoxLayout()
        self.lbl_title = QLabel("New Chatroom")
        self.lbl_title.setStyleSheet("font-size: 22px; font-weight: bold; color: #FFF; background-color: transparent;")
        header_layout.addWidget(self.lbl_title)
        header_layout.addStretch()

        self.btn_close = QPushButton("✕")
        self.btn_close.setObjectName("panelCloseBtn")
        self.btn_close.setFixedSize(30, 30)
        self.btn_close.setCursor(Qt.PointingHandCursor)
        self.btn_close.clicked.connect(self._on_close_clicked)
        header_layout.addWidget(self.btn_close)
        layout.addLayout(header_layout)

        # Scroll Area for Cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        content = QWidget()
        self.content_layout = QVBoxLayout(content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(16)
        self.content_layout.setAlignment(Qt.AlignTop)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        # Card 1: Room Details
        card_details = QFrame()
        card_details.setObjectName("editorCard")
        cd_layout = QVBoxLayout(card_details)
        cd_layout.setContentsMargins(18, 16, 18, 16)
        cd_layout.setSpacing(12)

        lbl_sec1 = QLabel("Chatroom Information")
        lbl_sec1.setStyleSheet("font-size: 15px; font-weight: bold; color: #FFF; background-color: transparent;")
        cd_layout.addWidget(lbl_sec1)

        lbl_name = QLabel("Room Name:")
        cd_layout.addWidget(lbl_name)
        self.txt_name = QLineEdit()
        self.txt_name.setMaxLength(40)
        self.txt_name.setPlaceholderText("e.g. Design Team, Research Lab, Project Alpha")
        cd_layout.addWidget(self.txt_name)

        lbl_purpose = QLabel("Purpose (Optional):")
        cd_layout.addWidget(lbl_purpose)
        self.txt_purpose = QLineEdit()
        self.txt_purpose.setMaxLength(200)
        self.txt_purpose.setPlaceholderText("Brief description of the room's purpose and scope")
        cd_layout.addWidget(self.txt_purpose)

        self.content_layout.addWidget(card_details)

        # Card 2: Participating Agents
        card_members = QFrame()
        card_members.setObjectName("editorCard")
        cm_layout = QVBoxLayout(card_members)
        cm_layout.setContentsMargins(18, 16, 18, 16)
        cm_layout.setSpacing(12)

        self.lbl_sec2 = QLabel("Participating Agents")
        self.lbl_sec2.setStyleSheet("font-size: 15px; font-weight: bold; color: #FFF; background-color: transparent;")
        cm_layout.addWidget(self.lbl_sec2)

        lbl_hint = QLabel("Select which agents can participate. Enabling 'Watch' notifies the agent when new messages are posted.")
        lbl_hint.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 13px; background-color: transparent;")
        lbl_hint.setWordWrap(True)
        cm_layout.addWidget(lbl_hint)

        self.btn_new_agent = QPushButton("+ New Agent")
        self.btn_new_agent.setCursor(Qt.PointingHandCursor)
        self.btn_new_agent.setStyleSheet("""
            QPushButton {
                background-color: #333333;
                color: #EEEEEE;
                border: 1px solid #555555;
                border-radius: 4px;
                padding: 6px 14px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #444444;
                color: #FFFFFF;
                border-color: #777777;
            }
        """)
        self.btn_new_agent.clicked.connect(self._on_new_agent_clicked)
        cm_layout.addWidget(self.btn_new_agent, 0, Qt.AlignLeft)

        self.members_container = QWidget()
        self.members_container.setStyleSheet("background-color: transparent;")
        self.members_layout = QVBoxLayout(self.members_container)
        self.members_layout.setContentsMargins(0, 4, 0, 4)
        self.members_layout.setSpacing(8)
        cm_layout.addWidget(self.members_container)

        self.content_layout.addWidget(card_members)

        # Bottom Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setMinimumWidth(100)
        self.btn_cancel.clicked.connect(self.close_requested.emit)
        btn_layout.addWidget(self.btn_cancel)

        self.btn_save = QPushButton("Save Chatroom")
        self.btn_save.setMinimumWidth(120)
        self.btn_save.clicked.connect(self._on_save_clicked)
        btn_layout.addWidget(self.btn_save)

        layout.addLayout(btn_layout)

    def load_new_chatroom(self):
        self.is_edit_mode = False
        self.current_chatroom_id = None
        self.lbl_title.setText("New Chatroom")
        self.txt_name.clear()
        self.txt_purpose.clear()
        self._populate_agent_roster({})

    def load_edit_chatroom(self, chatroom_id: int):
        self.is_edit_mode = True
        self.current_chatroom_id = chatroom_id
        self.lbl_title.setText("Edit Chatroom")

        room = self.manager.get_chatroom(chatroom_id)
        if room:
            self.txt_name.setText(room["name"])
            self.txt_purpose.setText(room.get("purpose", "") or "")

        members = self.manager.get_members(chatroom_id)
        member_map = {m["agent_uid"]: m["watch"] for m in members}
        self._populate_agent_roster(member_map)

    def _populate_agent_roster(self, member_map: Dict[str, bool]):
        # Clear existing rows
        while self.members_layout.count():
            item = self.members_layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)

        self.agent_rows.clear()

        # Get agents ordered oldest to newest
        agent_ids = self.agent_manager.get_agents_by_age() if self.agent_manager else []

        for aid in agent_ids:
            uid = self.agent_manager.get_agent_uid(aid)
            name = self.agent_manager.get_agent_name(aid)
            orch = self.agent_manager.get_orchestrator(aid)
            is_paused = getattr(orch, "is_paused", False)

            roster_item = AgentRosterItem(
                uid=uid,
                name=name,
                is_paused=is_paused,
                initial_access=(uid in member_map),
                initial_watch=bool(member_map.get(uid, False)),
                parent=self.members_container
            )

            self.members_layout.addWidget(roster_item)
            self.agent_rows[uid] = {
                "item": roster_item,
                "access_cb": roster_item.cb_access,
                "watch_cb": roster_item.cb_watch,
                "watch_btn": roster_item.btn_watch,
            }

    def _on_access_toggled(self, checked: bool, watch_cb: QCheckBox):
        watch_cb.setEnabled(checked)
        if not checked:
            watch_cb.setChecked(False)

    def _on_new_agent_clicked(self):
        self._on_close_clicked()
        self.new_agent_requested.emit()

    def _on_close_clicked(self):
        if not self.is_edit_mode:
            # In New Chatroom mode: [x] discards any changes and does not create a chatroom
            self.close_requested.emit()
        else:
            # In Edit Chatroom mode: [x] saves any changes without asking (consistent with Settings panel)
            self._save_changes()

    def _on_save_clicked(self):
        self._save_changes()

    def _save_changes(self):
        name = self.txt_name.text().strip()
        purpose = self.txt_purpose.text().strip()

        if not name:
            QMessageBox.warning(self, "Invalid Name", "Please enter a name for the chatroom.")
            return

        if name.lower() == "all agents":
            QMessageBox.warning(self, "Reserved Name", "'All Agents' is a reserved tab name. Please choose another name.")
            return

        members_payload = []
        for uid, cbs in self.agent_rows.items():
            if cbs["access_cb"].isChecked():
                members_payload.append({
                    "agent_uid": uid,
                    "watch": cbs["watch_cb"].isChecked()
                })

        try:
            if not self.is_edit_mode:
                rid = self.manager.create_chatroom(
                    name=name,
                    members=members_payload,
                    purpose=purpose
                )
                self.saved.emit(rid)
                self.close_requested.emit()
            else:
                self.manager.update_chatroom(
                    chatroom_id=self.current_chatroom_id,
                    name=name,
                    purpose=purpose,
                    members=members_payload
                )
                self.saved.emit(self.current_chatroom_id)
                self.close_requested.emit()
        except Exception as e:
            QMessageBox.critical(self, "Error Saving Chatroom", str(e))
