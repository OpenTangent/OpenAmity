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
        logging.debug(f"GeminiWorker.__init__ called. Existing thought_received: {hasattr(self, 'thought_received')}")
        self.thought_received = Signal() # text, list of function calls
        self.error_occurred = Signal()
        self.tokens_consumed = Signal() # int
        
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
        logging.debug("start_session called in GeminiWorker")
        if not getattr(self, 'available', False):
            logging.error("Attempted to start session but GeminiWorker is unavailable.")
            self.error_occurred.emit("Session Start Error: Gemini Worker is not available")
            return
            
        logging.debug("Gemini client successfully initialized for start_session")

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
                tools=tool_list if tool_list else None,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
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
        logging.debug("send_prompt called in GeminiWorker")
        if not self.running or not self.thinker_chat:
            logging.error(f"Attempted to send prompt but session is not running. running: {self.running}, thinker_chat: {self.thinker_chat is not None}")
            self.error_occurred.emit("Session not started.")
            return

        self._abort_flag = False
        logging.debug("Starting _process_thought thread...")
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
                                uploaded_file = self.client.files.upload(file=file_path, mime_type=mime_type)
                                multimodal_parts.append(uploaded_file)
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
                    import mimetypes
                    mime_type, _ = mimetypes.guess_type(image_path)
                    uploaded_img = self.client.files.upload(file=image_path, mime_type=mime_type or 'image/jpeg')
                    content.append(uploaded_img)
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
                    mime_type, _ = mimetypes.guess_type(audio_path)
                    uploaded_audio = self.client.files.upload(file=audio_path, mime_type=mime_type or 'audio/wav')
                    content.append(uploaded_audio)
                except Exception as e:
                    self.error_occurred.emit(f"Audio load error: {e}")
                    self.is_processing = False
                    return

        # Intelligent Media Culling: Strip heavy multimodal tokens from older context
        if self.thinker_chat and hasattr(self.thinker_chat, 'history'):
            history_len = len(self.thinker_chat.history)
            if history_len > 2:
                from google.genai import types
                for i in range(history_len - 2):
                    content_msg = self.thinker_chat.history[i]
                    if getattr(content_msg, 'parts', None):
                        new_parts = []
                        modified = False
                        for part in content_msg.parts:
                            if getattr(part, 'inline_data', None) or getattr(part, 'file_data', None):
                                if getattr(part, 'file_data', None) and getattr(part.file_data, 'file_uri', None):
                                    try:
                                        file_name = part.file_data.file_uri.split('/')[-1]
                                        self.client.files.delete(name=f"files/{file_name}")
                                        logging.debug(f"Automatically deleted pruned file from API: files/{file_name}")
                                    except Exception as e:
                                        logging.debug(f"Could not delete pruned file {part.file_data.file_uri}: {e}")
                                new_parts.append(types.Part.from_text(text="[Media attachment automatically culled to save tokens/memory]"))
                                modified = True
                            else:
                                new_parts.append(part)
                        if modified:
                            content_msg.parts = new_parts

        start_idx = self.thinking_models.index(self.current_thinker_model) if self.current_thinker_model in self.thinking_models else 0

        for idx in range(start_idx, len(self.thinking_models)):
            model_name = self.thinking_models[idx]
            logging.debug(f"_process_thought attempting to use model: {model_name}")

            # Re-init chat if we fallback
            if model_name != self.current_thinker_model or not self.thinker_chat:
                logging.debug(f"Re-initializing chat for model {model_name}")
                self.current_thinker_model = model_name
                old_history = self.thinker_chat.history if (self.thinker_chat and hasattr(self.thinker_chat, 'history')) else None
                try:
                    self.thinker_chat = self.client.chats.create(model=model_name, config=self.thinker_config, history=old_history)
                except Exception as ex:
                    logging.debug(f"Failed creating chat with history: {ex}. Falling back to fresh chat.")
                    self.thinker_chat = self.client.chats.create(model=model_name, config=self.thinker_config)
            
            try:
                logging.debug(f"Calling self.thinker_chat.send_message_stream with content length {len(content)}...")
                response_stream = self.thinker_chat.send_message_stream(message=content)
                logging.debug("send_message_stream returned.")
                full_text = ""
                function_calls = []
                logging.debug("Beginning to iterate over response_stream...")
                for chunk_num, chunk in enumerate(response_stream):
                    logging.debug(f"Received chunk {chunk_num} from response_stream")
                    if getattr(self, '_abort_flag', False):
                        logging.debug("_abort_flag is True, returning from _process_thought")
                        self.is_processing = False
                        return
                    if getattr(chunk, 'parts', None):
                        for part in chunk.parts:
                            if getattr(part, 'text', None):
                                full_text += part.text
                    if chunk.function_calls:
                        function_calls.extend(chunk.function_calls)
                
                logging.debug("Finished iterating over response_stream.")
                if getattr(self, '_abort_flag', False):
                    logging.debug("_abort_flag is True after stream, returning")
                    return
                        
                # Token tracking
                tokens = 0
                if 'chunk' in locals() and hasattr(chunk, 'usage_metadata') and chunk.usage_metadata and hasattr(chunk.usage_metadata, 'total_token_count') and chunk.usage_metadata.total_token_count:
                    tokens = chunk.usage_metadata.total_token_count
                else:
                    # Fallback estimate
                    est_content_chars = sum(len(str(p)) for p in content)
                    tokens = int((est_content_chars + len(full_text)) / 4)
                
                if tokens > 0 and hasattr(self, 'tokens_consumed'):
                    self.tokens_consumed.emit(tokens)

                logging.debug(f"About to emit thought_received. Callback count: {len(self.thought_received._callbacks)}")
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
