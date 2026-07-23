import logging
import threading
import json
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
except ImportError:
    from .gemini_worker import GeminiWorker
    from .agy_worker import AgyWorker
    from .audio_input import AudioService
    from .audio_output import TTSWorker
    from .mempalace_manager import MemPalaceManager
    from .cerebrum import Cerebrum
    from .pulse_engine import PulseEngine
    from .settings_manager import SettingsManager

class AmityOrchestrator:
    def __init__(self):
        self.on_message_appended = Signal() # sender, text
        self.on_busy_state_changed = Signal() # is_busy, is_speaking
        self.on_amplitude_emitted = Signal() # float

        self.settings_manager = SettingsManager()
        self.mempalace_manager = MemPalaceManager()
        self.cerebrum = Cerebrum(settings_manager=self.settings_manager)
        
        # State
        self.is_busy = False
        self.is_thinking = False
        self.last_action_result = None
        self.current_user_prompt = ""
        self.recent_history = []
        self.is_silent_pulse = False
        
        # Budget
        self.current_task_weight = 0
        self.current_loop_count = 0
        self.last_executed_command = None
        self.duplicate_command_count = 0
        self.accumulated_thoughts = ""
        self.speech_queue = []
        self.prompt_queue = []
        
        self.system_prompt = self.mempalace_manager.wake_up()
        self.system_prompt += "\n" + self.cerebrum.get_agent_manual()
        if self.settings_manager.get("core.low-token-mode", False):
            self.system_prompt += "\n\n[SYSTEM STATE: LOW TOKEN MODE IS ACTIVE]"
        
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

        self.audio_service = AudioService()
        self.audio_service.initialized.connect(self.on_audio_initialized)
        self.audio_service.listening_started.connect(self.on_listening_start)
        self.audio_service.listening_stopped.connect(self.on_listening_stop)
        self.audio_service.audio_prompt_ready.connect(self.on_audio_prompt_ready)
        self.audio_service.error_occurred.connect(self.on_audio_error)
        
        self.tts_worker = None
        self.audio_service.start_initialization()

    def init_worker(self):
        if self.gemini_worker is not None:
            return

        if self.settings_manager.get("core.antigravity.agy-mode", False):
            self.gemini_worker = AgyWorker()
        else:
            self.gemini_worker = GeminiWorker()
            
        self.gemini_worker.thought_received.connect(self.handle_gemini_thought)
        if hasattr(self.gemini_worker, 'speech_received'):
            self.gemini_worker.speech_received.connect(self.handle_gemini_speech)
        self.gemini_worker.error_occurred.connect(self.handle_gemini_error)

        if self.audio_service and hasattr(self.audio_service, 'running') and getattr(self.gemini_worker, 'available', False):
            tools = self.cerebrum.get_all_tool_declarations()
            self.gemini_worker.start_session(self.system_prompt, tools=tools)

    def reload_settings(self):
        self.cerebrum.reload_skills()
        
        if not self.gemini_worker:
            self.init_worker()
        elif not getattr(self.gemini_worker, 'available', False):
            self.gemini_worker = None
            self.init_worker()
            
        if self.gemini_worker and getattr(self.gemini_worker, 'available', False):
            tools = self.cerebrum.get_all_tool_declarations()
            self.gemini_worker.start_session(self.system_prompt, tools=tools)
            
        if hasattr(self, 'agy_worker') and self.agy_worker and not getattr(self.agy_worker, 'available', False):
            self.agy_worker = None

    def set_busy_state(self, busy: bool, speaking: bool = False):
        self.is_busy = busy
        self.on_busy_state_changed.emit(busy, speaking)

    def user_interacted(self):
        self.pulse_engine.user_interacted()

    def toggle_mic(self):
        self.user_interacted()
        if self.is_busy:
            self.stop_all_processing()
        else:
            self.audio_service.start_listening()

    def stop_all_processing(self):
        if self.audio_service.running:
            self.audio_service.stop_listening()
        if self.gemini_worker and hasattr(self.gemini_worker, 'is_processing') and self.gemini_worker.is_processing:
            self.gemini_worker.abort()
        if self.tts_worker:
            self.tts_worker.stop()
            
        self.speech_queue.clear()
        self.prompt_queue.clear()
        self.is_thinking = False
        logging.info("System: Processing aborted by user.")
        self.set_busy_state(False)

    def process_text_input(self, text):
        if not text: return
        if self.is_busy:
            self.prompt_queue.append(text)
        else:
            self.process_input(text)

    def finish_thinking(self):
        logging.debug("finish_thinking called")
        self.is_thinking = False
        self.check_cycle_completion()

    def check_cycle_completion(self):
        is_speaking = (self.tts_worker and self.tts_worker.is_alive()) or len(self.speech_queue) > 0
        logging.debug(f"check_cycle_completion evaluated is_speaking: {is_speaking}, is_thinking: {self.is_thinking}")
        if not self.is_thinking and not is_speaking:
            logging.debug("check_cycle_completion calling set_busy_state(False)")
            self.set_busy_state(False)
            if self.prompt_queue:
                logging.debug("check_cycle_completion popping next prompt from queue")
                next_prompt = self.prompt_queue.pop(0)
                self.process_input(next_prompt)
        else:
            logging.debug("check_cycle_completion calling set_busy_state(True)")
            self.set_busy_state(True, speaking=is_speaking)

    def process_input(self, text, audio_path=None):
        import os
        current_env_key = self.settings_manager.get_env("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY")
        if self.gemini_worker and hasattr(self.gemini_worker, 'api_key') and self.gemini_worker.api_key != current_env_key:
            logging.info("System: API Key mismatch detected. Hot-reloading Gemini Worker...")
            try:
                self.gemini_worker.thought_received.disconnect(self.handle_gemini_thought)
                if hasattr(self.gemini_worker, 'speech_received'):
                    self.gemini_worker.speech_received.disconnect(self.handle_gemini_speech)
                self.gemini_worker.error_occurred.disconnect(self.handle_gemini_error)
            except Exception as e:
                logging.debug(f"Failed to disconnect signals during hot-reload: {e}")
            self.gemini_worker = None
            self.init_worker()

        if not self.gemini_worker or not getattr(self.gemini_worker, 'available', False):
            logging.error("System: The Gemini Worker failed to initialise (worker is None or unavailable).")
            self.append_to_conversation("System", "The Gemini Worker failed to initialise")
            self.set_busy_state(False)
            return

        import time
        session_timeout_mins = self.settings_manager.get("core.gemini.session-timeout", 60)
        time_since_last = time.time() - getattr(self, 'last_prompt_time', time.time())
        if time_since_last > (session_timeout_mins * 60):
            logging.info(f"System: Session timeout exceeded ({session_timeout_mins} mins). Wiping context to enforce MemPalace usage.")
            if self.gemini_worker.running:
                self.gemini_worker.stop_session()
        self.last_prompt_time = time.time()

        if not self.gemini_worker.running:
            logging.info("System: Brain offline. Starting session...")
            tools = self.cerebrum.get_all_tool_declarations()
            logging.debug("Calling self.gemini_worker.start_session...")
            self.gemini_worker.start_session(self.system_prompt, tools=tools)
            logging.debug("start_session returned.")
            
        self.append_to_conversation("User", text)
        self.is_thinking = True
        self.set_busy_state(True)
        self.current_user_prompt = text
        self.is_silent_pulse = False
        
        self.current_task_weight = 0
        self.current_loop_count = 0
        self.last_executed_command = None
        self.duplicate_command_count = 0
        self.accumulated_thoughts = ""
        
        threading.Thread(target=self._async_query_prep, args=(text, self.recent_history.copy(), audio_path), daemon=True).start()

    def _async_query_prep(self, text, history, audio_path):
        logging.debug("_async_query_prep started.")
        logging.debug("Calling reformulate_query...")
        reformulated = self.gemini_worker.reformulate_query(text, history)
        logging.debug(f"reformulate_query returned: {reformulated}")
        if reformulated != text:
            logging.debug(f"System: Reformulated query -> {reformulated}")
        
        prompt = "[CHANNEL: LOCAL_GUI]\n"
        if self.last_action_result:
            prompt += f"[System Feedback from previous turn]: {self.last_action_result}\n\n"
            self.last_action_result = None
            
        prompt += f"[User]: {self.current_user_prompt}"
        logging.debug(f"Calling gemini_worker.send_prompt with prompt length {len(prompt)}...")
        self.gemini_worker.send_prompt(prompt, audio_path=audio_path)
        logging.debug("gemini_worker.send_prompt returned.")

    def process_pulse(self, text="Autonomy Pulse"):
        if not self.gemini_worker or not getattr(self.gemini_worker, 'available', False):
            logging.error("System: Pulse aborted. The Gemini Worker failed to initialise (worker is None or unavailable).")
            return

        import time
        session_timeout_mins = self.settings_manager.get("core.gemini.session-timeout", 60)
        time_since_last = time.time() - getattr(self, 'last_prompt_time', time.time())
        if time_since_last > (session_timeout_mins * 60):
            logging.info(f"System: Session timeout exceeded ({session_timeout_mins} mins). Wiping context to enforce MemPalace usage.")
            if self.gemini_worker.running:
                self.gemini_worker.stop_session()
        self.last_prompt_time = time.time()

        if not self.gemini_worker.running:
            tools = self.cerebrum.get_all_tool_declarations()
            self.gemini_worker.start_session(self.system_prompt, tools=tools)

        self.is_silent_pulse = False
        self.append_to_conversation("System", "[Autonomy Pulse Triggered]")
        self.is_thinking = True
        self.set_busy_state(True)
        self.current_user_prompt = text
        
        self.current_task_weight = 0
        self.current_loop_count = 0
        self.last_executed_command = None
        self.duplicate_command_count = 0
        self.accumulated_thoughts = ""
        
        self.gemini_worker.send_prompt(text)

    def append_to_conversation(self, sender, text):
        self.recent_history.append((sender, text))
        max_history = 6 if self.settings_manager.get("core.low-token-mode", False) else 10
        while len(self.recent_history) > max_history:
            self.recent_history.pop(0)
        self.on_message_appended.emit(sender, text)

    def on_audio_initialized(self):
        logging.info("System: Ready.")
        if self.gemini_worker and getattr(self.gemini_worker, 'available', False):
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

    def handle_gemini_thought(self, text: str, function_calls: list):
        logging.debug(f"handle_gemini_thought called with text length: {len(text)}, function_calls count: {len(function_calls) if function_calls else 0}")
        if getattr(self, 'is_silent_pulse', False):
            logging.debug("handle_gemini_thought returning early due to is_silent_pulse")
            return
            
        clean_text = text.strip() if text else ""
        if clean_text:
            logging.debug("handle_gemini_thought logging agent thought to info")
            logging.info(f"Agent Thought: {clean_text}")
            self.accumulated_thoughts += clean_text + "\n"
            
        if not function_calls:
            logging.debug("handle_gemini_thought found no function calls, calling finish_thinking")
            self.finish_thinking()
            return

        logging.debug("handle_gemini_thought starting _async_tool_execution thread")
        threading.Thread(target=self._async_tool_execution, args=(function_calls, self.current_task_weight), daemon=True).start()

    def _async_tool_execution(self, function_calls, current_weight):
        function_responses = []
        executed_tools = []
        
        for call in function_calls:
            function_name = call.name
            tool_name = function_name.split("_")[0] if "_" in function_name else function_name
            args = call.args or {}
            
            logging.getLogger(f"agent.tool.{tool_name}").info(f"[Weight: {current_weight:.1f}] Executing command: {function_name}")
            
            skill_result = self.cerebrum.execute_tool_call(function_name, args)
            executed_tool_sig = f"{function_name}({json.dumps(args, sort_keys=True)})"
            executed_tools.append(executed_tool_sig)
            if isinstance(skill_result, dict):
                function_responses.append((function_name, skill_result))
            else:
                function_responses.append((function_name, {"result": str(skill_result)}))
                
        self.on_tool_execution_finished(function_responses, executed_tools)

    def on_tool_execution_finished(self, function_responses, executed_tools):
        if len(executed_tools) == 0:
            return

        for i, (name, resp) in enumerate(function_responses):
            if name.startswith("Speaker_"):
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
                    logging.debug(f"Expected valid JSON from Speaker tool but failed to parse: {e}")
                function_responses[i] = (name, resp)

        if hasattr(self, 'pulse_engine') and hasattr(self.pulse_engine, 'settings_manager'):
            sm = self.pulse_engine.settings_manager
            max_weight = sm.get("core.agentic-orchestration.cognitive-budget", 1000)
            low_token = sm.get("core.low-token-mode", False)
            base_weight = sm.get("core.agentic-orchestration.tool-cost", 2)
            exp_factor = sm.get("core.agentic-orchestration.exponential-factor", 1.5)
        else:
            max_weight = 1000
            low_token = False
            base_weight = 2
            exp_factor = 1.5
            
        if low_token:
            max_weight = max_weight / 2
        
        self.current_loop_count += 1
        added_weight = base_weight * (exp_factor ** (self.current_loop_count - 1))
        self.current_task_weight += added_weight
        
        current_batch = ", ".join(executed_tools)
        if current_batch == self.last_executed_command:
            self.duplicate_command_count += 1
            if self.duplicate_command_count >= 3:
                logging.warning("System: Duplicate Action Detected. Breaking loop.")
                self.handle_gemini_speech("I'm sorry, I seem to be stuck in a loop trying to figure this out. I'll stop here.")
                self.finish_thinking()
                return
        else:
            self.duplicate_command_count = 0

        if self.current_task_weight >= max_weight:
            logging.warning("System: Maximum operational capacity exceeded. Breaking loop.")
            self.handle_gemini_speech("I'm sorry, this task is taking too much of my cognitive capacity. I'll need to stop here and re-evaluate.")
            self.finish_thinking()
            return
            
        self.last_executed_command = current_batch
        budget_alert = f"\n[SYSTEM ALERT: Current Task Weight is {self.current_task_weight:.1f} out of {max_weight}. Evaluate necessity of further action.]"
        
        if function_responses:
            last_name, last_resp = function_responses[-1]
            last_resp["result"] = f"{last_resp['result']}{budget_alert}"
            function_responses[-1] = (last_name, last_resp)
            self.gemini_worker.send_function_responses(function_responses)

    def handle_gemini_speech(self, text: str):
        clean_text = text.strip()
        self.append_to_conversation("Agent", clean_text)
        if clean_text:
            self.speak(clean_text)
        else:
            self.finish_thinking()

    def handle_gemini_error(self, text):
        logging.warning(f"Gemini API Event: {text}")
        self.append_to_conversation("System Warning", text)
        self.finish_thinking()

    def speak(self, text):
        logging.getLogger("agent.speaker").info(f"[Weight: {self.current_task_weight:.1f}] {text}")
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
            
        self.tts_worker = TTSWorker(next_text)
        self.tts_worker.started_playback.connect(self._on_started_playback)
        self.tts_worker.amplitude_emitted.connect(self.on_amplitude_emitted.emit)
        self.tts_worker.on_finished.connect(self.on_tts_finished)
        self.tts_worker.start()

    def on_tts_finished(self):
        self.tts_worker = None
        self.process_speech_queue()

    def _on_started_playback(self):
        self.set_busy_state(self.is_busy, speaking=True)

    def shutdown(self):
        logging.info("System: Shutting down orchestrator...")
        if self.pulse_engine:
            self.pulse_engine.stop()
        self.stop_all_processing()
        if self.gemini_worker and hasattr(self.gemini_worker, 'stop_session'):
            self.gemini_worker.stop_session()
        if self.cerebrum:
            self.cerebrum.shutdown()
