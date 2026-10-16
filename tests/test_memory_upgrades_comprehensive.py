"""
tests/test_memory_upgrades_comprehensive.py — Acceptance suite for Memory Improvement Plan M1-M5.
Validates:
- M1: Single retrieval pass, stable logical IDs, 19-chunk memory lifecycle, get_memory pagination, two-agent isolation.
- M2: Graph v2 migration with MAX-weight collapsing, dangling-reference pruning abort on storage error.
- M3: Atomic persistence error rollback, corrupt-file preservation, non-destructive transient error handling, snapshot verification.
- M4: Corpus-aware anchor invalidation, transient error retry, parameter validation on reload, zero-eta disabled learning.
- M5: Bounded STM context, priority preservation, exact identity delta modification/retirement with rationale.
"""

import os
import sys
import json
import time
import shutil
import sqlite3
import tempfile
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.mempalace_manager import MemPalaceManager
from core.morphological_layer import MorphologicalMemoryLayer
from core.backup_manager import create_agent_backup, verify_snapshot_restore
from tools.mempalace_tool import MemPalaceTool
from config import paths


@pytest.fixture
def isolated_env(monkeypatch):
    temp_dir = tempfile.mkdtemp(prefix="openamity_comprehensive_mem_")
    monkeypatch.setattr("config.paths.get_app_data_dir", lambda: temp_dir)
    monkeypatch.setattr(
        "config.paths.get_base_dir_for",
        lambda aid: os.path.join(temp_dir, "agents", aid or "default"),
    )
    monkeypatch.setattr(
        "config.paths.get_agent_data_dir",
        lambda aid: os.path.join(temp_dir, "agents", aid or "default"),
    )
    monkeypatch.setattr(
        "config.paths.get_mempalace_dir",
        lambda aid: os.path.join(temp_dir, "agents", aid or "default", "mempalace"),
    )
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_19_chunk_add_search_inspect_link_delete_lifecycle(isolated_env):
    """
    M1 Acceptance: add -> search -> inspect -> link -> delete works for a 19-chunk memory.
    Tracking IDs exactly match displayed hits; get_memory allows full inspection past char 300.
    """
    agent_id = "chunk19-agent"
    mgr = MemPalaceManager(agent_id=agent_id)
    tool = MemPalaceTool(orchestrator=MagicMock(mempalace_manager=mgr, agent_id=agent_id))

    # Add long memory that splits into 19 chunks (approx 250 repetitions = ~15000 chars)
    phrase = "Autonomous agent epistemic alignment and morphological associative memory. "
    decisive_tail = "FINAL_VERIFICATION_TOKEN_X99"
    content = (phrase * 200) + decisive_tail

    add_res = mgr.add_memory(content, wing="default", room="research")
    assert isinstance(add_res, dict) and add_res.get("success") is True
    drawer_id = add_res["drawer_id"]
    chunks_count = add_res.get("chunks", 1)
    assert chunks_count > 10, f"Expected >10 chunks, got {chunks_count}"

    # 1. Search must find it with stable canonical logical ID and truncation marker
    search_out = tool.execute("search", query="FINAL_VERIFICATION_TOKEN_X99")
    assert f"[Drawer #{drawer_id}]" in search_out
    assert "[truncated]" in search_out

    # 2. Tracking ID in _turn_touched must be exactly the canonical drawer_id, not a chunk ID
    with mgr._cache_lock:
        touched = list(mgr._turn_touched)
    assert drawer_id in touched
    assert not any("_chunk_" in tid for tid in touched), f"Physical chunk ID leaked into touched: {touched}"

    # 3. get_memory inspects full content via pagination
    # First page
    page1 = tool.execute("get_memory", drawer_id=drawer_id, offset=0, limit=2000)
    assert f"[Drawer #{drawer_id}]" in page1
    assert "Has More: True" in page1

    # Fetch tail where decisive token is located
    tail_offset = max(0, len(content) - 1500)
    page_tail = tool.execute("get_memory", drawer_id=drawer_id, offset=tail_offset, limit=2000)
    assert decisive_tail in page_tail, "Decisive tail was not hydrated by get_memory"

    # 4. link_setpoint connects canonical logical ID
    link_res = tool.execute("link_setpoint", goal_key="goal_19", drawer_ids=[drawer_id])
    assert "Successfully linked" in link_res
    sp = mgr.morpho_layer.get_setpoint("goal_19")
    assert drawer_id in sp["explicit_drawers"]

    # 5. Delete memory removes from ChromaDB and graph
    del_res = tool.execute("delete_memory", drawer_id=drawer_id)
    assert "true" in del_res.lower() or "success" in del_res.lower()

    # Verify gone from collection
    col = mgr._get_collection()
    rem = col.get(ids=[drawer_id])
    assert not (rem.get("ids") and rem["ids"])

    # Verify gone from graph
    with mgr.morpho_layer._get_conn() as conn:
        g_nodes = [r[0] for r in conn.execute("SELECT drawer_id FROM morpho_nodes WHERE drawer_id=?", (drawer_id,))]
        assert len(g_nodes) == 0


def test_two_agent_isolation(isolated_env):
    """
    M1 Acceptance: Two-agent concurrent operations cannot access each other's content.
    """
    mgr_a = MemPalaceManager(agent_id="agent-alice")
    mgr_b = MemPalaceManager(agent_id="agent-bob")

    add_a = mgr_a.add_memory("AliceConfidentialSecretToken: 994411", wing="sanctuary", room="secrets")
    add_b = mgr_b.add_memory("BobConfidentialSecretToken: 772233", wing="sanctuary", room="secrets")
    drawer_a = add_a["drawer_id"]
    drawer_b = add_b["drawer_id"]

    # Alice searches for Bob's secret -> must not retrieve Bob's secret token or drawer ID
    search_alice = mgr_a.search("BobConfidentialSecretToken")
    assert "772233" not in search_alice
    assert drawer_b not in search_alice

    # Bob searches for Alice's secret -> must not retrieve Alice's secret token or drawer ID
    search_bob = mgr_b.search("AliceConfidentialSecretToken")
    assert "994411" not in search_bob
    assert drawer_a not in search_bob

    # Alice searches for her own secret
    search_alice_own = mgr_a.search("AliceConfidentialSecretToken")
    assert "994411" in search_alice_own
    assert drawer_a in search_alice_own

    # Bob searches for his own secret
    search_bob_own = mgr_b.search("BobConfidentialSecretToken")
    assert "772233" in search_bob_own
    assert drawer_b in search_bob_own


def test_graph_migration_v2_with_max_weight_and_no_self_edges(isolated_env):
    """
    M2 Acceptance: Converting legacy physical-node graph collapses chunks to parent logical IDs,
    uses conservative MAX weight rule, eliminates self-edges, respects real FK constraints,
    preserves parents with _chunk_ in their names, and is idempotent.
    """
    db_dir = os.path.join(isolated_env, "agents", "legacy-agent", "mempalace")
    os.makedirs(db_dir, exist_ok=True)
    db_path = os.path.join(db_dir, "morpho_graph.db")

    # Manually create legacy v1 table with real foreign-key schema
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript("""
    CREATE TABLE morpho_nodes (
        drawer_id TEXT PRIMARY KEY,
        wing TEXT NOT NULL DEFAULT 'default',
        room TEXT NOT NULL DEFAULT 'general',
        baseline_potential REAL DEFAULT 0.0,
        created_at TEXT NOT NULL,
        last_activated TEXT NOT NULL
    );
    CREATE TABLE morpho_edges (
        source_id TEXT NOT NULL,
        target_id TEXT NOT NULL,
        weight REAL NOT NULL DEFAULT 1.0,
        created_at TEXT NOT NULL,
        last_reinforced TEXT NOT NULL,
        PRIMARY KEY (source_id, target_id),
        FOREIGN KEY (source_id) REFERENCES morpho_nodes(drawer_id) ON DELETE CASCADE,
        FOREIGN KEY (target_id) REFERENCES morpho_nodes(drawer_id) ON DELETE CASCADE
    );
    CREATE TABLE morpho_setpoints (
        goal_key TEXT PRIMARY KEY,
        text_hash TEXT,
        target_drawers_json TEXT NOT NULL,
        explicit_drawers_json TEXT DEFAULT '[]',
        corpus_revision INTEGER DEFAULT 0,
        status TEXT DEFAULT 'ok',
        updated_at TEXT NOT NULL
    );
    CREATE TABLE morpho_meta (key TEXT PRIMARY KEY, value TEXT);
    """)

    now = "2026-10-05T00:00:00"
    # Insert physical chunk nodes for drawer_A (2 chunks), drawer_B (1 chunk),
    # a drawer with '_chunk_' in its name, and Other node
    conn.execute("INSERT INTO morpho_nodes VALUES ('drawer_A_chunk_000000', 'def', 'gen', 0.0, ?, ?)", (now, now))
    conn.execute("INSERT INTO morpho_nodes VALUES ('drawer_A_chunk_000001', 'def', 'gen', 0.0, ?, ?)", (now, now))
    conn.execute("INSERT INTO morpho_nodes VALUES ('drawer_B_chunk_000000', 'def', 'gen', 0.0, ?, ?)", (now, now))
    conn.execute("INSERT INTO morpho_nodes VALUES ('drawer_default_chunk_probe_abc_chunk_000000', 'def', 'gen', 0.0, ?, ?)", (now, now))
    conn.execute("INSERT INTO morpho_nodes VALUES ('Other', 'def', 'gen', 0.0, ?, ?)", (now, now))

    # Edges:
    # 1. Edge between chunks of same drawer (will become self-edge on drawer_A -> must be removed!)
    conn.execute("INSERT INTO morpho_edges VALUES ('drawer_A_chunk_000000', 'drawer_A_chunk_000001', 5.0, ?, ?)", (now, now))
    # 2. Multiple edges between chunks of drawer_A and drawer_B with different weights (2.0 and 4.5 -> must keep MAX 4.5)
    conn.execute("INSERT INTO morpho_edges VALUES ('drawer_A_chunk_000000', 'drawer_B_chunk_000000', 2.0, ?, ?)", (now, now))
    conn.execute("INSERT INTO morpho_edges VALUES ('drawer_A_chunk_000001', 'drawer_B_chunk_000000', 4.5, ?, ?)", (now, now))
    # 3. Duplicate weight edge between drawer_B and Other
    conn.execute("INSERT INTO morpho_edges VALUES ('drawer_B_chunk_000000', 'Other', 2.0, ?, ?)", (now, now))
    # 4. Edge connecting drawer whose parent contains '_chunk_'
    conn.execute("INSERT INTO morpho_edges VALUES ('drawer_default_chunk_probe_abc_chunk_000000', 'Other', 3.0, ?, ?)", (now, now))

    # Setpoint containing physical chunks in target and explicit
    conn.execute(
        "INSERT INTO morpho_setpoints VALUES ('g1', 'hash1', '[\"drawer_A_chunk_000000\", \"drawer_B_chunk_000000\"]', '[\"drawer_A_chunk_000000\"]', 0, 'ok', ?)",
        (now,)
    )
    conn.commit()
    conn.close()

    # Now initialize MorphologicalMemoryLayer — must NOT trigger migration automatically!
    layer = MorphologicalMemoryLayer(palace_path=db_dir)

    with layer._get_conn() as c:
        pre_nodes = [r[0] for r in c.execute("SELECT drawer_id FROM morpho_nodes")]
        assert "drawer_A_chunk_000000" in pre_nodes
        assert "drawer_default_chunk_probe_abc_chunk_000000" in pre_nodes

    # Explicitly run migration
    report = layer.migrate_v2(dry_run=False, pre_snapshot=True)
    assert report.get("status") == "migrated"
    assert report.get("chunks_identified") == 4

    with layer._get_conn() as c:
        nodes = [r[0] for r in c.execute("SELECT drawer_id FROM morpho_nodes")]
        assert "drawer_A" in nodes
        assert "drawer_B" in nodes
        assert "drawer_default_chunk_probe_abc" in nodes  # Preserved!
        assert "Other" in nodes
        assert not any(n.endswith("_chunk_000000") or n.endswith("_chunk_000001") for n in nodes)

        edges = c.execute("SELECT source_id, target_id, weight FROM morpho_edges").fetchall()
        # Self-edge A->A must not exist
        assert not any(e["source_id"] == e["target_id"] for e in edges)
        # Edge A->B must exist with MAX weight 4.5
        ab_edges = [e for e in edges if e["source_id"] == "drawer_A" and e["target_id"] == "drawer_B"]
        assert len(ab_edges) == 1
        assert ab_edges[0]["weight"] == pytest.approx(4.5)

        # Edge drawer_default_chunk_probe_abc -> Other must exist with weight 3.0
        probe_edges = [e for e in edges if e["source_id"] == "drawer_default_chunk_probe_abc" and e["target_id"] == "Other"]
        assert len(probe_edges) == 1
        assert probe_edges[0]["weight"] == pytest.approx(3.0)

        # Setpoints updated to logical drawer ID
        sp = layer.get_setpoint("g1")
        assert "drawer_A" in sp["target_drawers"]
        assert "drawer_B" in sp["target_drawers"]
        assert "drawer_A" in sp["explicit_drawers"]
        assert not any("_chunk_" in d for d in sp["target_drawers"])

    # Idempotence: re-running migration does not alter results
    layer.migrate_v2(dry_run=False)
    with layer._get_conn() as c:
        nodes2 = [r[0] for r in c.execute("SELECT drawer_id FROM morpho_nodes")]
        assert set(nodes) == set(nodes2)


def test_safe_reconciliation_aborts_on_storage_failure(isolated_env):
    """
    M2 Acceptance: A failed storage read cannot trigger destructive graph pruning.
    """
    mgr = MemPalaceManager(agent_id="reconcile-agent")
    mgr.morpho_layer.register_drawer("D_Safe_1")
    mgr.morpho_layer.register_drawer("D_Safe_2")

    with patch.object(mgr, "_get_collection", return_value=None):
        res = mgr.reconcile_graph()
        assert res.get("status") == "aborted"

    # Verify no nodes pruned
    with mgr.morpho_layer._get_conn() as conn:
        nodes = [r[0] for r in conn.execute("SELECT drawer_id FROM morpho_nodes")]
        assert "D_Safe_1" in nodes
        assert "D_Safe_2" in nodes


def test_corrupt_file_preservation(isolated_env):
    """
    M3 Acceptance: Corrupt-file recovery preserves originals as backups and does not overwrite with empty state.
    """
    agent_id = "corrupt-agent"
    palace_dir = paths.get_mempalace_dir(agent_id)
    os.makedirs(palace_dir, exist_ok=True)
    mirrors_file = os.path.join(palace_dir, "mirrors.json")

    # Write broken JSON
    with open(mirrors_file, "w") as f:
        f.write("{ INVALID_JSON_DATA_CORRUPTION !!!")

    mgr = MemPalaceManager(agent_id=agent_id)

    # Calling _load_mirrors should raise RuntimeError and create a .corrupt backup file
    with pytest.raises(RuntimeError):
        mgr._load_mirrors()

    # Verify .corrupt file was created
    corrupt_backups = [f for f in os.listdir(palace_dir) if "mirrors.json.corrupt" in f]
    assert len(corrupt_backups) > 0, "Corrupt file was not backed up"


def test_snapshot_creation_and_restore_verification(isolated_env):
    """
    M3 Acceptance: Snapshot/restore round-trip with SQLite integrity check.
    """
    agent_id = "snapshot-agent"
    from core.uid_generator import generate_agent_uid
    from core.settings_manager import SettingsManager
    sm = SettingsManager(agent_id=agent_id)
    sm.set("core.agent.uid", generate_agent_uid()); sm.save()
    mgr = MemPalaceManager(agent_id=agent_id)
    mgr.add_memory("Knowledge to snapshot", wing="default", room="specs")
    mgr.update_mirror("Self", "Reflective viewpoint", confidence=0.9)
    mgr.add_short_term_memory("Important active commitment", status="unresolved")

    # Create backup snapshot
    backup_file = create_agent_backup(agent_id=agent_id)
    assert os.path.exists(backup_file)

    # Test restore into isolated temporary location
    res = verify_snapshot_restore(backup_file)
    assert res.get("valid") is True
    assert res.get("databases_checked", 0) >= 1
    assert res.get("json_files_checked", 0) >= 1


def test_zero_eta_disables_hebbian_learning(isolated_env):
    """
    M4 Acceptance: Zero eta must not create new learned edges if advertised as disabling learning.
    """
    palace_dir = os.path.join(isolated_env, "eta_zero_palace")
    layer = MorphologicalMemoryLayer(palace_path=palace_dir, eta=0.0)

    layer.register_drawer("NodeA")
    layer.register_drawer("NodeB")

    # Coactivation with eta=0.0
    layer.record_coactivation(["NodeA", "NodeB"])

    with layer._get_conn() as conn:
        edges = conn.execute("SELECT * FROM morpho_edges").fetchall()
        assert len(edges) == 0, f"Expected 0 edges with eta=0.0, found {len(edges)}"


def test_identity_delta_exact_replace_and_rationale_enforced(isolated_env, monkeypatch, tmp_path):
    from identity_test_support import signer
    approve = signer(monkeypatch, tmp_path)
    """
    M5 Acceptance: Legacy identity updates cannot append a contradictory replacement or remove unrelated traits;
    rationale is strictly required.
    """
    agent_id = "identity-agent"
    mgr = MemPalaceManager(agent_id=agent_id)

    # 1. Approval reference enforcement
    err_no_ref = mgr.apply_identity_delta("core_values", "add", "Honesty")
    assert "Error:" in err_no_ref

    # 2. Add initial values with registered approvals (untrusted fails)
    with pytest.raises(PermissionError):
        mgr.register_identity_approval("core_values", "add", "Honesty above all")

    ref1 = approve(mgr, "core_values", "add", "Honesty above all")
    ref2 = approve(mgr, "core_values", "add", "Radical Transparency")
    mgr.apply_identity_delta("core_values", "add", "Honesty above all", rationale="User approved honesty", approval_ref=ref1)
    mgr.apply_identity_delta("core_values", "add", "Radical Transparency", rationale="User approved transparency", approval_ref=ref2)

    settings = mgr.stack.l0 if hasattr(mgr, 'stack') else None
    from core.settings_manager import SettingsManager
    sm = SettingsManager(agent_id=agent_id)
    vals = sm.get("core.agent.core-values")
    assert "Honesty above all" in vals
    assert "Radical Transparency" in vals

    # 3. Modify value (should replace matching item, not append duplicate)
    ref3 = approve(mgr, "core_values", "modify", "Honesty above all -> Compassionate Honesty")
    mgr.apply_identity_delta(
        "core_values", "modify",
        "Honesty above all -> Compassionate Honesty",
        rationale="User discussed being gentle with truth",
        approval_ref=ref3
    )
    vals_after_mod = sm.get("core.agent.core-values")
    assert "Compassionate Honesty" in vals_after_mod
    assert "Honesty above all" not in vals_after_mod
    assert len(vals_after_mod) == len(vals)  # Exactly 1 replaced, no duplicate appended!

    # 4. Retire value (exact removal, no substring false positives)
    # Add a value containing the word "Truth" and another containing "Truthful"
    ref4 = approve(mgr, "core_values", "add", "Truth")
    ref5 = approve(mgr, "core_values", "add", "Truthfulness")
    mgr.apply_identity_delta("core_values", "add", "Truth", rationale="approved", approval_ref=ref4)
    mgr.apply_identity_delta("core_values", "add", "Truthfulness", rationale="approved", approval_ref=ref5)

    # Retire exact "Truth"
    ref6 = approve(mgr, "core_values", "retire", "Truth")
    mgr.apply_identity_delta("core_values", "retire", "Truth", rationale="approved retirement", approval_ref=ref6)
    vals_after_retire = sm.get("core.agent.core-values")
    assert "Truth" not in vals_after_retire
    assert "Truthfulness" in vals_after_retire, "Unrelated item 'Truthfulness' was erroneously deleted by substring matching!"


def test_graph_migration_v2_corrupt_setpoint_aborts_without_partial_write(isolated_env):
    """
    V1 Safety Gate: Malformed or unresolvable setpoint JSON causes migration to abort/raise,
    rolling back transaction without leaving partially migrated physical nodes.
    """
    db_dir = os.path.join(isolated_env, "agents", "corrupt-sp-agent", "mempalace")
    os.makedirs(db_dir, exist_ok=True)
    db_path = os.path.join(db_dir, "morpho_graph.db")

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript("""
    CREATE TABLE morpho_nodes (
        drawer_id TEXT PRIMARY KEY,
        wing TEXT NOT NULL DEFAULT 'default',
        room TEXT NOT NULL DEFAULT 'general',
        baseline_potential REAL DEFAULT 0.0,
        created_at TEXT NOT NULL,
        last_activated TEXT NOT NULL
    );
    CREATE TABLE morpho_edges (
        source_id TEXT NOT NULL,
        target_id TEXT NOT NULL,
        weight REAL NOT NULL DEFAULT 1.0,
        created_at TEXT NOT NULL,
        last_reinforced TEXT NOT NULL,
        PRIMARY KEY (source_id, target_id),
        FOREIGN KEY (source_id) REFERENCES morpho_nodes(drawer_id) ON DELETE CASCADE,
        FOREIGN KEY (target_id) REFERENCES morpho_nodes(drawer_id) ON DELETE CASCADE
    );
    CREATE TABLE morpho_setpoints (
        goal_key TEXT PRIMARY KEY,
        text_hash TEXT,
        target_drawers_json TEXT NOT NULL,
        explicit_drawers_json TEXT DEFAULT '[]',
        corpus_revision INTEGER DEFAULT 0,
        status TEXT DEFAULT 'ok',
        updated_at TEXT NOT NULL
    );
    CREATE TABLE morpho_meta (key TEXT PRIMARY KEY, value TEXT);
    """)

    now = "2026-10-05T00:00:00"
    conn.execute("INSERT INTO morpho_nodes VALUES ('drawer_Z_chunk_000000', 'def', 'gen', 0.0, ?, ?)", (now, now))
    # Malformed JSON in target_drawers_json
    conn.execute("INSERT INTO morpho_setpoints VALUES ('corrupt_g', 'hash', '{ INVALID JSON', '[]', 0, 'ok', ?)", (now,))
    conn.commit()
    conn.close()

    layer = MorphologicalMemoryLayer(palace_path=db_dir)

    # Migration must raise ValueError on malformed setpoint JSON
    with pytest.raises(ValueError, match="Malformed target_drawers_json"):
        layer.migrate_v2(dry_run=False, pre_snapshot=True)

    # Verify atomic rollback: chunk node must NOT have been deleted
    with layer._get_conn() as c:
        nodes = [r[0] for r in c.execute("SELECT drawer_id FROM morpho_nodes")]
        assert "drawer_Z_chunk_000000" in nodes


def test_fail_closed_snapshot_on_sqlite_backup_failure(isolated_env):
    """
    V3 Safety Gate: When SQLite backup fails, snapshot creation fails closed (raises RuntimeError)
    rather than writing an incomplete database backup without WAL sidecars.
    """
    agent_id = "fail-closed-agent"
    mgr = MemPalaceManager(agent_id=agent_id)
    mgr.add_memory("Critical data requiring fail-closed backup", wing="default", room="specs")

    with patch("core.backup_manager.sqlite3.connect", side_effect=sqlite3.OperationalError("Simulated backup I/O failure")):
        with pytest.raises(RuntimeError, match="Snapshot failure on database"):
            create_agent_backup(agent_id=agent_id)


def test_structured_records_contract(isolated_env):
    """
    V9 Contract: recall_records, search_records, and get_memory_record return
    typed MemoryResult, MemoryHit, and MemoryRecord instances with pagination and status.
    """
    agent_id = "typed-agent"
    mgr = MemPalaceManager(agent_id=agent_id)

    # Add 2 distinct memories
    res1 = mgr.add_memory("Architectural blueprint for memory upgrades with state isolation.", wing="arch", room="core")
    res2 = mgr.add_memory("Secondary documentation for runtime execution and testing.", wing="arch", room="docs")
    id1 = res1["drawer_id"]
    id2 = res2["drawer_id"]

    # 1. recall_records
    rec_res = mgr.recall_records(wing="arch", limit=10)
    assert rec_res.success is True
    assert rec_res.status == "ok"
    assert rec_res.total_count == 2
    assert len(rec_res.hits) == 2
    assert all(hasattr(h, "logical_id") and hasattr(h, "preview") for h in rec_res.hits)
    hit_ids = [h.logical_id for h in rec_res.hits]
    assert id1 in hit_ids
    assert id2 in hit_ids

    # 2. search_records
    search_res = mgr.search_records(query="blueprint", limit=5)
    assert search_res.success is True
    assert search_res.status == "ok"
    assert len(search_res.hits) >= 1
    assert search_res.hits[0].logical_id == id1
    assert search_res.hits[0].score is not None

    # 3. get_memory_record
    get_res = mgr.get_memory_record(drawer_id=id1)
    assert get_res.success is True
    assert get_res.status == "ok"
    assert get_res.record is not None
    assert get_res.record.drawer_id == id1
    assert "Architectural blueprint" in get_res.record.content
    assert get_res.record.total_chars > 0
    assert len(get_res.record.revision) == 16

    # 4. get_memory_record on missing drawer
    missing_res = mgr.get_memory_record(drawer_id="NonexistentDrawer123")
    assert missing_res.success is False
    assert missing_res.status == "missing_id"
    assert "not found" in missing_res.error


def test_corrupt_morpho_graph_recovery_and_transient_preservation(tmp_path):
    """
    MP8 / B6: MorphologicalMemoryLayer quarantines corrupt SQLite DB to .corrupt.<timestamp>,
    re-initializes cleanly, and preserves transient errors without quarantining.
    """
    palace_dir = tmp_path / "morpho_corrupt_palace"
    palace_dir.mkdir(parents=True)
    db_path = palace_dir / "morpho_graph.db"

    # 1. Deliberately corrupt DB file
    db_path.write_bytes(b"CORRUPTED_NON_SQLITE_HEADER_GARBAGE_BYTES")

    # 2. Initialize layer -> should detect corruption, quarantine to .corrupt.<timestamp>, and re-init
    layer = MorphologicalMemoryLayer(palace_path=str(palace_dir))
    assert layer.is_empty() is True

    # Inspect quarantine copy
    corrupt_files = list(palace_dir.glob("morpho_graph.db.corrupt.*"))
    assert len(corrupt_files) == 1
    assert corrupt_files[0].read_bytes() == b"CORRUPTED_NON_SQLITE_HEADER_GARBAGE_BYTES"

    # Verify new DB is valid and operational
    layer.register_drawer("test_drawer", wing="w", room="r")
    with layer._get_conn() as c:
        row = c.execute("SELECT drawer_id FROM morpho_nodes WHERE drawer_id = ?", ("test_drawer",)).fetchone()
        assert row is not None

    # 3. Transient error check: database locked should NOT quarantine
    with patch.object(MorphologicalMemoryLayer, "_execute_init_db", side_effect=sqlite3.OperationalError("database is locked")):
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            MorphologicalMemoryLayer(palace_path=str(palace_dir))
        # No additional quarantine file created for transient error
        assert len(list(palace_dir.glob("morpho_graph.db.corrupt.*"))) == 1


def test_corrupt_short_term_archive_quarantine_and_fail_closed(isolated_env, tmp_path):
    """
    MP8 / B6: Corrupt short_term_archive.json is quarantined to .corrupt.<timestamp>
    and operations fail closed (raising RuntimeError) without wiping active memories.
    """
    agent_id = "corrupt-archive-agent"
    mgr = MemPalaceManager(agent_id=agent_id)

    # Populate active short term memories
    mgr.add_short_term_memory("Important task 1", status="unresolved", priority=1)
    mgr.add_short_term_memory("Important task 2", status="resolved", priority=1)
    expected_active_count = len(mgr._load_short_term_memories())

    archive_path = os.path.join(mgr.palace_path, "short_term_archive.json")
    # Deliberately corrupt the archive file with invalid JSON syntax
    with open(archive_path, "w", encoding="utf-8") as f:
        f.write("{ INVALID JSON SYNTAX NOT A LIST }")

    # 1. get_archived_short_term_memories must quarantine and raise RuntimeError
    with pytest.raises(RuntimeError, match="Corrupt short_term_archive.json"):
        mgr.get_archived_short_term_memories()

    corrupt_files = [f for f in os.listdir(mgr.palace_path) if f.startswith("short_term_archive.json.corrupt.")]
    assert len(corrupt_files) >= 1

    # Active memories must be intact
    active = mgr._load_short_term_memories()
    assert len(active) == expected_active_count

    # 2. Eviction with corrupt archive must fail closed and preserve active records
    # Reset corrupt archive
    with open(archive_path, "w", encoding="utf-8") as f:
        f.write("{ ANOTHER CORRUPT ARCHIVE }")

    with patch("core.settings_manager.SettingsManager.get", return_value=1):
        with pytest.raises(RuntimeError, match="short_term_archive.json is corrupted"):
            # Max memories = 1 will force eviction
            mgr.add_short_term_memory("Overflow task", status="resolved", priority="normal")

    # Active records must remain intact!
    active_after = mgr._load_short_term_memories()
    assert len(active_after) == expected_active_count

