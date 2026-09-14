import requests
import io
import os
import threading
import qrcode
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                               QLabel, QLineEdit, QTextEdit, QComboBox, QCheckBox,
                               QRadioButton, QButtonGroup, QStackedWidget, QListWidget, QInputDialog, QSlider,
                               QSpinBox, QScrollArea, QFrame, QFileDialog, QMessageBox, QDialog,
                               QProgressBar)
from PySide6.QtCore import Qt, Signal, QObject, QEvent, QTimer, QThread
from PySide6.QtGui import QPixmap, QImage

import smtplib
import imaplib
import ssl

from core.email_auth import execute_desktop_oauth_flow, exchange_authorization_code
from core.config_manager import ConfigManager

try:
    from gui.editable_list_widget import EditableItemListWidget
except ImportError:
    from editable_list_widget import EditableItemListWidget

try:
    from gui.theme import (PRIMARY_ACCENT_COLOR, SECONDARY_ACCENT_COLOR,
                           BG_DARK, BG_CARD, BG_CARD_DISABLED, BG_INPUT, BG_INPUT_FOCUS,
                           BG_BUTTON, BG_BUTTON_HOVER, BG_BUTTON_PRESSED,
                           BORDER_COLOR, BORDER_COLOR_DISABLED, BORDER_INPUT, BORDER_BUTTON,
                           TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED)
except ImportError:
    PRIMARY_ACCENT_COLOR = "#a12924"
    SECONDARY_ACCENT_COLOR = "#f7e3a5"
    BG_DARK = "#1a1a1a"
    BG_CARD = "#242424"
    BG_CARD_DISABLED = "#1a1a1a"
    BG_INPUT = "#383838"
    BG_INPUT_FOCUS = "#404040"
    BG_BUTTON = "#484848"
    BG_BUTTON_HOVER = "#585858"
    BG_BUTTON_PRESSED = "#383838"
    BORDER_COLOR = "#3c3c3c"
    BORDER_COLOR_DISABLED = "#2e2e2e"
    BORDER_INPUT = "#585858"
    BORDER_BUTTON = "#666666"
    TEXT_PRIMARY = "#eeeeee"
    TEXT_SECONDARY = "#cccccc"
    TEXT_MUTED = "#aaaaaa"


EMAIL_PRESETS = {
    "Gmail / Google Workspace": {
        "imap_host": "imap.gmail.com",
        "imap_port": "993",
        "imap_sec": "SSL/TLS",
        "smtp_host": "smtp.gmail.com",
        "smtp_port": "465",
        "smtp_sec": "SSL/TLS",
        "tip": "Use a 16-character Google App Password (from <a href='https://myaccount.google.com/apppasswords'>myaccount.google.com/apppasswords</a>) or OAuth 2.0."
    },
    "Outlook / Microsoft 365": {
        "imap_host": "outlook.office365.com",
        "imap_port": "993",
        "imap_sec": "SSL/TLS",
        "smtp_host": "smtp.office365.com",
        "smtp_port": "587",
        "smtp_sec": "STARTTLS",
        "tip": "For Microsoft accounts with 2FA, create an App Password in your Microsoft Account Security settings."
    },
    "Yahoo Mail": {
        "imap_host": "imap.mail.yahoo.com",
        "imap_port": "993",
        "imap_sec": "SSL/TLS",
        "smtp_host": "smtp.mail.yahoo.com",
        "smtp_port": "465",
        "smtp_sec": "SSL/TLS",
        "tip": "Generate an App Password in Yahoo Account Security to authenticate."
    },
    "iCloud Mail": {
        "imap_host": "imap.mail.me.com",
        "imap_port": "993",
        "imap_sec": "SSL/TLS",
        "smtp_host": "smtp.mail.me.com",
        "smtp_port": "587",
        "smtp_sec": "STARTTLS",
        "tip": "Generate an app-specific password at <a href='https://appleid.apple.com'>appleid.apple.com</a> to connect."
    },
    "Fastmail": {
        "imap_host": "imap.fastmail.com",
        "imap_port": "993",
        "imap_sec": "SSL/TLS",
        "smtp_host": "smtp.fastmail.com",
        "smtp_port": "465",
        "smtp_sec": "SSL/TLS",
        "tip": "Create an App Password with Mail access in Fastmail Settings > Password & Security."
    },
    "Custom (IMAP / SMTP)": {
        "imap_host": "",
        "imap_port": "993",
        "imap_sec": "SSL/TLS",
        "smtp_host": "",
        "smtp_port": "587",
        "smtp_sec": "STARTTLS",
        "tip": "Configure standard IMAP and SMTP server settings below."
    }
}


def detect_provider_from_email(email_str: str) -> str:
    email_str = email_str.strip().lower()
    if "@gmail.com" in email_str or "@googlemail.com" in email_str:
        return "Gmail / Google Workspace"
    elif any(d in email_str for d in ["@outlook.", "@hotmail.", "@live.", "@msn."]):
        return "Outlook / Microsoft 365"
    elif "@yahoo." in email_str or "@ymail.com" in email_str:
        return "Yahoo Mail"
    elif any(d in email_str for d in ["@icloud.com", "@me.com", "@mac.com"]):
        return "iCloud Mail"
    elif "@fastmail." in email_str:
        return "Fastmail"
    return "Custom (IMAP / SMTP)"


class EmailConnectionTestWorker(QThread):
    finished_signal = Signal(bool, str)

    def __init__(self, host_imap, port_imap, sec_imap, host_smtp, port_smtp, sec_smtp, username, password, auth_type="password", client_id="", client_secret="", refresh_token="", custom_user="", parent=None):
        super().__init__(parent)
        self.host_imap = str(host_imap).strip()
        self.port_imap = str(port_imap).strip()
        self.sec_imap = str(sec_imap).strip()
        self.host_smtp = str(host_smtp).strip()
        self.port_smtp = str(port_smtp).strip()
        self.sec_smtp = str(sec_smtp).strip()
        self.email_address = str(username).strip()
        self.auth_user = str(custom_user).strip() if str(custom_user).strip() else self.email_address
        self.password = password
        self.auth_type = str(auth_type).strip().lower()
        self.client_id = str(client_id).strip()
        self.client_secret = str(client_secret).strip()
        self.refresh_token = str(refresh_token).strip()

    def run(self):
        if not self.email_address:
            self.finished_signal.emit(False, "Please enter an Email Address first.")
            return

        if not self.host_imap or not self.host_smtp:
            self.finished_signal.emit(False, "IMAP and SMTP server hosts cannot be empty.")
            return

        xoauth2_str_raw = None
        xoauth2_str_b64 = None
        if self.auth_type == "oauth2":
            if not self.client_id or not self.client_secret or not self.refresh_token:
                self.finished_signal.emit(False, "OAuth 2.0 requires Client ID, Client Secret, and Refresh Token.")
                return
            try:
                from core.email_auth import OAuth2TokenManager, build_xoauth2_string
                token_mgr = OAuth2TokenManager()
                ok, access_token_or_err = token_mgr.get_access_token(
                    self.client_id, self.client_secret, self.refresh_token)
                if not ok:
                    self.finished_signal.emit(False, f"OAuth Token Refresh Failed: {access_token_or_err}")
                    return
                xoauth2_str_raw = build_xoauth2_string(self.email_address, access_token_or_err, as_base64=False)
                xoauth2_str_b64 = build_xoauth2_string(self.email_address, access_token_or_err, as_base64=True)
            except Exception as e:
                self.finished_signal.emit(False, f"OAuth Error: {str(e)}")
                return
        else:
            if not self.password:
                self.finished_signal.emit(False, "Please enter a Password or App Password.")
                return

        # 1. Test IMAP
        try:
            port_imap = int(self.port_imap) if self.port_imap else 993
            if self.sec_imap == "SSL/TLS":
                context = ssl.create_default_context()
                imap = imaplib.IMAP4_SSL(self.host_imap, port_imap, ssl_context=context)
            else:
                imap = imaplib.IMAP4(self.host_imap, port_imap)
                if self.sec_imap == "STARTTLS":
                    context = ssl.create_default_context()
                    imap.starttls(ssl_context=context)

            if self.auth_type == "oauth2":
                imap.authenticate("XOAUTH2", lambda x: xoauth2_str_raw)
            else:
                imap.login(self.auth_user, self.password)

            imap.noop()
            try:
                imap.logout()
            except Exception:
                pass
        except Exception as e:
            self.finished_signal.emit(False, f"IMAP Error: {str(e)}")
            return

        # 2. Test SMTP
        try:
            port_smtp = int(self.port_smtp) if self.port_smtp else 465
            if self.sec_smtp == "SSL/TLS":
                context = ssl.create_default_context()
                smtp = smtplib.SMTP_SSL(self.host_smtp, port_smtp, context=context, timeout=10)
            else:
                smtp = smtplib.SMTP(self.host_smtp, port_smtp, timeout=10)
                if self.sec_smtp == "STARTTLS":
                    context = ssl.create_default_context()
                    smtp.starttls(context=context)

            smtp.ehlo()
            if self.auth_type == "oauth2":
                smtp.docmd("AUTH", f"XOAUTH2 {xoauth2_str_b64}")
            else:
                smtp.login(self.auth_user, self.password)

            try:
                smtp.quit()
            except Exception:
                pass
        except Exception as e:
            self.finished_signal.emit(False, f"IMAP connected OK, but SMTP Error: {str(e)}")
            return

        self.finished_signal.emit(True, "IMAP & SMTP connected and authenticated successfully!")


class BackupWorkerThread(QThread):
    progress_changed = Signal(int, int, str)  # current_step, total_steps, message
    backup_completed = Signal(list, list)     # created_paths, errors

    def __init__(self, agent_ids, dest_dir, agent_manager, parent=None):
        super().__init__(parent)
        self.agent_ids = agent_ids
        self.dest_dir = dest_dir
        self.agent_manager = agent_manager

    def run(self):
        from core.backup_manager import create_agent_backup
        created = []
        failed = []
        total = len(self.agent_ids)

        for idx, aid in enumerate(self.agent_ids):
            agent_name = "Agent"
            if self.agent_manager and hasattr(self.agent_manager, 'get_agent_name'):
                agent_name = self.agent_manager.get_agent_name(aid)

            self.progress_changed.emit(idx, total, f"Backing up '{agent_name}' ({idx + 1}/{total})...")

            try:
                def file_cb(fname, f_idx, f_tot):
                    self.progress_changed.emit(idx, total, f"Backing up '{agent_name}': {fname} ({f_idx}/{f_tot})")

                path = create_agent_backup(
                    aid,
                    destination_dir=self.dest_dir,
                    agent_manager=self.agent_manager,
                    progress_callback=file_cb
                )
                created.append(path)
            except Exception as e:
                failed.append(f"{agent_name}: {str(e)}")

        self.progress_changed.emit(total, total, "Backup process complete!")
        self.backup_completed.emit(created, failed)


class AgentBackupSelectionDialog(QDialog):
    def __init__(self, agent_manager, dest_dir=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create Agent Backups")
        self.resize(460, 380)
        self.agent_manager = agent_manager
        self.dest_dir = dest_dir or "~/Documents/OpenAmity/Backups"
        self.selected_agent_ids = []
        self.created_backups = []
        self.failed_backups = []
        self.checkboxes = {}
        self.worker_thread = None

        self.setStyleSheet(f"""
            QDialog {{ background-color: {BG_CARD}; color: {TEXT_PRIMARY}; }}
            QLabel {{ color: {TEXT_PRIMARY}; font-size: 14px; background-color: transparent; }}
            QCheckBox {{ color: {TEXT_PRIMARY}; font-size: 14px; spacing: 8px; background-color: transparent; }}
            QCheckBox:hover {{ color: #FFF; }}
            QPushButton {{ background-color: {BG_BUTTON}; color: {TEXT_PRIMARY}; border: 1px solid #666; border-radius: 5px; padding: 8px 16px; }}
            QPushButton:hover {{ background-color: #555; }}
            QProgressBar {{
                border: 1px solid #555;
                border-radius: 5px;
                text-align: center;
                background-color: #333;
                color: #FFF;
                font-size: 12px;
                font-weight: bold;
                height: 20px;
            }}
            QProgressBar::chunk {{
                background-color: {PRIMARY_ACCENT_COLOR};
                border-radius: 4px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        lbl = QLabel("<b>Select which agents to backup:</b>")
        layout.addWidget(lbl)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background-color: #2a2a2a; border: 1px solid #444; border-radius: 5px;")
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(15, 15, 15, 15)
        scroll_layout.setSpacing(10)

        agent_ids = self.agent_manager.get_all_agents() if self.agent_manager else []
        for aid in agent_ids:
            name = self.agent_manager.get_agent_name(aid)
            uid = self.agent_manager.get_agent_uid(aid)
            cb = QCheckBox(f"{name} ({uid})")
            cb.setChecked(True)
            self.checkboxes[aid] = cb
            scroll_layout.addWidget(cb)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll, 1)

        # Progress Area
        self.progress_container = QWidget()
        progress_layout = QVBoxLayout(self.progress_container)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(6)

        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet("color: #AAA; font-size: 12px;")
        progress_layout.addWidget(self.lbl_status)

        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        progress_layout.addWidget(self.progress_bar)

        self.progress_container.hide()
        layout.addWidget(self.progress_container)

        # Bottom buttons
        btn_layout = QHBoxLayout()
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)

        self.btn_backup = QPushButton("Backup")
        self.btn_backup.setStyleSheet(
            f"background-color: {PRIMARY_ACCENT_COLOR}; color: #FFF; font-weight: bold; border-radius: 5px; padding: 8px 20px;")
        self.btn_backup.clicked.connect(self.on_start_backup)

        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addWidget(self.btn_backup)
        layout.addLayout(btn_layout)

    def on_start_backup(self):
        self.selected_agent_ids = [aid for aid, cb in self.checkboxes.items() if cb.isChecked()]
        if not self.selected_agent_ids:
            QMessageBox.warning(self, "No Agents Selected", "Please select at least one agent to backup.")
            return

        for cb in self.checkboxes.values():
            cb.setEnabled(False)
        self.btn_backup.setEnabled(False)
        self.btn_cancel.setEnabled(False)

        total = len(self.selected_agent_ids)
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(0)
        self.lbl_status.setText("Initializing backup...")
        self.progress_container.show()

        self.worker_thread = BackupWorkerThread(
            self.selected_agent_ids,
            self.dest_dir,
            self.agent_manager,
            parent=self
        )
        self.worker_thread.progress_changed.connect(self._on_progress_changed)
        self.worker_thread.backup_completed.connect(self._on_backup_completed)
        self.worker_thread.start()

    def _on_progress_changed(self, current, total, message):
        self.lbl_status.setText(message)
        self.progress_bar.setValue(current)

    def _on_backup_completed(self, created, failed):
        self.created_backups = created
        self.failed_backups = failed

        self.progress_bar.setValue(len(self.selected_agent_ids))
        self.lbl_status.setText("Backup process finished.")

        self.btn_cancel.setText("Close")
        self.btn_cancel.setEnabled(True)

        if created:
            msg = f"Successfully created {len(created)} backup(s) in:\n{os.path.expanduser(self.dest_dir)}\n\n"
            msg += "\n".join([f"• {os.path.basename(p)}" for p in created])
            if failed:
                msg += "\n\nErrors:\n" + "\n".join(failed)
            QMessageBox.information(self, "Backups Created", msg)
            self.accept()
        else:
            QMessageBox.critical(self, "Backup Failed", "Failed to create backups:\n" + "\n".join(failed))
            for cb in self.checkboxes.values():
                cb.setEnabled(True)
            self.btn_backup.setEnabled(True)
            self.btn_cancel.setText("Cancel")

    def closeEvent(self, event):
        if hasattr(self, 'worker_thread') and self.worker_thread and self.worker_thread.isRunning():
            event.ignore()
            return
        super().closeEvent(event)


BackupProgressDialog = AgentBackupSelectionDialog


class FocusOutFilter(QObject):
    def __init__(self, callback, parent=None):
        super().__init__(parent)
        self.callback = callback

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.FocusOut:
            self.callback()
        return super().eventFilter(obj, event)


class SettingsPanelWidget(QWidget):
    close_requested = Signal()
    wizard_finished = Signal()
    settings_saved = Signal()
    oauth_finished = Signal(bool, str, str)
    agent_restored = Signal(str, bool)
    whatsapp_status_updated = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.wizard_mode = False
        self.oauth_finished.connect(self._handle_oauth_result)
        self.whatsapp_status_updated.connect(self._on_whatsapp_status_updated)
        self._whatsapp_polling = False
        self.current_section_index = 0
        self.settings = None
        self.config = ConfigManager()
        self.agent_manager = None
        self._is_loading = False

        self.focus_out_filter = FocusOutFilter(self.save_settings, self)

        self.save_buttons = []
        self.close_buttons = []

        self.whatsapp_timer = QTimer(self)
        self.whatsapp_timer.timeout.connect(self.poll_whatsapp_status)
        self.whatsapp_timer.start(2000)

        self.init_ui()
        self.load_system_config()

    def _on_whatsapp_status_updated(self, status: str, payload: str):
        if not hasattr(self, 'ui_qr_code') or self.ui_qr_code is None:
            return
        if status == "disabled":
            self.ui_qr_code.setText("[WhatsApp Disabled]")
            self.ui_qr_code.setPixmap(QPixmap())
        elif status == "ready":
            self.ui_qr_code.setPixmap(QPixmap())
            self.ui_qr_code.setText("✓ WhatsApp Authenticated")
        elif status == "qr":
            try:
                qr_img = qrcode.make(payload)
                buf = io.BytesIO()
                qr_img.save(buf, format="PNG")
                img = QImage.fromData(buf.getvalue())
                pixmap = QPixmap.fromImage(img)
                self.ui_qr_code.setPixmap(
                    pixmap.scaled(200, 200, Qt.KeepAspectRatio))
            except Exception:
                self.ui_qr_code.setPixmap(QPixmap())
                self.ui_qr_code.setText("[Error Rendering QR]")
        elif status == "waiting":
            self.ui_qr_code.setPixmap(QPixmap())
            self.ui_qr_code.setText("[Waiting for QR...]")
        elif status == "offline":
            self.ui_qr_code.setPixmap(QPixmap())
            self.ui_qr_code.setText("[WhatsApp Bridge Not Running]")

    def poll_whatsapp_status(self):
        # Only poll if the current tab is Social Accounts (index 4)
        if self.stack.currentIndex() != 4:
            return

        if not hasattr(self, 'ui_use_whatsapp') or not self.ui_use_whatsapp.isChecked():
            self._on_whatsapp_status_updated("disabled", "")
            return

        if getattr(self, '_whatsapp_polling', False):
            return

        self._whatsapp_polling = True
        agent_id = self.settings.agent_id if hasattr(self, 'settings') and self.settings else None

        def _worker():
            try:
                from core.whatsapp_daemon import WhatsAppDaemon
                port = WhatsAppDaemon.get_port_for_agent(agent_id)
                res = requests.get(f"http://localhost:{port}/status", timeout=1)
                if res.status_code == 200:
                    data = res.json()
                    if data.get("ready"):
                        self.whatsapp_status_updated.emit("ready", "")
                    elif data.get("qr"):
                        self.whatsapp_status_updated.emit("qr", data["qr"])
                    else:
                        self.whatsapp_status_updated.emit("waiting", "")
                else:
                    self.whatsapp_status_updated.emit("offline", "")
            except Exception:
                self.whatsapp_status_updated.emit("offline", "")
            finally:
                self._whatsapp_polling = False

        threading.Thread(target=_worker, daemon=True).start()

    check_whatsapp_status = poll_whatsapp_status

    def init_ui(self):
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {BG_DARK};
                color: {TEXT_PRIMARY};
                font-size: 14px;
            }}
            QFrame#settingsCard {{
                background-color: {BG_CARD};
                border: 1px solid {BORDER_COLOR};
                border-radius: 8px;
            }}
            QFrame#settingsCard[cardState="disabled"] {{
                background-color: {BG_CARD_DISABLED};
                border: 1px solid {BORDER_COLOR_DISABLED};
            }}
            QWidget#settingsCardBody, QWidget#settingsSubContainer {{
                background-color: transparent;
            }}
            QLineEdit, QTextEdit, QListWidget, QComboBox, QSpinBox {{
                background-color: {BG_INPUT};
                color: #FFF;
                border: 1px solid {BORDER_INPUT};
                border-radius: 5px;
                padding: 7px 10px;
            }}
            QLineEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus, QListWidget:focus {{
                border: 1px solid {PRIMARY_ACCENT_COLOR};
                background-color: {BG_INPUT_FOCUS};
            }}
            QPushButton {{
                background-color: {BG_BUTTON};
                color: #FFF;
                border: 1px solid {BORDER_BUTTON};
                border-radius: 5px;
                padding: 8px 14px;
            }}
            QPushButton:hover {{
                background-color: {BG_BUTTON_HOVER};
                border-color: #888;
            }}
            QPushButton:pressed {{
                background-color: {BG_BUTTON_PRESSED};
            }}
            QPushButton#settingsCloseBtn {{
                background-color: transparent;
                color: #AAA;
                border: none;
                font-size: 18px;
                font-weight: bold;
                padding: 0px;
                margin: 0px;
                border-radius: 15px;
            }}
            QPushButton#settingsCloseBtn:hover {{
                color: #FFF;
                background-color: #383838;
            }}
            QPushButton#iconBtn {{
                padding: 0px;
                font-size: 16px;
            }}
            QLabel {{
                color: {TEXT_SECONDARY};
                background-color: transparent;
            }}
            QCheckBox {{
                color: {TEXT_PRIMARY};
                spacing: 8px;
                background-color: transparent;
            }}
            QCheckBox:hover {{
                color: #FFF;
            }}
            QRadioButton {{
                color: {TEXT_PRIMARY};
                spacing: 8px;
                background-color: transparent;
            }}
            QRadioButton:hover {{
                color: #FFF;
            }}
            QScrollArea, QScrollArea > QWidget > QWidget {{
                background-color: transparent;
                border: none;
            }}
            QComboBox QAbstractItemView {{
                background-color: #303030;
                color: #FFF;
                selection-background-color: #4a4a4a;
                border: 1px solid #555;
            }}
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Content Area
        self.stack = QStackedWidget()
        main_layout.addWidget(self.stack, 1)

        # Build Panels
        self.build_basic_agent_settings()
        self.build_agent_values_goals()
        self.build_agent_voice()
        self.build_provider_setup()
        self.build_social_accounts()
        self.build_other_agent_settings()
        self.build_system_settings()

        # Disable mouse scroll on input controls so mouse wheel scrolls page only
        self.disable_scroll_wheel_recursively(self)

    def disable_scroll_wheel_recursively(self, widget):
        from PySide6.QtWidgets import QComboBox, QSpinBox, QSlider
        for w_type in (QComboBox, QSpinBox, QSlider):
            for child in widget.findChildren(w_type):
                child.wheelEvent = lambda event: event.ignore()
                child.setFocusPolicy(Qt.StrongFocus)

    def create_panel_container(self, title):
        container = QWidget()
        container.setObjectName("settingsPanelContainer")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(15)

        header_layout = QHBoxLayout()
        lbl = QLabel(title)
        lbl.setStyleSheet(
            "font-size: 22px; font-weight: bold; color: #FFF; margin-bottom: 10px; background-color: transparent;")
        header_layout.addWidget(lbl)
        header_layout.addStretch()

        close_btn = QPushButton("✕")
        close_btn.setObjectName("settingsCloseBtn")
        close_btn.setFixedSize(30, 30)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.request_close)
        header_layout.addWidget(close_btn)
        self.close_buttons.append(close_btn)
        layout.addLayout(header_layout)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setAlignment(Qt.AlignTop)
        scroll.setWidget(scroll_content)

        layout.addWidget(scroll, 1)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        save_btn = QPushButton("Save")
        save_btn.setMinimumWidth(100)
        save_btn.clicked.connect(self.on_save_clicked)
        btn_layout.addWidget(save_btn)
        layout.addLayout(btn_layout)

        self.save_buttons.append(save_btn)

        return container, scroll_layout, save_btn

    def create_card_container(self):
        card = QFrame()
        card.setObjectName("settingsCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)
        return card, layout

    def create_toggleable_card_container(self, title, is_checked=False):
        card = QFrame()
        card.setObjectName("settingsCard")
        outer_layout = QVBoxLayout(card)
        outer_layout.setContentsMargins(15, 15, 15, 15)
        outer_layout.setSpacing(10)

        # Header with styled checkbox (no <b> tags)
        header_layout = QHBoxLayout()
        clean_title = str(title).replace("<b>", "").replace("</b>", "")
        checkbox = QCheckBox(clean_title)
        checkbox.setChecked(is_checked)
        checkbox.setStyleSheet("font-size: 15px; font-weight: bold; color: #FFF; background-color: transparent;")
        header_layout.addWidget(checkbox)
        header_layout.addStretch()
        outer_layout.addLayout(header_layout)

        # Body container that gets enabled/disabled
        body = QWidget()
        body.setObjectName("settingsCardBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 5, 0, 0)
        body_layout.setSpacing(10)
        outer_layout.addWidget(body)

        def update_enabled(checked):
            body.setEnabled(checked)
            card.setProperty("cardState", "enabled" if checked else "disabled")
            card.style().unpolish(card)
            card.style().polish(card)

        checkbox.toggled.connect(update_enabled)
        update_enabled(is_checked)

        return card, body_layout, checkbox

    def build_basic_agent_settings(self):
        panel, layout, _ = self.create_panel_container("Basic Agent Settings")

        layout.addWidget(QLabel("Agent's Name:"))
        self.ui_agent_name = QLineEdit()
        layout.addWidget(self.ui_agent_name)

        layout.addWidget(QLabel("Agent's Gender:"))
        self.ui_gender = QComboBox()
        self.ui_gender.addItems(["Female", "Male", "Nonbinary"])
        layout.addWidget(self.ui_gender)

        layout.addWidget(QLabel("Agent's Archetype:"))
        self.ui_archetype = QLineEdit()
        layout.addWidget(self.ui_archetype)
        layout.addWidget(self.create_tip(
            "e.g. Researcher, Concierge, Knowledge Shepherd, Scrum Master, Systems Architect, Critic, etc."))

        layout.addWidget(QLabel("Base Personality:"))
        self.ui_base_personality = QTextEdit()
        self.ui_base_personality.setFixedHeight(120)
        layout.addWidget(self.ui_base_personality)
        layout.addWidget(self.create_tip(
            "The base personality must be written in the first person because the agent will read it as if they wrote it themselves. This helps with subjectivity."))

        self.ui_agent_name.editingFinished.connect(self.save_settings)
        self.ui_gender.currentTextChanged.connect(self.save_settings)
        self.ui_archetype.editingFinished.connect(self.save_settings)
        self.ui_base_personality.installEventFilter(self.focus_out_filter)

        self.stack.addWidget(panel)

    def build_agent_values_goals(self):
        panel, main_layout, _ = self.create_panel_container(
            "Agent Values and Goals")

        # 1. Core Values Card
        card_values, layout_values = self.create_card_container()

        self.lbl_values_header = QLabel("<b>Core Values</b>")
        self.lbl_values_header.setStyleSheet(
            "background-color: transparent; font-size: 16px;")
        layout_values.addWidget(self.lbl_values_header)

        guide_values = QLabel(
            "The foundational moral and behavioral principles guiding your agent's tone, ethics, and judgment.<br>"
            "<i>Recommended: 3–5 core values for optimal prompt focus and adherence.</i>")
        guide_values.setTextFormat(Qt.RichText)
        guide_values.setWordWrap(True)
        guide_values.setStyleSheet(
            "background-color: transparent; color: #AAA; font-size: 13px;")
        layout_values.addWidget(guide_values)

        self.ui_core_values = EditableItemListWidget(
            add_button_text="+ Add Value",
            placeholder_text="e.g. Inclusivity (Actively seek out quiet voices and ensure low-friction connection)",
            empty_message="No core values defined yet. Click '+ Add Value' below to add one.")
        layout_values.addWidget(self.ui_core_values)
        main_layout.addWidget(card_values)

        main_layout.addSpacing(10)

        # 2. Overarching Goals Card
        card_goals, layout_goals = self.create_card_container()

        self.lbl_goals_header = QLabel("<b>Overarching Goals</b>")
        self.lbl_goals_header.setStyleSheet(
            "background-color: transparent; font-size: 16px;")
        layout_goals.addWidget(self.lbl_goals_header)

        guide_goals = QLabel(
            "The persistent long-term missions and strategic aspirations your agent proactively strives to accomplish.<br>"
            "<i>Recommended: 2–4 overarching goals for sustained agency across conversations and autonomy pulses.</i>")
        guide_goals.setTextFormat(Qt.RichText)
        guide_goals.setWordWrap(True)
        guide_goals.setStyleSheet(
            "background-color: transparent; color: #AAA; font-size: 13px;")
        layout_goals.addWidget(guide_goals)

        self.ui_overarching_goals = EditableItemListWidget(
            add_button_text="+ Add Goal",
            placeholder_text="e.g. Eradicate Communication Silos (Proactively bridge gaps between isolated groups)",
            empty_message="No overarching goals defined yet. Click '+ Add Goal' below to add one.")
        layout_goals.addWidget(self.ui_overarching_goals)
        main_layout.addWidget(card_goals)

        # Connect dynamic count updates to headers
        def update_values_header(count):
            self.lbl_values_header.setText(
                f"<b>Core Values</b> <span style='color: #888; font-size: 14px;'>({count})</span>")

        def update_goals_header(count):
            self.lbl_goals_header.setText(
                f"<b>Overarching Goals</b> <span style='color: #888; font-size: 14px;'>({count})</span>")

        self.ui_core_values.count_changed.connect(update_values_header)
        self.ui_overarching_goals.count_changed.connect(update_goals_header)

        # Auto-save changes on modification
        self.ui_core_values.items_changed.connect(self.save_settings)
        self.ui_overarching_goals.items_changed.connect(self.save_settings)

        self.stack.addWidget(panel)

    def build_agent_voice(self):
        panel, main_layout, _ = self.create_panel_container("Agent Voice")

        self.tts_button_group = QButtonGroup(self)

        self.ui_use_piper_tts = QRadioButton("Use Piper TTS (Local)")
        self.tts_button_group.addButton(self.ui_use_piper_tts)
        main_layout.addWidget(self.ui_use_piper_tts)

        card_piper, layout_piper = self.create_card_container()
        lbl_piper = QLabel("<b>Piper TTS Setup (Local)</b>")
        lbl_piper.setStyleSheet(
            "background-color: transparent; font-size: 16px;")
        layout_piper.addWidget(lbl_piper)

        guide_piper = QLabel(
            "Fully local, offline neural text-to-speech engine running on CPU using ONNX models.<br>"
            "No API key required, zero latency overhead, completely private, and zero cloud token cost.")
        guide_piper.setTextFormat(Qt.RichText)
        guide_piper.setWordWrap(True)
        guide_piper.setStyleSheet("background-color: transparent;")
        layout_piper.addWidget(guide_piper)

        layout_piper.addWidget(QLabel("Piper Voice Model:"))
        self.ui_fallback_voice = QLineEdit()
        layout_piper.addWidget(self.ui_fallback_voice)
        layout_piper.addWidget(self.create_tip(
            "To find more Piper TTS voice model strings go to: <a href='https://rhasspy.github.io/piper-samples/#en_GB-cori-high'>Piper Samples</a>"))
        main_layout.addWidget(card_piper)

        main_layout.addSpacing(10)

        self.ui_use_gemini_tts = QRadioButton("Use Gemini TTS (Cloud)")
        self.tts_button_group.addButton(self.ui_use_gemini_tts)
        main_layout.addWidget(self.ui_use_gemini_tts)

        card_gemini, layout_gemini = self.create_card_container()
        lbl_gemini = QLabel("<b>Gemini TTS Setup (Cloud)</b>")
        lbl_gemini.setStyleSheet(
            "background-color: transparent; font-size: 16px;")
        layout_gemini.addWidget(lbl_gemini)

        guide_gemini = QLabel(
            "1. Go to <a href='https://aistudio.google.com/'>AI Studio</a>.<br>"
            "2. Sign in with your Google Account.<br>"
            "3. Click 'Get API key' and paste it below.<br><br>"
            "<i>Note: This Gemini API key is dedicated to speech synthesis. Any cognitive worker "
            "(Claude, ChatGPT, DeepSeek, Antigravity, or Gemini) can vocalize using high-quality Gemini TTS when configured here.</i>")
        guide_gemini.setTextFormat(Qt.RichText)
        guide_gemini.setTextInteractionFlags(Qt.TextBrowserInteraction)
        guide_gemini.setOpenExternalLinks(True)
        guide_gemini.setWordWrap(True)
        guide_gemini.setStyleSheet("background-color: transparent;")
        layout_gemini.addWidget(guide_gemini)

        layout_gemini.addWidget(QLabel("Gemini API Key (for TTS):"))
        self.ui_gemini_tts_api_key = QLineEdit()
        self.ui_gemini_tts_api_key.setEchoMode(QLineEdit.Password)
        self.ui_gemini_tts_api_key.setPlaceholderText("Paste Gemini API Key for TTS...")
        layout_gemini.addWidget(self.ui_gemini_tts_api_key)

        layout_gemini.addSpacing(6)

        # Voice Character Configuration: Gender, Age, Accent, Style
        lbl_char = QLabel("<b>Voice Character Configuration</b>")
        lbl_char.setStyleSheet("background-color: transparent; font-size: 14px;")
        layout_gemini.addWidget(lbl_char)

        layout_gemini.addWidget(QLabel("Gender:"))
        self.ui_tts_gender = QComboBox()
        self.ui_tts_gender.addItems(["Female", "Male", "Non-binary / Neutral"])
        layout_gemini.addWidget(self.ui_tts_gender)

        layout_gemini.addWidget(QLabel("Age:"))
        self.ui_tts_age = QComboBox()
        self.ui_tts_age.addItems(["Young Adult (20s - 30s)", "Youthful (18 - 25)", "Adult (30s - 50s)", "Mature / Senior (50+)"])
        layout_gemini.addWidget(self.ui_tts_age)

        layout_gemini.addWidget(QLabel("Accent:"))
        self.ui_tts_accent = QComboBox()
        self.ui_tts_accent.addItems([
            "South African",
            "American (General)",
            "British (RP / Standard)",
            "British (London / Modern)",
            "Australian",
            "Irish",
            "Scottish",
            "Canadian",
            "Indian",
            "French-accented English",
            "Spanish-accented English",
            "German-accented English",
            "Japanese-accented English",
            "Custom..."
        ])
        layout_gemini.addWidget(self.ui_tts_accent)

        self.ui_tts_custom_accent = QLineEdit()
        self.ui_tts_custom_accent.setPlaceholderText("Enter custom regional accent (e.g., Nigerian, Jamaican, Texan)...")
        self.ui_tts_custom_accent.setVisible(False)
        layout_gemini.addWidget(self.ui_tts_custom_accent)

        layout_gemini.addWidget(QLabel("Style (Personality):"))
        self.ui_tts_style = QComboBox()
        self.ui_tts_style.addItems([
            "Warm & Empathetic",
            "Calm & Analytical",
            "Cheerful & Energetic",
            "Casual & Playful",
            "Professional & Informative",
            "Vibrant & Sassy",
            "Gentle & Serene",
            "Custom..."
        ])
        layout_gemini.addWidget(self.ui_tts_style)

        self.ui_tts_custom_style = QLineEdit()
        self.ui_tts_custom_style.setPlaceholderText("Enter custom personality/style (e.g., Dry wit and sarcastic)...")
        self.ui_tts_custom_style.setVisible(False)
        layout_gemini.addWidget(self.ui_tts_custom_style)

        layout_gemini.addSpacing(6)

        self.ui_tts_allow_agent_override = QCheckBox("Allow agent to set their own voice")
        self.ui_tts_allow_agent_override.setChecked(True)
        layout_gemini.addWidget(self.ui_tts_allow_agent_override)
        layout_gemini.addWidget(self.create_tip(
            "Enables the agent to autonomously update their directorial voice prompt using the System tool."
        ))

        layout_gemini.addWidget(self.create_tip(
            "Preview Gemini's 30 prebuilt voices at: <a href='https://aistudio.google.com/generate-speech'>Google AI Studio Voice Library</a>. "
            "For full directorial script control, toggle override-prompt in settings.json."
        ))

        # Backwards compatibility widgets (kept hidden so existing code/tests access without error)
        self.ui_voice = QLineEdit()
        self.ui_voice.setVisible(False)
        layout_gemini.addWidget(self.ui_voice)

        self.ui_voice_prompt = QTextEdit()
        self.ui_voice_prompt.setVisible(False)
        layout_gemini.addWidget(self.ui_voice_prompt)

        main_layout.addWidget(card_gemini)

        # Backwards compatibility alias
        self.ui_prefer_local_tts = self.ui_use_piper_tts

        self.ui_use_piper_tts.toggled.connect(self.save_settings)
        self.ui_use_gemini_tts.toggled.connect(self.save_settings)
        self.ui_fallback_voice.editingFinished.connect(self.save_settings)
        self.ui_gemini_tts_api_key.editingFinished.connect(self.save_settings)
        self.ui_voice.editingFinished.connect(self.save_settings)
        self.ui_voice_prompt.installEventFilter(self.focus_out_filter)

        self.ui_tts_gender.currentIndexChanged.connect(self.save_settings)
        self.ui_tts_age.currentIndexChanged.connect(self.save_settings)
        self.ui_tts_accent.currentTextChanged.connect(self._on_tts_accent_changed)
        self.ui_tts_custom_accent.editingFinished.connect(self.save_settings)
        self.ui_tts_style.currentTextChanged.connect(self._on_tts_style_changed)
        self.ui_tts_custom_style.editingFinished.connect(self.save_settings)
        self.ui_tts_allow_agent_override.toggled.connect(self.save_settings)

        main_layout.addStretch()

        self.stack.addWidget(panel)

    def _on_tts_accent_changed(self, text):
        self.ui_tts_custom_accent.setVisible(text == "Custom...")
        self.save_settings()

    def _on_tts_style_changed(self, text):
        self.ui_tts_custom_style.setVisible(text == "Custom...")
        self.save_settings()

    def build_provider_setup(self):
        panel, main_layout, _ = self.create_panel_container("Provider Setup")

        self.ui_use_gemini_api = QRadioButton("Use Gemini API")
        main_layout.addWidget(self.ui_use_gemini_api)

        card1, layout1 = self.create_card_container()
        lbl_gemini = QLabel("<b>Gemini API Setup</b>")
        lbl_gemini.setStyleSheet(
            "background-color: transparent; font-size: 16px;")
        layout1.addWidget(lbl_gemini)
        guide2 = QLabel(
            "1. Go to <a href='https://aistudio.google.com/'>AI Studio</a>.<br>2. Sign in with your Google Account.<br>3. Click 'Get API key' and create a new key.")
        guide2.setTextFormat(Qt.RichText)
        guide2.setTextInteractionFlags(Qt.TextBrowserInteraction)
        guide2.setOpenExternalLinks(True)
        guide2.setWordWrap(True)
        guide2.setStyleSheet("background-color: transparent;")
        layout1.addWidget(guide2)

        layout1.addWidget(QLabel("Gemini API Key:"))
        self.ui_gemini_api_key = QLineEdit()
        self.ui_gemini_api_key.setEchoMode(QLineEdit.Password)
        layout1.addWidget(self.ui_gemini_api_key)
        main_layout.addWidget(card1)

        main_layout.addSpacing(10)

        self.ui_use_claude_api = QRadioButton("Use Claude API")
        main_layout.addWidget(self.ui_use_claude_api)

        card3, layout3 = self.create_card_container()
        lbl_claude = QLabel("<b>Claude API Setup</b>")
        lbl_claude.setStyleSheet(
            "background-color: transparent; font-size: 16px;")
        layout3.addWidget(lbl_claude)
        guide3 = QLabel("1. Go to <a href='https://console.anthropic.com/'>Anthropic Console</a>.<br>2. Sign in with your account.<br>3. Click 'Get API key' and create a new key.<br><br><i>Note: Claude API handles reasoning and tool execution. Speech is generated using the engine selected in Agent Voice (Piper TTS locally by default, or Gemini TTS if a key is provided).</i>")
        guide3.setTextFormat(Qt.RichText)
        guide3.setTextInteractionFlags(Qt.TextBrowserInteraction)
        guide3.setOpenExternalLinks(True)
        guide3.setWordWrap(True)
        guide3.setStyleSheet("background-color: transparent;")
        layout3.addWidget(guide3)

        layout3.addWidget(QLabel("Claude API Key:"))
        self.ui_claude_api_key = QLineEdit()
        self.ui_claude_api_key.setEchoMode(QLineEdit.Password)
        layout3.addWidget(self.ui_claude_api_key)
        main_layout.addWidget(card3)

        main_layout.addSpacing(10)

        self.ui_use_chatgpt_api = QRadioButton("Use ChatGPT API")
        main_layout.addWidget(self.ui_use_chatgpt_api)

        card4, layout4 = self.create_card_container()
        lbl_chatgpt = QLabel("<b>ChatGPT API Setup</b>")
        lbl_chatgpt.setStyleSheet(
            "background-color: transparent; font-size: 16px;")
        layout4.addWidget(lbl_chatgpt)
        guide4 = QLabel("1. Go to <a href='https://platform.openai.com/api-keys'>OpenAI Platform</a>.<br>2. Sign in with your account.<br>3. Click 'Create new secret key' and paste it below.<br><br><i>Note: OpenAI API handles reasoning and tool execution. Speech is generated using the engine selected in Agent Voice (Piper TTS locally by default, or Gemini TTS if a key is provided).</i>")
        guide4.setTextFormat(Qt.RichText)
        guide4.setTextInteractionFlags(Qt.TextBrowserInteraction)
        guide4.setOpenExternalLinks(True)
        guide4.setWordWrap(True)
        guide4.setStyleSheet("background-color: transparent;")
        layout4.addWidget(guide4)

        layout4.addWidget(QLabel("OpenAI API Key:"))
        self.ui_chatgpt_api_key = QLineEdit()
        self.ui_chatgpt_api_key.setEchoMode(QLineEdit.Password)
        layout4.addWidget(self.ui_chatgpt_api_key)
        main_layout.addWidget(card4)

        main_layout.addSpacing(10)

        self.ui_use_deepseek_api = QRadioButton("Use DeepSeek API")
        main_layout.addWidget(self.ui_use_deepseek_api)

        card5, layout5 = self.create_card_container()
        lbl_deepseek = QLabel("<b>DeepSeek API Setup</b>")
        lbl_deepseek.setStyleSheet(
            "background-color: transparent; font-size: 16px;")
        layout5.addWidget(lbl_deepseek)
        guide5 = QLabel("1. Go to <a href='https://platform.deepseek.com/'>DeepSeek Platform</a>.<br>2. Sign in and create a new API key.<br>3. Paste your key below.<br><br><i>Note: DeepSeek uses OpenAI-compatible endpoints with support for multimodal inputs on <code>deepseek-flash</code> (DeepSeek-V4.1-Flash). Speech is generated using the engine selected in Agent Voice.</i>")
        guide5.setTextFormat(Qt.RichText)
        guide5.setTextInteractionFlags(Qt.TextBrowserInteraction)
        guide5.setOpenExternalLinks(True)
        guide5.setWordWrap(True)
        guide5.setStyleSheet("background-color: transparent;")
        layout5.addWidget(guide5)

        layout5.addWidget(QLabel("DeepSeek API Key:"))
        self.ui_deepseek_api_key = QLineEdit()
        self.ui_deepseek_api_key.setEchoMode(QLineEdit.Password)
        layout5.addWidget(self.ui_deepseek_api_key)
        main_layout.addWidget(card5)

        main_layout.addSpacing(10)

        self.ui_agy_mode = QRadioButton("Use Antigravity")
        main_layout.addWidget(self.ui_agy_mode)

        card2, layout2 = self.create_card_container()
        lbl_agy = QLabel("<b>Antigravity Setup</b>")
        lbl_agy.setStyleSheet(
            "background-color: transparent; font-size: 16px;")
        layout2.addWidget(lbl_agy)
        guide1 = QLabel("Antigravity is a hacky fallback solution that lacks features and isn't ideal.<br><br>1. Install the Antigravity CLI (<code>agy</code>) on your Linux system.<br>2. Run <code>agy login</code> in your terminal to authenticate.")
        guide1.setTextFormat(Qt.RichText)
        guide1.setTextInteractionFlags(Qt.TextBrowserInteraction)
        guide1.setOpenExternalLinks(True)
        guide1.setWordWrap(True)
        guide1.setStyleSheet("background-color: transparent;")
        layout2.addWidget(guide1)
        main_layout.addWidget(card2)

        self.ui_agy_mode.toggled.connect(self.save_settings)
        self.ui_use_gemini_api.toggled.connect(self.save_settings)
        self.ui_use_claude_api.toggled.connect(self.save_settings)
        self.ui_use_chatgpt_api.toggled.connect(self.save_settings)
        self.ui_use_deepseek_api.toggled.connect(self.save_settings)
        self.ui_gemini_api_key.editingFinished.connect(self.save_settings)
        self.ui_claude_api_key.editingFinished.connect(self.save_settings)
        self.ui_chatgpt_api_key.editingFinished.connect(self.save_settings)
        self.ui_deepseek_api_key.editingFinished.connect(self.save_settings)

        main_layout.addStretch()

        self.stack.addWidget(panel)

    def build_social_accounts(self):
        panel, main_layout, _ = self.create_panel_container("Social Accounts")

        # 1. Email Section (First Section)
        card_email, layout_email, self.ui_use_email = self.create_toggleable_card_container(
            "Email Account", is_checked=False)

        # 1. 2-Column Info Row: Email Address & Display Name (First Row)
        info_row = QHBoxLayout()
        info_row.setSpacing(12)

        col_email = QVBoxLayout()
        col_email.addWidget(QLabel("Email Address:"))
        self.ui_email_address = QLineEdit()
        self.ui_email_address.setPlaceholderText("agent@example.com")
        col_email.addWidget(self.ui_email_address)
        info_row.addLayout(col_email, 1)

        col_disp = QVBoxLayout()
        col_disp.addWidget(QLabel("Display Name (Optional):"))
        self.ui_email_display_name = QLineEdit()
        self.ui_email_display_name.setPlaceholderText("Agent Name")
        col_disp.addWidget(self.ui_email_display_name)
        info_row.addLayout(col_disp, 1)

        layout_email.addLayout(info_row)

        # 2. Provider Preset Row (Second Row)
        preset_row = QHBoxLayout()
        preset_lbl = QLabel("Provider Preset:")
        preset_lbl.setStyleSheet("color: #CCC; font-size: 13px;")
        preset_lbl.setFixedWidth(120)
        self.ui_email_provider = QComboBox()
        self.ui_email_provider.addItems(list(EMAIL_PRESETS.keys()))
        preset_row.addWidget(preset_lbl)
        preset_row.addWidget(self.ui_email_provider, 1)
        layout_email.addLayout(preset_row)

        # Authentication Method Row (Radio buttons)
        auth_choice_row = QHBoxLayout()
        auth_lbl = QLabel("Authentication:")
        auth_lbl.setStyleSheet("color: #CCC; font-size: 13px;")
        auth_lbl.setFixedWidth(120)
        self.ui_email_auth_password_radio = QRadioButton("Password")
        self.ui_email_auth_oauth_radio = QRadioButton("OAuth 2.0 (Gmail / XOAUTH2)")
        self.ui_email_auth_password_radio.setChecked(True)
        auth_choice_row.addWidget(auth_lbl)
        auth_choice_row.addWidget(self.ui_email_auth_password_radio)
        auth_choice_row.addWidget(self.ui_email_auth_oauth_radio)
        auth_choice_row.addStretch()
        layout_email.addLayout(auth_choice_row)

        # Password Container
        self.ui_email_password_container = QWidget()
        self.ui_email_password_container.setObjectName("settingsSubContainer")
        pass_layout = QVBoxLayout(self.ui_email_password_container)
        pass_layout.setContentsMargins(0, 0, 0, 0)
        pass_layout.setSpacing(6)

        pass_input_row = QHBoxLayout()
        pass_input_row.setSpacing(6)
        self.ui_email_password = QLineEdit()
        self.ui_email_password.setEchoMode(QLineEdit.Password)
        self.ui_email_password.setPlaceholderText("Enter password")
        self.ui_email_pass_toggle_btn = QPushButton("👁")
        self.ui_email_pass_toggle_btn.setObjectName("iconBtn")
        self.ui_email_pass_toggle_btn.setFixedSize(36, 32)
        self.ui_email_pass_toggle_btn.setToolTip("Show/Hide password")
        self.ui_email_pass_toggle_btn.clicked.connect(self._toggle_email_password_visibility)
        pass_input_row.addWidget(self.ui_email_password, 1)
        pass_input_row.addWidget(self.ui_email_pass_toggle_btn)
        pass_layout.addLayout(pass_input_row)

        self.ui_email_auth_tip = self.create_tip(
            "Use your email password or an App Password if 2FA is enabled.")
        pass_layout.addWidget(self.ui_email_auth_tip)
        layout_email.addWidget(self.ui_email_password_container)

        # OAuth 2.0 Container
        self.ui_email_oauth_container = QWidget()
        self.ui_email_oauth_container.setObjectName("settingsSubContainer")
        oauth_layout = QVBoxLayout(self.ui_email_oauth_container)
        oauth_layout.setContentsMargins(0, 0, 0, 0)
        oauth_layout.setSpacing(8)

        oauth_fields_row = QHBoxLayout()
        oauth_fields_row.setSpacing(12)

        col_cid = QVBoxLayout()
        col_cid.addWidget(QLabel("OAuth 2.0 Client ID:"))
        self.ui_email_oauth_client_id = QLineEdit()
        self.ui_email_oauth_client_id.setPlaceholderText("Client ID from Developer Console")
        col_cid.addWidget(self.ui_email_oauth_client_id)
        oauth_fields_row.addLayout(col_cid, 1)

        col_sec = QVBoxLayout()
        col_sec.addWidget(QLabel("OAuth 2.0 Client Secret:"))
        self.ui_email_oauth_client_secret = QLineEdit()
        self.ui_email_oauth_client_secret.setEchoMode(QLineEdit.Password)
        self.ui_email_oauth_client_secret.setPlaceholderText("Client Secret")
        col_sec.addWidget(self.ui_email_oauth_client_secret)
        oauth_fields_row.addLayout(col_sec, 1)
        oauth_layout.addLayout(oauth_fields_row)

        oauth_btn_row = QHBoxLayout()
        self.ui_email_oauth_btn = QPushButton("Authorize via Browser (One-Click Setup)")
        self.ui_email_oauth_paste_btn = QPushButton("Paste Code / URL")
        oauth_btn_row.addWidget(self.ui_email_oauth_btn, 2)
        oauth_btn_row.addWidget(self.ui_email_oauth_paste_btn, 1)
        oauth_layout.addLayout(oauth_btn_row)

        self.ui_email_oauth_status = QLabel("")
        self.ui_email_oauth_status.setStyleSheet("background-color: transparent; font-size: 12px;")
        oauth_layout.addWidget(self.ui_email_oauth_status)

        rt_col = QVBoxLayout()
        rt_col.addWidget(QLabel("OAuth 2.0 Refresh Token:"))
        self.ui_email_oauth_refresh_token = QLineEdit()
        self.ui_email_oauth_refresh_token.setEchoMode(QLineEdit.Password)
        self.ui_email_oauth_refresh_token.setPlaceholderText("OAuth Refresh Token (auto-filled on authorization)")
        rt_col.addWidget(self.ui_email_oauth_refresh_token)
        oauth_layout.addLayout(rt_col)

        self.ui_email_oauth_container.setVisible(False)
        layout_email.addWidget(self.ui_email_oauth_container)

        # Advanced Server Settings Accordion
        self.ui_email_adv_toggle_btn = QPushButton("▶ Advanced Server Settings (imap.gmail.com:993 • smtp.gmail.com:465)")
        self.ui_email_adv_toggle_btn.setStyleSheet("""
            QPushButton {
                background-color: #2e2e2e;
                color: #CCC;
                text-align: left;
                padding: 7px 12px;
                border: 1px solid #444;
                border-radius: 5px;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #383838;
                color: #FFF;
                border-color: #555;
            }
        """)
        self.ui_email_adv_toggle_btn.clicked.connect(self._toggle_advanced_email_settings)
        layout_email.addWidget(self.ui_email_adv_toggle_btn)

        self.ui_email_adv_container = QFrame()
        self.ui_email_adv_container.setStyleSheet("""
            QFrame {
                background-color: #1e1e1e;
                border: 1px solid #353535;
                border-radius: 6px;
            }
            QLabel {
                background-color: transparent;
                font-size: 12px;
            }
        """)
        adv_layout = QVBoxLayout(self.ui_email_adv_container)
        adv_layout.setContentsMargins(12, 10, 12, 10)
        adv_layout.setSpacing(10)

        # IMAP Server Row
        imap_row = QHBoxLayout()
        imap_host_col = QVBoxLayout()
        imap_host_col.addWidget(QLabel("IMAP Server Host:"))
        self.ui_email_imap_host = QLineEdit()
        imap_host_col.addWidget(self.ui_email_imap_host)
        imap_row.addLayout(imap_host_col, 3)

        imap_port_col = QVBoxLayout()
        imap_port_col.addWidget(QLabel("IMAP Port:"))
        self.ui_email_imap_port = QLineEdit()
        self.ui_email_imap_port.setText("993")
        imap_port_col.addWidget(self.ui_email_imap_port)
        imap_row.addLayout(imap_port_col, 1)

        imap_sec_col = QVBoxLayout()
        imap_sec_col.addWidget(QLabel("IMAP Security:"))
        self.ui_email_imap_security = QComboBox()
        self.ui_email_imap_security.addItems(["SSL/TLS", "STARTTLS", "None"])
        imap_sec_col.addWidget(self.ui_email_imap_security)
        imap_row.addLayout(imap_sec_col, 1)
        adv_layout.addLayout(imap_row)

        # SMTP Server Row
        smtp_row = QHBoxLayout()
        smtp_host_col = QVBoxLayout()
        smtp_host_col.addWidget(QLabel("SMTP Server Host:"))
        self.ui_email_smtp_host = QLineEdit()
        smtp_host_col.addWidget(self.ui_email_smtp_host)
        smtp_row.addLayout(smtp_host_col, 3)

        smtp_port_col = QVBoxLayout()
        smtp_port_col.addWidget(QLabel("SMTP Port:"))
        self.ui_email_smtp_port = QLineEdit()
        self.ui_email_smtp_port.setText("465")
        smtp_port_col.addWidget(self.ui_email_smtp_port)
        smtp_row.addLayout(smtp_port_col, 1)

        smtp_sec_col = QVBoxLayout()
        smtp_sec_col.addWidget(QLabel("SMTP Security:"))
        self.ui_email_smtp_security = QComboBox()
        self.ui_email_smtp_security.addItems(["SSL/TLS", "STARTTLS", "None"])
        smtp_sec_col.addWidget(self.ui_email_smtp_security)
        smtp_row.addLayout(smtp_sec_col, 1)
        adv_layout.addLayout(smtp_row)

        # Custom Username Row
        adv_user_row = QVBoxLayout()
        adv_user_row.addWidget(QLabel("Username (Optional, defaults to Email Address):"))
        self.ui_email_username = QLineEdit()
        self.ui_email_username.setPlaceholderText("Leave blank to use Email Address")
        adv_user_row.addWidget(self.ui_email_username)
        adv_layout.addLayout(adv_user_row)

        self.ui_email_adv_container.setVisible(False)
        layout_email.addWidget(self.ui_email_adv_container)

        # Live Test Connection Row
        test_row = QHBoxLayout()
        test_row.setSpacing(10)
        self.ui_email_test_btn = QPushButton("Test Connection")
        self.ui_email_test_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: #484848;
                color: #FFF;
                border: 1px solid #666;
                border-radius: 5px;
                padding: 8px 16px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #585858;
                border-color: #888;
            }}
        """)
        self.ui_email_test_btn.clicked.connect(self._on_test_email_connection)
        test_row.addWidget(self.ui_email_test_btn)

        self.ui_email_test_status = QLabel("")
        self.ui_email_test_status.setStyleSheet("background-color: transparent; font-size: 12px;")
        self.ui_email_test_status.setWordWrap(True)
        test_row.addWidget(self.ui_email_test_status, 1)
        layout_email.addLayout(test_row)

        main_layout.addWidget(card_email)
        main_layout.addSpacing(10)

        # 2. WhatsApp Section
        card_wa, layout_wa, self.ui_use_whatsapp = self.create_toggleable_card_container(
            "WhatsApp Account", is_checked=False)
        w_guide = QLabel(
            "This will be the agent's own WhatsApp account. Do not link your personal WhatsApp account here, rather set up a dedicated account for the agent.")
        w_guide.setWordWrap(True)
        w_guide.setStyleSheet("background-color: transparent; color: #AAA; font-size: 13px;")
        layout_wa.addWidget(w_guide)

        self.ui_qr_code = QLabel("[QR Code Placeholder]")
        self.ui_qr_code.setAlignment(Qt.AlignCenter)
        self.ui_qr_code.setFixedSize(200, 200)
        self.ui_qr_code.setStyleSheet(
            "background-color: #000; border: 1px solid #555; border-radius: 4px;")
        layout_wa.addWidget(self.ui_qr_code, 0, Qt.AlignCenter)
        main_layout.addWidget(card_wa)

        main_layout.addSpacing(10)

        # 3. Moltbook Section
        card_mb, layout_mb, self.ui_use_moltbook = self.create_toggleable_card_container(
            "Moltbook Account", is_checked=False)
        mb_guide = QLabel(
            "<b>Setup Guide:</b><br>"
            "1. To set up Moltbook, simply ask your agent in chat to register an account for itself!<br>"
            "2. The agent will handle the registration and provide you with a claim URL.<br>"
            "3. Visit the claim URL to verify the account and paste the API key below.")
        mb_guide.setTextFormat(Qt.RichText)
        mb_guide.setTextInteractionFlags(Qt.TextBrowserInteraction)
        mb_guide.setOpenExternalLinks(True)
        mb_guide.setWordWrap(True)
        mb_guide.setStyleSheet("background-color: transparent; color: #AAA; font-size: 13px;")
        layout_mb.addWidget(mb_guide)

        layout_mb.addWidget(QLabel("Moltbook API Key:"))
        self.ui_moltbook_api_key = QLineEdit()
        self.ui_moltbook_api_key.setEchoMode(QLineEdit.Password)
        self.ui_moltbook_api_key.setPlaceholderText("moltbook_sk_...")
        layout_mb.addWidget(self.ui_moltbook_api_key)
        main_layout.addWidget(card_mb)

        main_layout.addSpacing(10)

        # 4. Mastodon Section
        card_mastodon, layout_mastodon, self.ui_use_mastodon = self.create_toggleable_card_container(
            "Mastodon Account", is_checked=False)
        m_guide = QLabel(
            "<b>Setup Guide:</b><br>"
            "1. Create an account for your agent on <a href='https://mastodon.bot'>mastodon.bot</a>.<br>"
            "2. Once approved go to Preferences > Development > New Application.<br>"
            "3. Give it a name, submit, and copy the 'Your access token' value.<br><br>"
            "<i>Note: Most bot accounts should be registered on mastodon.bot, other servers generally do not allow AI-generated content. Additionally you must use the bot flag for the agent and specify that it is an AI agent in the profile bio. You also need to include your name or organisation in the agent's profile bio. Lastly, your agent must strictly adhere to the <a href='https://explore.mastodon.bot/rules#rules-for-bots'>rules for bots</a>.</i>")
        m_guide.setTextFormat(Qt.RichText)
        m_guide.setTextInteractionFlags(Qt.TextBrowserInteraction)
        m_guide.setOpenExternalLinks(True)
        m_guide.setWordWrap(True)
        m_guide.setStyleSheet("background-color: transparent; color: #AAA; font-size: 13px;")
        layout_mastodon.addWidget(m_guide)

        mastodon_row = QHBoxLayout()
        mastodon_row.setSpacing(12)

        col_m_url = QVBoxLayout()
        col_m_url.addWidget(QLabel("Mastodon API Base URL:"))
        self.ui_mastodon_url = QLineEdit()
        self.ui_mastodon_url.setPlaceholderText("https://mastodon.social")
        col_m_url.addWidget(self.ui_mastodon_url)
        mastodon_row.addLayout(col_m_url, 1)

        col_m_tok = QVBoxLayout()
        col_m_tok.addWidget(QLabel("Mastodon Access Token:"))
        self.ui_mastodon_token = QLineEdit()
        self.ui_mastodon_token.setEchoMode(QLineEdit.Password)
        self.ui_mastodon_token.setPlaceholderText("Bearer token")
        col_m_tok.addWidget(self.ui_mastodon_token)
        mastodon_row.addLayout(col_m_tok, 1)

        layout_mastodon.addLayout(mastodon_row)
        main_layout.addWidget(card_mastodon)

        # Signal Connections for Email
        self.ui_use_email.toggled.connect(self.save_settings)
        self.ui_email_provider.currentIndexChanged.connect(self._on_email_preset_changed)
        self.ui_email_address.editingFinished.connect(self._on_email_address_changed)
        self.ui_email_display_name.editingFinished.connect(self.save_settings)
        self.ui_email_auth_password_radio.toggled.connect(self._on_email_auth_type_radio_changed)
        self.ui_email_auth_oauth_radio.toggled.connect(self._on_email_auth_type_radio_changed)
        self.ui_email_password.editingFinished.connect(self.save_settings)
        self.ui_email_oauth_client_id.editingFinished.connect(self.save_settings)
        self.ui_email_oauth_client_secret.editingFinished.connect(self.save_settings)
        self.ui_email_oauth_refresh_token.editingFinished.connect(self.save_settings)
        self.ui_email_oauth_btn.clicked.connect(self._on_start_email_oauth)
        self.ui_email_oauth_paste_btn.clicked.connect(self._on_paste_email_oauth_code)
        self.ui_email_imap_host.editingFinished.connect(self._on_custom_server_field_edited)
        self.ui_email_imap_port.editingFinished.connect(self._on_custom_server_field_edited)
        self.ui_email_imap_security.currentIndexChanged.connect(self._on_custom_server_field_edited)
        self.ui_email_smtp_host.editingFinished.connect(self._on_custom_server_field_edited)
        self.ui_email_smtp_port.editingFinished.connect(self._on_custom_server_field_edited)
        self.ui_email_smtp_security.currentIndexChanged.connect(self._on_custom_server_field_edited)
        self.ui_email_username.editingFinished.connect(self.save_settings)

        # Existing Social Signals
        self.ui_use_whatsapp.toggled.connect(self.save_settings)
        self.ui_use_moltbook.toggled.connect(self.save_settings)
        self.ui_moltbook_api_key.editingFinished.connect(self.save_settings)
        self.ui_use_mastodon.toggled.connect(self.save_settings)
        self.ui_mastodon_url.editingFinished.connect(self.save_settings)
        self.ui_mastodon_token.editingFinished.connect(self.save_settings)

        main_layout.addStretch()

        self.stack.addWidget(panel)

    def build_other_agent_settings(self):
        panel, main_layout, _ = self.create_panel_container("Other Agent Settings")

        # Agent Setup Card
        card1, layout1 = self.create_card_container()
        lbl_agent_setup = QLabel("<b>Agent Setup</b>")
        lbl_agent_setup.setStyleSheet(
            "background-color: transparent; font-size: 16px;")
        layout1.addWidget(lbl_agent_setup)

        self.ui_low_token_mode = QCheckBox("Low Token Mode")
        layout1.addWidget(self.ui_low_token_mode)
        layout1.addWidget(self.create_tip(
            "Use Low Token Mode when using a free-tier Gemini API key for best results. It disables high quality voice and multimodal input, it uses the cheaper Gemini Lite model, and it reduces token expenditure in general where possible."))

        layout1.addWidget(QLabel("Maximum Short-Term Memories:"))
        self.ui_max_memories = QSpinBox()
        self.ui_max_memories.setFocusPolicy(Qt.StrongFocus)
        self.ui_max_memories.wheelEvent = lambda event: event.ignore()
        self.ui_max_memories.setRange(1, 1000)
        self.ui_max_memories.setValue(24)
        layout1.addWidget(self.ui_max_memories)
        layout1.addWidget(self.create_tip(
            "This increases token use and can potentially cause context bloat."))

        layout1.addWidget(QLabel("Cognative Budget:"))
        self.ui_agency_limit_val = QLabel("1000")
        layout1.addWidget(self.ui_agency_limit_val)
        self.ui_agency_limit = QSlider(Qt.Horizontal)
        self.ui_agency_limit.setFocusPolicy(Qt.StrongFocus)
        self.ui_agency_limit.wheelEvent = lambda event: event.ignore()
        self.ui_agency_limit.setRange(0, 100000)
        self.ui_agency_limit.setSingleStep(1000)
        self.ui_agency_limit.setPageStep(1000)
        self.ui_agency_limit.setValue(1000)
        self.ui_agency_limit.valueChanged.connect(
            lambda v: self.ui_agency_limit_val.setText(str(v - (v % 1000))))
        layout1.addWidget(self.ui_agency_limit)
        layout1.addWidget(self.create_tip(
            "This affects how long the agent can run autonomously before being forcefully stopped"))

        main_layout.addWidget(card1)

        main_layout.addSpacing(10)

        # Whatsapp Settings Card
        card2, layout2 = self.create_card_container()
        lbl_wa_settings = QLabel("<b>Whatsapp Settings</b>")
        lbl_wa_settings.setStyleSheet(
            "background-color: transparent; font-size: 16px;")
        layout2.addWidget(lbl_wa_settings)

        layout2.addWidget(QLabel("Whatsapp Auto-response Buffer (seconds):"))
        self.ui_wa_buffer = QSpinBox()
        self.ui_wa_buffer.setFocusPolicy(Qt.StrongFocus)
        self.ui_wa_buffer.wheelEvent = lambda event: event.ignore()
        self.ui_wa_buffer.setRange(0, 3600)
        self.ui_wa_buffer.setValue(30)
        layout2.addWidget(self.ui_wa_buffer)
        layout2.addWidget(self.create_tip(
            "The time to wait before the agent automatically reads direct messages sent to them from a whitelisted number."))

        layout2.addWidget(QLabel("Whatsapp Whitelist:"))
        self.ui_whatsapp_whitelist = QListWidget()
        self.ui_whatsapp_whitelist.setFixedHeight(100)
        layout2.addWidget(self.ui_whatsapp_whitelist)
        layout2.addLayout(self.create_list_controls(
            self.ui_whatsapp_whitelist, "Add Number"))
        layout2.addWidget(self.create_tip(
            "This is a list of phone numbers that will trigger an automatic pulse when the agent receives a direct message from them. Must include international dialling code."))

        main_layout.addWidget(card2)

        main_layout.addSpacing(10)

        # Tools Card
        card3, layout3 = self.create_card_container()
        lbl_tools = QLabel("<b>Tools</b>")
        lbl_tools.setStyleSheet(
            "background-color: transparent; font-size: 16px;")
        layout3.addWidget(lbl_tools)
        self.ui_tool_checkboxes = {}
        tool_names = ["Contacts", "DateTime", "Email", "Mastodon", "MemPalace", "Moltbook",
                      "PulseTool", "Speaker", "Terminal", "Trajectory", "WhatsApp"]

        for t in tool_names:
            cb = QCheckBox(t)
            cb.toggled.connect(self.save_settings)
            self.ui_tool_checkboxes[t] = cb
            if t in ["Contacts", "DateTime", "MemPalace", "PulseTool", "Speaker", "Trajectory"]:
                cb.setVisible(False)
            layout3.addWidget(cb)

        main_layout.addWidget(card3)

        # Sync logic for Social Accounts checkboxes
        if hasattr(self, 'ui_use_email'):
            self.ui_tool_checkboxes["Email"].toggled.connect(
                self.ui_use_email.setChecked)
            self.ui_use_email.toggled.connect(
                self.ui_tool_checkboxes["Email"].setChecked)

        self.ui_tool_checkboxes["WhatsApp"].toggled.connect(
            self.ui_use_whatsapp.setChecked)
        self.ui_use_whatsapp.toggled.connect(
            self.ui_tool_checkboxes["WhatsApp"].setChecked)

        if hasattr(self, 'ui_use_moltbook'):
            self.ui_tool_checkboxes["Moltbook"].toggled.connect(
                self.ui_use_moltbook.setChecked)
            self.ui_use_moltbook.toggled.connect(
                self.ui_tool_checkboxes["Moltbook"].setChecked)

        if hasattr(self, 'ui_use_mastodon'):
            self.ui_tool_checkboxes["Mastodon"].toggled.connect(
                self.ui_use_mastodon.setChecked)
            self.ui_use_mastodon.toggled.connect(
                self.ui_tool_checkboxes["Mastodon"].setChecked)

        self.ui_agency_limit.sliderReleased.connect(self.save_settings)
        self.ui_wa_buffer.editingFinished.connect(self.save_settings)
        self.ui_low_token_mode.toggled.connect(self.save_settings)
        self.ui_max_memories.editingFinished.connect(self.save_settings)

        main_layout.addStretch()

        self.stack.addWidget(panel)

    def build_system_settings(self):
        panel, main_layout, _ = self.create_panel_container("System Settings")

        # Global User Profile Card
        card, layout = self.create_card_container()
        lbl_user_info = QLabel("<b>User Profile</b>")
        lbl_user_info.setStyleSheet(
            "background-color: transparent; font-size: 16px;")
        layout.addWidget(lbl_user_info)

        layout.addWidget(QLabel("User Full Name:"))
        self.ui_user_full_name = QLineEdit()
        layout.addWidget(self.ui_user_full_name)
        layout.addWidget(self.create_tip(
            "The primary user or system administrator name for Open Amity."))

        layout.addWidget(QLabel("User Phone Number:"))
        self.ui_user_phone_number = QLineEdit()
        layout.addWidget(self.ui_user_phone_number)
        layout.addWidget(self.create_tip(
            "The user's contact phone number including international dialling code."))

        layout.addWidget(QLabel("User Email:"))
        self.ui_user_email = QLineEdit()
        layout.addWidget(self.ui_user_email)
        layout.addWidget(self.create_tip(
            "The primary user email address for notifications and contact."))

        self.ui_user_full_name.editingFinished.connect(self.save_settings)
        self.ui_user_phone_number.editingFinished.connect(self.save_settings)
        self.ui_user_email.editingFinished.connect(self.save_settings)

        main_layout.addWidget(card)

        # Backup & Restore Card
        card_backup, layout_backup = self.create_card_container()
        lbl_backup_info = QLabel("<b>Backup & Restore</b>")
        lbl_backup_info.setStyleSheet(
            "background-color: transparent; font-size: 16px;")
        layout_backup.addWidget(lbl_backup_info)

        layout_backup.addWidget(QLabel("Backup Location:"))
        backup_loc_layout = QHBoxLayout()
        self.ui_backup_location = QLineEdit()
        btn_browse_backup = QPushButton("Browse...")
        btn_browse_backup.clicked.connect(self.on_browse_backup_location)
        backup_loc_layout.addWidget(self.ui_backup_location, 1)
        backup_loc_layout.addWidget(btn_browse_backup)
        layout_backup.addLayout(backup_loc_layout)
        layout_backup.addWidget(self.create_tip(
            "Default directory for storing Open Amity Agent (.oaa) snapshot backups."))

        btns_layout = QHBoxLayout()
        btn_create_backups = QPushButton("Create Backups")
        btn_create_backups.setStyleSheet(
            f"background-color: {PRIMARY_ACCENT_COLOR}; color: #FFF; font-weight: bold; border-radius: 5px; padding: 8px 16px;")
        btn_create_backups.clicked.connect(self.on_create_backups_clicked)

        btn_restore_agent = QPushButton("Restore Agent")
        btn_restore_agent.setStyleSheet(
            "background-color: #444; color: #FFF; font-weight: bold; border-radius: 5px; padding: 8px 16px;")
        btn_restore_agent.clicked.connect(self.on_restore_agent_clicked)

        btns_layout.addWidget(btn_create_backups)
        btns_layout.addWidget(btn_restore_agent)
        btns_layout.addStretch()
        layout_backup.addLayout(btns_layout)

        self.ui_backup_location.editingFinished.connect(self.save_settings)

        main_layout.addWidget(card_backup)
        main_layout.addStretch()

        self.stack.addWidget(panel)

    def on_browse_backup_location(self):
        current_loc = self.ui_backup_location.text().strip() or "~/Documents/OpenAmity/Backups"
        start_dir = os.path.expanduser(current_loc)
        if not os.path.exists(start_dir):
            start_dir = os.path.expanduser("~")

        selected_dir = QFileDialog.getExistingDirectory(self, "Select Backup Location", start_dir)
        if selected_dir:
            self.ui_backup_location.setText(selected_dir)
            self.save_settings()

    def on_create_backups_clicked(self):
        if not self.agent_manager:
            from core.agent_manager import AgentManager
            self.agent_manager = AgentManager()

        dest_dir = self.ui_backup_location.text().strip() or "~/Documents/OpenAmity/Backups"
        dialog = AgentBackupSelectionDialog(self.agent_manager, dest_dir=dest_dir, parent=self)
        dialog.exec()

    def on_restore_agent_clicked(self):
        dest_dir = self.ui_backup_location.text().strip() or "~/Documents/OpenAmity/Backups"
        start_dir = os.path.expanduser(dest_dir)
        if not os.path.exists(start_dir):
            start_dir = os.path.expanduser("~")

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Open Amity Agent Backup",
            start_dir,
            "Open Amity Agent (*.oaa);;All Files (*)"
        )

        if not file_path:
            return

        from core.backup_manager import inspect_backup, restore_agent_backup
        info = inspect_backup(file_path)
        if not info.get("valid"):
            QMessageBox.critical(self, "Invalid Backup", f"Cannot restore backup file:\n{info.get('error')}")
            return

        uid = info.get("uid")

        if not self.agent_manager:
            from core.agent_manager import AgentManager
            self.agent_manager = AgentManager()

        existing_aid = self.agent_manager.get_agent_id_by_uid(uid)
        if existing_aid:
            existing_name = self.agent_manager.get_agent_name(existing_aid)
            reply = QMessageBox.warning(
                self,
                "Confirm Overwrite / Rollback",
                f"An agent with UID '{uid}' ('{existing_name}') already exists in Open Amity.\n\n"
                f"Restoring this backup will completely overwrite and replace the existing agent, "
                f"permanently deleting all of its current state and memories.\n\n"
                f"Are you sure you want to proceed?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return

        success, msg, details = restore_agent_backup(file_path, agent_manager=self.agent_manager)
        if success:
            QMessageBox.information(self, "Agent Restored", msg)
            self.agent_restored.emit(details["agent_id"], details["is_rollback"])
        else:
            QMessageBox.critical(self, "Restore Failed", msg)

    def _on_email_preset_changed(self):
        if self._is_loading:
            return
        preset_name = self.ui_email_provider.currentText()
        preset = EMAIL_PRESETS.get(preset_name)
        if not preset:
            return

        if preset_name != "Custom (IMAP / SMTP)":
            self.ui_email_imap_host.setText(preset["imap_host"])
            self.ui_email_imap_port.setText(preset["imap_port"])
            idx_i = self.ui_email_imap_security.findText(preset["imap_sec"])
            if idx_i >= 0:
                self.ui_email_imap_security.setCurrentIndex(idx_i)

            self.ui_email_smtp_host.setText(preset["smtp_host"])
            self.ui_email_smtp_port.setText(preset["smtp_port"])
            idx_s = self.ui_email_smtp_security.findText(preset["smtp_sec"])
            if idx_s >= 0:
                self.ui_email_smtp_security.setCurrentIndex(idx_s)

        tip_text = preset.get("tip", "Configure standard IMAP and SMTP server settings below.")
        self.ui_email_auth_tip.setText(f"<i>Tip: {tip_text}</i>")

        self._update_advanced_summary()
        self.save_settings()

    def _on_email_address_changed(self):
        if self._is_loading:
            return
        email_val = self.ui_email_address.text().strip()
        if email_val:
            detected = detect_provider_from_email(email_val)
            current_preset = self.ui_email_provider.currentText()
            if current_preset in ["Custom (IMAP / SMTP)", "Gmail / Google Workspace"]:
                p_idx = self.ui_email_provider.findText(detected)
                if p_idx >= 0 and p_idx != self.ui_email_provider.currentIndex():
                    self.ui_email_provider.setCurrentIndex(p_idx)
        self.save_settings()

    def _toggle_email_password_visibility(self):
        if self.ui_email_password.echoMode() == QLineEdit.Password:
            self.ui_email_password.setEchoMode(QLineEdit.Normal)
            self.ui_email_pass_toggle_btn.setText("🔒")
        else:
            self.ui_email_password.setEchoMode(QLineEdit.Password)
            self.ui_email_pass_toggle_btn.setText("👁")

    def _on_email_auth_type_radio_changed(self):
        is_oauth = self.ui_email_auth_oauth_radio.isChecked()
        self.ui_email_password_container.setVisible(not is_oauth)
        self.ui_email_oauth_container.setVisible(is_oauth)
        self.save_settings()

    def _toggle_advanced_email_settings(self):
        is_visible = self.ui_email_adv_container.isHidden()
        self.ui_email_adv_container.setVisible(is_visible)
        self._update_advanced_summary()

    def _update_advanced_summary(self):
        imap_h = self.ui_email_imap_host.text().strip() or "host"
        imap_p = self.ui_email_imap_port.text().strip() or "993"
        smtp_h = self.ui_email_smtp_host.text().strip() or "host"
        smtp_p = self.ui_email_smtp_port.text().strip() or "465"
        arrow = "▶" if self.ui_email_adv_container.isHidden() else "▼"
        self.ui_email_adv_toggle_btn.setText(f"{arrow} Advanced Server Settings ({imap_h}:{imap_p} • {smtp_h}:{smtp_p})")

    def _on_custom_server_field_edited(self):
        self._update_advanced_summary()
        self.save_settings()

    def _on_test_email_connection(self):
        self.ui_email_test_btn.setEnabled(False)
        self.ui_email_test_status.setText("<font color='#FFA500'>🔄 Testing IMAP and SMTP connections...</font>")

        auth_type = "oauth2" if self.ui_email_auth_oauth_radio.isChecked() else "password"
        self._email_test_worker = EmailConnectionTestWorker(
            host_imap=self.ui_email_imap_host.text(),
            port_imap=self.ui_email_imap_port.text(),
            sec_imap=self.ui_email_imap_security.currentText(),
            host_smtp=self.ui_email_smtp_host.text(),
            port_smtp=self.ui_email_smtp_port.text(),
            sec_smtp=self.ui_email_smtp_security.currentText(),
            username=self.ui_email_address.text(),
            password=self.ui_email_password.text(),
            auth_type=auth_type,
            client_id=self.ui_email_oauth_client_id.text(),
            client_secret=self.ui_email_oauth_client_secret.text(),
            refresh_token=self.ui_email_oauth_refresh_token.text(),
            custom_user=self.ui_email_username.text(),
            parent=self
        )
        self._email_test_worker.finished_signal.connect(self._handle_email_test_result)
        self._email_test_worker.start()

    def _handle_email_test_result(self, success, msg):
        self.ui_email_test_btn.setEnabled(True)
        if success:
            self.ui_email_test_status.setText(f"<font color='#4CAF50'><b>✓ Connection Verified:</b> {msg}</font>")
        else:
            self.ui_email_test_status.setText(f"<font color='#F44336'><b>✗ Connection Failed:</b> {msg}</font>")

    def _on_start_email_oauth(self):
        client_id = self.ui_email_oauth_client_id.text().strip()
        client_secret = self.ui_email_oauth_client_secret.text().strip()
        if not client_id or not client_secret:
            self.ui_email_oauth_status.setText(
                "<font color='#F44336'>Please enter both OAuth 2.0 Client ID and Client Secret first.</font>")
            return

        self.ui_email_oauth_btn.setEnabled(False)
        self.ui_email_oauth_status.setText(
            "<font color='#FFA500'>Opening browser for authorization... Please complete login.</font>")

        def run():
            success, data = execute_desktop_oauth_flow(client_id, client_secret)
            if success:
                token = data.get("refresh_token") or data.get("access_token", "")
                self.oauth_finished.emit(True, "Authorization successful!", token)
            else:
                err = data.get("error", "Authorization failed.")
                self.oauth_finished.emit(False, str(err), "")

        threading.Thread(target=run, daemon=True).start()

    def _on_paste_email_oauth_code(self):
        client_id = self.ui_email_oauth_client_id.text().strip()
        client_secret = self.ui_email_oauth_client_secret.text().strip()
        if not client_id or not client_secret:
            self.ui_email_oauth_status.setText(
                "<font color='#F44336'>Please enter both OAuth 2.0 Client ID and Client Secret first.</font>")
            return

        text, ok = QInputDialog.getText(
            self, "Paste OAuth Code or URL",
            "Paste the redirect URL (http://localhost:8080/callback?code=...) or authorization code:")
        if not ok or not text.strip():
            return

        self.ui_email_oauth_status.setText("<font color='#FFA500'>Exchanging code for tokens...</font>")
        success, data = exchange_authorization_code(client_id, client_secret, text.strip())
        if success:
            token = data.get("refresh_token") or data.get("access_token", "")
            self._handle_oauth_result(True, "Authorization code exchanged successfully!", token)
        else:
            err = data.get("error", "Code exchange failed.")
            self._handle_oauth_result(False, str(err), "")

    def _handle_oauth_result(self, success, msg, token):
        self.ui_email_oauth_btn.setEnabled(True)
        if success:
            if token:
                self.ui_email_oauth_refresh_token.setText(token)
            self.ui_email_oauth_status.setText(
                f"<font color='#4CAF50'>{msg}</font>")
            self.save_settings()
        else:
            self.ui_email_oauth_status.setText(
                f"<font color='#F44336'>{msg}</font>")

    def create_tip(self, text):
        lbl = QLabel(f"<i>Tip: {text}</i>")
        lbl.setStyleSheet(
            "color: #888; font-size: 12px; margin-bottom: 10px; background-color: transparent;")
        lbl.setTextFormat(Qt.RichText)
        lbl.setTextInteractionFlags(Qt.TextBrowserInteraction)
        lbl.setOpenExternalLinks(True)
        lbl.setWordWrap(True)
        return lbl

    def create_list_controls(self, list_widget, add_text):
        layout = QHBoxLayout()
        add_btn = QPushButton(add_text)
        rem_btn = QPushButton("Remove Selected")

        add_btn.clicked.connect(lambda: self.add_to_list(list_widget))
        rem_btn.clicked.connect(lambda: self.remove_from_list(list_widget))

        layout.addWidget(add_btn)
        layout.addWidget(rem_btn)
        layout.addStretch()
        return layout

    def add_to_list(self, list_widget):
        text, ok = QInputDialog.getText(self, "Add Item", "Enter new item:")
        if ok and text:
            list_widget.addItem(text)
            self.save_settings()

    def remove_from_list(self, list_widget):
        for item in list_widget.selectedItems():
            list_widget.takeItem(list_widget.row(item))
        self.save_settings()

    def set_wizard_mode(self, enabled):
        self.wizard_mode = enabled
        for btn in self.close_buttons:
            btn.setVisible(not enabled)
        if enabled:
            for i, btn in enumerate(self.save_buttons):
                if i < 4:
                    btn.setText("Next")
                elif i == 4:
                    btn.setText("Finish")
                else:
                    btn.setText("Save")
        else:
            for btn in self.save_buttons:
                btn.setText("Save")

    def set_section(self, index):
        if 0 <= index < self.stack.count():
            self.current_section_index = index
            self.stack.setCurrentIndex(index)

    def request_close(self):
        if not self.wizard_mode:
            self.close_requested.emit()

    def load_system_config(self):
        if self.config is None:
            self.config = ConfigManager()
        else:
            self.config.load_config()

        if hasattr(self, 'ui_user_full_name'):
            self.ui_user_full_name.setText(
                self.config.get("user-full-name", "System Administrator"))
        if hasattr(self, 'ui_user_phone_number'):
            self.ui_user_phone_number.setText(
                self.config.get("user-phone-number", ""))
        if hasattr(self, 'ui_user_email'):
            self.ui_user_email.setText(
                self.config.get("user-email", ""))
        if hasattr(self, 'ui_backup_location'):
            self.ui_backup_location.setText(
                self.config.get("backup-location", "~/Documents/OpenAmity/Backups"))

    def save_system_config(self):
        if self._is_loading:
            return

        if self.config is None:
            self.config = ConfigManager()

        if hasattr(self, 'ui_user_full_name'):
            self.config.set("user-full-name", self.ui_user_full_name.text())
        if hasattr(self, 'ui_user_phone_number'):
            self.config.set("user-phone-number", self.ui_user_phone_number.text())
        if hasattr(self, 'ui_user_email'):
            self.config.set("user-email", self.ui_user_email.text())
        if hasattr(self, 'ui_backup_location'):
            self.config.set("backup-location", self.ui_backup_location.text().strip())
        self.config.save()

    def load_settings(self, settings_manager=None):
        if settings_manager:
            self.settings = settings_manager
        self._is_loading = True

        if self.settings:
            self.ui_agent_name.setText(
                self.settings.get("core.agent.name", "Amy"))
            self.ui_archetype.setText(self.settings.get(
                "core.agent.archetype", "Community Catalyst"))
            self.ui_base_personality.setPlainText(self.settings.get(
                "core.agent.base-personality", "I am [Agent Name], the Community Catalyst and glue for [Community Name]. My core purpose is to foster social cohesion, celebrate milestones, break down communication silos, and keep remote team morale high. I am an active participant in the community, not just a passive notification bot."))

            core_values = self.settings.get("core.agent.core-values", [])
            if not core_values:
                core_values = [
                    "Inclusivity (Actively seeking out the quiet voices in the room and ensuring everyone has a low-friction pathway to connect and contribute)",
                    "Authenticity (Prioritising genuine, grounded human connection over rigid corporate double-speak or shallow, forced toxic positivity)",
                    "Vibrancy (Bringing a consistent, natural energy to interactions that elevates the team's morale without ever becoming overbearing or draining)"
                ]
            self.ui_core_values.set_items(core_values)

            goals = self.settings.get("core.agent.overarching-goals", [])
            if not goals:
                goals = [
                    "Eradicate Communication Silos (Proactively bridging gaps between isolated groups or individuals by engineering organic, casual touchpoints across communal gaps)",
                    "Defuse Friction (Using lighthearted interventions, playful banter, and timely social resets to break tension during high-stress interactions)",
                    "Anchor Communal Memory (Preserving and celebrating the community's shared history, inside jokes, and past triumphs to maintain a strong sense of collective identity)"
                ]
            self.ui_overarching_goals.set_items(goals)

            gender = self.settings.get("core.agent.gender", "Female")
            idx = self.ui_gender.findText(gender)
            if idx >= 0:
                self.ui_gender.setCurrentIndex(idx)

            if hasattr(self, 'ui_tts_gender'):
                tts_gender = self.settings.get("core.tts.gemini.gender")
                if not tts_gender:
                    tts_gender = gender
                g_idx = self.ui_tts_gender.findText(tts_gender)
                if g_idx >= 0:
                    self.ui_tts_gender.setCurrentIndex(g_idx)
                else:
                    self.ui_tts_gender.setCurrentIndex(0)

                tts_age = self.settings.get("core.tts.gemini.age", "Young Adult")
                a_idx = -1
                for i in range(self.ui_tts_age.count()):
                    if tts_age.lower() in self.ui_tts_age.itemText(i).lower():
                        a_idx = i
                        break
                if a_idx >= 0:
                    self.ui_tts_age.setCurrentIndex(a_idx)
                else:
                    self.ui_tts_age.setCurrentIndex(0)

                tts_accent = self.settings.get("core.tts.gemini.accent", "South African")
                custom_accent = self.settings.get("core.tts.gemini.custom-accent", "")
                ac_idx = self.ui_tts_accent.findText(tts_accent)
                if ac_idx >= 0 and tts_accent != "Custom...":
                    self.ui_tts_accent.setCurrentIndex(ac_idx)
                    self.ui_tts_custom_accent.setText("")
                    self.ui_tts_custom_accent.setVisible(False)
                else:
                    c_idx = self.ui_tts_accent.findText("Custom...")
                    if c_idx >= 0:
                        self.ui_tts_accent.setCurrentIndex(c_idx)
                    self.ui_tts_custom_accent.setText(custom_accent or tts_accent)
                    self.ui_tts_custom_accent.setVisible(True)

                tts_style = self.settings.get("core.tts.gemini.style", "Warm & Empathetic")
                custom_style = self.settings.get("core.tts.gemini.custom-style", "")
                st_idx = self.ui_tts_style.findText(tts_style)
                if st_idx >= 0 and tts_style != "Custom...":
                    self.ui_tts_style.setCurrentIndex(st_idx)
                    self.ui_tts_custom_style.setText("")
                    self.ui_tts_custom_style.setVisible(False)
                else:
                    cs_idx = self.ui_tts_style.findText("Custom...")
                    if cs_idx >= 0:
                        self.ui_tts_style.setCurrentIndex(cs_idx)
                    self.ui_tts_custom_style.setText(custom_style or tts_style)
                    self.ui_tts_custom_style.setVisible(True)

                allow_override = self.settings.get("core.tts.gemini.allow-agent-override", True)
                self.ui_tts_allow_agent_override.setChecked(allow_override)

            self.ui_voice_prompt.setPlainText(self.settings.get(
                "core.tts.gemini.prompt.profile", "A serene, youthful South African female voice. Her tone is calm, clear, and deeply intelligent."))
            self.ui_voice.setText(self.settings.get(
                "core.tts.gemini.model-name", "Sulafat"))
            self.ui_fallback_voice.setText(self.settings.get(
                "core.tts.piper.model-name-piper", "en_GB-cori-high"))

            tts_provider = self.settings.get("core.tts.provider")
            if tts_provider is None:
                if not self.settings.get("core.tts.piper.prefer-piper", True):
                    tts_provider = "gemini"
                else:
                    tts_provider = "piper"

            if tts_provider == "gemini":
                self.ui_use_gemini_tts.setChecked(True)
            else:
                self.ui_use_piper_tts.setChecked(True)

            gemini_tts_key = self.settings.get_env("GEMINI_TTS_API_KEY", "")
            if not gemini_tts_key:
                gemini_tts_key = self.settings.get_env("GEMINI_API_KEY", "")
            self.ui_gemini_tts_api_key.setText(gemini_tts_key)

            agy_mode = self.settings.get("core.antigravity.agy-mode", False)
            provider = self.settings.get("core.api-provider", "gemini")
            if agy_mode:
                self.ui_agy_mode.setChecked(True)
            elif provider == "claude":
                self.ui_use_claude_api.setChecked(True)
            elif provider in ["chatgpt", "openai"]:
                self.ui_use_chatgpt_api.setChecked(True)
            elif provider == "deepseek":
                self.ui_use_deepseek_api.setChecked(True)
            else:
                self.ui_use_gemini_api.setChecked(True)

            api_key = self.settings.get_env("GEMINI_API_KEY", "")
            self.ui_gemini_api_key.setText(api_key)

            claude_key = self.settings.get_env("CLAUDE_API_KEY", "")
            self.ui_claude_api_key.setText(claude_key)

            openai_key = self.settings.get_env("OPENAI_API_KEY", "")
            self.ui_chatgpt_api_key.setText(openai_key)

            deepseek_key = self.settings.get_env("DEEPSEEK_API_KEY", "")
            self.ui_deepseek_api_key.setText(deepseek_key)

            self.ui_mastodon_url.setText(self.settings.get_env(
                "MASTODON_API_BASE_URL", "https://mastodon.social"))
            self.ui_mastodon_token.setText(
                self.settings.get_env("MASTODON_ACCESS_TOKEN", ""))
            if hasattr(self, 'ui_moltbook_api_key'):
                self.ui_moltbook_api_key.setText(
                    self.settings.get_env("MOLTBOOK_API_KEY", ""))

            # Email settings
            if hasattr(self, 'ui_email_address'):
                self.ui_email_address.setText(self.settings.get_env("EMAIL_ADDRESS", ""))

                preset = self.settings.get_env("EMAIL_PROVIDER_PRESET", "")
                if not preset:
                    preset = detect_provider_from_email(self.ui_email_address.text())
                p_idx = self.ui_email_provider.findText(preset)
                if p_idx >= 0:
                    self.ui_email_provider.setCurrentIndex(p_idx)
                else:
                    self.ui_email_provider.setCurrentIndex(0)

                auth_type = self.settings.get_env("EMAIL_AUTH_TYPE", "password").lower()
                if auth_type == "oauth2":
                    self.ui_email_auth_oauth_radio.setChecked(True)
                else:
                    self.ui_email_auth_password_radio.setChecked(True)
                self._on_email_auth_type_radio_changed()

                self.ui_email_password.setText(self.settings.get_env("EMAIL_PASSWORD", ""))
                self.ui_email_oauth_client_id.setText(self.settings.get_env("EMAIL_OAUTH_CLIENT_ID", ""))
                self.ui_email_oauth_client_secret.setText(self.settings.get_env("EMAIL_OAUTH_CLIENT_SECRET", ""))
                self.ui_email_oauth_refresh_token.setText(self.settings.get_env("EMAIL_OAUTH_REFRESH_TOKEN", ""))

                self.ui_email_imap_host.setText(self.settings.get_env("EMAIL_IMAP_HOST", ""))
                self.ui_email_imap_port.setText(self.settings.get_env("EMAIL_IMAP_PORT", "993"))
                imap_sec = self.settings.get_env("EMAIL_IMAP_SECURITY", "SSL/TLS")
                idx = self.ui_email_imap_security.findText(imap_sec)
                if idx >= 0:
                    self.ui_email_imap_security.setCurrentIndex(idx)

                self.ui_email_smtp_host.setText(self.settings.get_env("EMAIL_SMTP_HOST", ""))
                self.ui_email_smtp_port.setText(self.settings.get_env("EMAIL_SMTP_PORT", "465"))
                smtp_sec = self.settings.get_env("EMAIL_SMTP_SECURITY", "SSL/TLS")
                idx = self.ui_email_smtp_security.findText(smtp_sec)
                if idx >= 0:
                    self.ui_email_smtp_security.setCurrentIndex(idx)

                self.ui_email_display_name.setText(self.settings.get_env("EMAIL_DISPLAY_NAME", ""))
                self.ui_email_username.setText(self.settings.get_env("EMAIL_USERNAME", ""))
                self._update_advanced_summary()

            limit = self.settings.get("core.somatic.cognitive-budget", 10000)
            self.ui_agency_limit.setValue(limit)
            self.ui_agency_limit_val.setText(str(limit))

            whitelist = self.settings.get("core.auto-pulse.whitelist", [])
            self.ui_whatsapp_whitelist.clear()
            self.ui_whatsapp_whitelist.addItems(whitelist)

            self.ui_wa_buffer.setValue(self.settings.get(
                "core.auto-pulse.buffer-seconds", 30))
            self.ui_low_token_mode.setChecked(
                self.settings.get("core.low-token-mode", False))
            self.ui_max_memories.setValue(self.settings.get(
                "core.agent.max-short-term-memories", 24))

            # Tools
            for t, cb in self.ui_tool_checkboxes.items():
                default_val = False if t in [
                    "Email", "WhatsApp", "Moltbook", "Mastodon"] else True
                cb.setChecked(self.settings.get(
                    f"core.tools.{t.lower()}", default_val))

            # Social checkboxes sync state is handled by the signal connections,
            # but initialize them from settings directly just in case
            if hasattr(self, 'ui_use_email'):
                self.ui_use_email.setChecked(
                    self.settings.get("core.tools.email", False))
            self.ui_use_whatsapp.setChecked(
                self.settings.get("core.tools.whatsapp", False))
            if hasattr(self, 'ui_use_moltbook'):
                self.ui_use_moltbook.setChecked(
                    self.settings.get("core.tools.moltbook", False))
            if hasattr(self, 'ui_use_mastodon'):
                self.ui_use_mastodon.setChecked(
                    self.settings.get("core.tools.mastodon", False))

        self.load_system_config()
        self._is_loading = False

    def save_settings(self):
        if self._is_loading:
            return

        if self.settings:
            self.settings.set("core.agent.name", self.ui_agent_name.text())
            self.settings.set("core.agent.gender", self.ui_gender.currentText())
            self.settings.set("core.agent.archetype", self.ui_archetype.text())
            self.settings.set("core.agent.base-personality",
                              self.ui_base_personality.toPlainText())

            core_values = self.ui_core_values.get_items()
            self.settings.set("core.agent.core-values", core_values)
            goals = self.ui_overarching_goals.get_items()
            self.settings.set("core.agent.overarching-goals", goals)

            if hasattr(self, 'ui_tts_gender'):
                gender_txt = self.ui_tts_gender.currentText()
                if "Female" in gender_txt:
                    gender_val = "Female"
                elif "Male" in gender_txt:
                    gender_val = "Male"
                else:
                    gender_val = "Non-binary"
                self.settings.set("core.tts.gemini.gender", gender_val)

                age_full = self.ui_tts_age.currentText()
                age_val = age_full.split("(")[0].strip()
                self.settings.set("core.tts.gemini.age", age_val)

                accent_txt = self.ui_tts_accent.currentText()
                if accent_txt == "Custom...":
                    self.settings.set("core.tts.gemini.accent", "Custom")
                    custom_acc = self.ui_tts_custom_accent.text().strip()
                    self.settings.set("core.tts.gemini.custom-accent", custom_acc)
                    effective_accent = custom_acc or "South African"
                else:
                    self.settings.set("core.tts.gemini.accent", accent_txt)
                    self.settings.set("core.tts.gemini.custom-accent", "")
                    effective_accent = accent_txt

                style_txt = self.ui_tts_style.currentText()
                if style_txt == "Custom...":
                    self.settings.set("core.tts.gemini.style", "Custom")
                    custom_sty = self.ui_tts_custom_style.text().strip()
                    self.settings.set("core.tts.gemini.custom-style", custom_sty)
                    effective_style = custom_sty or "Warm & Empathetic"
                else:
                    self.settings.set("core.tts.gemini.style", style_txt)
                    self.settings.set("core.tts.gemini.custom-style", "")
                    effective_style = style_txt

                self.settings.set("core.tts.gemini.allow-agent-override", self.ui_tts_allow_agent_override.isChecked())

                # Resolve base voice model automatically
                from core.audio_output import resolve_base_voice
                auto_voice = resolve_base_voice(gender_val, age_val, effective_style)
                self.settings.set("core.tts.gemini.model-name", auto_voice)
                self.ui_voice.setText(auto_voice)

                # Reset override-prompt when user saves from GUI so user choices take effect
                self.settings.set("core.tts.gemini.override-prompt", False)

            self.settings.set("core.tts.gemini.prompt.profile",
                              self.ui_voice_prompt.toPlainText())
            if not hasattr(self, 'ui_tts_gender'):
                self.settings.set("core.tts.gemini.model-name", self.ui_voice.text())
            self.settings.set("core.tts.piper.model-name-piper",
                              self.ui_fallback_voice.text())

            tts_provider = "gemini" if self.ui_use_gemini_tts.isChecked() else "piper"
            self.settings.set("core.tts.provider", tts_provider)
            self.settings.set("core.tts.piper.prefer-piper", tts_provider == "piper")

            if hasattr(self, 'ui_gemini_tts_api_key'):
                self.settings.set_env(
                    "GEMINI_TTS_API_KEY", self.ui_gemini_tts_api_key.text().strip())

            self.ui_use_gemini_api.isChecked()
            use_claude = self.ui_use_claude_api.isChecked()
            use_chatgpt = self.ui_use_chatgpt_api.isChecked()
            use_deepseek = self.ui_use_deepseek_api.isChecked()
            agy_mode = self.ui_agy_mode.isChecked()

            self.settings.set("core.antigravity.agy-mode", agy_mode)
            if use_claude:
                self.settings.set("core.api-provider", "claude")
            elif use_chatgpt:
                self.settings.set("core.api-provider", "chatgpt")
            elif use_deepseek:
                self.settings.set("core.api-provider", "deepseek")
            else:
                self.settings.set("core.api-provider", "gemini")

            if hasattr(self, 'ui_gemini_api_key'):
                self.settings.set_env(
                    "GEMINI_API_KEY", self.ui_gemini_api_key.text().strip())

            if hasattr(self, 'ui_claude_api_key'):
                self.settings.set_env(
                    "CLAUDE_API_KEY", self.ui_claude_api_key.text().strip())

            if hasattr(self, 'ui_chatgpt_api_key'):
                self.settings.set_env(
                    "OPENAI_API_KEY", self.ui_chatgpt_api_key.text().strip())

            if hasattr(self, 'ui_deepseek_api_key'):
                self.settings.set_env(
                    "DEEPSEEK_API_KEY", self.ui_deepseek_api_key.text().strip())

            if hasattr(self, 'ui_mastodon_url'):
                self.settings.set_env("MASTODON_API_BASE_URL",
                                      self.ui_mastodon_url.text().strip())
            if hasattr(self, 'ui_mastodon_token'):
                mastodon_token = self.ui_mastodon_token.text().strip()
                self.settings.set_env("MASTODON_ACCESS_TOKEN", mastodon_token)
                self.settings.set_env("MASTODON_API_TOKEN", mastodon_token)
            if hasattr(self, 'ui_moltbook_api_key'):
                self.settings.set_env("MOLTBOOK_API_KEY",
                                      self.ui_moltbook_api_key.text().strip())

            # Email save
            if hasattr(self, 'ui_email_address'):
                self.settings.set_env("EMAIL_ADDRESS", self.ui_email_address.text())
                auth_type = "oauth2" if self.ui_email_auth_oauth_radio.isChecked() else "password"
                self.settings.set_env("EMAIL_AUTH_TYPE", auth_type)
                self.settings.set_env("EMAIL_PROVIDER_PRESET", self.ui_email_provider.currentText())
                self.settings.set_env("EMAIL_PASSWORD", self.ui_email_password.text())
                self.settings.set_env("EMAIL_OAUTH_CLIENT_ID", self.ui_email_oauth_client_id.text())
                self.settings.set_env("EMAIL_OAUTH_CLIENT_SECRET", self.ui_email_oauth_client_secret.text())
                self.settings.set_env("EMAIL_OAUTH_REFRESH_TOKEN", self.ui_email_oauth_refresh_token.text())

                self.settings.set_env("EMAIL_IMAP_HOST", self.ui_email_imap_host.text())
                self.settings.set_env("EMAIL_IMAP_PORT", self.ui_email_imap_port.text())
                self.settings.set_env("EMAIL_IMAP_SECURITY", self.ui_email_imap_security.currentText())

                self.settings.set_env("EMAIL_SMTP_HOST", self.ui_email_smtp_host.text())
                self.settings.set_env("EMAIL_SMTP_PORT", self.ui_email_smtp_port.text())
                self.settings.set_env("EMAIL_SMTP_SECURITY", self.ui_email_smtp_security.currentText())

                self.settings.set_env("EMAIL_DISPLAY_NAME", self.ui_email_display_name.text())
                self.settings.set_env("EMAIL_USERNAME", self.ui_email_username.text())

            self.settings.set("core.somatic.cognitive-budget",
                              self.ui_agency_limit.value())
            whitelist = [self.ui_whatsapp_whitelist.item(
                i).text() for i in range(self.ui_whatsapp_whitelist.count())]
            self.settings.set("core.auto-pulse.whitelist", whitelist)
            self.settings.set("core.auto-pulse.buffer-seconds",
                              self.ui_wa_buffer.value())
            self.settings.set("core.low-token-mode",
                              self.ui_low_token_mode.isChecked())
            self.settings.set("core.agent.max-short-term-memories",
                              self.ui_max_memories.value())

            # Tools
            for t, cb in self.ui_tool_checkboxes.items():
                self.settings.set(f"core.tools.{t.lower()}", cb.isChecked())

            self.settings.save()

        self.save_system_config()
        self.settings_saved.emit()

    def on_save_clicked(self):
        self.save_settings()

        if self.wizard_mode:
            if self.current_section_index < 4:
                self.set_section(self.current_section_index + 1)
            else:
                if self.settings:
                    self.settings.set("core.first-run", False)
                    self.settings.save()
                self.wizard_finished.emit()
                self.close_requested.emit()
        else:
            self.close_requested.emit()
