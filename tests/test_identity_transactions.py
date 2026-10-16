import json
import threading
import multiprocessing as mp
from pathlib import Path
import pytest
from core.mempalace_manager import MemPalaceManager as M
from core.settings_manager import SettingsManager
from identity_test_support import signer


def manager(path, agent_id="transaction-fixture"):
    path.mkdir(exist_ok=True)
    m = M.__new__(M)
    m.agent_id = agent_id
    m.palace_path = str(path)
    m._cache_lock = threading.RLock()
    m._corpus_revision = 1
    m.morpho_layer = None
    m._sync_identity = lambda: None
    return m


@pytest.fixture
def env(tmp_path, monkeypatch):
    config = tmp_path / "config"
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda *a: str(config))
    SettingsManager.clear_instances()
    settings = SettingsManager(agent_id="transaction-fixture")
    settings.set("core.identity_evolution.mode", "signed_approval")
    settings.set("core.agent.core-values", [])
    settings.save()
    return manager(tmp_path / "palace"), signer(monkeypatch, tmp_path), config


def test_forged_caller_and_unsigned_ledger_rejected(env):
    m, approve, config = env
    for kw in [{}, {"trusted_source": True}, {"trusted_source": True, "auth_token": "made-up"}]:
        with pytest.raises(PermissionError):
            m.register_identity_approval("core_values", "add", "bad", **kw)
    path = Path(m.palace_path) / "identity_approvals.json"
    path.write_text(json.dumps([dict(approval_ref="forged", agent_id=m.agent_id,
                                   target_section="core_values", delta_type="add", content="bad",
                                   user_id="Andrew", status="approved", used=False)]))
    assert m.apply_identity_delta("core_values", "add", "bad", approval_ref="forged").startswith("Error:")
    assert SettingsManager(agent_id=m.agent_id).get("core.agent.core-values") == []


def test_missing_approval_journal_is_preserved(env):
    m, approve, config = env
    p = Path(m.palace_path) / "identity_delta_journal.json"
    p.write_text(json.dumps(dict(approval_ref="missing", target_section="core_values",
                                delta_type="add", content="bad", before_items=[], after_items=["bad"])))
    with pytest.raises(PermissionError):
        m._recover_identity_delta_journal()
    assert p.exists()
    assert SettingsManager(agent_id=m.agent_id).get("core.agent.core-values") == []


def test_tampered_journal_after_image_rejected(env):
    m, approve, config = env
    ref = approve(m, "core_values", "add", "good")
    p = Path(m.palace_path) / "identity_delta_journal.json"
    p.write_text(json.dumps(dict(approval_ref=ref, target_section="core_values",
                                delta_type="add", content="good", before_items=[], after_items=["bad"])))
    with pytest.raises(PermissionError):
        m._recover_identity_delta_journal()
    assert p.exists()
    assert SettingsManager(agent_id=m.agent_id).get("core.agent.core-values") == []


def test_failed_save_recovery_and_replay(env, monkeypatch):
    m, approve, config = env
    ref = approve(m, "core_values", "add", "good")
    original = SettingsManager.save
    with monkeypatch.context() as patch:
        patch.setattr(SettingsManager, "save", lambda self: (_ for _ in ()).throw(RuntimeError("disk failure")))
        assert m.apply_identity_delta("core_values", "add", "good", approval_ref=ref).startswith("Error:")
        with pytest.raises(RuntimeError):
            m._recover_identity_delta_journal()
    p = Path(m.palace_path) / "identity_delta_journal.json"
    assert p.exists()
    m._recover_identity_delta_journal()
    assert not p.exists()
    assert SettingsManager(agent_id=m.agent_id).get("core.agent.core-values") == ["good"]
    assert "already been used" in m.apply_identity_delta("core_values", "add", "good", approval_ref=ref)
    history = json.loads((Path(m.palace_path) / "identity_evolution.json").read_text())
    assert len(history) == 1


def test_two_process_distinct_approvals_no_lost_update(env):
    m, approve, config = env
    refs = [approve(m, "core_values", "add", text) for text in ["first", "second"]]
    ctx = mp.get_context("fork")
    barrier = ctx.Barrier(2)
    queue = ctx.Queue()
    def child(text, ref):
        SettingsManager.clear_instances()
        # Deliberately load stale settings BEFORE either process commits.
        SettingsManager(agent_id=m.agent_id)
        barrier.wait(timeout=10)
        local = manager(Path(m.palace_path))
        queue.put(local.apply_identity_delta("core_values", "add", text, approval_ref=ref))
    processes = [ctx.Process(target=child, args=(text, ref)) for text, ref in zip(["first", "second"], refs)]
    for process in processes: process.start()
    for process in processes:
        process.join(timeout=15)
        if process.is_alive(): process.terminate(); pytest.fail("Identity transaction deadlocked")
        assert process.exitcode == 0
    responses = [queue.get(timeout=3) for _ in processes]
    assert all("Successfully applied" in response for response in responses)
    SettingsManager.clear_instances()
    assert set(SettingsManager(agent_id=m.agent_id).get("core.agent.core-values")) == {"first", "second"}


def test_two_process_same_reference_single_use(env):
    m, approve, config = env
    ref = approve(m, "core_values", "add", "once")
    ctx = mp.get_context("fork"); barrier = ctx.Barrier(2); queue = ctx.Queue()
    def child():
        barrier.wait(timeout=10)
        queue.put(manager(Path(m.palace_path)).apply_identity_delta("core_values", "add", "once", approval_ref=ref))
    processes = [ctx.Process(target=child) for _ in range(2)]
    for process in processes: process.start()
    for process in processes:
        process.join(timeout=15)
        if process.is_alive(): process.terminate(); pytest.fail("Identity transaction deadlocked")
        assert process.exitcode == 0
    responses = [queue.get(timeout=3) for _ in processes]
    assert sum("Successfully applied" in response for response in responses) == 1
    assert sum("already been used" in response for response in responses) == 1


def test_missing_trust_key_rejects_signed_import(env,monkeypatch,tmp_path):
    m,approve,config=env
    from core import identity_authorisation as auth
    monkeypatch.setattr(auth,"TRUST_KEY",tmp_path/"not_provisioned.pem")
    with pytest.raises(PermissionError):approve(m,"core_values","add","good")


def test_corrupt_settings_do_not_commit_defaults(env):
    m,approve,config=env;ref=approve(m,"core_values","add","good")
    original=b"{ invalid settings"
    (config/"settings.json").write_bytes(original)
    result=m.apply_identity_delta("core_values","add","good",approval_ref=ref)
    assert result.startswith("Error:")
    assert (config/"settings.json").read_bytes()==original
    ledger=json.loads((Path(m.palace_path)/"identity_approvals.json").read_text())
    assert not ledger[0]["used"]


def test_completed_reference_cannot_replay_via_recreated_journal(env):
    m,approve,config=env;ref=approve(m,"core_values","add","good")
    assert "Successfully applied" in m.apply_identity_delta("core_values","add","good",approval_ref=ref)
    settings=SettingsManager(agent_id=m.agent_id);settings.set("core.agent.core-values",[]);settings.save()
    path=Path(m.palace_path)/"identity_delta_journal.json"
    path.write_text(json.dumps(dict(approval_ref=ref,target_section="core_values",delta_type="add",
                                    content="good",before_items=[],after_items=["good"])))
    with pytest.raises(PermissionError):m._recover_identity_delta_journal()
    assert path.exists()
    assert SettingsManager(agent_id=m.agent_id).get("core.agent.core-values")==[]
