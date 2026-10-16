import json
import threading
from pathlib import Path
import pytest
from core.mempalace_manager import MemPalaceManager as M
from core.settings_manager import SettingsManager

@pytest.fixture
def env(tmp_path,monkeypatch):
    config=tmp_path/"config"
    monkeypatch.setattr("config.paths.get_base_dir_for",lambda *a:str(config))
    SettingsManager.clear_instances();s=SettingsManager(agent_id="autonomous")
    s.set("core.agent.core-values",[]);s.save()
    palace=tmp_path/"mempalace";palace.mkdir()
    m=M.__new__(M);m.agent_id="autonomous";m.palace_path=str(palace)
    m._cache_lock=threading.RLock();m._corpus_revision=1;m.morpho_layer=None;m._sync_identity=lambda:None
    return m,s

def test_default_autonomy_without_admin_or_key(env):
    m,s=env
    assert s.get("core.identity_evolution.mode")=="autonomous"
    assert "Successfully applied" in m.apply_identity_delta("core_values","add","Discernment",rationale="Repeated evidence favours considered decisions",operation_id="deliberate-1")
    assert s.get("core.agent.core-values")==["Discernment"]
    history=json.loads((Path(m.palace_path)/"identity_evolution.json").read_text())
    assert history[0]["authority"]=="autonomous"
    assert history[0]["before_items"]==[] and history[0]["after_items"]==["Discernment"]
    assert "already completed" in m.apply_identity_delta("core_values","add","Discernment",rationale="Repeated evidence favours considered decisions",operation_id="deliberate-1")
    assert "different change" in m.apply_identity_delta("core_values","add","Other",rationale="Different",operation_id="deliberate-1")

def test_rationale_and_permissions_boundary(env):
    m,s=env
    assert m.apply_identity_delta("core_values","add","Trait").startswith("Error:")
    before=s.get("core")
    assert m.apply_identity_delta("tools","add","shell",rationale="I want access").startswith("Error:")
    assert s.get("core")==before

def test_autonomous_modify_retire_and_reversal(env):
    m,s=env
    for kind,text in [("add","Old"),("modify","Old -> New"),("retire","New"),("add","New")]:
        assert "Successfully applied" in m.apply_identity_delta("core_values",kind,text,rationale="Deliberate reconsideration")
    assert s.get("core.agent.core-values")==["New"]

def test_autonomous_failed_commit_recovers(env,monkeypatch):
    m,s=env
    with monkeypatch.context() as patch:
        patch.setattr(SettingsManager,"save",lambda self:(_ for _ in ()).throw(RuntimeError("interrupted")))
        assert m.apply_identity_delta("core_values","add","Trait",rationale="Considered",operation_id="recover").startswith("Error:")
    m._recover_identity_delta_journal()
    assert s.get("core.agent.core-values")==["Trait"]
    assert "already completed" in m.apply_identity_delta("core_values","add","Trait",rationale="Considered",operation_id="recover")

def test_disabled_mode_blocks_evolution(env):
    m,s=env;s.set("core.identity_evolution.mode","disabled");s.save()
    assert "disabled" in m.apply_identity_delta("core_values","add","Trait",rationale="Considered")
    assert s.get("core.agent.core-values")==[]
