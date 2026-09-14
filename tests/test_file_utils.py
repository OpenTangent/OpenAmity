import os
import sys
import threading
import tempfile
import json
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.file_utils import atomic_json_write


def test_atomic_json_write_basic(tmp_path):
    target = str(tmp_path / "test.json")
    data = {"name": "Nova", "count": 42}

    atomic_json_write(target, data)

    assert os.path.exists(target)
    with open(target, "r", encoding="utf-8") as f:
        loaded = json.load(f)
    assert loaded == data


def test_atomic_json_write_concurrent(tmp_path):
    target = str(tmp_path / "concurrent.json")
    errors = []

    def writer(idx):
        try:
            for i in range(20):
                atomic_json_write(target, {"writer": idx, "step": i})
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0
    assert os.path.exists(target)
    with open(target, "r", encoding="utf-8") as f:
        loaded = json.load(f)
    assert "writer" in loaded and "step" in loaded
