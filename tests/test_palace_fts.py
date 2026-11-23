import os
import sys
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from config import paths
from core.mempalace_manager import MemPalaceManager
from core.palace_fts import (
    PalaceFTSIndex,
    build_safe_fts5_query,
    extract_query_centered_snippet,
    fuse_rrf,
)
from core.settings_manager import SettingsManager


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    app_data = tmp_path / "app_data"
    app_data.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(paths, "get_app_data_dir", lambda: str(app_data))
    monkeypatch.setattr(paths, "get_config_file", lambda: str(app_data / "config.json"))
    monkeypatch.setattr(
        paths,
        "get_agent_data_dir",
        lambda agent_id="default": str(app_data / "agents" / (agent_id or "default")),
    )
    monkeypatch.setattr(
        paths,
        "get_base_dir_for",
        lambda agent_id=None: str(app_data / "agents" / agent_id)
        if agent_id
        else str(app_data),
    )
    monkeypatch.setattr(
        paths,
        "get_mempalace_dir",
        lambda agent_id=None: str(app_data / "agents" / (agent_id or "default") / "mempalace"),
    )
    SettingsManager._instances.clear()
    yield app_data
    SettingsManager._instances.clear()


def test_safe_fts5_query_sanitization():
    q = build_safe_fts5_query('What is my "previous" job? (marketing-specialist: NOT OR NEAR/3 *)')
    assert '"marketing"' in q and '"specialist"' in q
    assert "NEAR" not in q and "*" not in q and "(" not in q


def test_fts_crud_and_filters(tmp_path):
    idx = PalaceFTSIndex(str(tmp_path))
    assert idx.available
    idx.upsert_drawer(
        "d1",
        "[Session 027 | Date: 2023/04/12]\nUser: Before this role I worked as a marketing specialist at a small startup.",
        wing="default",
        room="events",
        source_file="session_027",
    )
    idx.upsert_drawer(
        "d2",
        "[Session 034 | Date: 2023/03/31]\n" + ("filler chatter " * 40) + "We saw a bluegrass band that features a banjo player.",
        wing="default",
        room="events",
        source_file="session_034",
    )
    idx.upsert_drawer("d3", "Person: Andrew\nRelation: Partner", wing="sanctuary", room="people")

    assert idx.count() == 3
    hits = idx.search("What was my previous occupation as a marketing specialist?")
    assert hits and hits[0]["drawer_id"] == "d1"

    sanct = idx.search("Andrew partner", wing="sanctuary", room="people")
    assert len(sanct) == 1 and sanct[0]["drawer_id"] == "d3"

    # Update idempotency (no duplicate rows)
    idx.upsert_drawer("d1", "Updated content about beekeeping", wing="default", room="events")
    assert idx.count() == 3
    assert idx.search("marketing specialist") == []
    assert idx.search("beekeeping")[0]["drawer_id"] == "d1"

    # Delete
    idx.delete_drawer("d1")
    assert idx.count() == 2


def test_query_centered_snippet_finds_buried_fact():
    long_doc = (
        "[Session 034 | Date: 2023/03/31 | Part 1/1]\n"
        + ("We talked about weekend errands and weather. " * 15)
        + "Then we saw a bluegrass band that features a banjo player at the park."
    )
    naive = long_doc[:297]
    assert "banjo" not in naive
    snip = extract_query_centered_snippet(long_doc, "What instrument was in the bluegrass band?", max_len=300)
    assert snip.startswith("[Session 034 | Date: 2023/03/31 | Part 1/1]")
    assert "bluegrass" in snip and "banjo" in snip
    assert len(snip) <= 300


def test_weighted_rrf_fusion():
    dense = ["a", "b", "c"]
    bm25 = ["d", "b", "a"]
    fused = fuse_rrf(dense, bm25, w_dense=1.0, w_bm25=0.6)
    ids = [x[0] for x in fused]
    assert set(ids[:2]) == {"a", "b"}
    assert "d" in ids


@pytest.mark.core
def test_fts_lazy_backfill_and_corrupt_recovery(isolated_env):
    agent_id = "agent_fts_recovery"
    mp = MemPalaceManager(agent_id=agent_id)
    assert mp.fts_index is not None and mp.fts_index.available

    # Multi-chunk drawer (>1000 chars) with a buried exact phrase in chunk 2
    buried = (
        "[Session 088 | Date: 2026/05/01]\n"
        + ("General gardening notes and composting temperatures. " * 24)
        + "Rare heirloom saffron crocus bulbs planted near the limestone wall."
    )
    add_res = mp.add_memory(buried, wing="default", room="events", source_file="session_088")
    assert add_res["chunks"] >= 2
    lid = add_res["drawer_id"]

    # 1. Delete palace_fts.db to simulate missing sidecar / upgrade from v0.3.9
    fts_db = os.path.join(mp.palace_path, "palace_fts.db")
    for suffix in ("", "-wal", "-shm"):
        p = fts_db + suffix
        if os.path.exists(p):
            os.remove(p)

    mp2 = MemPalaceManager(agent_id=agent_id)
    res = mp2.search_records("saffron crocus bulbs limestone", limit=5)
    assert res.success and res.hits
    hit = next(h for h in res.hits if h.logical_id == lid)
    assert "saffron crocus" in hit.preview.lower()
    assert hit.preview.startswith("[Session 088 | Date: 2026/05/01]")
    assert len(hit.preview) <= 450

    # 2. Startup corrupt palace_fts.db -> quarantined & rebuilt cleanly
    with open(fts_db, "wb") as f:
        f.write(b"CORRUPT_HEADER_BYTES_STARTUP")

    mp3 = MemPalaceManager(agent_id=agent_id)
    quarantined_startup = [fn for fn in os.listdir(mp3.palace_path) if fn.startswith("palace_fts.db.corrupt.")]
    assert len(quarantined_startup) >= 1

    res2 = mp3.search_records("saffron crocus bulbs", limit=5)
    assert res2.success and any(h.logical_id == lid for h in res2.hits)

    # 3. Mid-session corruption -> self-heals on search_records and rebuilds automatically
    with open(fts_db, "wb") as f:
        f.write(b"CORRUPT_HEADER_BYTES_MIDSESSION")

    res3 = mp3.search_records("limestone wall saffron", limit=5)
    assert res3.success and any(h.logical_id == lid for h in res3.hits)
    quarantined_mid = [fn for fn in os.listdir(mp3.palace_path) if fn.startswith("palace_fts.db.corrupt.")]
    assert len(quarantined_mid) >= 2


@pytest.mark.core
def test_search_records_bm25_only_hit_formatting(isolated_env):
    agent_id = "agent_bm25_fmt"
    mp = MemPalaceManager(agent_id=agent_id)
    add_res = mp.add_memory(
        "[Session 012 | Date: 2026/02/10]\nExact token ZYZZYX-9941 recorded in laboratory notebook.",
        wing="default",
        room="notes",
        source_file="session_012",
    )
    did = add_res["drawer_id"]

    # Force Chroma dense query to return empty so hit comes strictly from FTS5 BM25
    fake_col = MagicMock()
    fake_col.count.return_value = 1
    fake_col.query.return_value = {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

    with patch.object(mp, "_get_collection", return_value=fake_col):
        res = mp.search_records("ZYZZYX-9941", limit=5)
        assert res.success and res.status == "ok"
        assert len(res.hits) == 1
        assert res.hits[0].logical_id == did
        assert res.hits[0].score is None
        assert res.hits[0].metric == "bm25"

        formatted = mp.search("ZYZZYX-9941", n_results=5)
        assert f"[Drawer #{did}]" in formatted
        assert "sim=N/A" in formatted
        assert "kw" in formatted

    # Verify ablation toggles hybrid_search=False and snippet_query_centered=False
    settings = SettingsManager(agent_id=agent_id)
    settings.set("core.mempalace.hybrid_search", False)
    settings.set("core.mempalace.snippet_query_centered", False)
    settings.save()
    mp.reload_settings()

    long_doc = (
        "[Session 099]\n"
        + ("Filler sentence about routine daily weather. " * 12)
        + "Special marker QUARTZ-ALPHA-77."
    )
    q_res = mp.add_memory(long_doc, wing="default", room="notes", source_file="session_099")
    qid = q_res["drawer_id"]
    baseline_res = mp.search_records("QUARTZ-ALPHA-77", limit=5)
    hit = next(h for h in baseline_res.hits if h.logical_id == qid)
    assert len(hit.preview) <= 300
    assert "QUARTZ-ALPHA-77" not in hit.preview  # naive [:297] misses buried tail when toggle is off


@pytest.mark.core
def test_initialize_sanctuary_clears_turn_touched(isolated_env):
    mp = MemPalaceManager(agent_id="agent_sanct_clean")
    assert mp._turn_touched == []

    # First user-turn memory should NOT wire to sanctuary seed drawers
    add_res = mp.add_memory("First session user note.", wing="default", room="events", source_file="session_001")
    mp.flush_turn_coactivation()
    assert mp._turn_touched == []

    with mp.morpho_layer._get_conn() as conn:
        edges = conn.execute(
            "SELECT * FROM morpho_edges WHERE source_id = ? OR target_id = ?",
            (add_res["drawer_id"], add_res["drawer_id"]),
        ).fetchall()
        assert edges == []
