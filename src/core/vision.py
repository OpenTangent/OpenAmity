
import cv2
import threading
import time
from PySide6.QtCore import QObject, Signal, QThread
from PySide6.QtGui import QImage, QPixmap

class VisualCortex(QObject):
    frame_ready = Signal(QImage)
    error_occurred = Signal(str)

    def __init__(self, camera_index=0):
        super().__init__()
        self.camera_index = camera_index
        self.is_active = False
        self.capture_thread = None
        self.cap = None

    def start_camera(self):
        if self.is_active:
            return
        
        self.is_active = True
        self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.capture_thread.start()

    def stop_camera(self):
        self.is_active = False
        if self.capture_thread:
            self.capture_thread.join(timeout=2.0)
            self.capture_thread = None

    def save_snapshot(self):
        """Captures a single frame and saves it to a temp file. Returns path."""
        if not self.cap or not self.cap.isOpened():
            # If camera not running, try to open swiftly
            cap = cv2.VideoCapture(self.camera_index)
            ret, frame = cap.read()
            cap.release()
            if not ret:
                return None
        else:
            # If running, we might be fighting for the frame?
            # Actually, `self.cap.read()` in the loop consumes frames.
            # We can just cache the last frame in the loop!
            return self.last_frame_path

    def _capture_loop(self):
        try:
            self.cap = cv2.VideoCapture(self.camera_index)
            if not self.cap.isOpened():
                self.error_occurred.emit(f"Could not open camera {self.camera_index}")
                self.is_active = False
                return

            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

            while self.is_active:
                ret, frame = self.cap.read()
                if not ret:
                    time.sleep(0.1)
                    continue

                self.current_frame = frame

                # Convert BGR to RGB
                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                h, w, ch = rgb_frame.shape
                bytes_per_line = ch * w
                
                # Create QImage
                q_img = QImage(rgb_frame.data, w, h, bytes_per_line, QImage.Format_RGB888)
                
                # Emit COPY
                self.frame_ready.emit(q_img.copy())
                
                time.sleep(1/30)

        except Exception as e:
            self.error_occurred.emit(str(e))
        finally:
            if self.cap:
                self.cap.release()

    def snapshot(self):
        if hasattr(self, 'current_frame') and self.current_frame is not None:
            import tempfile
            import os
            path = os.path.join(tempfile.gettempdir(), "amity_vision_snapshot.jpg")
            cv2.imwrite(path, self.current_frame)
            return path
        return None
