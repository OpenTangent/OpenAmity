import os
import sys
import threading
import time
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.events import Signal


def test_signal_basic_connect_emit():
    sig = Signal()
    received = []

    def handler(val):
        received.append(val)

    sig.connect(handler)
    sig.emit("hello")
    assert received == ["hello"]

    sig.disconnect(handler)
    sig.emit("world")
    assert received == ["hello"]


def test_signal_no_duplicate_connect():
    sig = Signal()
    count = [0]

    def handler():
        count[0] += 1

    sig.connect(handler)
    sig.connect(handler)
    sig.emit()
    assert count[0] == 1


def test_signal_error_isolation(caplog):
    sig = Signal()
    received = []

    def bad_handler():
        raise ValueError("Intentional crash in listener")

    def good_handler():
        received.append("ok")

    sig.connect(bad_handler)
    sig.connect(good_handler)

    # Should not raise exception
    sig.emit()

    assert received == ["ok"]
    assert "Intentional crash in listener" in caplog.text


def test_signal_concurrent_emit_and_connect():
    sig = Signal()
    results = []
    errors = []

    def listener(n):
        results.append(n)

    def worker_emit():
        for i in range(100):
            try:
                sig.emit(i)
            except Exception as e:
                errors.append(e)

    def worker_connect():
        for _ in range(50):
            try:
                sig.connect(listener)
                sig.disconnect(listener)
            except Exception as e:
                errors.append(e)

    threads = [
        threading.Thread(target=worker_emit),
        threading.Thread(target=worker_connect),
        threading.Thread(target=worker_emit),
        threading.Thread(target=worker_connect),
    ]

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0


def test_signal_type_error_internal_not_masked(caplog):
    sig = Signal()
    calls = []

    def handler_with_internal_type_error(x, y=None):
        calls.append((x, y))
        # Internal TypeError inside function body
        return "number: " + 42  # raises TypeError: can only concatenate str to str

    sig.connect(handler_with_internal_type_error)
    sig.emit("first", "second")

    # It should only have been called ONCE, not retried with fewer args:
    assert len(calls) == 1
    assert calls[0] == ("first", "second")
    assert "can only concatenate str" in caplog.text

