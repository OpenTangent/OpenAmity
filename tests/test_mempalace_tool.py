import os
import sys
import shutil
import tempfile
import sqlite3
import pytest
from unittest.mock import MagicMock

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from tools.mempalace_tool import MemPalaceTool


@pytest.fixture
def temp_agent_environment(monkeypatch):
    """Fixture that creates a temporary app data directory for isolated testing."""
    temp_dir = tempfile.mkdtemp(prefix="openamity_test_mempalace_")
    monkeypatch.setattr("config.paths.get_app_data_dir", lambda: temp_dir)
    monkeypatch.setattr("config.paths.get_base_dir_for",
                        lambda aid: os.path.join(temp_dir, "agents", aid) if aid else os.path.join(temp_dir, "agents", "default"))
    monkeypatch.setattr("config.paths.get_mempalace_dir",
                        lambda aid: os.path.join(temp_dir, "agents", aid, "mempalace") if aid else os.path.join(temp_dir, "agents", "default", "mempalace"))

    yield temp_dir

    shutil.rmtree(temp_dir, ignore_errors=True)


def test_mempalace_curiosity_pulse_scheduling(temp_agent_environment, caplog):
    """Verifies that MemPalace_search triggers a curiosity pulse without agent_id path errors."""
    agent_id = "test-agent-curiosity"
    mock_orchestrator = MagicMock()
    mock_orchestrator.agent_id = agent_id

    tool = MemPalaceTool(orchestrator=mock_orchestrator)
    # Mock manager.search to return "No results found." to trigger intrinsic curiosity hook
    tool.manager.search = MagicMock(return_value="No results found.")

    # Initialize empty pulses db schema
    agent_dir = os.path.join(temp_agent_environment, "agents", agent_id)
    os.makedirs(agent_dir, exist_ok=True)
    db_path = os.path.join(agent_dir, "pulses.db")
    conn = sqlite3.connect(db_path)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS pulses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            context TEXT,
            scheduled_time TEXT,
            recurrence TEXT,
            status TEXT,
            has_run INTEGER,
            created_at TEXT,
            pulse_type TEXT
        )
    ''')
    conn.commit()
    conn.close()

    # Search for non-existent topic
    import logging
    with caplog.at_level(logging.ERROR):
        result = tool.execute("search", query="Quantum Entanglement Teleportation")

    assert result == "No results found."

    # Assert no error logged
    assert "Error scheduling curiosity pulse" not in caplog.text

    # Verify pulse was inserted into pulses.db
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT title, context FROM pulses")
    pulses = cursor.fetchall()
    conn.close()

    assert len(pulses) == 1
    assert pulses[0][0] == "Curiosity: Quantum Entanglement Teleportation"
    assert "Quantum Entanglement Teleportation" in pulses[0][1]
