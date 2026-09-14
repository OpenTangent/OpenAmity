import json
import os
import threading
import uuid
from typing import Any, Optional

_global_file_locks = {}
_locks_dict_lock = threading.Lock()


def _get_lock_for_path(filepath: str) -> threading.Lock:
    norm_path = os.path.abspath(filepath)
    with _locks_dict_lock:
        if norm_path not in _global_file_locks:
            _global_file_locks[norm_path] = threading.Lock()
        return _global_file_locks[norm_path]


def atomic_json_write(filepath: str, data: Any, lock: Optional[threading.Lock] = None, indent: int = 2) -> None:
    """
    Atomically writes data to filepath as JSON using a temporary staging file and os.replace.
    Thread-safe via provided lock or an internal path-based lock.
    """
    path_lock = lock if lock is not None else _get_lock_for_path(filepath)

    with path_lock:
        dir_name = os.path.dirname(filepath)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)

        temp_file = f"{filepath}.tmp.{uuid.uuid4().hex}"
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=indent, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_file, filepath)
        except Exception:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except OSError:
                    pass
            raise
