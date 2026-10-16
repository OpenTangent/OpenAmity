import base64
import uuid
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from core import identity_authorisation as auth


def signer(monkeypatch, tmp_path):
    key = Ed25519PrivateKey.generate()
    public = tmp_path / "approval_public.pem"
    public.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,
                                                    serialization.PublicFormat.SubjectPublicKeyInfo))
    monkeypatch.setattr(auth, "TRUST_KEY", public)
    def register(manager, section, delta_type, content, user_id="user"):
        from core.settings_manager import SettingsManager
        settings = SettingsManager(agent_id=manager.agent_id)
        settings.set("core.identity_evolution.mode", "signed_approval"); settings.save()
        record = dict(agent_id=manager.agent_id, approval_ref="appr_" + uuid.uuid4().hex,
                      target_section=section, delta_type=delta_type, content=content, user_id=user_id)
        record["signature"] = base64.b64encode(key.sign(auth.approval_payload(record))).decode("ascii")
        return manager.register_identity_approval(section, delta_type, content,
                                                  user_id=user_id, signed_approval=record)
    return register
