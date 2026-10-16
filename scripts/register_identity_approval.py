#!/usr/bin/env python3
"""Human-operated offline signer. Private key MUST be outside agent access.

Create an Ed25519 key with openssl genpkey -algorithm ED25519; provision its
public key at /etc/openamity/identity_approval_public.pem as administrator.
This script emits a signed JSON approval; --import-file imports it without
requiring access to the signing key. Never put a private key in agent state.
"""
import argparse
import base64
import json
import sys
import uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from core.identity_authorisation import approval_payload

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--import-file", type=Path)
    parser.add_argument("--private-key", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--section", choices=["core_values", "overarching_goals", "anti_patterns"])
    parser.add_argument("--type", choices=["add", "modify", "retire"])
    parser.add_argument("--content")
    parser.add_argument("--user", default="Andrew")
    args = parser.parse_args()
    if args.import_file:
        from core.mempalace_manager import MemPalaceManager
        record = json.loads(args.import_file.read_text())
        manager = MemPalaceManager(agent_id=args.agent_id)
        ref = manager.register_identity_approval(record["target_section"], record["delta_type"],
            record["content"], user_id=record["user_id"], signed_approval=record)
        print(ref)
        return
    if not all([args.private_key, args.output, args.section, args.type, args.content]):
        parser.error("Signing requires --private-key, --output, --section, --type, --content")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = serialization.load_pem_private_key(args.private_key.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        parser.error("Expected an Ed25519 private key")
    record = dict(agent_id=args.agent_id, approval_ref="appr_" + uuid.uuid4().hex,
                  target_section=args.section, delta_type=args.type, content=args.content,
                  user_id=args.user)
    record["signature"] = base64.b64encode(key.sign(approval_payload(record))).decode("ascii")
    with args.output.open("x", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
    print(f"Signed approval written to {args.output}; import separately into the agent.")

if __name__ == "__main__":
    main()
