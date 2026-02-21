import sounddevice as sd
import numpy as np
import openwakeword
from openwakeword.model import Model
import queue
import time
import traceback

# Configuration
SAMPLE_RATE = 16000
CHUNK_SIZE = 1280

def test_loop():
    print("Initializing OpenWakeWord...")
    try:
        # Get paths
        all_models = openwakeword.get_pretrained_model_paths()
        # Filter for alexa and hey_jarvis
        selected_models = [m for m in all_models if "alexa" in m]
        print(f"Loading models: {selected_models}")
        
        if not selected_models:
            print("No models found!")
            return

        model = Model(wakeword_model_paths=selected_models)
    except Exception as e:
        print(f"Failed to load model: {e}")
        traceback.print_exc()
        return

    q = queue.Queue()

    def callback(indata, frames, time, status):
        if status:
            print(f"Callback status: {status}")
        # Convert to int16 (commonly expected by openwakeword examples)
        # indata is float32 by default in sounddevice
        # But openwakeword supports float32 if scaled properly or just raw?
        # Let's try passing raw float32 first, as per some examples, but many use int16.
        # Let's convert to int16 to be safe.
        # data_int16 = (indata * 32767).astype(np.int16)
        # q.put(data_int16.flatten())
        
        # Actually, let's try just passing the float32 array first, flattened.
        q.put(indata.copy().flatten())

    print("Starting audio stream...")
    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, blocksize=CHUNK_SIZE, channels=1, dtype='int16', callback=callback):
            print("Stream started. Speak 'Alexa'...")
            start_time = time.time()
            while time.time() - start_time < 20: # Run for 20 seconds
                try:
                    chunk = q.get(timeout=1.0)
                    prediction = model.predict(chunk)
                    
                    for md in model.prediction_buffer.keys():
                        score = model.prediction_buffer[md][-1]
                        if score > 0.05: # Print low scores too
                            print(f"Score for {md}: {score:.4f}")
                        if score > 0.5:
                            print(f"*** DETECTED: {md} ***")
                            model.reset()
                except queue.Empty:
                    pass
                except Exception as e:
                    print(f"Loop Error: {e}")
                    traceback.print_exc()
                    break
    except Exception as e:
        print(f"Stream Error: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    test_loop()
