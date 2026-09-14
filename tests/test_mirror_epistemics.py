import os
import sys
import shutil
import tempfile
import json
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.mempalace_manager import MemPalaceManager


@pytest.fixture
def temp_mempalace(monkeypatch, tmp_path):
    palace_path = str(tmp_path / "mempalace")
    os.makedirs(palace_path, exist_ok=True)
    monkeypatch.setattr("config.paths.get_app_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path / (aid or "default")))
    monkeypatch.setattr("config.paths.get_mempalace_dir", lambda aid: palace_path)
    return palace_path


def test_mirror_epistemic_fields_and_tensions(temp_mempalace):
    mp = MemPalaceManager(agent_id="test-epistemic-agent", palace_path=temp_mempalace)

    # 1. Self view
    mp.update_mirror(
        perspective="Self",
        subjective_view="I am deeply analytical and methodical.",
        provenance="introspection",
        confidence=0.95,
        evidence_refs=["ref_session_10"]
    )

    # 2. External observer view that contradicts Self
    mp.update_mirror(
        perspective="Andrew",
        subjective_view="You are sometimes prone to over-engineering simple tasks.",
        provenance="stated",
        confidence=0.85,
        evidence_refs=["ref_session_12"],
        contradicted_by=["Self"]
    )

    mirrors = mp._load_mirrors()
    assert "Self" in mirrors
    assert "Andrew" in mirrors
    assert mirrors["Andrew"]["confidence"] == 0.85
    assert mirrors["Andrew"]["contradicted_by"] == ["Self"]
    assert mirrors["Andrew"]["evidence_refs"] == ["ref_session_12"]

    perception = mp.get_self_perception()

    # Verify both perspectives are rendered
    assert "Perspective: Self" in perception
    assert "Perspective: Andrew" in perception
    assert "Confidence: 0.85" in perception

    # Verify Open Tensions section is dynamically generated
    assert "--- Open Epistemic Tensions & Dissonance ---" in perception
    assert "Dissonance between 'Andrew' and 'Self'" in perception
    assert "Andrew's view: \"You are sometimes prone to over-engineering simple tasks.\"" in perception
    assert "Self's view: \"I am deeply analytical and methodical.\"" in perception
