import logging
import threading
import json
import time
from .events import Signal

try:
    from core.gemini_worker import GeminiWorker
    from core.agy_worker import AgyWorker
    from core.audio_input import AudioService
    from core.audio_output import TTSWorker
    from core.mempalace_manager import MemPalaceManager
    from core.cerebrum import Cerebrum
    from core.pulse_engine import PulseEngine
    from core.settings_manager import SettingsManager
    from core.subagent_worker import SubagentWorker
    from core.config_manager import ConfigManager
except ImportError:
    from .gemini_worker import GeminiWorker
    from .agy_worker import AgyWorker
    from .audio_input import AudioService
    from .audio_output import TTSWorker
    from .mempalace_manager import MemPalaceManager
    from .cerebrum import Cerebrum
    from .pulse_engine import PulseEngine
    from .settings_manager import SettingsManager
    from .subagent_worker import SubagentWorker
    from .config_manager import ConfigManager


def with_agent_context(func):
    def wrapper(self, *args, **kwargs):
        from core.logger_config import agent_id_var
        if hasattr(self, 'agent_id'):
            agent_id_var.set(self.agent_id)
        return func(self, *args, **kwargs)
    return wrapper


class AmityOrchestrator:
    def __init__(self, agent_id=None):
        self.agent_id = agent_id
        from core.logger_config import agent_id_var
        agent_id_var.set(self.agent_id)

        self.on_message_appended = Signal()  # sender, text
        self.on_busy_state_changed = Signal()  # is_busy, is_speaking
        self.on_amplitude_emitted = Signal()  # float
        self.on_paused_state_changed = Signal()  # is_paused (bool)
        self.on_pause_pending = Signal()  # is_pending (bool)
        self.on_tool_started = Signal()  # call_id, tool_name, icon, color, call_text, is_async
        self.on_tool_finished = Signal()  # call_id
        self.subagent_call_ids = {}

        self.settings_manager = SettingsManager(agent_id=self.agent_id)
        self.config_manager = ConfigManager()
        self.mempalace_manager = MemPalaceManager(agent_id=self.agent_id)
        self.cerebrum = Cerebrum(
            orchestrator=self, settings_manager=self.settings_manager)

        # State
        self.is_paused = self.settings_manager.get("core.paused", False)
        self._pausing_in_progress = False
        self.is_busy = False
        self.is_thinking = False
        self.last_action_result = None
        self.current_user_prompt = ""
        self.recent_history = []
        self._is_user_interaction = False
        self._speaker_invoked_in_cycle = False
        self._user_turn_nudged = False

        self.on_shutdown_complete = Signal()
        self.session_fatigue_tokens = 0

        # Budget
        self.budget_lock = threading.Lock()
        self.current_task_weight = 0
        self._last_notified_weight_threshold = 0
        self.current_loop_count = 0
        self.last_executed_command = None
        self.duplicate_command_count = 0
        self.accumulated_thoughts = ""
        self.speech_queue = []
        self.event_queue = []
        self.active_subagents = {}
        self.subagent_last_activity = {}
        self._shutdown_flag = False
        self._start_subagent_gc()

        self.build_system_prompt()

        self.pulse_engine = PulseEngine(self)
        self.pulse_engine.trigger_pulse.connect(self.process_pulse)

        wa_skill = self.cerebrum.tools.get("WhatsApp")
        if wa_skill:
            wa_skill.message_received_callback = self.pulse_engine.handle_whatsapp_message

        self.gemini_worker = None
        self.audio_service = None

        is_first_run = self.settings_manager.get("core.first-run", True)
        if not is_first_run:
            self.init_worker()

        self.audio_service = AudioService(agent_id=self.agent_id)
        self.audio_service.initialized.connect(self.on_audio_initialized)
        self.audio_service.listening_started.connect(self.on_listening_start)
        self.audio_service.listening_stopped.connect(self.on_listening_stop)
        self.audio_service.audio_prompt_ready.connect(
            self.on_audio_prompt_ready)
        self.audio_service.error_occurred.connect(self.on_audio_error)

        self.tts_worker = None
        self.audio_service.start_initialization()

    def init_worker(self):
        if self.gemini_worker is not None:
            return

        provider = self.settings_manager.get("core.api-provider", "gemini")
        if self.settings_manager.get("core.antigravity.agy-mode", False):
            self.gemini_worker = AgyWorker(agent_id=self.agent_id)
        elif provider == "claude":
            from .claude_worker import ClaudeWorker
            self.gemini_worker = ClaudeWorker(agent_id=self.agent_id)
        elif provider in ["chatgpt", "openai"]:
            from .chatgpt_worker import ChatGptWorker
            self.gemini_worker = ChatGptWorker(agent_id=self.agent_id)
        elif provider == "deepseek":
            from .deepseek_worker import DeepSeekWorker
            self.gemini_worker = DeepSeekWorker(agent_id=self.agent_id)
        else:
            self.gemini_worker = GeminiWorker(agent_id=self.agent_id)

        self.gemini_worker.thought_received.connect(self.handle_gemini_thought)
        if hasattr(self.gemini_worker, 'tokens_consumed'):
            self.gemini_worker.tokens_consumed.connect(self.add_fatigue)
        if hasattr(self.gemini_worker, 'speech_received'):
            self.gemini_worker.speech_received.connect(
                self.handle_gemini_speech)
        self.gemini_worker.error_occurred.connect(self.handle_gemini_error)

        if not getattr(self, 'is_paused', False) and getattr(self.gemini_worker, 'available', False):
            tools = self.cerebrum.get_all_tool_declarations()
            self.gemini_worker.start_session(self.system_prompt, tools=tools)

    @property
    def agent_uid(self) -> str:
        from core.uid_generator import generate_agent_uid, is_valid_agent_uid
        uid = self.settings_manager.get("core.agent.uid", "")
        if not uid or not is_valid_agent_uid(uid):
            uid = generate_agent_uid()
            self.settings_manager.set("core.agent.uid", uid)
            self.settings_manager.save()
        return uid

    def build_system_prompt(self):
        self.system_prompt = self.mempalace_manager.wake_up()
        self.system_prompt += f"\n\n[AGENT IDENTIFIER: Your unique agent ID (phone number) is {self.agent_uid}]"
        self.system_prompt += "\n" + self.cerebrum.get_agent_manual()
        if self.settings_manager.get("core.low-token-mode", False):
            self.system_prompt += "\n\n[SYSTEM STATE: LOW TOKEN MODE IS ACTIVE]"

    def _teardown_worker(self):
        if self.gemini_worker:
            try:
                if hasattr(self.gemini_worker, 'abort'):
                    self.gemini_worker.abort()
                if hasattr(self.gemini_worker, 'stop_session'):
                    self.gemini_worker.stop_session()
                if hasattr(self.gemini_worker, 'thought_received'):
                    try:
                        self.gemini_worker.thought_received.disconnect(self.handle_gemini_thought)
                    except Exception:
                        pass
                if hasattr(self.gemini_worker, 'tokens_consumed'):
                    try:
                        self.gemini_worker.tokens_consumed.disconnect(self.add_fatigue)
                    except Exception:
                        pass
                if hasattr(self.gemini_worker, 'speech_received'):
                    try:
                        self.gemini_worker.speech_received.disconnect(self.handle_gemini_speech)
                    except Exception:
                        pass
                if hasattr(self.gemini_worker, 'error_occurred'):
                    try:
                        self.gemini_worker.error_occurred.disconnect(self.handle_gemini_error)
                    except Exception:
                        pass
            except Exception as e:
                logging.debug(f"Error during worker cleanup: {e}")
            self.gemini_worker = None

    def restart_worker(self):
        self._teardown_worker()
        self.reload_settings()

    def reload_settings(self):
        self.settings_manager.settings = self.settings_manager.load_settings()
        self.is_paused = self.settings_manager.get("core.paused", False)
        self.mempalace_manager.reload_settings()
        self.build_system_prompt()
        self.cerebrum.reload_skills()
        wa_skill = self.cerebrum.tools.get("WhatsApp")
        if wa_skill:
            wa_skill.message_received_callback = self.pulse_engine.handle_whatsapp_message

        provider = self.settings_manager.get("core.api-provider", "gemini")
        agy_mode = self.settings_manager.get("core.antigravity.agy-mode", False)

        worker_mismatch = False
        if self.gemini_worker is None:
            worker_mismatch = True
        elif agy_mode and type(self.gemini_worker).__name__ != "AgyWorker":
            worker_mismatch = True
        elif not agy_mode and provider == "claude" and type(self.gemini_worker).__name__ != "ClaudeWorker":
            worker_mismatch = True
        elif not agy_mode and provider in ["chatgpt", "openai"] and type(self.gemini_worker).__name__ != "ChatGptWorker":
            worker_mismatch = True
        elif not agy_mode and provider == "deepseek" and type(self.gemini_worker).__name__ != "DeepSeekWorker":
            worker_mismatch = True
        elif not agy_mode and provider not in ["claude", "chatgpt", "openai", "deepseek"] and type(self.gemini_worker).__name__ != "GeminiWorker":
            worker_mismatch = True
        elif not agy_mode and provider == "claude":
            current_key = self.settings_manager.get_env("CLAUDE_API_KEY")
            if getattr(self.gemini_worker, 'api_key', None) != current_key or not getattr(self.gemini_worker, 'available', False):
                worker_mismatch = True
        elif not agy_mode and provider in ["chatgpt", "openai"]:
            current_key = self.settings_manager.get_env("OPENAI_API_KEY")
            if getattr(self.gemini_worker, 'api_key', None) != current_key or not getattr(self.gemini_worker, 'available', False):
                worker_mismatch = True
        elif not agy_mode and provider == "deepseek":
            current_key = self.settings_manager.get_env("DEEPSEEK_API_KEY")
            if getattr(self.gemini_worker, 'api_key', None) != current_key or not getattr(self.gemini_worker, 'available', False):
                worker_mismatch = True
        elif not agy_mode and provider not in ["claude", "chatgpt", "openai", "deepseek"]:
            current_key = self.settings_manager.get_env("GEMINI_API_KEY")
            if getattr(self.gemini_worker, 'api_key', None) != current_key or not getattr(self.gemini_worker, 'available', False):
                worker_mismatch = True

        if worker_mismatch:
            self._teardown_worker()
            self.init_worker()
        else:
            if not self.is_paused and self.gemini_worker and getattr(self.gemini_worker, 'available', False):
                tools = self.cerebrum.get_all_tool_declarations()
                self.gemini_worker.start_session(self.system_prompt, tools=tools)

    def set_busy_state(self, busy: bool, speaking: bool = False):
        self.is_busy = busy
        self.on_busy_state_changed.emit(busy, speaking)

    def add_fatigue(self, tokens: int):
        self.session_fatigue_tokens += tokens

    def get_fatigue(self) -> float:
        session_token_cap = self.settings_manager.get(
            "core.somatic.session-token-cap", 500000.0)
        is_low_token = self.settings_manager.get("core.low-token-mode", False)
        max_tokens = (session_token_cap / 2.0) if is_low_token else float(session_token_cap)
        return min(self.session_fatigue_tokens / max_tokens, 1.0)

    def user_interacted(self):
        self.pulse_engine.user_interacted()

    def toggle_mic(self):
        if self.is_paused:
            return
        self.user_interacted()
        if self.is_busy:
            self.stop_all_processing()
        else:
            self.audio_service.start_listening()

    @with_agent_context
    def stop_all_processing(self):
        if self.audio_service.running:
            self.audio_service.stop_listening()
        if self.gemini_worker and hasattr(self.gemini_worker, 'is_processing') and self.gemini_worker.is_processing:
            self.gemini_worker.abort()
        if self.tts_worker:
            self.tts_worker.stop()

        self.speech_queue.clear()
        self.event_queue.clear()
        self.is_thinking = False
        logging.info("System: Processing aborted by user.")
        self.set_busy_state(False)

    def toggle_pause(self):
        if getattr(self, '_pausing_in_progress', False):
            return
        self.set_paused(not self.is_paused)

    def set_paused(self, paused: bool):
        if getattr(self, '_pausing_in_progress', False):
            return

        if paused:
            if self.is_paused:
                return

            fatigue = self.get_fatigue() if hasattr(self, 'get_fatigue') else 0.0
            if fatigue >= 0.02:
                logging.info(f"System: Initiating memory consolidation prior to pausing agent {self.agent_id}...")
                self._pausing_in_progress = True
                self.on_pause_pending.emit(True)
                self.append_to_conversation(
                    "System", "Consolidating memories before pausing... please wait.")

                if self.is_busy:
                    self.stop_all_processing()

                title = "Sleep Cycle (Memory Consolidation)"
                context = (
                    "You are pausing. It is time for a Sleep Cycle. Review your active session history. "
                    "Synthesize this episodic memory into generalized facts and store them in the Sanctuary or Deep Search (Chroma) if they are important. "
                    "Then, update your short-term memory (using MemPalace) so that you have a condensed summary of your current state and ongoing tasks before this session is archived. "
                    "You MUST perform this cycle completely silently: do NOT speak, talk, output spoken text, or invoke the Speaker tool."
                )
                self.pulse_engine.fire_pulse(title, context, "sleep_cycle")
                self.pulse_engine.settings_manager.set(
                    "core.auto-pulse.last-sleep-cycle", time.time())
                self.pulse_engine.settings_manager.save()

                def check_busy():
                    if not self.is_busy and not self.is_thinking:
                        self._finalize_pause()
                    else:
                        threading.Timer(1.0, check_busy).start()

                threading.Timer(2.0, check_busy).start()
                return

            self._finalize_pause()
        else:
            if not self.is_paused:
                return
            self._resume_agent()

    def _finalize_pause(self):
        logging.info(f"System: Pausing agent {self.agent_id} (offline)...")
        self._pausing_in_progress = False
        self.is_paused = True
        self.settings_manager.set("core.paused", True)
        self.settings_manager.save()

        for sid in list(self.active_subagents.keys()):
            try:
                self.dispose_subagent(sid)
            except Exception as e:
                logging.debug(f"Error disposing subagent {sid} on pause: {e}")

        self.stop_all_processing()

        if self.gemini_worker and hasattr(self.gemini_worker, 'stop_session'):
            try:
                self.gemini_worker.stop_session()
            except Exception as e:
                logging.debug(f"Error stopping worker session on pause: {e}")

        self.session_fatigue_tokens = 0
        self.set_busy_state(False)
        self.on_paused_state_changed.emit(True)
        self.append_to_conversation(
            "System", "[Agent Paused - Autonomy and background processing offline]")

    def _resume_agent(self):
        logging.info(f"System: Resuming agent {self.agent_id} (online)...")
        self.is_paused = False
        self.settings_manager.set("core.paused", False)
        self.settings_manager.save()

        self.build_system_prompt()
        if self.gemini_worker and getattr(self.gemini_worker, 'available', False):
            tools = self.cerebrum.get_all_tool_declarations()
            self.gemini_worker.start_session(self.system_prompt, tools=tools)

        if self.pulse_engine:
            self.pulse_engine.last_interaction_time = time.time()

        self.on_paused_state_changed.emit(False)
        self.append_to_conversation(
            "System", "[Agent Resumed - Autonomy online]")

    def process_text_input(self, text):
        if not text or self.is_paused:
            return
        if self.is_busy:
            self.event_queue.append({"type": "input", "text": text})
        else:
            self.process_input(text)

    @with_agent_context
    def finish_thinking(self):
        logging.debug("finish_thinking called")
        self.is_thinking = False
        if hasattr(self, 'mempalace_manager') and self.mempalace_manager:
            try:
                is_sleep = getattr(self, '_is_sleep_cycle', False)
                self.mempalace_manager.flush_turn_coactivation(is_sleep_cycle=is_sleep)
            except Exception as e:
                logging.warning(f"Error flushing turn coactivation in finish_thinking: {e}")
        self.check_cycle_completion()

    @with_agent_context
    def check_cycle_completion(self):
        is_speaking = (self.tts_worker and self.tts_worker.is_alive()) or len(
            self.speech_queue) > 0
        logging.debug(
            f"check_cycle_completion evaluated is_speaking: {is_speaking}, is_thinking: {self.is_thinking}")
        if not self.is_thinking and not is_speaking:
            logging.debug(
                "check_cycle_completion calling set_busy_state(False)")

            if getattr(self, '_is_sleep_cycle', False):
                self._is_sleep_cycle = False
                logging.info("System: Memory consolidation complete. Resetting active session context...")
                self.build_system_prompt()
                self.last_action_result = (
                    "[LIFECYCLE EVENT: You have completed memory consolidation and awoken to a new waking cycle. "
                    "Your Layer 1 short-term continuity and mirrors have been synthesized. "
                    "Orient yourself with Trajectory_get_bearings before taking new action.]"
                )
                if self.gemini_worker:
                    tools = self.cerebrum.get_all_tool_declarations()
                    if hasattr(self.gemini_worker, 'stop_session'):
                        self.gemini_worker.stop_session()
                    if hasattr(self.gemini_worker, 'start_session'):
                        if not getattr(self, '_pausing_in_progress', False) and not getattr(self, '_shutdown_flag', False):
                            self.gemini_worker.start_session(self.system_prompt, tools=tools)

            self.set_busy_state(False)
            if self.event_queue:
                logging.debug(
                    "check_cycle_completion popping next event from queue")
                next_event = self.event_queue.pop(0)
                if next_event["type"] == "input":
                    self.process_input(next_event["text"])
                elif next_event["type"] == "pulse":
                    self.process_pulse(next_event["text"], purpose=next_event.get("purpose"))
        else:
            logging.debug(
                "check_cycle_completion calling set_busy_state(True)")
            self.set_busy_state(True, speaking=is_speaking)

    @with_agent_context
    def process_input(self, text, audio_path=None):
        if self.is_paused:
            logging.debug(f"Input rejected: Agent {self.agent_id} is paused.")
            return
        import os
        provider = self.settings_manager.get("core.api-provider", "gemini")
        needs_reload = False

        if self.settings_manager.get("core.antigravity.agy-mode", False):
            if type(self.gemini_worker).__name__ != "AgyWorker":
                needs_reload = True
        elif provider == "claude":
            if type(self.gemini_worker).__name__ != "ClaudeWorker":
                needs_reload = True
            else:
                current_env_key = self.settings_manager.get_env(
                    "CLAUDE_API_KEY")
                if hasattr(self.gemini_worker, 'api_key') and self.gemini_worker.api_key != current_env_key:
                    needs_reload = True
        elif provider in ["chatgpt", "openai"]:
            if type(self.gemini_worker).__name__ != "ChatGptWorker":
                needs_reload = True
            else:
                current_env_key = self.settings_manager.get_env(
                    "OPENAI_API_KEY")
                if hasattr(self.gemini_worker, 'api_key') and self.gemini_worker.api_key != current_env_key:
                    needs_reload = True
        elif provider == "deepseek":
            if type(self.gemini_worker).__name__ != "DeepSeekWorker":
                needs_reload = True
            else:
                current_env_key = self.settings_manager.get_env(
                    "DEEPSEEK_API_KEY")
                if hasattr(self.gemini_worker, 'api_key') and self.gemini_worker.api_key != current_env_key:
                    needs_reload = True
        else:
            if type(self.gemini_worker).__name__ != "GeminiWorker":
                needs_reload = True
            else:
                current_env_key = self.settings_manager.get_env(
                    "GEMINI_API_KEY")
                if hasattr(self.gemini_worker, 'api_key') and self.gemini_worker.api_key != current_env_key:
                    needs_reload = True

        if needs_reload:
            logging.info(
                "System: Settings or API Key change detected. Hot-reloading Worker...")
            self.reload_settings()

        if not self.gemini_worker or not getattr(self.gemini_worker, 'available', False):
            logging.error(
                "System: The Cognitive Worker failed to initialise (worker is None or unavailable).")
            self.append_to_conversation(
                "System", "The Cognitive Worker failed to initialise")
            self.set_busy_state(False)
            return

        if not self.gemini_worker.running:
            logging.info("System: Brain offline. Starting session...")
            self.build_system_prompt()
            tools = self.cerebrum.get_all_tool_declarations()
            logging.debug("Calling self.gemini_worker.start_session...")
            self.gemini_worker.start_session(self.system_prompt, tools=tools)
            logging.debug("start_session returned.")

        self.append_to_conversation("User", text)
        self.is_thinking = True
        self.set_busy_state(True)
        self.current_user_prompt = text
        self._is_sleep_cycle = False
        self._is_user_interaction = True
        self._speaker_invoked_in_cycle = False
        self._user_turn_nudged = False

        with self.budget_lock:
            try:
                from config import paths
                import os
                import json
                import time
                state_path = os.path.join(paths.get_base_dir_for(
                    self.agent_id), "somatic_state.json")
                if os.path.exists(state_path):
                    with open(state_path, "r") as f:
                        somatic = json.load(f)

                    last_weight = somatic.get("current_task_weight", 0)
                    last_update = somatic.get("last_updated", time.time())

                    # Decay calculation: e.g. 50 weight points per minute of idle time
                    elapsed_mins = (time.time() - last_update) / 60.0
                    decay_rate = self.settings_manager.get(
                        "core.somatic.decay-per-minute", 50)
                    decay = elapsed_mins * decay_rate
                    self.current_task_weight = max(0, last_weight - decay)
                else:
                    self.current_task_weight = 0
            except Exception:
                self.current_task_weight = 0
            self.current_loop_count = 0
            self._last_notified_weight_threshold = 0
            self.last_executed_command = None
            self.duplicate_command_count = 0
            self.accumulated_thoughts = ""

        threading.Thread(target=self._async_query_prep, args=(
            text, None, audio_path), daemon=True).start()

    def _async_query_prep(self, text, history=None, audio_path=None):
        from core.logger_config import agent_id_var
        agent_id_var.set(self.agent_id)
        logging.debug("_async_query_prep started.")

        user_name = "User"
        if hasattr(self, 'config_manager') and self.config_manager:
            configured_name = self.config_manager.get("user-full-name", "").strip()
            if configured_name:
                user_name = configured_name

        prompt = "[CHANNEL: LOCAL_GUI]\n"
        if self.last_action_result:
            prompt += f"[System Feedback from previous turn]: {self.last_action_result}\n\n"
            self.last_action_result = None

        if hasattr(self, 'mempalace_manager') and self.mempalace_manager and text:
            try:
                mirrors = self.mempalace_manager._load_mirrors()
                known_entities = [k for k in mirrors.keys() if isinstance(k, str) and k.lower() != "self" and len(k) > 2]
                matched = [ent for ent in known_entities if ent.lower() in text.lower()]
                if matched:
                    ctx = self.mempalace_manager.get_entity_context(matched[:2])
                    if ctx:
                        prompt += f"[Proactive Memory Recall for {', '.join(matched[:2])}]:\n{ctx}\n\n"
            except Exception as e:
                logging.debug(f"Proactive entity recall check error: {e}")

        prompt += f"[{user_name} (User)]: {self.current_user_prompt}"
        logging.debug(
            f"Calling gemini_worker.send_prompt with prompt length {len(prompt)}...")
        self.gemini_worker.send_prompt(prompt, audio_path=audio_path)
        logging.debug("gemini_worker.send_prompt returned.")

    @with_agent_context
    def process_pulse(self, text="Autonomy Pulse", purpose=None):
        if self.is_paused:
            logging.debug(f"Pulse dropped: Agent {self.agent_id} is paused.")
            return

        if self.is_busy:
            self.event_queue.append({"type": "pulse", "text": text, "purpose": purpose})
            return

        if not self.gemini_worker or not getattr(self.gemini_worker, 'available', False):
            if self.settings_manager.get("core.first-run", True):
                logging.debug(
                    "System: Pulse aborted. Agent is still in first-run setup.")
            else:
                logging.error(
                    "System: Pulse aborted. The Gemini Worker failed to initialise (worker is None or unavailable).")
            return

        if not self.gemini_worker.running:
            self.build_system_prompt()
            tools = self.cerebrum.get_all_tool_declarations()
            self.gemini_worker.start_session(self.system_prompt, tools=tools)

        PREWRITTEN_PULSE_CATEGORIES = {
            "Terminal command completed",
            "Terminal command in progress",
            "WhatsApp message received",
            "Subagent task completed",
            "Subagent task error",
            "Memory consolidation cycle",
            "Scheduled routine",
            "Scheduled task",
            "Autonomous routine",
            "External pulse received",
        }

        category = purpose
        if category not in PREWRITTEN_PULSE_CATEGORIES:
            cat_lower = str(category).lower() if category else ""
            if "memory consolidation" in cat_lower or "sleep cycle" in cat_lower or "Sleep Cycle" in text:
                category = "Memory consolidation cycle"
            elif "whatsapp" in cat_lower or "WhatsApp Message" in text:
                category = "WhatsApp message received"
            elif "external" in cat_lower or "hook" in cat_lower:
                category = "External pulse received"
            elif "still running" in text or "in progress" in cat_lower:
                category = "Terminal command in progress"
            elif "[SYSTEM_NOTIFICATION] Background Task" in text or "terminal" in cat_lower or "background task" in cat_lower:
                category = "Terminal command completed"
            elif "[System Feedback: Subagent" in text or ("subagent" in cat_lower and "finish" in cat_lower):
                category = "Subagent task completed"
            elif "[System Warning: Subagent" in text or ("subagent" in cat_lower and ("error" in cat_lower or "fail" in cat_lower)):
                category = "Subagent task error"
            elif cat_lower == "scheduled task" or "task" in cat_lower:
                category = "Scheduled task"
            elif "[AGENT_PULSE]" in text or "routine" in cat_lower:
                category = "Scheduled routine"
            else:
                category = "Scheduled routine"

        self._is_sleep_cycle = ("Sleep Cycle" in text or category == "Memory consolidation cycle")
        if "You are shutting down." not in text:
            self.append_to_conversation("System", f"[Autonomy Pulse: {category}]")
        self.is_thinking = True
        self.set_busy_state(True)
        self.current_user_prompt = text
        self._is_user_interaction = False
        self._speaker_invoked_in_cycle = False
        self._user_turn_nudged = False

        with self.budget_lock:
            try:
                from config import paths
                import os
                import json
                import time
                state_path = os.path.join(paths.get_base_dir_for(
                    self.agent_id), "somatic_state.json")
                if os.path.exists(state_path):
                    with open(state_path, "r") as f:
                        somatic = json.load(f)

                    last_weight = somatic.get("current_task_weight", 0)
                    last_update = somatic.get("last_updated", time.time())

                    # Decay calculation
                    elapsed_mins = (time.time() - last_update) / 60.0
                    decay_rate = self.settings_manager.get(
                        "core.somatic.decay-per-minute", 50)
                    decay = elapsed_mins * decay_rate
                    self.current_task_weight = max(0, last_weight - decay)
                else:
                    self.current_task_weight = 0
            except Exception:
                self.current_task_weight = 0

        self.current_loop_count = 0
        self._last_notified_weight_threshold = 0
        self.last_executed_command = None
        self.duplicate_command_count = 0
        self.accumulated_thoughts = ""

        self.gemini_worker.send_prompt(text)

    def append_to_conversation(self, sender, text):
        self.recent_history.append((sender, text))
        max_history = 6 if self.settings_manager.get(
            "core.low-token-mode", False) else 10
        while len(self.recent_history) > max_history:
            self.recent_history.pop(0)
        self.on_message_appended.emit(sender, text)

    def on_audio_initialized(self):
        logging.info("System: Ready.")
        if not self.is_paused and self.gemini_worker and getattr(self.gemini_worker, 'available', False):
            tools = self.cerebrum.get_all_tool_declarations()
            self.gemini_worker.start_session(self.system_prompt, tools=tools)

    def on_listening_start(self):
        self.is_thinking = True
        self.set_busy_state(True)

    def on_listening_stop(self):
        pass

    def on_audio_prompt_ready(self, text, audio_path):
        if text:
            self.process_input(text, audio_path=audio_path)
        else:
            self.finish_thinking()

    def on_audio_error(self, error):
        logging.error(f"Audio Error: {error}")
        self.finish_thinking()

    @with_agent_context
    def handle_gemini_thought(self, text: str, function_calls: list):
        logging.debug(
            f"handle_gemini_thought called with text length: {len(text)}, function_calls count: {len(function_calls) if function_calls else 0}")

        clean_text = text.strip() if text else ""
        if clean_text:
            logging.debug(
                "handle_gemini_thought logging agent thought to info")
            provider = self.settings_manager.get("core.api-provider", "gemini")
            if self.settings_manager.get("core.antigravity.agy-mode", False):
                worker_type = "agyworker"
            elif provider == "claude":
                worker_type = "claudeworker"
            elif provider in ["chatgpt", "openai"]:
                worker_type = "chatgptworker"
            elif provider == "deepseek":
                worker_type = "deepseekworker"
            else:
                worker_type = "geminiworker"
            logging.getLogger(f"{worker_type}.Thoughts").info(clean_text)
            self.accumulated_thoughts += clean_text + "\n"

        if not function_calls:
            if (
                getattr(self, '_is_user_interaction', False)
                and not getattr(self, '_speaker_invoked_in_cycle', False)
                and not getattr(self, '_user_turn_nudged', False)
                and not getattr(self, 'is_paused', False)
                and not getattr(self, '_shutdown_flag', False)
                and self.gemini_worker
                and getattr(self.gemini_worker, 'running', False)
            ):
                self._user_turn_nudged = True
                nudge_text = "[System Notice: You have not responded to the user. Please invoke Speaker_speak_aloud or Speaker_output_text to respond now.]"
                logging.info(
                    f"Agent {self.agent_id} completed turn without speaking to user. Triggering auto-nudge.")
                self.gemini_worker.send_prompt(nudge_text)
                return

            logging.debug(
                "handle_gemini_thought found no function calls, calling finish_thinking")
            self.finish_thinking()
            return

        logging.debug(
            "handle_gemini_thought starting _async_tool_execution thread")
        threading.Thread(target=self._async_tool_execution, args=(
            function_calls, self.current_task_weight), daemon=True).start()

    def _async_tool_execution(self, function_calls, current_weight):
        from core.logger_config import agent_id_var
        agent_id_var.set(self.agent_id)
        function_responses = []
        executed_tools = []

        for call in function_calls:
            function_name = call.name
            tool_name = function_name.split(
                "_")[0] if "_" in function_name else function_name
            command_name = function_name.split(
                "_", 1)[1] if "_" in function_name else ""
            args = call.args or {}

            if len(args) == 1 and "text" in args:
                args_str = str(args["text"])
            elif args:
                args_str = ", ".join(f"{k}='{v}'" for k, v in args.items())
            else:
                args_str = "()"

            if args_str == "()":
                log_msg = f"[Weight: {current_weight:.1f}] {function_name}()"
                raw_call_text = f"{function_name}()"
            else:
                log_msg = f"[Weight: {current_weight:.1f}] {function_name}: {args_str}"
                raw_call_text = f"{function_name}: {args_str}"

            logging.getLogger(f"tool.{tool_name}").info(log_msg)

            # Determine icon, color, and async status from tool object
            tool_obj = self.cerebrum.tools.get(tool_name)
            if not tool_obj:
                from core.cerebrum import Tool
                tool_cls = Tool.get_tool_class(tool_name)
                icon = getattr(tool_cls, "icon", "🔧") if tool_cls else "🔧"
                color = getattr(tool_cls, "color", "#888888") if tool_cls else "#888888"
                is_async = False
            else:
                icon = getattr(tool_obj, "icon", "🔧")
                color = getattr(tool_obj, "color", "#888888")
                is_async = tool_obj.is_command_async(command_name, args)

            import uuid
            call_id = str(uuid.uuid4())[:8]

            self.on_tool_started.emit(call_id, tool_name, icon, color, raw_call_text, is_async)

            executed_tool_sig = f"{function_name}({json.dumps(args, sort_keys=True)})"
            executed_tools.append(executed_tool_sig)
            try:
                skill_result = self.cerebrum.execute_tool_call(function_name, args, call_id=call_id)
                if isinstance(skill_result, dict):
                    function_responses.append((function_name, skill_result))
                else:
                    function_responses.append(
                        (function_name, {"result": str(skill_result)}))

                # If async tool returned an immediate error, dismiss the pip
                if is_async:
                    result_str = str(skill_result)
                    if result_str.startswith("Error:"):
                        self.on_tool_finished.emit(call_id)
            except Exception as e:
                function_responses.append((function_name, {"error": str(e)}))
                if is_async:
                    self.on_tool_finished.emit(call_id)

        self.on_tool_execution_finished(function_responses, executed_tools)

    def on_tool_execution_finished(self, function_responses, executed_tools):
        if len(executed_tools) == 0:
            return

        for i, (name, resp) in enumerate(function_responses):
            if name.startswith("Speaker_"):
                self._speaker_invoked_in_cycle = True
                result_str = resp.get("result", "")
                try:
                    payload = json.loads(result_str)
                    action = payload.get("action")
                    if action == "trigger_speak_aloud":
                        text = payload.get("text", "")
                        self.handle_gemini_speech(text)
                        resp["result"] = "Speech queued."
                    elif action == "trigger_output_text":
                        text = payload.get("text", "")
                        self.append_to_conversation("Agent", text)
                        resp["result"] = "Text output to GUI."
                except Exception as e:
                    logging.debug(
                        f"Expected valid JSON from Speaker tool but failed to parse: {e}")
                function_responses[i] = (name, resp)

        if hasattr(self, 'pulse_engine') and hasattr(self.pulse_engine, 'settings_manager'):
            sm = self.pulse_engine.settings_manager
            max_weight = sm.get("core.somatic.cognitive-budget", 10000)
            low_token = sm.get("core.low-token-mode", False)
            base_weight = sm.get("core.somatic.tool-cost", 2)
            exp_factor = sm.get("core.somatic.exponential-factor", 1.5)
        else:
            max_weight = 1000
            low_token = False
            base_weight = 2
            exp_factor = 1.5

        if low_token:
            max_weight = max_weight / 2

        with self.budget_lock:
            self.current_loop_count += 1
            added_weight = base_weight * \
                (exp_factor ** (self.current_loop_count - 1))
            self.current_task_weight += added_weight

            # Write somatic state for tools (Atomic)
            try:
                from config import paths
                import os
                import time
                state_path = os.path.join(paths.get_base_dir_for(
                    self.agent_id), "somatic_state.json")
                temp_path = state_path + ".tmp"
                with open(temp_path, "w") as f:
                    json.dump({
                        "current_task_weight": self.current_task_weight,
                        "max_weight": max_weight,
                        "last_updated": time.time()
                    }, f)
                os.replace(temp_path, state_path)
            except Exception:
                pass

        current_batch = ", ".join(executed_tools)
        if current_batch == self.last_executed_command:
            self.duplicate_command_count += 1
            if self.duplicate_command_count >= 3:
                logging.warning(
                    "System: Duplicate Action Detected. Breaking loop.")
                self.handle_gemini_speech(
                    "I'm sorry, I seem to be stuck in a loop trying to figure this out. I'll stop here.")
                self.finish_thinking()
                return
        else:
            self.duplicate_command_count = 0

        if self.current_task_weight >= max_weight:
            logging.warning(
                "System: Maximum operational capacity exceeded. Breaking loop.")
            self.handle_gemini_speech(
                "I'm sorry, this task is taking too much of my cognitive capacity. I'll need to stop here and re-evaluate.")
            self.finish_thinking()
            return

        self.last_executed_command = current_batch
        budget_alert = ""
        if max_weight > 0:
            percent = (self.current_task_weight / max_weight) * 100
            last_alerted = getattr(self, '_last_notified_weight_threshold', 0)
            if percent >= 90 and last_alerted < 90:
                self._last_notified_weight_threshold = 90
                budget_alert = f"\n[SYSTEM ALERT: Task Weight is at {percent:.0f}% ({self.current_task_weight:.1f}/{max_weight}). Critical cognitive capacity reached. Conclude operations.]"
            elif percent >= 75 and last_alerted < 75:
                self._last_notified_weight_threshold = 75
                budget_alert = f"\n[SYSTEM ALERT: Task Weight is at {percent:.0f}% ({self.current_task_weight:.1f}/{max_weight}). High cognitive capacity. Wrap up current actions.]"
            elif percent >= 50 and last_alerted < 50:
                self._last_notified_weight_threshold = 50
                budget_alert = f"\n[SYSTEM ALERT: Task Weight is at {percent:.0f}% ({self.current_task_weight:.1f}/{max_weight}). Moderately elevated cognitive budget.]"

        if function_responses:
            if budget_alert:
                last_name, last_resp = function_responses[-1]
                last_resp["result"] = f"{last_resp['result']}{budget_alert}"
                function_responses[-1] = (last_name, last_resp)
            self.gemini_worker.send_function_responses(function_responses)

        # Flush turn co-activation in morphological memory layer (§9.3)
        if hasattr(self, 'mempalace_manager') and self.mempalace_manager:
            try:
                is_sleep = getattr(self, '_is_sleep_cycle', False)
                self.mempalace_manager.flush_turn_coactivation(is_sleep_cycle=is_sleep)
            except Exception as e:
                logging.warning(f"Error flushing turn coactivation in orchestrator: {e}")

    @property
    def worker(self):
        """Provider-agnostic cognitive worker alias."""
        return self.gemini_worker

    @worker.setter
    def worker(self, val):
        self.gemini_worker = val

    def handle_thought(self, text: str, function_calls=None):
        return self.handle_gemini_thought(text, function_calls)

    def handle_speech(self, text: str):
        return self.handle_gemini_speech(text)

    def handle_error(self, text: str):
        return self.handle_gemini_error(text)

    @with_agent_context
    def handle_gemini_speech(self, text: str):
        clean_text = text.strip()
        self.append_to_conversation("Agent", clean_text)
        if clean_text:
            self.speak(clean_text)

    @with_agent_context
    def handle_gemini_error(self, text):
        logging.warning(f"Gemini API Event: {text}")
        self.append_to_conversation("System Warning", text)
        self.finish_thinking()

    def speak(self, text):
        self.speech_queue.append(text)
        self.process_speech_queue()

    def process_speech_queue(self):
        if self.tts_worker and self.tts_worker.is_alive():
            return

        if not self.speech_queue:
            self.check_cycle_completion()
            return

        next_text = self.speech_queue.pop(0)

        if self.settings_manager.get("core.mute", False):
            self.process_speech_queue()
            return

        self.tts_worker = TTSWorker(next_text, agent_id=self.agent_id)
        self.tts_worker.started_playback.connect(self._on_started_playback)
        self.tts_worker.amplitude_emitted.connect(
            self.on_amplitude_emitted.emit)
        self.tts_worker.error_occurred.connect(self._on_tts_error)
        self.tts_worker.on_finished.connect(self.on_tts_finished)
        self.tts_worker.start()

    def _on_tts_error(self, message: str):
        self.append_to_conversation("System", message)

    def on_tts_finished(self):
        self.tts_worker = None
        self.process_speech_queue()

    def _on_started_playback(self):
        self.set_busy_state(self.is_busy, speaking=True)

    def shutdown(self, force_sleep=False):
        if self.is_paused:
            self._finalize_shutdown()
            return

        fatigue = self.get_fatigue()
        
        if force_sleep and fatigue >= 0.02:
            logging.info("System: Initiating graceful shutdown sleep cycle...")
            title = "Sleep Cycle (Memory Consolidation)"
            context = "You are shutting down. It is time for a Sleep Cycle. Review your active session history. Synthesize this episodic memory into generalized facts and store them in the Sanctuary or Deep Search (Chroma) if they are important. Then, update your short-term memory (using MemPalace) so that you have a condensed summary of your current state and ongoing tasks before this session is archived. You MUST perform this cycle completely silently: do NOT speak, talk, output spoken text, or invoke the Speaker tool."
            self.pulse_engine.fire_pulse(title, context, "sleep_cycle")
            self.pulse_engine.settings_manager.set(
                "core.auto-pulse.last-sleep-cycle", time.time())
            self.pulse_engine.settings_manager.save()

            def check_busy():
                if not self.is_busy and not self.is_thinking:
                    self._finalize_shutdown()
                else:
                    threading.Timer(1.0, check_busy).start()

            threading.Timer(2.0, check_busy).start()
            return

        self._finalize_shutdown()

    def _finalize_shutdown(self):
        logging.info("System: Shutting down orchestrator...")
        self._shutdown_flag = True
        for sid in list(self.active_subagents.keys()):
            try:
                self.dispose_subagent(sid)
            except Exception as e:
                logging.debug(f"Error disposing subagent {sid} on shutdown: {e}")
        if self.pulse_engine:
            self.pulse_engine.stop()
        self.stop_all_processing()
        if self.gemini_worker and hasattr(self.gemini_worker, 'stop_session'):
            self.gemini_worker.stop_session()
        if self.cerebrum:
            self.cerebrum.shutdown()
        if hasattr(self, 'on_shutdown_complete'):
            self.on_shutdown_complete.emit()

    def _start_subagent_gc(self):
        def gc_loop():
            while not getattr(self, '_shutdown_flag', False):
                time.sleep(60)
                if getattr(self, '_shutdown_flag', False):
                    break
                now = time.time()
                to_dispose = []
                for sid, last_active in list(self.subagent_last_activity.items()):
                    if now - last_active > 300:
                        to_dispose.append(sid)
                for sid in to_dispose:
                    logging.info(f"System: Auto-disposing idle subagent {sid}")
                    self.dispose_subagent(sid)
        threading.Thread(target=gc_loop, daemon=True).start()

    def spawn_subagent(self, task_description, model_tier="light", call_id=None):
        if self.is_paused:
            return "Error: Agent is currently paused."
        if len(self.active_subagents) >= 6:
            return "Error: Maximum concurrent subagents (6) reached."

        import uuid
        sid = str(uuid.uuid4())[:8]
        worker = SubagentWorker(sid, self, model_tier)

        worker.thought_received.connect(
            lambda text, funcs, s=sid: self.handle_subagent_thought(s, text, funcs))
        worker.error_occurred.connect(
            lambda err, s=sid: self.handle_subagent_error(s, err))

        self.active_subagents[sid] = worker
        self.subagent_last_activity[sid] = time.time()
        if call_id:
            self.subagent_call_ids[sid] = call_id

        worker.send_prompt(task_description)
        return f"Subagent {sid} spawned."

    def message_subagent(self, sid, message):
        if sid not in self.active_subagents:
            return f"Error: Subagent {sid} not found."

        self.subagent_last_activity[sid] = time.time()
        self.active_subagents[sid].send_prompt(message)
        return f"Message sent to Subagent {sid}."

    def dispose_subagent(self, sid):
        if sid in self.active_subagents:
            self.active_subagents[sid].abort()
            del self.active_subagents[sid]
            if sid in self.subagent_last_activity:
                del self.subagent_last_activity[sid]
            cid = self.subagent_call_ids.pop(sid, None)
            if cid:
                self.on_tool_finished.emit(cid)
            return f"Subagent {sid} disposed."
        return f"Error: Subagent {sid} not found."

    def list_subagents(self):
        if not self.active_subagents:
            return "No active subagents."
        return "Active subagents: " + ", ".join(self.active_subagents.keys())

    @with_agent_context
    def handle_subagent_thought(self, sid, text, function_calls):
        if sid not in self.active_subagents:
            return
        self.subagent_last_activity[sid] = time.time()

        if function_calls:
            threading.Thread(target=self._async_subagent_tool_execution, args=(
                sid, function_calls), daemon=True).start()
            return

        if text:
            cid = self.subagent_call_ids.pop(sid, None)
            if cid:
                self.on_tool_finished.emit(cid)
            self.event_queue.append(
                {"type": "pulse", "text": f"[System Feedback: Subagent {sid} finished - {text}]", "purpose": "Subagent task completed"})
            self.check_cycle_completion()

    def _async_subagent_tool_execution(self, sid, function_calls):
        from core.logger_config import agent_id_var
        if hasattr(self, 'agent_id'):
            agent_id_var.set(self.agent_id)

        subagent = self.active_subagents.get(sid)
        if subagent is None:
            return

        function_responses = []
        for call in function_calls:
            function_name = call.name
            args = call.args or {}
            try:
                skill_result = self.cerebrum.execute_tool_call(
                    function_name, args)
                if isinstance(skill_result, dict):
                    function_responses.append((function_name, skill_result))
                else:
                    function_responses.append(
                        (function_name, {"result": str(skill_result)}))
            except Exception as e:
                function_responses.append((function_name, {"error": str(e)}))

        subagent.send_function_responses(function_responses)

    @with_agent_context
    def handle_subagent_error(self, sid, error):
        cid = self.subagent_call_ids.pop(sid, None)
        if cid:
            self.on_tool_finished.emit(cid)
        self.event_queue.append(
            {"type": "pulse", "text": f"[System Warning: Subagent {sid} encountered an error: {error}]", "purpose": "Subagent task error"})
        self.check_cycle_completion()
