import logging
import threading
from typing import Callable, List


class Signal:
    """A thread-safe, error-isolated replacement for PySide6 Signal."""

    def __init__(self):
        self._callbacks: List[Callable] = []
        self._lock = threading.RLock()

    def connect(self, callback: Callable):
        with self._lock:
            if callback not in self._callbacks:
                self._callbacks.append(callback)

    def disconnect(self, callback: Callable):
        with self._lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

    def emit(self, *args, **kwargs):
        with self._lock:
            callbacks = list(self._callbacks)

        for callback in callbacks:
            try:
                callback(*args, **kwargs)
            except TypeError as te:
                if te.__traceback__.tb_next is None:
                    # Invocation-level argument count mismatch, safe to fallback
                    try:
                        if kwargs:
                            callback(*args)
                        elif len(args) > 1:
                            callback(args[0])
                        else:
                            raise te
                    except Exception as e:
                        cb_name = getattr(callback, "__name__", str(callback))
                        logging.error(f"Signal callback error in {cb_name}: {e}", exc_info=True)
                else:
                    # TypeError came from inside the callback body; do not retry with fewer arguments
                    cb_name = getattr(callback, "__name__", str(callback))
                    logging.error(f"Signal callback error in {cb_name}: {te}", exc_info=True)
            except Exception as e:
                cb_name = getattr(callback, "__name__", str(callback))
                logging.error(f"Signal callback error in {cb_name}: {e}", exc_info=True)

