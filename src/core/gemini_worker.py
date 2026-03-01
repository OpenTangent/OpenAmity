import os
import time
import threading
from google import genai
from google.genai import types
from dotenv import load_dotenv
from PIL import Image
from PySide6.QtCore import QObject, Signal
from .settings_manager import SettingsManager

# Load environment variables
load_dotenv()

class GeminiWorker(QObject):
    response_received = Signal(str)
    error_occurred = Signal(str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.settings = SettingsManager()
        self.api_key = os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            print("Error: GEMINI_API_KEY not found in .env")
            self.error_occurred.emit("Missing API Key. Please check .env file.")
            
        try:
            self.client = genai.Client(api_key=self.api_key)
        except Exception as e:
            print(f"Error initializing Gemini Client: {e}")
            self.error_occurred.emit(f"Client Init Error: {e}")

        self.chat = None
        self.running = False
        self.is_processing = False
        self.sys_instruct = None

    def is_running(self):
        return self.running

    def start_session(self, system_instruction=None):
        """Initializes the chat session."""
        self.sys_instruct = system_instruction
        try:
            # We don't "start" a chat object in the same way with the new SDK 
            # until we verify the model, but we can set up the configuration.
            # The new SDK is stateless in 'client' but stateful in 'chat'.
            
            # Primary model check
            primary_model = self.settings.get("gemini.primary_model", "gemini-3.1-pro-preview")
            fallback_models = self.settings.get("gemini.fallback_models", ["gemini-3-flash-preview"])
            if not isinstance(fallback_models, list):
                fallback_models = [fallback_models]
            
            # Note: The new SDK uses 'chats.create'
            config = types.GenerateContentConfig(
                system_instruction=self.sys_instruct
            )
            
            try:
                # We attempt to create the chat. If the model is invalid, it might not fail 
                # until the first message, but let's assume valid config for now.
                self.chat = self.client.chats.create(model=primary_model, config=config)
                self.current_model = primary_model
                print(f"Gemini SDK session started with {primary_model}.")
                self.running = True
            except Exception as e:
                print(f"Failed to load {primary_model} ({e}), attempting fallbacks...")
                success = False
                for fallback_model in fallback_models:
                    if fallback_model == primary_model:
                        continue
                    try:
                        self.chat = self.client.chats.create(model=fallback_model, config=config)
                        self.current_model = fallback_model
                        print(f"Gemini SDK session started with fallback {fallback_model}.")
                        self.running = True
                        success = True
                        break
                    except Exception as fallback_e:
                        print(f"Failed to load fallback {fallback_model}: {fallback_e}")
                
                if not success:
                    raise Exception("All fallback models failed to load.")
            
        except Exception as e:
            print(f"Failed to start session: {e}")
            self.running = False
            self.error_occurred.emit(f"Session Start Error: {e}")

    def stop_session(self):
        self.running = False
        self.chat = None
        print("Gemini SDK session stopped.")

    def abort(self):
        """Aborts the current processing."""
        self._abort_flag = True
        self.is_processing = False

    def send_prompt(self, prompt: str, image_path: str = None, yolo: bool = False):
        """Sends a prompt to the model in a separate thread."""
        if not self.running or not self.chat:
            self.error_occurred.emit("Session not started.")
            return

        self._abort_flag = False
        self.is_processing = True
        # Run blocking network call in a thread
        threading.Thread(target=self._process_prompt, args=(prompt, image_path, yolo), daemon=True).start()

    def _process_prompt(self, prompt, image_path, yolo):
        try:
            content = [prompt]
            
            if image_path:
                try:
                    img = Image.open(image_path)
                    content.append(img)
                    print(f"Attached image: {image_path}")
                except Exception as e:
                    print(f"Failed to load image: {e}")
                    self.error_occurred.emit(f"Image load error: {e}")
                    self.is_processing = False
                    return

            # Safety settings logic can be added here using types.SafetySetting
            # For now, relying on default or server-side enforcement.

            # Send message with streaming
            # The new SDK method is likely chat.send_message_stream
            response_stream = self.chat.send_message_stream(message=content)
            
            full_text = ""
            for chunk in response_stream:
                if getattr(self, '_abort_flag', False):
                    print("Gemini response aborted.")
                    self.is_processing = False
                    return
                if chunk.text:
                    full_text += chunk.text
            
            # Emit final complete response
            if not getattr(self, '_abort_flag', False):
                self.response_received.emit(full_text)

        except Exception as e:
            if getattr(self, '_abort_flag', False):
                self.is_processing = False
                return
            
            error_str = str(e)
            print(f"Gemini API Error with {self.current_model}: {error_str}")
            
            fallback_models = self.settings.get("gemini.fallback_models", ["gemini-3-flash-preview"])
            if not isinstance(fallback_models, list):
                fallback_models = [fallback_models]
            
            success = False
            for fallback_model in fallback_models:
                if self.current_model == fallback_model:
                    continue
                    
                print(f"Attempting fallback to {fallback_model}...")
                try:
                    config = types.GenerateContentConfig(system_instruction=self.sys_instruct)
                    self.chat = self.client.chats.create(model=fallback_model, config=config)
                    self.current_model = fallback_model
                    
                    # Retry the message
                    response_stream = self.chat.send_message_stream(message=content)
                    full_text = ""
                    for chunk in response_stream:
                        if getattr(self, '_abort_flag', False):
                            print("Gemini response aborted during fallback.")
                            self.is_processing = False
                            return
                        if chunk.text:
                            full_text += chunk.text
                    if not getattr(self, '_abort_flag', False):
                        self.response_received.emit(full_text)
                    
                    success = True
                    break # Success on retry
                except Exception as retry_e:
                    print(f"Fallback to {fallback_model} failed: {retry_e}")
                    error_str += f" | {fallback_model} Failed: {retry_e}"
            
            if not success:
                self.error_occurred.emit(error_str)
        
        finally:
            if not getattr(self, '_abort_flag', False):
                self.is_processing = False
