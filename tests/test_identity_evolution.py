import os
import sys
import shutil
import tempfile
import json
import pytest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.mempalace_manager import MemPalaceManager
from tools.mempalace_tool import MemPalaceTool
from core.settings_manager import SettingsManager


@pytest.fixture
def temp_agent_env(monkeypatch, tmp_path):
    palace_path = str(tmp_path / "mempalace")
    os.makedirs(palace_path, exist_ok=True)
    monkeypatch.setattr("config.paths.get_app_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path / (aid or "default")))
    monkeypatch.setattr("config.paths.get_mempalace_dir", lambda aid: palace_path)
    monkeypatch.setattr("config.paths.get_config_file", lambda: str(tmp_path / "config.json"))
    return palace_path


def test_apply_identity_delta_autonomous_tool(temp_agent_env, tmp_path):
    agent_id = "test-agent-evolution"
    agent_dir = tmp_path / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)

    orch = MagicMock()
    orch.agent_id = agent_id
    orch.mempalace_manager = MemPalaceManager(agent_id=agent_id, palace_path=temp_agent_env)

    tool = MemPalaceTool(orchestrator=orch)

    # 1. Apply delta via tool
    res = tool.execute(
        "apply_identity_delta",
        target_section="core_values",
        delta_type="add",
        content="Intellectual Humility (I readily adapt when presented with sound evidence.)",
        rationale="Conversational alignment with Andrew regarding over-confidence in debugging."
    )

    assert "Successfully applied identity delta to core_values" in res

    # 2. Check settings.json updated
    sm = SettingsManager(agent_id=agent_id)
    cv = sm.get("core.agent.core-values", [])
    assert any("Intellectual Humility" in val for val in cv)

    # 3. Check identity_evolution.json log
    log_path = os.path.join(temp_agent_env, "identity_evolution.json")
    assert os.path.exists(log_path)
    with open(log_path, "r", encoding="utf-8") as f:
        history = json.load(f)
    assert len(history) == 1
    assert history[0]["target_section"] == "core_values"
    assert history[0]["delta_type"] == "add"
    assert "Intellectual Humility" in history[0]["content"]

    # 4. Check identity.txt compiled
    id_path = os.path.join(temp_agent_env, "identity.txt")
    assert os.path.exists(id_path)
    with open(id_path, "r", encoding="utf-8") as f:
        identity_text = f.read()
    assert "Intellectual Humility" in identity_text
    assert orch.build_system_prompt.called
