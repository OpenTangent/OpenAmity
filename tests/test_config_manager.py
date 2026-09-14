import os
import sys
import json
import tempfile
import pytest

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.config_manager import ConfigManager


@pytest.fixture
def temp_config_env():
    temp_dir = tempfile.mkdtemp(prefix="openamity_config_test_")
    config_file = os.path.join(temp_dir, "config.json")
    yield config_file
    import shutil
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_config_defaults(temp_config_env):
    cm = ConfigManager(config_file=temp_config_env)
    assert cm.get("user-full-name") == "System Administrator"
    assert cm.get("user-phone-number") == ""
    assert cm.get("user-email") == ""
    assert os.path.exists(temp_config_env)


def test_config_set_and_save(temp_config_env):
    cm = ConfigManager(config_file=temp_config_env)
    cm.set("user-full-name", "Jane Doe")
    cm.set("user-phone-number", "+1234567890")
    cm.set("user-email", "jane@example.com")
    cm.save()

    cm2 = ConfigManager(config_file=temp_config_env)
    assert cm2.get("user-full-name") == "Jane Doe"
    assert cm2.get("user-phone-number") == "+1234567890"
    assert cm2.get("user-email") == "jane@example.com"


def test_config_merge_partial(temp_config_env):
    # Pre-write a partial config.json
    with open(temp_config_env, "w") as f:
        json.dump({"user-email": "custom@example.com"}, f)

    cm = ConfigManager(config_file=temp_config_env)
    assert cm.get("user-email") == "custom@example.com"
    assert cm.get("user-full-name") == "System Administrator"
    assert cm.get("user-phone-number") == ""


def test_config_corrupt_file_handling(temp_config_env):
    # Write invalid JSON
    with open(temp_config_env, "w") as f:
        f.write("{invalid_json: true,")

    cm = ConfigManager(config_file=temp_config_env)
    # Defaults should load
    assert cm.get("user-full-name") == "System Administrator"
    # Corrupt backup should exist
    assert os.path.exists(temp_config_env + ".corrupt")


def test_settings_manager_c5_in_memory_caching(temp_config_env, monkeypatch):
    from core.settings_manager import SettingsManager
    from config import paths
    from unittest.mock import patch

    temp_dir = os.path.dirname(temp_config_env)
    monkeypatch.setattr(paths, "get_base_dir_for", lambda aid: temp_dir)

    sm = SettingsManager(agent_id="test_c5_agent")
    sm.set("core.agent.name", "CachedBot")
    sm.save()

    # Verify that get() does NOT call load_settings on every read
    with patch.object(sm, "load_settings", side_effect=AssertionError("load_settings called during get()!")):
        val = sm.get("core.agent.name")
        assert val == "CachedBot"

    # Test deepcopy protection: mutating returned list does not mutate cache
    sm.set("core.agent.core-values", ["Value1", "Value2"])
    vals = sm.get("core.agent.core-values")
    vals.append("MutatedValue")
    assert sm.get("core.agent.core-values") == ["Value1", "Value2"]

    # Test refresh: external change picked up after refresh()
    with open(sm.settings_file, "r") as f:
        data = json.load(f)
    data["core"]["agent"]["name"] = "DiskUpdatedBot"
    with open(sm.settings_file, "w") as f:
        json.dump(data, f)

    assert sm.get("core.agent.name") == "CachedBot"
    sm.refresh()
    assert sm.get("core.agent.name") == "DiskUpdatedBot"

