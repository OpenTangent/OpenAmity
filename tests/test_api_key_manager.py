import os
import sys
import tempfile
import shutil
import pytest
from unittest.mock import patch
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from core.api_key_manager import ApiKeyManager


@pytest.fixture
def temp_agent_dir():
    temp_dir = tempfile.mkdtemp(prefix="openamity_apikey_test_")
    agent_id = "test_agent_123"
    agent_data_dir = os.path.join(temp_dir, "agents", agent_id)
    os.makedirs(agent_data_dir, exist_ok=True)

    with patch("config.paths.get_base_dir_for", return_value=agent_data_dir):
        yield {
            "temp_dir": temp_dir,
            "agent_id": agent_id,
            "agent_data_dir": agent_data_dir
        }
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_generate_key_and_verify(temp_agent_dir):
    agent_id = temp_agent_dir["agent_id"]
    mgr = ApiKeyManager(agent_id=agent_id)

    record, raw_key = mgr.generate_key(name="HomeAssistant", scopes=["pulse:inject"])
    assert raw_key.startswith("oa_sec_")
    assert record["name"] == "HomeAssistant"
    assert record["scopes"] == ["pulse:inject"]
    assert record["revoked"] is False
    assert record["expires_at"] is None
    assert "key_hash" in record

    # Verification with correct key
    is_valid, err, key_rec = mgr.verify_key(raw_key, required_scope="pulse:inject")
    assert is_valid is True
    assert err is None
    assert key_rec is not None
    assert key_rec["name"] == "HomeAssistant"
    assert key_rec["last_used_at"] is not None

    # Verification with invalid key
    is_valid_bad, err_bad, _ = mgr.verify_key("oa_sec_invalid_key_value", required_scope="pulse:inject")
    assert is_valid_bad is False
    assert err_bad == "INVALID_API_KEY"


def test_missing_and_empty_key(temp_agent_dir):
    agent_id = temp_agent_dir["agent_id"]
    mgr = ApiKeyManager(agent_id=agent_id)

    is_valid, err, _ = mgr.verify_key("")
    assert is_valid is False
    assert err == "MISSING_API_KEY"

    is_valid, err, _ = mgr.verify_key(None)
    assert is_valid is False
    assert err == "MISSING_API_KEY"


def test_scope_enforcement(temp_agent_dir):
    agent_id = temp_agent_dir["agent_id"]
    mgr = ApiKeyManager(agent_id=agent_id)

    record, raw_key = mgr.generate_key(name="ReadLogs", scopes=["logs:read"])
    is_valid, err, _ = mgr.verify_key(raw_key, required_scope="pulse:inject")
    assert is_valid is False
    assert err == "FORBIDDEN_SCOPE"

    # Match exact scope
    is_valid2, err2, _ = mgr.verify_key(raw_key, required_scope="logs:read")
    assert is_valid2 is True
    assert err2 is None

    # Wildcard scope
    _, raw_wildcard = mgr.generate_key(name="Admin", scopes=["*"])
    is_valid3, err3, _ = mgr.verify_key(raw_wildcard, required_scope="pulse:inject")
    assert is_valid3 is True
    assert err3 is None


def test_revocation(temp_agent_dir):
    agent_id = temp_agent_dir["agent_id"]
    mgr = ApiKeyManager(agent_id=agent_id)

    record, raw_key = mgr.generate_key(name="RevokeTest")
    key_id = record["key_id"]

    # Verify active
    is_valid, _, _ = mgr.verify_key(raw_key)
    assert is_valid is True

    # Revoke
    revoked = mgr.revoke_key(key_id)
    assert revoked is True

    # Verify denied
    is_valid2, err2, _ = mgr.verify_key(raw_key)
    assert is_valid2 is False
    assert err2 == "INVALID_API_KEY"


def test_expiration(temp_agent_dir):
    agent_id = temp_agent_dir["agent_id"]
    mgr = ApiKeyManager(agent_id=agent_id)

    # 1. Non-expired key (expires in 5 days)
    rec_valid, raw_valid = mgr.generate_key(name="FutureKey", expires_in_days=5)
    is_valid, _, _ = mgr.verify_key(raw_valid)
    assert is_valid is True

    # 2. Expired key (manually mock expired timestamp in storage)
    keys = mgr._load_keys()
    for k in keys:
        if k["name"] == "FutureKey":
            k["expires_at"] = (datetime.now() - timedelta(days=1)).isoformat()
    mgr._save_keys(keys)

    is_valid_exp, err_exp, _ = mgr.verify_key(raw_valid)
    assert is_valid_exp is False
    assert err_exp == "INVALID_API_KEY"


def test_list_keys_sanitization(temp_agent_dir):
    agent_id = temp_agent_dir["agent_id"]
    mgr = ApiKeyManager(agent_id=agent_id)

    mgr.generate_key(name="Key1")
    mgr.generate_key(name="Key2")
    keys = mgr.list_keys()
    assert len(keys) == 2
    for k in keys:
        assert "key_hash" not in k
        assert "name" in k
        assert "key_prefix" in k
