import os
import time
import threading
from google import genai
from google.genai import types
from dotenv import load_dotenv
from PIL import Image
import logging
from .events import Signal
from .settings_manager import SettingsManager

# Load environment variables
load_dotenv()

class GeminiWorker:
    def __init__(self):
        self.thought_received = Signal() # text, list of function calls
        self.error_occurred = Signal()
        
        self.settings = SettingsManager()
        self.api_key = self.settings.get_env("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY")
        self.available = False
        self.last_error = None
        if not self.api_key:
            self.last_error = "GEMINI_API_KEY not found in .env"
            if self.settings.get("core.first-run", False):
                logging.info("GEMINI_API_KEY not found in .env (expected on first run)")
            else:
                logging.error(self.last_error)
                self.error_occurred.emit("Missing API Key. Please check .env file.")
        else:
            try:
                self.client = genai.Client(api_key=self.api_key)
                self.available = True
            except Exception as e:
                self.last_error = f"Error initializing Gemini Client: {e}"
                logging.error(self.last_error, exc_info=True)
                self.error_occurred.emit(f"Client Init Error: {e}")

        self.thinker_chat = None
        self.running = False
        self.is_processing = False
        self.sys_instruct = None
        self.tools = None
        
        # State for remembering the first successful model per session
        self.thinking_models = []
        self.current_thinker_model = None
        self.thinker_config = None

    def is_running(self):
        return self.running

    def start_session(self, system_instruction=None, tools=None):
        if not getattr(self, 'available', False):
            logging.error("Attempted to start session but GeminiWorker is unavailable.")
            self.error_occurred.emit("Session Start Error: Gemini Worker is not available")
            return

        self.sys_instruct = system_instruction
        self.tools = tools
        if self.sys_instruct:
            self.sys_instruct += "\n\nCRITICAL INSTRUCTION: You must always output your internal reasoning and thought process as plain text BEFORE invoking any tool. Explain what you are about to do and why."
            self.sys_instruct += "\n\nAUTONOMOUS SPEECH INSTRUCTION: You are fully autonomous regarding your speech. You will NOT speak automatically. If you wish to communicate with the user, you MUST explicitly use the Speaker tool (e.g., Speaker_speak_aloud). Otherwise, you will remain completely silent. Speak from a first-person perspective."

        try:
            is_low_token = self.settings.get("core.low-token-mode", False)
            model_key = "core.gemini.light-models" if is_low_token else "core.gemini.gemini-models"
            self.thinking_models = self.settings.get(model_key, ["gemini-3.1-pro-preview"])
            if not isinstance(self.thinking_models, list):
                self.thinking_models = [self.thinking_models]

            tool_list = []
            if self.tools:
                # Group all function declarations into a Tool object
                from google.genai.types import Tool, FunctionDeclaration
                func_declarations = []
                for t in self.tools:
                    func_declarations.append(FunctionDeclaration(
                        name=t['name'],
                        description=t['description'],
                        parameters=t.get('parameters')
                    ))
                if func_declarations:
                    tool_list.append(Tool(function_declarations=func_declarations))

            self.thinker_config = types.GenerateContentConfig(
                system_instruction=self.sys_instruct,
                tools=tool_list if tool_list else None
            )

            # Reset to top of list if this is a fresh start
            if not self.current_thinker_model or self.current_thinker_model not in self.thinking_models:
                self.current_thinker_model = self.thinking_models[0]

            old_history = self.thinker_chat.history if (self.thinker_chat and hasattr(self.thinker_chat, 'history')) else None
            try:
                self.thinker_chat = self.client.chats.create(model=self.current_thinker_model, config=self.thinker_config, history=old_history)
            except Exception:
                self.thinker_chat = self.client.chats.create(model=self.current_thinker_model, config=self.thinker_config)
            logging.info(f"Thinker initialized with {self.current_thinker_model}.")

            self.running = True
            logging.info("Gemini SDK sessions started.")

        except Exception as e:
            logging.error(f"Failed to start session: {e}", exc_info=True)
            self.running = False
            self.error_occurred.emit(f"Session Start Error: {e}")

    def stop_session(self):
        self.running = False
        self.thinker_chat = None
        logging.info("Gemini SDK session stopped.")

    def abort(self):
        self._abort_flag = True
        self.is_processing = False

    def send_prompt(self, prompt: str, image_path: str = None, yolo: bool = False, audio_path: str = None):
        if not self.running or not self.thinker_chat:
            self.error_occurred.emit("Session not started.")
            return

        self._abort_flag = False
        self.is_processing = True
        threading.Thread(target=self._process_thought, args=(prompt, image_path, yolo, audio_path), daemon=True).start()

    def send_function_response(self, name: str, response: dict):
        if not self.running or not self.thinker_chat:
            self.error_occurred.emit("Session not started.")
            return

        self._abort_flag = False
        self.is_processing = True
        
        from google.genai import types
        part = types.Part.from_function_response(
            name=name,
            response=response
        )
        
        threading.Thread(target=self._process_thought, args=(part, None, False), daemon=True).start()

    def send_function_responses(self, responses: list):
        if not self.running or not self.thinker_chat:
            self.error_occurred.emit("Session not started.")
            return

        self._abort_flag = False
        self.is_processing = True
        
        from google.genai import types
        import mimetypes
        parts = []
        multimodal_parts = []
        is_low_token = self.settings.get("core.low-token-mode", False)
        for name, response in responses:
            if isinstance(response, dict):
                if 'media' in response and isinstance(response['media'], list):
                    for file_path in response['media']:
                        if is_low_token:
                            response['result'] = f"{response.get('result', '')}\n[Multimedia attachment skipped: Low Token Mode is active] (Path: {file_path})"
                        else:
                            response['result'] = f"{response.get('result', '')}\nAttached multimedia file: {file_path}"
                            try:
                                mime_type, _ = mimetypes.guess_type(file_path)
                                with open(file_path, 'rb') as f:
                                    data = f.read()
                                multimodal_parts.append(types.Part.from_bytes(data=data, mime_type=mime_type or 'application/octet-stream'))
                            except Exception as e:
                                logging.error(f"Failed to attach multimodal file {file_path}: {e}")
                    
                    # Remove media from response dict so Gemini SDK doesn't complain about unexpected keys
                    del response['media']

            parts.append(types.Part.from_function_response(
                name=name,
                response=response
            ))
            
        parts.extend(multimodal_parts)
            
        threading.Thread(target=self._process_thought, args=(parts, None, False, None), daemon=True).start()

    def _process_thought(self, prompt, image_path, yolo, audio_path=None):
        if isinstance(prompt, list):
            content = prompt
        else:
            content = [prompt]

        is_low_token = self.settings.get("core.low-token-mode", False)

        if image_path:
            if is_low_token:
                content.append("[Image attachment skipped: Low Token Mode is active]")
            else:
                try:
                    img = Image.open(image_path)
                    content.append(img)
                except Exception as e:
                    self.error_occurred.emit(f"Image load error: {e}")
                    self.is_processing = False
                    return

        if audio_path:
            if is_low_token:
                content.append("[Audio attachment skipped: Low Token Mode is active]")
            else:
                try:
                    import mimetypes
                    from google.genai import types
                    mime_type, _ = mimetypes.guess_type(audio_path)
                    with open(audio_path, 'rb') as f:
                        audio_data = f.read()
                    content.append(types.Part.from_bytes(data=audio_data, mime_type=mime_type or 'audio/wav'))
                except Exception as e:
                    self.error_occurred.emit(f"Audio load error: {e}")
                    self.is_processing = False
                    return

        start_idx = self.thinking_models.index(self.current_thinker_model) if self.current_thinker_model in self.thinking_models else 0

        for idx in range(start_idx, len(self.thinking_models)):
            model_name = self.thinking_models[idx]

            # Re-init chat if we fallback
            if model_name != self.current_thinker_model or not self.thinker_chat:
                self.current_thinker_model = model_name
                old_history = self.thinker_chat.history if (self.thinker_chat and hasattr(self.thinker_chat, 'history')) else None
                try:
                    self.thinker_chat = self.client.chats.create(model=model_name, config=self.thinker_config, history=old_history)
                except Exception:
                    self.thinker_chat = self.client.chats.create(model=model_name, config=self.thinker_config)

            try:
                response_stream = self.thinker_chat.send_message_stream(message=content)
                full_text = ""
                function_calls = []
                for chunk in response_stream:
                    if getattr(self, '_abort_flag', False):
                        self.is_processing = False
                        return
                    if getattr(chunk, 'parts', None):
                        for part in chunk.parts:
                            if getattr(part, 'text', None):
                                full_text += part.text
                    if chunk.function_calls:
                        function_calls.extend(chunk.function_calls)
                
                if getattr(self, '_abort_flag', False):
                    return
                    
                # Truncate history to prevent unbounded context growth
                max_history = self.settings.get("core.gemini.session-context-limit", 40)
                if is_low_token:
                    max_history = max_history // 2
                    
                if self.thinker_chat and hasattr(self.thinker_chat, 'history') and len(self.thinker_chat.history) > max_history:
                    new_history = self.thinker_chat.history[-max_history:]
                    if new_history and getattr(new_history[0], 'role', '') == 'model':
                        new_history = new_history[1:]
                    
                    if new_history:
                        from google.genai import types
                        warning_msg = "Context before this point has been automatically trimmed, use your MemPalace tool to ensure that important context is never lost."
                        warning_part = types.Part.from_text(text=warning_msg)
                        new_history.insert(0, types.Content(role="user", parts=[warning_part]))
                        self.thinker_chat.history = new_history
                        
                self.thought_received.emit(full_text, function_calls)
                self.is_processing = False
                return

            except Exception as e:
                err_str = str(e)
                
                # Non-recoverable errors
                if "401" in err_str or "403" in err_str:
                    logging.warning(f"API Key Invalid (401/403) with {model_name}: {err_str}")
                    self.error_occurred.emit("API key is invalid (401/403).")
                    self.is_processing = False
                    return
                elif "500" in err_str or "503" in err_str:
                    logging.warning(f"Servers at Peak Capacity (500/503) with {model_name}: {err_str}")
                    self.error_occurred.emit("Gemini servers are currently at peak capacity (500/503).")
                    self.is_processing = False
                    return
                elif "400" in err_str and "INVALID_ARGUMENT" in err_str:
                    logging.error(f"Thinker API Error (400 INVALID_ARGUMENT) with {model_name}: {err_str}", exc_info=True)
                    self.error_occurred.emit("The request sent to the Gemini API was invalid (400 INVALID_ARGUMENT).")
                    self.is_processing = False
                    return
                elif "499" in err_str:
                    logging.warning(f"Request Cancelled (499) with {model_name}: {err_str}")
                    self.is_processing = False
                    return

                # Recoverable errors (attempt fallback models)
                is_last_model = (idx == len(self.thinking_models) - 1)
                
                if "429" in err_str or "quota" in err_str.lower() or ("400" in err_str and "FAILED_PRECONDITION" in err_str):
                    logging.warning(f"API Rate Limit/Precondition (429/400) with {model_name}: {err_str}")
                    if is_last_model:
                        self.error_occurred.emit("API Quota Limit Reached.")
                        self.is_processing = False
                        return
                    continue
                elif "200" in err_str or "safety" in err_str.lower():
                    logging.warning(f"Safety Trigger (200) with {model_name}: {err_str}")
                    if is_last_model:
                        self.error_occurred.emit("A safety trigger was fired which immediately stopped the agent's thoughts.")
                        self.is_processing = False
                        return
                    continue
                elif "404" in err_str:
                    logging.warning(f"Model Invalid (404) with {model_name}: {err_str}")
                    if is_last_model:
                        self.error_occurred.emit("The model string is invalid (404).")
                        self.is_processing = False
                        return
                    continue

                # Any other error
                logging.warning(f"Thinker API Error with {model_name}: {err_str}")
                if is_last_model:
                    self.error_occurred.emit("Gemini API error.")
                    self.is_processing = False
                    return
                continue

        if not getattr(self, '_abort_flag', False):
            self.error_occurred.emit("The Gemini Worker failed to initialise")
            self.thought_received.emit("System Offline", [])
            self.is_processing = False

    def reformulate_query(self, user_prompt: str, history: list) -> str:
        if not self.api_key or not history:
            return user_prompt
            
        system_instruction = "You are a query reformulator. Your job is to rewrite the user's latest prompt to be standalone and context-independent by resolving any pronouns or ambiguous references using the provided conversation history. Output ONLY the rewritten query, nothing else. If the query is already standalone, output it exactly as is."
        
        prompt = "Conversation History:\n"
        for sender, text in history[-5:]:
            prompt += f"{sender}: {text}\n"
        prompt += f"\nUser's Latest Prompt: {user_prompt}\n\nRewritten Query:"
        
        is_low_token = self.settings.get("core.low-token-mode", False)
        model_key = "core.gemini.light-models" if is_low_token else "core.gemini.gemini-models"
        models = self.settings.get(model_key, ["gemini-3.1-flash-preview"])
        if not isinstance(models, list):
            models = [models]
            
        from google.genai import types
        for model_name in models:
            try:
                response = self.client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(system_instruction=system_instruction)
                )
                if getattr(response, 'parts', None):
                    text_parts = [part.text for part in response.parts if getattr(part, 'text', None)]
                    if text_parts:
                        return "".join(text_parts).strip()
            except Exception as e:
                err_str = str(e)
                
                # Non-recoverable errors
                if "401" in err_str or "403" in err_str:
                    logging.warning(f"Reformulator API Key Invalid (401/403) with {model_name}: {err_str}")
                    return user_prompt
                elif "500" in err_str or "503" in err_str:
                    logging.warning(f"Reformulator Servers at Peak Capacity (500/503) with {model_name}: {err_str}")
                    return user_prompt
                elif "400" in err_str and "INVALID_ARGUMENT" in err_str:
                    logging.error(f"Reformulator API Error (400 INVALID_ARGUMENT) with {model_name}: {err_str}", exc_info=True)
                    return user_prompt
                elif "499" in err_str:
                    logging.warning(f"Reformulator Request Cancelled (499) with {model_name}: {err_str}")
                    return user_prompt

                # Recoverable errors (attempt fallback models)
                if "429" in err_str or "quota" in err_str.lower() or ("400" in err_str and "FAILED_PRECONDITION" in err_str):
                    logging.warning(f"Reformulator API Rate Limit/Precondition (429/400) with {model_name}: {err_str}")
                    continue
                elif "200" in err_str or "safety" in err_str.lower():
                    logging.warning(f"Reformulator Safety Trigger (200) with {model_name}: {err_str}")
                    continue
                elif "404" in err_str:
                    logging.warning(f"Reformulator Model Invalid (404) with {model_name}: {err_str}")
                    continue

                # Any other error
                logging.warning(f"Reformulator API Error with {model_name}: {err_str}")
                continue
                
        return user_prompt
