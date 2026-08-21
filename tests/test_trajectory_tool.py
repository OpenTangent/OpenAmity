import os
import sys
import json
import shutil
import tempfile
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from tools.trajectory_tool import TrajectoryTool
from core.mempalace_manager import MemPalaceManager
from core.config_manager import ConfigManager


@pytest.fixture
def temp_environment(monkeypatch):
    """Fixture that creates a temporary app data directory for isolated testing."""
    temp_dir = tempfile.mkdtemp(prefix="openamity_test_trajectory_")
    monkeypatch.setattr("config.paths.get_app_data_dir", lambda: temp_dir)
    monkeypatch.setattr("config.paths.get_config_file", lambda: os.path.join(temp_dir, "config.json"))
    monkeypatch.setattr("config.paths.get_base_dir_for",
                        lambda aid: os.path.join(temp_dir, "agents", aid) if aid else os.path.join(temp_dir, "agents", "default"))
    monkeypatch.setattr("config.paths.get_mempalace_dir",
                        lambda aid: os.path.join(temp_dir, "agents", aid, "mempalace") if aid else os.path.join(temp_dir, "agents", "default", "mempalace"))

    yield temp_dir

    shutil.rmtree(temp_dir, ignore_errors=True)


def test_trajectory_get_bearings_user_details(temp_environment):
    """Verify that user details from config.json are rendered in get_bearings."""
    cfg = ConfigManager()
    cfg.set("user-full-name", "Alice Wonder")
    cfg.set("user-phone-number", "+27821112233")
    cfg.set("user-email", "alice@example.com")
    cfg.save()

    mock_orch = MagicMock()
    mock_orch.agent_id = "test-agent-1"
    mock_orch.config_manager = cfg

    tool = TrajectoryTool(orchestrator=mock_orch)
    output = tool.execute("get_bearings")

    assert "--- User Details ---" in output
    assert "Name: Alice Wonder" in output
    assert "Phone: +27821112233" in output
    assert "Email: alice@example.com" in output


def test_mempalace_get_self_perception_limit_24(temp_environment):
    """Verify that Theory of Mind records are sorted by recency and capped at 24."""
    agent_id = "test-agent-mirrors"
    mp = MemPalaceManager(agent_id=agent_id)
    mp._save_mirrors({})

    # Seed 30 mirrors with spaced timestamps
    base_time = datetime(2026, 8, 20, 10, 0, 0)
    for i in range(30):
        timestamp = (base_time + timedelta(minutes=i)).isoformat()
        mp.update_mirror(
            perspective=f"Perspective_{i:02d}",
            subjective_view=f"Subjective perception {i}",
            provenance="inferred"
        )
        # Explicitly set the timestamp to control ordering
        mirrors = mp._load_mirrors()
        mirrors[f"Perspective_{i:02d}"]["last_updated"] = timestamp
        mp._save_mirrors(mirrors)

    self_perception = mp.get_self_perception(limit=24)

    # Perspectives 06 to 29 (24 most recent) should be present
    for i in range(6, 30):
        assert f"Perspective: Perspective_{i:02d}" in self_perception

    # The oldest 6 perspectives (00 to 05) should be excluded
    for i in range(0, 6):
        assert f"Perspective: Perspective_{i:02d}" not in self_perception


def test_operational_hints_priority_in_bearings(temp_environment):
    """Verify that the Operational Protocols hint is the first operational hint."""
    mock_orch = MagicMock()
    mock_orch.agent_id = "test-agent-hints"

    tool = TrajectoryTool(orchestrator=mock_orch)
    output = tool.execute("get_bearings")

    assert "=== OPERATIONAL HINTS ===" in output
    hints_section = output.split("=== OPERATIONAL HINTS ===")[1].strip()
    hint_lines = [line.strip() for line in hints_section.splitlines() if line.strip().startswith("-")]

    assert len(hint_lines) >= 1
    assert hint_lines[0].startswith("- Operational Protocols:")
