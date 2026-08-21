import sys
import os

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.uid_generator import generate_agent_uid, is_valid_agent_uid, CROCKFORD_BASE32_ALPHABET


def test_uid_format_and_validation():
    uid = generate_agent_uid()
    assert uid.startswith("+OA-")
    assert len(uid) == 13
    assert uid[8] == "-"
    assert is_valid_agent_uid(uid) is True


def test_invalid_uids():
    assert is_valid_agent_uid("invalid") is False
    assert is_valid_agent_uid("+OA-123-456") is False
    assert is_valid_agent_uid("+OA-ILOU-1234") is False  # I, L, O, U are invalid in Crockford
    assert is_valid_agent_uid(None) is False
    assert is_valid_agent_uid(12345) is False


def test_uid_uniqueness():
    uids = set()
    for _ in range(5000):
        uid = generate_agent_uid()
        assert is_valid_agent_uid(uid)
        assert uid not in uids
        uids.add(uid)
