import sounddevice as sd
import numpy as np
import threading
import queue
import time
import wave
import tempfile
import os
import logging
from .events import Signal
from .settings_manager import SettingsManager
from google import genai
from google.genai import types

# Audio Configuration
SAMPLE_RATE = 16000
CHUNK_SIZE = 1280  # 80ms

class AudioService:
    def __init__(self):
        self.transcription_finished = Signal()
        self.audio_prompt_ready = Signal() # text, audio_path
        self.listening_started = Signal()
        self.listening_stopped = Signal()
        self.error_occurred = Signal()
        self.initialized = Signal()
        
        self.running = False
        self.audio_queue = queue.Queue()
        self.transcriber = None
        self.recording_thread = None
        self.abort_flag = False

    def start_initialization(self):
        threading.Thread(target=self._initialize_thread, daemon=True).start()

    def _initialize_thread(self):
        try:
            self.initialize()
            self.initialized.emit()
        except Exception as e:
            self.error_occurred.emit(str(e))

    def initialize(self):
        try:
            self.settings = SettingsManager()
            self.api_key = os.getenv("GEMINI_API_KEY")
            self.client = genai.Client(api_key=self.api_key) if self.api_key else None
            logging.info("Audio service initialized.")
        except Exception as e:
            self.error_occurred.emit(f"Failed to initialize audio models: {e}")

    def start_listening(self):
        """Manually trigger recording."""
        if self.running:
            return
        self.running = True
        self.abort_flag = False
        self.recording_thread = threading.Thread(target=self._record_loop, daemon=True)
        self.recording_thread.start()

    def stop_listening(self):
        """Cancel/Abort the current recording."""
        self.abort_flag = True
        self.running = False

    def _audio_callback(self, indata, frames, time, status):
        if status:
            logging.warning(f"Audio callback status: {status}")
        self.audio_queue.put(indata.copy().flatten())

    def _record_loop(self):
        logging.debug("Starting recording session...")
        with sd.InputStream(samplerate=SAMPLE_RATE, blocksize=CHUNK_SIZE, channels=1, dtype='int16', callback=self._audio_callback):
            # Clear queue before starting
            while not self.audio_queue.empty():
                self.audio_queue.get()
                
            self._record_and_transcribe()
            
        self.running = False
        logging.debug("Recording session finished.")

    def _record_and_transcribe(self):
        self.listening_started.emit()
        logging.debug("Recording command...")
        
        frames = []
        silence_frames = 0
        max_chunks = int(15 * SAMPLE_RATE / CHUNK_SIZE) 
        silence_threshold_chunks = int(1.5 * SAMPLE_RATE / CHUNK_SIZE)
        energy_threshold = 0.005 

        for _ in range(max_chunks):
            if self.abort_flag:
                logging.info("Recording aborted by user.")
                self.listening_stopped.emit()
                return

            try:
                data = self.audio_queue.get(timeout=1.0)
                frames.append(data)
                
                # Check for silence using normalized float32
                audio_float32 = data.astype(np.float32) / 32768.0
                rms = np.sqrt(np.mean(audio_float32**2))
                
                if rms < energy_threshold:
                    silence_frames += 1
                else:
                    silence_frames = 0
                
                if silence_frames > silence_threshold_chunks:
                    logging.debug("Silence detected, stopping recording.")
                    break
            except queue.Empty:
                break
        
        if self.abort_flag:
            self.listening_stopped.emit()
            return

        self.listening_stopped.emit()
        
        if not frames:
            logging.info("No audio recorded.")
            return

        logging.debug("Transcribing (using Gemini API)...")
        try:
            # Save audio to wav file
            audio_data_int16 = np.concatenate(frames)
            temp_fd, temp_wav = tempfile.mkstemp(suffix=".wav")
            os.close(temp_fd)
            with wave.open(temp_wav, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2) # 16-bit
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(audio_data_int16.tobytes())
                
            is_low_token = self.settings.get("core.low-token-mode", False)
            is_agy_mode = self.settings.get("core.antigravity.agy-mode", False)

            if is_low_token or is_agy_mode:
                from .local_stt import LocalSTT
                text = LocalSTT().transcribe(temp_wav)
                if not text or "Failed" in text:
                    raise Exception(f"Local STT error: {text}")
            else:
                if not self.client:
                    raise Exception("Gemini client not initialized. Check API Key.")
                models = self.settings.get("core.gemini.gemini-models", ["gemini-3.1-flash-preview"])
                if not isinstance(models, list):
                    models = [models]
                    
                audio_file = self.client.files.upload(file=temp_wav)
                prompt = "Please transcribe this audio accurately. Output only the exact transcription without any commentary."
                
                text = ""
                for model_name in models:
                    try:
                        response = self.client.models.generate_content(
                            model=model_name,
                            contents=[prompt, audio_file]
                        )
                        if response.text:
                            text = response.text.strip()
                            break
                    except Exception as e:
                        logging.warning(f"Transcription failed with {model_name}: {e}")
                        continue
                
                if not text:
                    raise Exception("All Gemini models failed to transcribe audio.")
            
            # Post-processing correction
            text = self._correct_phonetic_errors(text)
            
            logging.info(f"Transcription result: '{text}'")
            if text and not self.abort_flag:
                self.transcription_finished.emit(text)
                self.audio_prompt_ready.emit(text, temp_wav)
            
        except Exception as e:
            logging.error(f"Transcription failed: {e}", exc_info=True)
            self.error_occurred.emit(f"Transcription failed: {e}")

    def _correct_phonetic_errors(self, text: str) -> str:
        """Fixes common phonetic misinterpretations by the tiny model."""
        corrections = {

            r"\bAndrew's\b": "Andrew", # Sometimes adds possessive
        }
        import re
        for pattern, replacement in corrections.items():
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
        return text
