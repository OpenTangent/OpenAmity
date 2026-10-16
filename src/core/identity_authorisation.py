"""Signed human approval boundary.

The administrator provisions an Ed25519 public key in /etc/openamity; the
private key must remain outside the agent's filesystem/tools. An operator
able to replace runtime code or the root-owned trust store is out of scope.
No environment variable, TTY flag or caller boolean confers authority.
"""
import base64
import json
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

FIELDS = ("agent_id", "approval_ref", "target_section", "delta_type", "content", "user_id")
TRUST_KEY = Path("/etc/openamity/identity_approval_public.pem")


def approval_payload(record):
    if any(not isinstance(record.get(k), str) or not record[k].strip() for k in FIELDS):
        raise PermissionError("Approval has missing or invalid binding fields.")
    return json.dumps({k: record[k] for k in FIELDS}, sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def verify_approval(record, agent_id):
    if record.get("agent_id") != agent_id:
        raise PermissionError("Approval is bound to a different agent.")
    try:
        key = serialization.load_pem_public_key(TRUST_KEY.read_bytes())
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError("Expected Ed25519 public key")
        signature = base64.b64decode(record.get("signature", ""), validate=True)
        key.verify(signature, approval_payload(record))
    except Exception as exc:
        raise PermissionError("No valid administrator-signed identity approval; trust key may be unprovisioned.") from exc
    return record
