import os
import sys
import json
import pytest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.orchestrator import AmityOrchestrator
from core.config_manager import ConfigManager


def test_orchestrator_async_query_prep_with_custom_user_name(monkeypatch, tmp_path):
    cfg_file = str(tmp_path / "config.json")
    with open(cfg_file, "w") as f:
        json.dump({"user-full-name": "Bob Builder"}, f)

    monkeypatch.setattr("config.paths.get_config_file", lambda: cfg_file)
    monkeypatch.setattr("config.paths.get_app_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path / (aid or "default")))

    orch = AmityOrchestrator.__new__(AmityOrchestrator)
    orch.agent_id = "test_agent"
    orch.config_manager = ConfigManager(config_file=cfg_file)
    orch.last_action_result = None
    orch.current_user_prompt = "Can you help me build a wall?"
    
    mock_worker = MagicMock()
    mock_worker.reformulate_query.return_value = "Can you help me build a wall?"
    orch.gemini_worker = mock_worker

    orch._async_query_prep("Can you help me build a wall?", [], None)

    assert mock_worker.send_prompt.called
    sent_prompt = mock_worker.send_prompt.call_args[0][0]
    assert "[CHANNEL: LOCAL_GUI]" in sent_prompt
    assert "[Bob Builder (User)]: Can you help me build a wall?" in sent_prompt


def test_orchestrator_async_query_prep_fallback_when_name_empty(monkeypatch, tmp_path):
    cfg_file = str(tmp_path / "config.json")
    with open(cfg_file, "w") as f:
        json.dump({"user-full-name": ""}, f)

    monkeypatch.setattr("config.paths.get_config_file", lambda: cfg_file)
    monkeypatch.setattr("config.paths.get_app_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path / (aid or "default")))

    orch = AmityOrchestrator.__new__(AmityOrchestrator)
    orch.agent_id = "test_agent"
    orch.config_manager = ConfigManager(config_file=cfg_file)
    orch.last_action_result = None
    orch.current_user_prompt = "Hello!"
    
    mock_worker = MagicMock()
    mock_worker.reformulate_query.return_value = "Hello!"
    orch.gemini_worker = mock_worker

    orch._async_query_prep("Hello!", [], None)

    assert mock_worker.send_prompt.called
    sent_prompt = mock_worker.send_prompt.call_args[0][0]
    assert "[CHANNEL: LOCAL_GUI]" in sent_prompt
    assert "[User (User)]: Hello!" in sent_prompt
