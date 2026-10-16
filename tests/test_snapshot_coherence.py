import json
import threading
import zipfile
from pathlib import Path
import pytest
from core.backup_manager import create_agent_backup, verify_snapshot_restore
from core.file_utils import atomic_json_write
from core.mempalace_manager import MemPalaceManager as M
from core.settings_manager import SettingsManager
from core.state_barrier import state_barrier
from core.uid_generator import generate_agent_uid

@pytest.fixture
def state(tmp_path, monkeypatch):
    agent=tmp_path/"agent";agent.mkdir()
    monkeypatch.setattr("config.paths.get_agent_data_dir",lambda *a:str(agent))
    monkeypatch.setattr("config.paths.get_base_dir_for",lambda *a:str(agent))
    monkeypatch.setattr("config.paths.get_mempalace_dir",lambda *a:str(agent/"mempalace"))
    SettingsManager.clear_instances();sm=SettingsManager(agent_id="fixture")
    sm.set("core.agent.uid",generate_agent_uid());sm.save()
    manager=M(agent_id="fixture",palace_path=str(agent/"mempalace"))
    return agent,manager,tmp_path/"backups"


def test_snapshot_is_coherent_under_memory_writer(state):
    agent,manager,dest=state
    done=threading.Event();errors=[]
    def writer():
        try:
            for i in range(12):
                with state_barrier(str(agent)):
                    result=manager.add_memory(f"coherent drawer {i}")
                    if "Failed" in str(result):raise RuntimeError(result)
                    atomic_json_write(str(agent/"logical_state.json"),{"iteration":i})
        except Exception as exc:errors.append(exc)
        finally:done.set()
    thread=threading.Thread(target=writer);thread.start()
    backup=create_agent_backup("fixture",str(dest))
    thread.join(timeout=10)
    assert not thread.is_alive() and not errors
    canary=dest/"canary";res=verify_snapshot_restore(backup,str(canary))
    assert res["valid"],res
    manifest=json.loads((canary/"_snapshot_manifest.json").read_text())
    assert manifest["manifest_version"]==3
    assert "mempalace/short_term_mem.json" in manifest["required_inventory"]
    assert json.loads((canary/"_state_capture.json").read_text())["barrier"]=="cooperative-state-v1"
    if (canary/"logical_state.json").exists():
        iteration=json.loads((canary/"logical_state.json").read_text())["iteration"]
        import sqlite3
        with sqlite3.connect(canary/"mempalace/chroma.sqlite3") as conn:
            count=conn.execute("SELECT count(*) FROM embeddings").fetchone()[0]
        assert count>=iteration+1


def rewrite_archive(source,target,omit=None,extra=None,edit_manifest=None):
    with zipfile.ZipFile(source) as src,zipfile.ZipFile(target,"w") as dst:
        for info in src.infolist():
            if info.filename==omit:continue
            content=src.read(info.filename)
            if info.filename=="_snapshot_manifest.json" and edit_manifest:
                manifest=json.loads(content);edit_manifest(manifest);content=json.dumps(manifest).encode()
            dst.writestr(info,content)
        if extra:dst.writestr(*extra)


def test_each_required_member_missing_fails_closed(state):
    agent,manager,dest=state
    backup=create_agent_backup("fixture",str(dest))
    with zipfile.ZipFile(backup) as archive:manifest=json.loads(archive.read("_snapshot_manifest.json"))
    for i,name in enumerate(manifest["required_inventory"]):
        broken=dest/f"missing_{i}.oaa";rewrite_archive(backup,broken,omit=name)
        assert not verify_snapshot_restore(str(broken))["valid"]
    assert (agent/"settings.json").exists()


def test_unlisted_and_zip_slip_members_rejected(state):
    agent,manager,dest=state;backup=create_agent_backup("fixture",str(dest))
    for i,name in enumerate(["unlisted.txt","../outside.txt"]):
        broken=dest/f"extra_{i}.oaa";rewrite_archive(backup,broken,extra=(name,"payload"))
        assert not verify_snapshot_restore(str(broken))["valid"]
    assert not (dest/"outside.txt").exists()


def test_manifest_cannot_omit_settings_requirement(state):
    agent,manager,dest=state;backup=create_agent_backup("fixture",str(dest));broken=dest/"omit_settings.oaa"
    def edit(m):m["required_inventory"]=[];m["file_hashes"].pop("settings.json")
    rewrite_archive(backup,broken,omit="settings.json",edit_manifest=edit)
    assert not verify_snapshot_restore(str(broken))["valid"]


def test_compression_runs_outside_state_barrier(state,monkeypatch):
    agent,manager,dest=state
    import core.backup_manager as backup_manager
    original=backup_manager._stream_file_to_zip
    checked=[]
    def check(*args,**kwargs):
        if not checked:
            event=threading.Event()
            def writer():
                with state_barrier(str(agent)):event.set()
            thread=threading.Thread(target=writer);thread.start();thread.join(timeout=2)
            assert event.is_set(),"compression incorrectly holds live write barrier"
            checked.append(True)
        return original(*args,**kwargs)
    monkeypatch.setattr(backup_manager,"_stream_file_to_zip",check)
    assert verify_snapshot_restore(create_agent_backup("fixture",str(dest)))["valid"]
    assert checked
