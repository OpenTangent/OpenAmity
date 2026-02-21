import openwakeword
from openwakeword.model import Model
import faster_whisper
import os

print("Imports successful.")

# OpenWakeWord: List available models
print("Available OpenWakeWord models:", openwakeword.get_pretrained_model_paths())

# Faster-Whisper: Load model (downloads if needed)
print("Loading faster-whisper model (tiny.en)...")
try:
    model = faster_whisper.WhisperModel("tiny.en", device="cpu", compute_type="int8")
    print("faster-whisper model loaded.")
except Exception as e:
    print(f"Error loading faster-whisper: {e}")
