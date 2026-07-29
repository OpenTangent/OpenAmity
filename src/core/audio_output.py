import os
import subprocess
import logging
import math
import queue
import threading
import re
import requests
from pathlib import Path
from .events import Signal
from .settings_manager import SettingsManager
from google import genai
from google.genai import types

class TTSWorker(threading.Thread):
    def __init__(self, text, voice=None, daemon=True):
        super().__init__(daemon=daemon)
        self.started_playback = Signal()
        self.amplitude_emitted = Signal()
        
        self.settings = SettingsManager()
        self.text = text
        self.voice = voice or self.settings.get("core.tts.gemini.model-name", "Kore")
        self.voice_prompt = self.settings.get("core.tts.gemini.prompt", {})
        
        voice_models = self.settings.get("core.gemini.voice-models", ["gemini-3.1-flash-tts-preview"])
        self.model = voice_models[0] if voice_models else "gemini-3.1-flash-tts-preview"
        
        self.process = None
        self._is_stopped = False
        self.on_finished = Signal()

    def stop(self):
        self._is_stopped = True
        if self.process:
            try:
                self.process.kill()
            except Exception:
                pass

    def run(self):
        try:
            if not self._is_stopped:
                self._stream_and_play()
        except Exception as e:
            logging.getLogger("core.TTS").error(f"TTS Error: {e}", exc_info=True)
        finally:
            self.on_finished.emit()

    def _stream_and_play(self):
        if self.settings.get("core.low-token-mode", False) or self.settings.get("core.antigravity.agy-mode", False) or self.settings.get("core.tts.piper.prefer-piper", False):
            self._stream_and_play_piper()
        else:
            self._stream_and_play_gemini()

    def _stream_and_play_gemini(self):
        self.started_playback.emit()
        if self._is_stopped:
            return

        try:
            client = genai.Client()
            if isinstance(self.voice_prompt, dict):
                full_text = (
                    f"{self.voice_prompt.get('preamble', '')}\n\n"
                    f"Audio Profile: {self.voice_prompt.get('profile', '')}\n\n"
                    f"Scene Setting: {self.voice_prompt.get('scene', '')}\n\n"
                    f"Director's Notes: {self.voice_prompt.get('directors-notes', '')}\n\n"
                    f"Transcript:\n{self.text}"
                )
            else:
                full_text = f"{self.voice_prompt}\n\nTranscript:\n{self.text}"

            response_stream = client.models.generate_content_stream(
                model=self.model,
                contents=full_text,
                config=types.GenerateContentConfig(
                    speech_config=types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name=self.voice
                            )
                        )
                    )
                )
            )

            self.process = subprocess.Popen(
                ["ffplay", "-f", "s16le", "-ar", "24000", "-nodisp", "-autoexit", "-i", "-"],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            
            audio_queue = queue.Queue()
            
            def writer_thread():
                try:
                    while not self._is_stopped:
                        data = audio_queue.get()
                        if data is None:
                            break
                        
                        if self.process and self.process.stdin:
                            self.process.stdin.write(data)
                            self.process.stdin.flush()
                            
                            if len(data) > 0:
                                samples = len(data) // 2
                                sum_sq = 0
                                stride = max(1, samples // 100)
                                if stride > 0:
                                    for i in range(0, samples * 2, stride * 2):
                                        val = int.from_bytes(data[i:i+2], byteorder='little', signed=True)
                                        sum_sq += val * val
                                    rms = math.sqrt(sum_sq / (samples / stride))
                                    amp = min(1.0, rms / 32768.0)
                                    self.amplitude_emitted.emit(amp)
                except Exception as e:
                    logging.getLogger("core.TTS.Gemini").error(f"TTS writer thread error: {e}")
                finally:
                    if self.process and self.process.stdin:
                        try:
                            self.process.stdin.write(b'\x00' * 48000)
                            self.process.stdin.flush()
                        except Exception:
                            pass
                        try:
                            self.process.stdin.close()
                        except Exception:
                            pass

            writer = threading.Thread(target=writer_thread, daemon=True)
            writer.start()

            for chunk in response_stream:
                if self._is_stopped:
                    break
                
                try:
                    if not chunk.candidates:
                        continue
                        
                    candidate = chunk.candidates[0]
                    if not candidate.content or not candidate.content.parts:
                        continue
                        
                    for part in candidate.content.parts:
                        if hasattr(part, 'inline_data') and part.inline_data:
                            data = part.inline_data.data
                            if data:
                                audio_queue.put(data)
                except (IndexError, AttributeError, TypeError) as inner_err:
                    logging.getLogger("core.TTS.Gemini").warning(f"Skipped an unexpected TTS chunk. Reason: {inner_err}")
                    
        except Exception as e:
            logging.getLogger("core.TTS.Gemini").error(f"Error during TTS streaming: {e}")
        finally:
            if 'audio_queue' in locals():
                audio_queue.put(None)
            if 'writer' in locals():
                writer.join(timeout=10)
            if self.process:
                self.process.wait()

    def _stream_and_play_piper(self):
        try:
            piper_dir = Path.home() / ".local" / "share" / "Open Amity" / "piper_voices"
            piper_dir.mkdir(parents=True, exist_ok=True)
            model_path = piper_dir / "en_GB-cori-high.onnx"
            config_path = piper_dir / "en_GB-cori-high.onnx.json"
            
            if not model_path.exists() or not config_path.exists():
                logging.getLogger("core.TTS.Piper").info("Downloading Piper TTS model...")
                model_url = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_GB/cori/high/en_GB-cori-high.onnx"
                config_url = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_GB/cori/high/en_GB-cori-high.onnx.json"
                try:
                    r_model = requests.get(model_url, stream=True, timeout=10)
                    r_model.raise_for_status()
                    with open(model_path, 'wb') as f:
                        for chunk in r_model.iter_content(chunk_size=8192):
                            f.write(chunk)
                    
                    r_config = requests.get(config_url, stream=True, timeout=10)
                    r_config.raise_for_status()
                    with open(config_path, 'wb') as f:
                        for chunk in r_config.iter_content(chunk_size=8192):
                            f.write(chunk)
                    logging.getLogger("core.TTS.Piper").info("Piper TTS model downloaded successfully.")
                except requests.RequestException as e:
                    logging.getLogger("core.TTS.Piper").warning(f"Offline or failed to download Piper model: {e}")
                    if model_path.exists(): model_path.unlink()
                    if config_path.exists(): config_path.unlink()
                    return

            from piper.voice import PiperVoice
            voice = PiperVoice.load(str(model_path))

            clean_text = re.sub(r'\[.*?\]', '', self.text).strip()
            
            if not clean_text:
                return

            self.started_playback.emit()
            if self._is_stopped:
                return

            self.process = subprocess.Popen(
                ["ffplay", "-f", "s16le", "-ar", str(voice.config.sample_rate), "-nodisp", "-autoexit", "-i", "-"],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

            audio_queue = queue.Queue()

            def writer_thread():
                try:
                    while not self._is_stopped:
                        data = audio_queue.get()
                        if data is None:
                            break

                        if self.process and self.process.stdin:
                            self.process.stdin.write(data)
                            self.process.stdin.flush()

                            if len(data) > 0:
                                samples = len(data) // 2
                                sum_sq = 0
                                stride = max(1, samples // 100)
                                if stride > 0:
                                    for i in range(0, samples * 2, stride * 2):
                                        val = int.from_bytes(data[i:i+2], byteorder='little', signed=True)
                                        sum_sq += val * val
                                    rms = math.sqrt(sum_sq / (samples / stride))
                                    amp = min(1.0, rms / 32768.0)
                                    self.amplitude_emitted.emit(amp)
                except Exception as e:
                    logging.getLogger("core.TTS.Piper").error(f"Piper writer thread error: {e}")
                finally:
                    if self.process and self.process.stdin:
                        try:
                            self.process.stdin.write(b'\x00' * (voice.config.sample_rate * 2))
                            self.process.stdin.flush()
                        except Exception:
                            pass
                        try:
                            self.process.stdin.close()
                        except Exception:
                            pass

            writer = threading.Thread(target=writer_thread, daemon=True)
            writer.start()

            for chunk in voice.synthesize(clean_text):
                if self._is_stopped:
                    break
                if chunk and chunk.audio_int16_bytes:
                    audio_queue.put(chunk.audio_int16_bytes)

        except Exception as e:
            logging.getLogger("core.TTS.Piper").error(f"Error during Piper TTS streaming: {e}")
        finally:
            if 'audio_queue' in locals():
                audio_queue.put(None)
            if 'writer' in locals():
                writer.join(timeout=10)
            if self.process:
                self.process.wait()
