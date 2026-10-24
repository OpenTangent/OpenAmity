import os
import logging
import subprocess
import numpy as np
from faster_whisper import WhisperModel

# Suppress Hugging Face Hub unauthenticated warnings
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)


class LocalSTT:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(LocalSTT, cls).__new__(cls)
            cls._instance.model = None
            cls._instance.initial_prompt = "This recording explores the local colour and how we organise our town centres. It involves travelling and analysing behaviour."
        return cls._instance

    def _get_model(self, status_callback=None):
        if self.model is None:
            hf_cache = os.path.expanduser("~/.cache/huggingface/hub")
            has_cache = False
            if os.path.exists(hf_cache):
                try:
                    for item in os.listdir(hf_cache):
                        if "tiny.en" in item or "whisper-tiny.en" in item:
                            has_cache = True
                            break
                except Exception:
                    pass

            if not has_cache:
                logging.info("Downloading faster-whisper (tiny.en) model for the first time...")
                if status_callback:
                    try:
                        status_callback("[System: Downloading local speech recognition model (one-time setup)...]")
                    except Exception:
                        pass
            else:
                logging.info("Initializing faster-whisper (tiny.en) from cache...")

            self.model = WhisperModel(
                "tiny.en", device="cpu", compute_type="int8")
            logging.info("faster-whisper loaded.")
        return self.model

    @staticmethod
    def _decode_audio_ffmpeg(audio_path: str) -> np.ndarray:
        """Decodes audio using system ffmpeg into a 16kHz mono float32 numpy array,
        completely bypassing PyAV (av + av.libs)."""
        cmd = [
            "ffmpeg", "-nostdin", "-threads", "0",
            "-i", audio_path,
            "-f", "s16le",
            "-ac", "1",
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-"
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True)
        return np.frombuffer(res.stdout, np.int16).astype(np.float32) / 32768.0

    def transcribe(self, audio_path: str, status_callback=None) -> str:
        """Transcribes the audio at audio_path using faster-whisper."""
        try:
            if not audio_path or not os.path.exists(audio_path) or not os.path.isfile(audio_path):
                return f"[Transcription Failed: File not found or not a valid file: {audio_path}]"

            file_size = os.path.getsize(audio_path)
            if file_size < 32:
                return f"[Transcription Failed: Audio file {audio_path} is too small or truncated ({file_size} bytes)]"

            # Attempt FFmpeg PCM decode to bypass PyAV entirely
            try:
                audio_input = self._decode_audio_ffmpeg(audio_path)
            except Exception as e:
                logging.debug(f"LocalSTT: FFmpeg decode failed ({e}), passing path directly: {audio_path}")
                audio_input = audio_path

            model = self._get_model(status_callback=status_callback)
            logging.debug(f"Local STT transcribing: {audio_path}")

            segments, info = model.transcribe(
                audio_input,
                initial_prompt=self.initial_prompt
            )

            text = " ".join([segment.text for segment in segments]).strip()
            logging.info(f"Local Transcription result: '{text}'")

            if not text:
                return "[Transcription empty]"

            return text
        except Exception as e:
            logging.error(f"Local Transcription failed: {e}", exc_info=True)
            return f"[Transcription Failed: {e}]"

