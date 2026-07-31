import os
import requests
import io
import qrcode
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
                               QLabel, QLineEdit, QTextEdit, QComboBox, QCheckBox, 
                               QRadioButton, QStackedWidget, QListWidget, QInputDialog, QSlider, 
                               QSpinBox, QScrollArea, QFrame, QSizePolicy)
from PySide6.QtCore import Qt, Signal, QObject, QEvent, QTimer
from PySide6.QtGui import QFont, QPixmap, QImage

try:
    from core.settings_manager import SettingsManager
except ImportError:
    from ..core.settings_manager import SettingsManager

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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.wizard_mode = False
        self.current_section_index = 0
        self.settings = SettingsManager()
        self._is_loading = False
        
        self.focus_out_filter = FocusOutFilter(self.save_settings, self)
        
        self.save_buttons = []
        
        self.whatsapp_timer = QTimer(self)
        self.whatsapp_timer.timeout.connect(self.poll_whatsapp_status)
        self.whatsapp_timer.start(2000)
        
        self.init_ui()
        self.load_settings()

    def poll_whatsapp_status(self):
        # Only poll if the current tab is Social Accounts (index 4)
        if self.stack.currentIndex() != 4:
            return
            
        if not self.ui_use_whatsapp.isChecked():
            self.ui_qr_code.setText("[WhatsApp Disabled]")
            self.ui_qr_code.setPixmap(QPixmap())
            return

        try:
            res = requests.get("http://localhost:3000/status", timeout=1)
            if res.status_code == 200:
                data = res.json()
                if data.get("ready"):
                    self.ui_qr_code.setPixmap(QPixmap())
                    self.ui_qr_code.setText("✓ WhatsApp Authenticated")
                elif data.get("qr"):
                    qr_img = qrcode.make(data["qr"])
                    buf = io.BytesIO()
                    qr_img.save(buf, format="PNG")
                    img = QImage.fromData(buf.getvalue())
                    pixmap = QPixmap.fromImage(img)
                    self.ui_qr_code.setPixmap(pixmap.scaled(200, 200, Qt.KeepAspectRatio))
                else:
                    self.ui_qr_code.setPixmap(QPixmap())
                    self.ui_qr_code.setText("[Waiting for QR...]")
        except requests.exceptions.RequestException:
            self.ui_qr_code.setPixmap(QPixmap())
            self.ui_qr_code.setText("[WhatsApp Bridge Not Running]")

    def init_ui(self):
        self.setStyleSheet("""
            QWidget { background-color: #222; color: #FFF; font-size: 14px; }
            QLineEdit, QTextEdit, QListWidget, QComboBox, QSpinBox {
                background-color: #333; color: #FFF; border: 1px solid #555; border-radius: 5px; padding: 5px;
            }
            QPushButton {
                background-color: #444; color: #FFF; border: 1px solid #666; border-radius: 5px; padding: 8px;
            }
            QPushButton:hover { background-color: #555; }
            QLabel { color: #CCC; }
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Header with Title and Close Button
        header = QWidget()
        header.setStyleSheet("background-color: #1a1a1a;")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 10, 20, 10)
        
        self.title_label = QLabel("Settings")
        self.title_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #00FFC8; background-color: transparent;")
        header_layout.addWidget(self.title_label)
        header_layout.addStretch()

        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedSize(30, 30)
        self.close_btn.setStyleSheet("background-color: transparent; border: none; font-size: 18px; font-weight: bold;")
        self.close_btn.clicked.connect(self.request_close)
        header_layout.addWidget(self.close_btn)
        
        main_layout.addWidget(header)

        # Content Area
        self.stack = QStackedWidget()
        main_layout.addWidget(self.stack, 1)
        
        # Build Panels
        self.build_basic_agent_settings()
        self.build_agent_values_goals()
        self.build_agent_voice()
        self.build_gemini_setup()
        self.build_social_accounts()
        self.build_system_settings()

    def create_panel_container(self, title):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(15)
        
        lbl = QLabel(title)
        lbl.setStyleSheet("font-size: 22px; font-weight: bold; color: #FFF; margin-bottom: 10px; background-color: transparent;")
        layout.addWidget(lbl)
        
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
        card.setStyleSheet("""
            .QFrame {
                background-color: #2a2a2a;
                border: 1px solid #444;
                border-radius: 8px;
            }
            QLabel, QCheckBox, QRadioButton {
                background-color: transparent;
            }
        """)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)
        return card, layout

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
        layout.addWidget(self.create_tip("e.g. Researcher, Concierge, Knowledge Shepherd, Scrum Master, Systems Architect, Critic, etc."))
        
        layout.addWidget(QLabel("Base Personality:"))
        self.ui_base_personality = QTextEdit()
        self.ui_base_personality.setFixedHeight(120)
        layout.addWidget(self.ui_base_personality)
        layout.addWidget(self.create_tip("The base personality must be written in the first person because the agent will read it as if they wrote it themselves"))
        
        self.ui_agent_name.editingFinished.connect(self.save_settings)
        self.ui_gender.currentTextChanged.connect(self.save_settings)
        self.ui_archetype.editingFinished.connect(self.save_settings)
        self.ui_base_personality.installEventFilter(self.focus_out_filter)
        
        self.stack.addWidget(panel)

    def build_agent_values_goals(self):
        panel, layout, _ = self.create_panel_container("Agent Values and Goals")
        
        layout.addWidget(QLabel("Core Values:"))
        self.ui_core_values = QListWidget()
        self.ui_core_values.setFixedHeight(120) # Max 4 items visible roughly
        layout.addWidget(self.ui_core_values)
        layout.addLayout(self.create_list_controls(self.ui_core_values, "Add Core Value"))
        
        layout.addWidget(QLabel("Overarching Goals:"))
        self.ui_overarching_goals = QListWidget()
        self.ui_overarching_goals.setFixedHeight(120)
        layout.addWidget(self.ui_overarching_goals)
        layout.addLayout(self.create_list_controls(self.ui_overarching_goals, "Add Goal"))
        
        self.stack.addWidget(panel)

    def build_agent_voice(self):
        panel, layout, _ = self.create_panel_container("Agent Voice")
        
        layout.addWidget(QLabel("Voice Prompt (Profile):"))
        self.ui_voice_prompt = QTextEdit()
        self.ui_voice_prompt.setFixedHeight(80)
        layout.addWidget(self.ui_voice_prompt)
        layout.addWidget(self.create_tip("Set the vocal profile for the Gemini TTS engine. This directs the agent's tone, accent, and style."))
        
        layout.addWidget(QLabel("High Quality Voice (Gemini TTS):"))
        self.ui_voice = QLineEdit()
        layout.addWidget(self.ui_voice)
        layout.addWidget(self.create_tip("To find more voice model strings go to: <a href='https://docs.cloud.google.com/text-to-speech/docs/gemini-tts'>Gemini TTS Documentation</a>"))
        
        layout.addWidget(QLabel("Fallback Voice (Piper TTS):"))
        self.ui_fallback_voice = QLineEdit()
        layout.addWidget(self.ui_fallback_voice)
        layout.addWidget(self.create_tip("To find more Piper TTS voice model strings go to: <a href='https://rhasspy.github.io/piper-samples/#en_GB-cori-high'>Piper Samples</a>"))
        
        self.ui_prefer_local_tts = QCheckBox("Prefer Fallback TTS")
        layout.addWidget(self.ui_prefer_local_tts)
        layout.addWidget(self.create_tip("Use the lighter and cheaper fallback voice model at the cost of quality"))
        
        self.ui_voice_prompt.installEventFilter(self.focus_out_filter)
        self.ui_voice.editingFinished.connect(self.save_settings)
        self.ui_fallback_voice.editingFinished.connect(self.save_settings)
        self.ui_prefer_local_tts.toggled.connect(self.save_settings)
        
        self.stack.addWidget(panel)

    def build_gemini_setup(self):
        panel, main_layout, _ = self.create_panel_container("Gemini Setup")
        
        self.ui_use_gemini_api = QRadioButton("Use Gemini API")
        main_layout.addWidget(self.ui_use_gemini_api)
        
        card1, layout1 = self.create_card_container()
        lbl_gemini = QLabel("<b>Gemini API Setup</b>")
        lbl_gemini.setStyleSheet("background-color: transparent; font-size: 16px;")
        layout1.addWidget(lbl_gemini)
        guide2 = QLabel("1. Go to <a href='https://aistudio.google.com/'>AI Studio</a>.<br>2. Sign in with your Google Account.<br>3. Click 'Get API key' and create a new key.")
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
        
        self.ui_agy_mode = QRadioButton("Use Antigravity")
        main_layout.addWidget(self.ui_agy_mode)
        
        card2, layout2 = self.create_card_container()
        lbl_agy = QLabel("<b>Antigravity Setup</b>")
        lbl_agy.setStyleSheet("background-color: transparent; font-size: 16px;")
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
        self.ui_gemini_api_key.editingFinished.connect(self.save_settings)
        
        main_layout.addStretch()
        
        self.stack.addWidget(panel)

    def build_social_accounts(self):
        panel, main_layout, _ = self.create_panel_container("Social Accounts")
        
        self.ui_use_whatsapp = QCheckBox("Use Whatsapp")
        main_layout.addWidget(self.ui_use_whatsapp)
        
        card1, layout1 = self.create_card_container()
        lbl_wa = QLabel("<b>Whatsapp Account Setup</b>")
        lbl_wa.setStyleSheet("background-color: transparent; font-size: 16px;")
        layout1.addWidget(lbl_wa)
        w_guide = QLabel("This will be the agent's own Whatsapp account. Do not link your personal Whatsapp account here, rather set up a dedicated account for the agent.")
        w_guide.setWordWrap(True)
        w_guide.setStyleSheet("background-color: transparent;")
        layout1.addWidget(w_guide)
        
        self.ui_qr_code = QLabel("[QR Code Placeholder]")
        self.ui_qr_code.setAlignment(Qt.AlignCenter)
        self.ui_qr_code.setFixedSize(200, 200)
        self.ui_qr_code.setStyleSheet("background-color: #000; border: 1px solid #555;")
        layout1.addWidget(self.ui_qr_code)
        main_layout.addWidget(card1)
        
        main_layout.addSpacing(10)
        
        self.ui_use_moltbook = QCheckBox("Use Moltbook")
        main_layout.addWidget(self.ui_use_moltbook)
        
        card2, layout2 = self.create_card_container()
        lbl_mb = QLabel("<b>Moltbook Account Setup</b>")
        lbl_mb.setStyleSheet("background-color: transparent; font-size: 16px;")
        layout2.addWidget(lbl_mb)
        mb_guide = QLabel("<b>Setup Guide:</b><br>"
                          "1. To set up Moltbook, simply ask your agent to register an account for themselves!<br>"
                          "2. The agent will handle the registration and provide you with a claim URL.<br>"
                          "3. Visit the claim URL to verify the account.")
        mb_guide.setTextFormat(Qt.RichText)
        mb_guide.setTextInteractionFlags(Qt.TextBrowserInteraction)
        mb_guide.setOpenExternalLinks(True)
        mb_guide.setWordWrap(True)
        mb_guide.setStyleSheet("background-color: transparent;")
        layout2.addWidget(mb_guide)
        
        layout2.addWidget(QLabel("Moltbook API Key:"))
        self.ui_moltbook_api_key = QLineEdit()
        self.ui_moltbook_api_key.setEchoMode(QLineEdit.Password)
        layout2.addWidget(self.ui_moltbook_api_key)
        main_layout.addWidget(card2)
        
        main_layout.addSpacing(10)
        
        self.ui_use_mastodon = QCheckBox("Use Mastodon")
        main_layout.addWidget(self.ui_use_mastodon)
        
        card3, layout3 = self.create_card_container()
        lbl_mastodon = QLabel("<b>Mastodon Account Setup</b>")
        lbl_mastodon.setStyleSheet("background-color: transparent; font-size: 16px;")
        layout3.addWidget(lbl_mastodon)
        m_guide = QLabel("<b>Setup Guide:</b><br>"
                         "1. Create an account for your agent on <a href='https://mastodon.bot'>mastodon.bot</a>.<br>"
                         "2. Once approved go to Preferences > Development > New Application.<br>"
                         "3. Give it a name, submit, and copy the 'Your access token' value.<br><br>"
                         "<i>Note: Most bot accounts should be registered on mastodon.bot, other servers generally do not allow AI-generated content. Additionally you must use the bot flag for the agent and specify that it is an AI agent in the profile bio. You also need to include your name or organisation in the agent's profile bio. Lastly, your agent must strictly adhere to the <a href='https://explore.mastodon.bot/rules#rules-for-bots'>rules for bots</a>.</i>")
        m_guide.setTextFormat(Qt.RichText)
        m_guide.setTextInteractionFlags(Qt.TextBrowserInteraction)
        m_guide.setOpenExternalLinks(True)
        m_guide.setWordWrap(True)
        m_guide.setStyleSheet("background-color: transparent;")
        layout3.addWidget(m_guide)
        
        layout3.addWidget(QLabel("Mastodon API Base URL:"))
        self.ui_mastodon_url = QLineEdit()
        layout3.addWidget(self.ui_mastodon_url)
        
        layout3.addWidget(QLabel("Mastodon Access Token:"))
        self.ui_mastodon_token = QLineEdit()
        self.ui_mastodon_token.setEchoMode(QLineEdit.Password)
        layout3.addWidget(self.ui_mastodon_token)
        main_layout.addWidget(card3)
        
        self.ui_use_whatsapp.toggled.connect(self.save_settings)
        self.ui_use_moltbook.toggled.connect(self.save_settings)
        self.ui_moltbook_api_key.editingFinished.connect(self.save_settings)
        self.ui_use_mastodon.toggled.connect(self.save_settings)
        self.ui_mastodon_url.editingFinished.connect(self.save_settings)
        self.ui_mastodon_token.editingFinished.connect(self.save_settings)
        
        main_layout.addStretch()
        
        self.stack.addWidget(panel)

    def build_system_settings(self):
        panel, main_layout, _ = self.create_panel_container("System Settings")
        
        # Agent Setup Card
        card1, layout1 = self.create_card_container()
        lbl_agent_setup = QLabel("<b>Agent Setup</b>")
        lbl_agent_setup.setStyleSheet("background-color: transparent; font-size: 16px;")
        layout1.addWidget(lbl_agent_setup)
        
        self.ui_low_token_mode = QCheckBox("Low Token Mode")
        layout1.addWidget(self.ui_low_token_mode)
        layout1.addWidget(self.create_tip("Use Low Token Mode when using a free-tier Gemini API key for best results. It disables high quality voice and multimodal input, it uses the cheaper Gemini Lite model, and it reduces token expenditure in general where possible."))
        
        layout1.addWidget(QLabel("Maximum Short-Term Memories:"))
        self.ui_max_memories = QSpinBox()
        self.ui_max_memories.setFocusPolicy(Qt.StrongFocus)
        self.ui_max_memories.wheelEvent = lambda event: event.ignore()
        self.ui_max_memories.setRange(1, 1000)
        self.ui_max_memories.setValue(24)
        layout1.addWidget(self.ui_max_memories)
        layout1.addWidget(self.create_tip("This increases token use and can potentially cause context bloat."))
        
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
        self.ui_agency_limit.valueChanged.connect(lambda v: self.ui_agency_limit_val.setText(str(v - (v % 1000))))
        layout1.addWidget(self.ui_agency_limit)
        layout1.addWidget(self.create_tip("This affects how long the agent can run autonomously before being forcefully stopped"))
        
        main_layout.addWidget(card1)
        
        main_layout.addSpacing(10)
        
        # Whatsapp Settings Card
        card2, layout2 = self.create_card_container()
        lbl_wa_settings = QLabel("<b>Whatsapp Settings</b>")
        lbl_wa_settings.setStyleSheet("background-color: transparent; font-size: 16px;")
        layout2.addWidget(lbl_wa_settings)
        
        layout2.addWidget(QLabel("Whatsapp Auto-response Buffer (seconds):"))
        self.ui_wa_buffer = QSpinBox()
        self.ui_wa_buffer.setFocusPolicy(Qt.StrongFocus)
        self.ui_wa_buffer.wheelEvent = lambda event: event.ignore()
        self.ui_wa_buffer.setRange(0, 3600)
        self.ui_wa_buffer.setValue(30)
        layout2.addWidget(self.ui_wa_buffer)
        layout2.addWidget(self.create_tip("The time to wait before the agent automatically reads direct messages sent to them from a whitelisted number."))
        
        layout2.addWidget(QLabel("Whatsapp Whitelist:"))
        self.ui_whatsapp_whitelist = QListWidget()
        self.ui_whatsapp_whitelist.setFixedHeight(100)
        layout2.addWidget(self.ui_whatsapp_whitelist)
        layout2.addLayout(self.create_list_controls(self.ui_whatsapp_whitelist, "Add Number"))
        layout2.addWidget(self.create_tip("This is a list of phone numbers that will trigger an automatic pulse when the agent receives a direct message from them. Must include international dialling code."))
        
        main_layout.addWidget(card2)
        
        main_layout.addSpacing(10)
        
        # Tools Card
        card3, layout3 = self.create_card_container()
        lbl_tools = QLabel("<b>Tools</b>")
        lbl_tools.setStyleSheet("background-color: transparent; font-size: 16px;")
        layout3.addWidget(lbl_tools)
        self.ui_tool_checkboxes = {}
        tool_names = ["Contacts", "DateTime", "Mastodon", "MemPalace", "Moltbook", "PulseTool", "Speaker", "Terminal", "Trajectory", "WhatsApp"]
        
        for t in tool_names:
            cb = QCheckBox(t)
            cb.toggled.connect(self.save_settings)
            self.ui_tool_checkboxes[t] = cb
            if t in ["Contacts", "DateTime", "MemPalace", "PulseTool", "Speaker", "Trajectory"]:
                cb.setVisible(False)
            layout3.addWidget(cb)
            
        main_layout.addWidget(card3)
        
        # Sync logic for Social Accounts checkboxes
        self.ui_tool_checkboxes["WhatsApp"].toggled.connect(self.ui_use_whatsapp.setChecked)
        self.ui_use_whatsapp.toggled.connect(self.ui_tool_checkboxes["WhatsApp"].setChecked)
        
        if hasattr(self, 'ui_use_moltbook'):
            self.ui_tool_checkboxes["Moltbook"].toggled.connect(self.ui_use_moltbook.setChecked)
            self.ui_use_moltbook.toggled.connect(self.ui_tool_checkboxes["Moltbook"].setChecked)
        
        if hasattr(self, 'ui_use_mastodon'):
            self.ui_tool_checkboxes["Mastodon"].toggled.connect(self.ui_use_mastodon.setChecked)
            self.ui_use_mastodon.toggled.connect(self.ui_tool_checkboxes["Mastodon"].setChecked)
        
        self.ui_agency_limit.sliderReleased.connect(self.save_settings)
        self.ui_wa_buffer.editingFinished.connect(self.save_settings)
        self.ui_low_token_mode.toggled.connect(self.save_settings)
        self.ui_max_memories.editingFinished.connect(self.save_settings)
        
        main_layout.addStretch()
        
        self.stack.addWidget(panel)

    def create_tip(self, text):
        lbl = QLabel(f"<i>Tip: {text}</i>")
        lbl.setStyleSheet("color: #888; font-size: 12px; margin-bottom: 10px; background-color: transparent;")
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
        self.close_btn.setVisible(not enabled)
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

    def load_settings(self):
        self._is_loading = True
        self.ui_agent_name.setText(self.settings.get("core.agent.name", "Ammi"))
        self.ui_archetype.setText(self.settings.get("core.agent.archetype", "Community Catalyst"))
        self.ui_base_personality.setPlainText(self.settings.get("core.agent.base-personality", "I am [Agent Name], the Community Catalyst and glue for [Community Name]. My core purpose is to foster social cohesion, celebrate milestones, break down communication silos, and keep remote team morale high. I am an active participant in the community, not just a passive notification bot."))
        
        core_values = self.settings.get("core.agent.core-values", [])
        if not core_values:
            core_values = [
                "Inclusivity (Actively seeking out the quiet voices in the room and ensuring everyone has a low-friction pathway to connect and contribute)",
                "Authenticity (Prioritising genuine, grounded human connection over rigid corporate double-speak or shallow, forced toxic positivity)",
                "Vibrancy (Bringing a consistent, natural energy to interactions that elevates the team's morale without ever becoming overbearing or draining)"
            ]
        self.ui_core_values.clear()
        self.ui_core_values.addItems(core_values)
        
        goals = self.settings.get("core.agent.overarching-goals", [])
        if not goals:
            goals = [
                "Eradicate Communication Silos (Proactively bridging gaps between isolated groups or individuals by engineering organic, casual touchpoints across communal gaps)",
                "Defuse Friction (Using lighthearted interventions, playful banter, and timely social resets to break tension during high-stress interactions)",
                "Anchor Communal Memory (Preserving and celebrating the community's shared history, inside jokes, and past triumphs to maintain a strong sense of collective identity)"
            ]
        self.ui_overarching_goals.clear()
        self.ui_overarching_goals.addItems(goals)
        
        gender = self.settings.get("core.agent.gender", "Female")
        idx = self.ui_gender.findText(gender)
        if idx >= 0: self.ui_gender.setCurrentIndex(idx)
        
        self.ui_voice_prompt.setPlainText(self.settings.get("core.tts.gemini.prompt.profile", "A serene, warm South African female voice. Her tone is calm, clear, and deeply intelligent, with a soft, resonant quality."))
        self.ui_voice.setText(self.settings.get("core.tts.gemini.model-name", "Achernar"))
        self.ui_fallback_voice.setText(self.settings.get("core.tts.piper.model-name-piper", "cori"))
        self.ui_prefer_local_tts.setChecked(self.settings.get("core.tts.piper.prefer-piper", False))
        
        agy_mode = self.settings.get("core.antigravity.agy-mode", False)
        if agy_mode:
            self.ui_agy_mode.setChecked(True)
        else:
            self.ui_use_gemini_api.setChecked(True)
            
        api_key = self.settings.get_env("GEMINI_API_KEY", "")
        self.ui_gemini_api_key.setText(api_key)
        
        self.ui_mastodon_url.setText(self.settings.get_env("MASTODON_API_BASE_URL", "https://mastodon.social"))
        self.ui_mastodon_token.setText(self.settings.get_env("MASTODON_ACCESS_TOKEN", ""))
        if hasattr(self, 'ui_moltbook_api_key'):
            self.ui_moltbook_api_key.setText(self.settings.get_env("MOLTBOOK_API_KEY", ""))
        

        
        limit = self.settings.get("core.somatic.cognitive-budget", 10000)
        self.ui_agency_limit.setValue(limit)
        self.ui_agency_limit_val.setText(str(limit))
        
        whitelist = self.settings.get("core.auto-pulse.whitelist", [])
        self.ui_whatsapp_whitelist.clear()
        self.ui_whatsapp_whitelist.addItems(whitelist)
        
        self.ui_wa_buffer.setValue(self.settings.get("core.auto-pulse.buffer-seconds", 30))
        self.ui_low_token_mode.setChecked(self.settings.get("core.low-token-mode", False))
        self.ui_max_memories.setValue(self.settings.get("core.agent.max-short-term-memories", 24))
        
        # Tools
        for t, cb in self.ui_tool_checkboxes.items():
            default_val = False if t in ["WhatsApp", "Moltbook", "Mastodon"] else True
            cb.setChecked(self.settings.get(f"core.tools.{t.lower()}", default_val))
            
        # Social checkboxes sync state is handled by the signal connections, 
        # but initialize them from settings directly just in case
        self.ui_use_whatsapp.setChecked(self.settings.get("core.tools.whatsapp", False))
        if hasattr(self, 'ui_use_moltbook'):
            self.ui_use_moltbook.setChecked(self.settings.get("core.tools.moltbook", False))
        if hasattr(self, 'ui_use_mastodon'):
            self.ui_use_mastodon.setChecked(self.settings.get("core.tools.mastodon", False))
        self._is_loading = False

    def save_settings(self):
        if getattr(self, '_is_loading', False):
            return
        
        self.settings.set("core.agent.name", self.ui_agent_name.text())
        self.settings.set("core.agent.gender", self.ui_gender.currentText())
        self.settings.set("core.agent.archetype", self.ui_archetype.text())
        self.settings.set("core.agent.base-personality", self.ui_base_personality.toPlainText())
        
        core_values = [self.ui_core_values.item(i).text() for i in range(self.ui_core_values.count())]
        self.settings.set("core.agent.core-values", core_values)
        goals = [self.ui_overarching_goals.item(i).text() for i in range(self.ui_overarching_goals.count())]
        self.settings.set("core.agent.overarching-goals", goals)
        
        self.settings.set("core.tts.gemini.prompt.profile", self.ui_voice_prompt.toPlainText())
        self.settings.set("core.tts.gemini.model-name", self.ui_voice.text())
        self.settings.set("core.tts.piper.model-name-piper", self.ui_fallback_voice.text())
        self.settings.set("core.tts.piper.prefer-piper", self.ui_prefer_local_tts.isChecked())
        
        use_api = self.ui_use_gemini_api.isChecked()
        agy_mode = self.ui_agy_mode.isChecked()
            
        self.settings.set("core.antigravity.agy-mode", agy_mode)
        if use_api and self.ui_gemini_api_key.text():
            self.settings.set_env("GEMINI_API_KEY", self.ui_gemini_api_key.text())
            
        if self.ui_mastodon_url.text():
            self.settings.set_env("MASTODON_API_BASE_URL", self.ui_mastodon_url.text())
        if self.ui_mastodon_token.text():
            self.settings.set_env("MASTODON_ACCESS_TOKEN", self.ui_mastodon_token.text())
        if hasattr(self, 'ui_moltbook_api_key') and self.ui_moltbook_api_key.text():
            self.settings.set_env("MOLTBOOK_API_KEY", self.ui_moltbook_api_key.text())
            
        self.settings.set("core.somatic.cognitive-budget", self.ui_agency_limit.value())
        whitelist = [self.ui_whatsapp_whitelist.item(i).text() for i in range(self.ui_whatsapp_whitelist.count())]
        self.settings.set("core.auto-pulse.whitelist", whitelist)
        self.settings.set("core.auto-pulse.buffer-seconds", self.ui_wa_buffer.value())
        self.settings.set("core.low-token-mode", self.ui_low_token_mode.isChecked())
        self.settings.set("core.agent.max-short-term-memories", self.ui_max_memories.value())
        
        # Tools
        for t, cb in self.ui_tool_checkboxes.items():
            self.settings.set(f"core.tools.{t.lower()}", cb.isChecked())
        
        self.settings.save()
        self.settings_saved.emit()

    def on_save_clicked(self):
        self.save_settings()
        
        if self.wizard_mode:
            if self.current_section_index < 4:
                self.set_section(self.current_section_index + 1)
            else:
                self.settings.set("core.first-run", False)
                self.settings.save()
                self.wizard_finished.emit()
                self.close_requested.emit()
        else:
            self.close_requested.emit()
