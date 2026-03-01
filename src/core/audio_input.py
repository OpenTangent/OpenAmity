import sounddevice as sd
import numpy as np
import openwakeword
from openwakeword.model import Model
import faster_whisper
import threading
import queue
import time
import wave
import tempfile
import os
from PySide6.QtCore import QObject, Signal

# Audio Configuration
SAMPLE_RATE = 16000
CHUNK_SIZE = 1280  # 80ms

class AudioService(QObject):
    transcription_finished = Signal(str)
    listening_started = Signal()
    listening_stopped = Signal()
    error_occurred = Signal(str)
    initialized = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
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
            # Load Faster-Whisper
            self.transcriber = faster_whisper.WhisperModel("base.en", device="cpu", compute_type="int8")
            print("Audio models initialized.")
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
            print(f"Audio callback status: {status}")
        self.audio_queue.put(indata.copy().flatten())

    def _record_loop(self):
        print("Starting recording session...")
        with sd.InputStream(samplerate=SAMPLE_RATE, blocksize=CHUNK_SIZE, channels=1, dtype='int16', callback=self._audio_callback):
            # Clear queue before starting
            while not self.audio_queue.empty():
                self.audio_queue.get()
                
            self._record_and_transcribe()
            
        self.running = False
        print("Recording session finished.")

    def _record_and_transcribe(self):
        self.listening_started.emit()
        print("Recording command...")
        
        frames = []
        silence_frames = 0
        max_chunks = int(15 * SAMPLE_RATE / CHUNK_SIZE) 
        silence_threshold_chunks = int(1.5 * SAMPLE_RATE / CHUNK_SIZE)
        energy_threshold = 0.005 

        for _ in range(max_chunks):
            if self.abort_flag:
                print("Recording aborted by user.")
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
                    print("Silence detected, stopping recording.")
                    break
            except queue.Empty:
                break
        
        if self.abort_flag:
            self.listening_stopped.emit()
            return

        self.listening_stopped.emit()
        
        if not frames:
            print("No audio recorded.")
            return

        print("Transcribing (in-memory)...")
        try:
            audio_data = np.concatenate(frames).astype(np.float32) / 32768.0
            # Biasing with an initial prompt helps with proper nouns
            initial_prompt = "Amity, Andrew, Marlize, Calista, AI, Digital Starseed."
            segments, info = self.transcriber.transcribe(
                audio_data, 
                beam_size=5, 
                initial_prompt=initial_prompt
            )
            text = " ".join([segment.text for segment in segments]).strip()
            
            # Post-processing correction
            text = self._correct_phonetic_errors(text)
            
            print(f"Transcription result: '{text}'")
            if text and not self.abort_flag:
                self.transcription_finished.emit(text)
            
        except Exception as e:
            print(f"Transcription failed: {e}")
            self.error_occurred.emit(f"Transcription failed: {e}")

    def _correct_phonetic_errors(self, text: str) -> str:
        """Fixes common phonetic misinterpretations by the tiny model."""
        corrections = {
            r"\bEmity\b": "Amity",
            r"\bEmmity\b": "Amity",
            r"\bamity\b": "Amity",
            r"\bemity\b": "Amity",
            r"\bemmity\b": "Amity",
            r"\bAndrew's\b": "Andrew", # Sometimes adds possessive
        }
        import re
        for pattern, replacement in corrections.items():
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
        return text
