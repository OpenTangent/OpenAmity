import json
import threading
import pytest
from core.mempalace_manager import MemPalaceManager as M
from core.morphological_layer import MorphologicalMemoryLayer as L

class FixedEmbedding:
    def __call__(self, input):
        return [[1., 0.] if "relevant" in text else [0.,1.] for text in input]
    def embed_query(self, input):return self(input)
    def name(self):return "fixture-fixed"
    def get_config(self):return {}
    def is_legacy(self):return True

def test_actual_chroma_outside_prefix_same_id_edit_and_restart(tmp_path, monkeypatch):
    import chromadb
    palace=tmp_path/"palace";palace.mkdir()
    client=chromadb.PersistentClient(path=str(palace))
    col=client.get_or_create_collection("freshness",embedding_function=FixedEmbedding(),metadata={"hnsw:space":"cosine"})
    ids=[f"doc_{i:03d}" for i in range(30)]
    docs=["irrelevant" for _ in ids];docs[28]="relevant A"
    # embedding fixture matches exact prefix, not substring irrelevant
    col.add(ids=ids,documents=docs,embeddings=[[1.,0.] if i==28 else [0.,1.] for i in range(30)])
    layer=L(str(palace));layer.register_drawers_batch([(did,"default","general") for did in ids])
    (tmp_path/"trajectory.json").write_text(json.dumps({"aspirations":{"short_term":[{"id":"g","status":"active","description":"relevant"}]},"tasks":[]}))
    monkeypatch.setattr("config.paths.get_base_dir_for",lambda *a:str(tmp_path))
    def manager():
        m=M.__new__(M);m.agent_id="freshness-fixture";m.palace_path=str(palace)
        m._cache_lock=threading.RLock();m._corpus_revision=1;m._morpho_enabled=True
        m.morpho_layer=layer;m._morpho_settings={"top_k":5};m._get_collection=lambda **kw:col
        return m
    m=manager();m._get_prospective_memory_context();before=m.get_corpus_revision()
    assert "doc_028" in layer.get_setpoint("g")["target_drawers"]
    col.update(ids=["doc_028","doc_029"],documents=["irrelevant A","relevant B"],embeddings=[[0.,1.],[1.,0.]])
    m._get_prospective_memory_context()
    assert m.get_corpus_revision()>before
    assert layer.get_setpoint("g")["target_drawers"]==["doc_029"]
    stable=m.get_corpus_revision();restarted=manager();restarted._get_prospective_memory_context()
    assert restarted.get_corpus_revision()==stable


def test_actual_corrupt_chroma_store_canary_preserved(tmp_path,monkeypatch):
    from core.settings_manager import SettingsManager
    config=tmp_path/"agent";palace=config/"mempalace";palace.mkdir(parents=True)
    monkeypatch.setattr("config.paths.get_base_dir_for",lambda *a:str(config))
    SettingsManager.clear_instances()
    (palace/"chroma.sqlite3").write_bytes(b"not a sqlite database: synthetic canary")
    (palace/"short_term_mem.json").write_text("[]")
    mgr=M(agent_id="corrupt-canary",palace_path=str(palace))
    # Depending on dependency version, opening may be deferred until collection
    # access. It must either quarantine preserving bytes or fail closed.
    try:collection=mgr._get_collection(create=False)
    except Exception:collection=None
    quarantines=list(config.glob("mempalace_quarantined_*"))
    if quarantines:
        assert (quarantines[0]/"chroma.sqlite3").read_bytes().startswith(b"not a sqlite database")
        assert (palace/"short_term_mem.json").read_text()=="[]"
    else:
        assert (palace/"chroma.sqlite3").read_bytes().startswith(b"not a sqlite database")
        assert collection is None
