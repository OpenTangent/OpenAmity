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
