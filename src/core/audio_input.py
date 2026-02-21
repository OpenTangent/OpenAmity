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
    wake_word_detected = Signal(str)
    transcription_finished = Signal(str)
    listening_started = Signal()
    listening_stopped = Signal()
    error_occurred = Signal(str)
    initialized = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.running = False
        self.audio_queue = queue.Queue()
        self.model = None
        self.transcriber = None
        self.wake_word_thread = None

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
            # Load OpenWakeWord
            # Get paths
            all_models = openwakeword.get_pretrained_model_paths()
            # Filter for amity first, then alexa
            selected_models = [m for m in all_models if "amity" in m.lower()]
            
            if not selected_models:
                print("Warning: 'Amity' wake word model not found. Falling back to 'Alexa' as proxy.")
                selected_models = [m for m in all_models if "alexa" in m.lower()]
            
            if not selected_models:
                self.error_occurred.emit("No suitable wake word models found (Amity or Alexa).")
                return

            print(f"Loading wake word models: {selected_models}")
            self.model = Model(wakeword_model_paths=selected_models)
            
            # Load Faster-Whisper
            self.transcriber = faster_whisper.WhisperModel("tiny.en", device="cpu", compute_type="int8")
            
            print("Audio models initialized.")
        except Exception as e:
            self.error_occurred.emit(f"Failed to initialize audio models: {e}")

    def start_wake_word_detection(self):
        if self.running:
            return
        self.running = True
        self.wake_word_thread = threading.Thread(target=self._wake_word_loop, daemon=True)
        self.wake_word_thread.start()

    def stop_wake_word_detection(self):
        self.running = False
        if self.wake_word_thread:
            # We don't join here to avoid blocking UI, just set flag
            pass

    def _audio_callback(self, indata, frames, time, status):
        if status:
            print(f"Audio callback status: {status}")
        self.audio_queue.put(indata.copy().flatten())

    def _wake_word_loop(self):
        print("Starting wake word loop...")
        with sd.InputStream(samplerate=SAMPLE_RATE, blocksize=CHUNK_SIZE, channels=1, dtype='int16', callback=self._audio_callback):
            while self.running:
                try:
                    # Get audio chunk
                    audio_chunk = self.audio_queue.get(timeout=1.0)
                    
                    # OpenWakeWord Prediction
                    # Convert to int16 for compatibility if needed, but float32 usually works.
                    # openwakeword.Model.predict() handles it.
                    prediction = self.model.predict(audio_chunk)
                    
                    # Check for wake word
                    for md in self.model.prediction_buffer.keys():
                        score = self.model.prediction_buffer[md][-1]
                        if score > 0.5:
                            print(f"Wake word detected: {md} (Score: {score:.2f})")
                            self.wake_word_detected.emit(md)
                            self.model.reset()
                            self._record_and_transcribe()
                            break # Break strictly to avoid multiple triggers
                            
                except queue.Empty:
                    continue
                except Exception as e:
                    print(f"Error in wake word loop: {e}")
                    self.error_occurred.emit(str(e))
                    time.sleep(1) # Prevent tight loop on error

    def _record_and_transcribe(self):
        self.listening_started.emit()
        print("Recording command...")
        
        frames = []
        silence_frames = 0
        # Wait for up to 5 seconds of audio
        max_chunks = int(5 * SAMPLE_RATE / CHUNK_SIZE) 
        silence_threshold_chunks = int(1.5 * SAMPLE_RATE / CHUNK_SIZE) # 1.5s of silence
        
        # Simple energy based silence detection
        # Threshold needs calibration, but 0.01 on float32 [-1,1] is decent for quiet rooms
        energy_threshold = 0.01 

        for _ in range(max_chunks):
            try:
                data = self.audio_queue.get(timeout=2.0)
                frames.append(data)
                
                # Check for silence
                rms = np.sqrt(np.mean((data.astype(np.float32) / 32768.0)**2))
                if rms < energy_threshold:
                    silence_frames += 1
                else:
                    silence_frames = 0
                
                if silence_frames > silence_threshold_chunks:
                    print("Silence detected, stopping recording.")
                    break
            except queue.Empty:
                break
        
        self.listening_stopped.emit()
        
        if not frames:
            print("No audio recorded.")
            return

        print("Transcribing...")
        # Save to temp wav
        try:
            temp_fd, temp_filename = tempfile.mkstemp(suffix=".wav")
            os.close(temp_fd)
            
            # Concatenate and convert to int16 (already int16)
            audio_data = np.concatenate(frames)
            
            with wave.open(temp_filename, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(audio_data.tobytes())
            
            segments, info = self.transcriber.transcribe(temp_filename, beam_size=5)
            text = " ".join([segment.text for segment in segments]).strip()
            
            print(f"Transcription result: '{text}'")
            if text:
                self.transcription_finished.emit(text)
            
            os.remove(temp_filename)
            
        except Exception as e:
            print(f"Transcription failed: {e}")
            self.error_occurred.emit(f"Transcription failed: {e}")
