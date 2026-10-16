"""Cooperative cross-process state barrier for snapshot capture.

All Open Amity memory mutators and JSON/graph writes participate. External
backend writers are outside this contract and must be quiesced by operator.
Compression occurs after the captured state is staged, outside the barrier.
"""
import contextlib
import fcntl
import functools
import os
import threading
from pathlib import Path

_locks = {}
_guard = threading.Lock()
_local = threading.local()


def state_root(path):
    path = Path(path).absolute()
    for parent in (path, *path.parents):
        if parent.name == "mempalace":
            return str(parent.parent)
    return str(path if path.is_dir() else path.parent)


@contextlib.contextmanager
def state_barrier(root):
    root = os.path.realpath(root)
    with _guard:
        lock = _locks.setdefault(root, threading.RLock())
    with lock:
        held = getattr(_local, "held", {})
        if root in held:
            held[root] += 1
            try: yield
            finally: held[root] -= 1
            return
        os.makedirs(root, exist_ok=True)
        with open(os.path.join(root, ".state_snapshot.lock"), "a+") as file:
            fcntl.flock(file.fileno(), fcntl.LOCK_EX)
            held[root] = 1
            _local.held = held
            try: yield
            finally:
                held.pop(root, None)
                fcntl.flock(file.fileno(), fcntl.LOCK_UN)


def memory_state_operation(fn):
    @functools.wraps(fn)
    def call(self, *args, **kwargs):
        with state_barrier(state_root(self.palace_path)):
            return fn(self, *args, **kwargs)
    return call
