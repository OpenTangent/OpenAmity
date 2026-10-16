"""
tests/test_memory_regressions.py — M0 baseline failing regression tests for MemPalace memory audit.
Validates defects identified in MemoryAudit2026-10-05 before M1-M5 fixes:
1. Long chunked memories cause unregistered physical chunk IDs and foreign-key failures during coactivation.
2. Injected disk write failures falsely acknowledge success while updating in-memory cache.
3. Goal anchor caching treats empty results and transient query errors as permanent for unchanged goal text.
4. Enabled morphological engine settings reload leaves numeric parameters un-updated.
5. Search/recall truncates long content past 300 chars without exposing a full-content retrieval interface.
"""

import os
import sys
import json
import shutil
import tempfile
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.mempalace_manager import MemPalaceManager
from core.morphological_layer import MorphologicalMemoryLayer
from tools.mempalace_tool import MemPalaceTool


@pytest.fixture
def temp_memory_env(monkeypatch):
    temp_dir = tempfile.mkdtemp(prefix="openamity_regression_test_")
    monkeypatch.setattr("config.paths.get_app_data_dir", lambda: temp_dir)
    monkeypatch.setattr(
        "config.paths.get_base_dir_for",
        lambda aid: os.path.join(temp_dir, "agents", aid or "default"),
    )
    monkeypatch.setattr(
        "config.paths.get_mempalace_dir",
        lambda aid: os.path.join(temp_dir, "agents", aid or "default", "mempalace"),
    )
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_chunk_coactivation_and_id_alignment(temp_memory_env):
    """
    Audit Finding 1: MemPalace chunks long content (>1000 chars), but Open Amity
    registered only the logical group ID in morpho_nodes while search/collection returns physical chunk IDs.
    Co-activating physical chunk IDs causes SQLite foreign key failures.
    """
    agent_id = "chunk-agent"
    mgr = MemPalaceManager(agent_id=agent_id)
    # Add long memory that splits into multiple chunks
    long_content = "Chunk boundary assessment and memory retrieval integrity. " * 50
    res = mgr.add_memory(long_content, wing="default", room="chunk_probe")
    assert isinstance(res, dict) and res.get("success") is True
    assert res.get("chunks", 1) > 1

    col = mgr._get_collection()
    data = col.get(where={"room": "chunk_probe"}, include=["metadatas"])
    physical_ids = data["ids"]
    assert len(physical_ids) > 1

    with mgr.morpho_layer._get_conn() as conn:
        graph_ids = [r[0] for r in conn.execute("SELECT drawer_id FROM morpho_nodes WHERE room='chunk_probe'")]

    # In baseline: physical chunk IDs were NOT registered in morpho_nodes, so record_coactivation fails
    # Post-fix: either all physical IDs map to canonical logical IDs, or coactivation works cleanly without FK failure
    # Here we test that touching drawers with physical chunk IDs (e.g. from search) does not crash or violate FK.
    try:
        mgr.morpho_layer.record_coactivation(physical_ids[:2])
    except sqlite3.IntegrityError:
        # Expected failure on unpatched baseline
        pytest.fail("Baseline reproduced: IntegrityError foreign key constraint failed on physical chunk coactivation")


def test_persistence_failure_propagation_and_cache(temp_memory_env):
    """
    Audit Finding 4: Injected disk failure in atomic_json_write swallowed exceptions,
    falsely returning success and corrupting in-memory cache with unpersisted records.
    """
    mgr = MemPalaceManager(agent_id="persist-agent")
    tool = MemPalaceTool.__new__(MemPalaceTool)
    tool.manager = mgr
    tool.orchestrator = None

    with patch("core.mempalace_manager.atomic_json_write", side_effect=OSError("synthetic disk full")):
        st_res = tool.execute("add_short_term", content="not durable")
        mirror_res = tool.execute("update_mirror", perspective="Synthetic", subjective_view="not durable")

    # In baseline: st_res was "Successfully added to short-term memory."
    # Post-fix: Must report error, and cache must NOT retain the unpersisted data!
    assert "error" in st_res.lower() or "failed" in st_res.lower(), f"Expected error but got {st_res}"
    assert "error" in mirror_res.lower() or "failed" in mirror_res.lower(), f"Expected error but got {mirror_res}"


def test_anchor_caching_and_corpus_invalidation(temp_memory_env):
    """
    Audit Finding 3: Empty results and transient query errors were cached permanently
    by goal text hash. Adding matching memories did not invalidate the anchor.
    """
    base_dir = os.path.join(temp_memory_env, "agents", "anchor-agent")
    os.makedirs(base_dir, exist_ok=True)
    traj_path = os.path.join(base_dir, "trajectory.json")
    with open(traj_path, "w") as f:
        json.dump({"aspirations": {"short_term": [{"id": "g1", "status": "active", "description": "find project specs"}]}, "tasks": []}, f)

    mgr = MemPalaceManager(agent_id="anchor-agent")
    # First wake/prospective call with empty palace
    mgr._get_prospective_memory_context()

    # Now add matching memory
    mgr.add_memory("Detailed project specs and architecture documentation for the team.", wing="default", room="general")

    # Second prospective call: baseline never re-queried because goal text hash didn't change!
    # Post-fix: Corpus revision increment should invalidate the cached empty anchor and discover matching memory.
    mgr._get_prospective_memory_context()
    setpoint = mgr.morpho_layer.get_setpoint("g1")
    assert setpoint is not None
    # On baseline this would remain empty []
    assert len(setpoint.get("target_drawers", [])) > 0, "Anchor was not refreshed after corpus was updated"


def test_enabled_settings_reload_applies_parameters(temp_memory_env):
    """
    Audit Finding: Changing morphological parameters in settings and calling reload_settings()
    leaves an existing enabled engine's numeric parameters unchanged.
    """
    mgr = MemPalaceManager(agent_id="reload-agent")
    assert mgr.morpho_layer is not None
    initial_gamma = mgr.morpho_layer.gamma

    # Simulate settings update
    new_gamma = 0.25
    new_decay = 0.015
    with patch.object(mgr, "_load_morpho_settings") as mock_load:
        def apply_mock():
            mgr._morpho_enabled = True
            mgr._morpho_settings = {"gamma": new_gamma, "decay_per_minute": new_decay}
        mock_load.side_effect = apply_mock
        mgr.reload_settings()

    assert mgr.morpho_layer.gamma == pytest.approx(new_gamma), f"Expected gamma {new_gamma}, got {mgr.morpho_layer.gamma}"
    assert mgr.morpho_layer.decay_rate == pytest.approx(new_decay), f"Expected decay_rate {new_decay}, got {mgr.morpho_layer.decay_rate}"


def test_full_content_inspection_and_get_memory(temp_memory_env):
    """
    Audit Finding 2: Search/recall truncates content to 300 characters.
    There was no get_memory tool to retrieve the complete memory or paginate through it.
    """
    mgr = MemPalaceManager(agent_id="inspect-agent")
    tool = MemPalaceTool(orchestrator=MagicMock(mempalace_manager=mgr, agent_id="inspect-agent"))

    # Add memory whose critical correction is beyond char 300
    prefix = "A" * 350
    decisive_correction = "CRITICAL_CORRECTION: The server port is 9999 not 8080."
    full_text = prefix + " " + decisive_correction
    res = mgr.add_memory(full_text, wing="office", room="specs")
    drawer_id = res["drawer_id"] if isinstance(res, dict) else None
    assert drawer_id is not None

    # Search returns preview truncated
    search_out = tool.execute("search", query="server port")
    assert "[truncated]" in search_out.lower() or "..." in search_out

    # MemPalace_get_memory must exist and allow inspecting the full content
    get_res = tool.execute("get_memory", drawer_id=drawer_id)
    assert decisive_correction in get_res, "Decisive correction past char 300 was not retrieved by get_memory"


def test_r1_protected_l0_identity_preserved(temp_memory_env):
    """
    R1 Regression: Never truncate Layer 0 Identity in wake_up.
    Sentinel at the end of large identity must be preserved in full.
    """
    mgr = MemPalaceManager(agent_id="r1-agent")
    mgr.stack = MagicMock()
    long_identity = "Role and values description. " * 300 + "PROTECTED_SENTINEL_POLICY"
    mgr.stack.l0.render.return_value = long_identity
    mgr.stack.status.return_value = {"total_drawers": 0}
    mgr._morpho_enabled = False

    wake = mgr.wake_up()
    assert "PROTECTED_SENTINEL_POLICY" in wake
    assert "Layer 0 Identity truncated" not in wake


def test_r2_unresolved_stm_overflow_and_archive(temp_memory_env):
    """
    R2 Regression: Unresolved commitments must not be deleted to meet count limits.
    Evicted items are durably archived into short_term_archive.json.
    """
    mgr = MemPalaceManager(agent_id="r2-agent")
    mgr._short_term_cache = [
        {"id": "item1", "date": "now", "content": "unresolved commitment 1", "status": "unresolved"},
        {"id": "item2", "date": "now", "content": "unresolved commitment 2", "status": "unresolved"},
    ]
    with patch("core.mempalace_manager.SettingsManager") as mock_settings:
        mock_settings.return_value.get.side_effect = lambda k, d=None: 2 if k == "core.agent.max-short-term-memories" else d
        res = mgr.add_short_term_memory("unresolved commitment 3", status="unresolved")

    assert res.get("success") is True
    # All 3 unresolved items must be preserved in cache
    remaining_ids = [x["id"] for x in mgr._short_term_cache]
    assert "item1" in remaining_ids
    assert "item2" in remaining_ids
    assert res["id"] in remaining_ids


def test_r3_migrate_v2_fail_closed_and_dry_run(temp_memory_env):
    """
    R3 Regression:
    - Pre-migration snapshot failure aborts without mutating schema_version or tables.
    - dry_run=True on empty graph must not write schema_version=2.
    """
    palace_path = os.path.join(temp_memory_env, "agents", "r3-agent", "mempalace")
    layer = MorphologicalMemoryLayer(palace_path)

    # 1. Dry run on empty graph
    dry_rep = layer.migrate_v2(dry_run=True)
    assert dry_rep.get("status") == "dry_run"
    with layer._get_conn() as c:
        meta_ver = [r[0] for r in c.execute("SELECT value FROM morpho_meta WHERE key='schema_version'")]
        assert meta_ver == [], "dry_run must not write schema_version"

    # 2. Add physical chunk node and test snapshot failure
    with layer._get_conn() as c:
        c.execute("INSERT INTO morpho_nodes (drawer_id, created_at, last_activated) VALUES ('drawer_x_chunk_000000', 'now', 'now')")
        c.execute("INSERT INTO morpho_nodes (drawer_id, created_at, last_activated) VALUES ('target_node', 'now', 'now')")
        c.execute("INSERT INTO morpho_edges VALUES ('drawer_x_chunk_000000', 'target_node', 2.0, 'now', 'now')")

    real_connect = sqlite3.connect
    def connect_with_snap_failure(path, *a, **kw):
        if ".bak." in str(path):
            raise sqlite3.OperationalError("simulated snapshot failure")
        return real_connect(path, *a, **kw)

    with patch("core.morphological_layer.sqlite3.connect", side_effect=connect_with_snap_failure):
        snap_fail_rep = layer.migrate_v2(pre_snapshot=True)

    assert snap_fail_rep.get("status") == "snapshot_failed"
    assert snap_fail_rep.get("schema_version") is None
    # Verify graph was not mutated
    with layer._get_conn() as c:
        nodes = [r[0] for r in c.execute("SELECT drawer_id FROM morpho_nodes")]
        assert "drawer_x_chunk_000000" in nodes
        assert "drawer_x" not in nodes


def test_r5_identity_evolution_approval_rules(temp_memory_env, monkeypatch, tmp_path):
    from identity_test_support import signer
    approve = signer(monkeypatch, tmp_path)
    """
    R5 Regression: Reject missing, invented, or mismatched approval references.
    Accept only validated human-controlled approval records.
    """
    mgr = MemPalaceManager(agent_id="r5-agent")
    from core.settings_manager import SettingsManager
    policy = SettingsManager(agent_id="r5-agent")
    policy.set("core.identity_evolution.mode", "signed_approval"); policy.save()

    # 1. No approval_ref must be rejected
    res_none = mgr.apply_identity_delta(
        target_section="core_values",
        delta_type="add",
        content="Testing",
        rationale="Autonomous internal reflection."
    )
    assert "Error:" in res_none and "approval_ref" in res_none

    # 2. Fabricated/invented approval_ref must be rejected
    res_invented = mgr.apply_identity_delta(
        target_section="core_values",
        delta_type="add",
        content="Testing",
        rationale="synthetic audit",
        approval_ref="NONEXISTENT-APPROVAL-RECORD"
    )
    assert "Error:" in res_invented and "not found" in res_invented

    # 3. Untrusted caller must be rejected
    with pytest.raises(PermissionError):
        mgr.register_identity_approval("core_values", "add", "Actual Trait")

    # Mismatched approval_ref (registered for different content)
    ref_other = approve(mgr, "core_values", "add", "Actual Trait")
    res_mismatch = mgr.apply_identity_delta(
        target_section="core_values",
        delta_type="add",
        content="Different Trait",
        rationale="Attempted substitution",
        approval_ref=ref_other
    )
    assert "Error:" in res_mismatch and "bound to this exact mutation" in res_mismatch

    # 4. Valid approval_ref succeeds
    res_ok = mgr.apply_identity_delta(
        target_section="core_values",
        delta_type="add",
        content="Actual Trait",
        rationale="Conversational alignment with Andrew during review.",
        approval_ref=ref_other
    )
    assert "Successfully applied" in res_ok

    # 5. Anti-replay: reusing already used approval_ref fails
    res_replay = mgr.apply_identity_delta(
        target_section="core_values",
        delta_type="add",
        content="Actual Trait",
        rationale="Replay attempt",
        approval_ref=ref_other
    )
    assert "Error:" in res_replay and "already been used" in res_replay


def test_v3_archive_failure_preserves_active_storage(temp_memory_env):
    """
    Verification 3 Regression: Failure writing short_term_archive.json
    must abort before active memory is evicted, preventing active record loss.
    """
    mgr = MemPalaceManager(agent_id="v3-archive-agent")
    old = {"id": "old_active", "date": "now", "content": "active obligation", "status": "active"}
    mgr._save_short_term_memories([old])

    from core.file_utils import atomic_json_write as real_write
    def fail_archive(path, data, *a, **kw):
        if str(path).endswith("short_term_archive.json"):
            raise OSError("simulated archive disk failure")
        return real_write(path, data, *a, **kw)

    with patch("core.mempalace_manager.SettingsManager") as mock_settings, \
         patch("core.mempalace_manager.atomic_json_write", side_effect=fail_archive):
        mock_settings.return_value.get.side_effect = lambda k, d=None: 1 if k == "core.agent.max-short-term-memories" else d
        with pytest.raises(OSError):
            mgr.add_short_term_memory("new obligation", status="active")

    active_records = mgr._load_short_term_memories()
    assert any(m["id"] == "old_active" for m in active_records), "Old active record was erroneously deleted on archive failure!"


def test_v3_same_count_external_replacement_detected(temp_memory_env):
    """
    Verification 3 Regression: External replacement of collection IDs/content
    with unchanged count must be detected and refresh prospective goal anchors.
    """
    base_dir = os.path.join(temp_memory_env, "agents", "v3-freshness-agent")
    os.makedirs(base_dir, exist_ok=True)
    with open(os.path.join(base_dir, "trajectory.json"), "w") as f:
        json.dump({"aspirations": {"short_term": [{"id": "g", "status": "active", "description": "project"}]}, "tasks": []}, f)

    mgr = MemPalaceManager(agent_id="v3-freshness-agent")

    class ColMock:
        def __init__(self):
            self.ids = ["A"]
            self.calls = 0
        def count(self):
            return 1
        def query(self, **kw):
            self.calls += 1
            return {"ids": [self.ids], "distances": [[0.1] * len(self.ids)], "metadatas": [[{}] * len(self.ids)]}
        def get(self, **kw):
            return {"ids": self.ids, "documents": ["neighbour"], "metadatas": [{}]}

    col = ColMock()
    mgr._get_collection = lambda create=False: col

    # Initial query
    mgr._get_prospective_memory_context()
    assert col.calls == 1
    assert mgr.morpho_layer.get_setpoint("g")["target_drawers"] == ["A"]

    # External replacement with same count
    col.ids = ["B"]
    mgr._get_prospective_memory_context()
    assert col.calls == 2, "Second query was not triggered after external ID replacement at same count!"
    assert mgr.morpho_layer.get_setpoint("g")["target_drawers"] == ["B"]


def test_r6_link_setpoint_validation(temp_memory_env):
    """
    R6 Regression: link_setpoint raises ValueError on unregistered drawer IDs,
    and returns linked/rejected lists for partial matches.
    """
    palace_path = os.path.join(temp_memory_env, "agents", "r6-agent", "mempalace")
    layer = MorphologicalMemoryLayer(palace_path)
    layer.register_drawer("registered_drawer")

    # 1. Nonexistent drawer raises ValueError
    with pytest.raises(ValueError) as excinfo:
        layer.link_setpoint("goal1", ["nonexistent_drawer"])
    assert "not registered" in str(excinfo.value)

    # 2. Mixed drawers: links valid, rejects invalid
    res = layer.link_setpoint("goal1", ["registered_drawer", "nonexistent_drawer"])
    assert res.get("linked") == ["registered_drawer"]
    assert res.get("rejected") == ["nonexistent_drawer"]


def test_fractional_parameters_rejected(temp_memory_env):
    """
    Regression: fractional n_steps or top_k must raise ValueError.
    """
    palace_path = os.path.join(temp_memory_env, "agents", "param-agent", "mempalace")
    with pytest.raises(ValueError):
        MorphologicalMemoryLayer(palace_path, n_steps=1.5)

    layer = MorphologicalMemoryLayer(palace_path)
    with pytest.raises(ValueError):
        layer.update_parameters(n_steps=2.7)
    with pytest.raises(ValueError):
        layer.update_parameters(top_k=3.2)


def test_v3_corrupt_archive_preserves_active_and_aborts(temp_memory_env):
    """
    Verification 3: Corrupt short_term_archive.json must raise RuntimeError,
    refuse to overwrite history, and preserve active records untouched.
    """
    mgr = MemPalaceManager(agent_id="corrupt-archive-agent")
    active_item = {"id": "act-1", "date": "now", "content": "active duty", "status": "active"}
    mgr._save_short_term_memories([active_item])

    archive_path = os.path.join(mgr.palace_path, "short_term_archive.json")
    with open(archive_path, "w") as f:
        f.write("{THIS IS NOT VALID JSON")

    with patch("core.mempalace_manager.SettingsManager") as mock_settings:
        mock_settings.return_value.get.side_effect = lambda k, d=None: 1 if k == "core.agent.max-short-term-memories" else d
        with pytest.raises(RuntimeError) as excinfo:
            mgr.add_short_term_memory("new task", status="active")
        assert "corrupted" in str(excinfo.value)

    # Active storage on disk must be completely preserved
    loaded = mgr._load_short_term_memories()
    assert len(loaded) == 1
    assert loaded[0]["id"] == "act-1"

    # Corrupt archive file must NOT be overwritten with empty list
    with open(archive_path, "r") as f:
        content = f.read()
    assert content == "{THIS IS NOT VALID JSON"


def test_v3_archive_deduplication_and_inspect_by_id(temp_memory_env):
    """
    Verification 3: Archive deduplication by ID and inspect-by-ID retrieval.
    """
    mgr = MemPalaceManager(agent_id="dedup-agent")
    archive_path = os.path.join(mgr.palace_path, "short_term_archive.json")
    # Pre-populate archive with an item
    initial_archived = [{"id": "item-to-evict", "date": "now", "content": "already archived", "status": "resolved"}]
    with open(archive_path, "w") as f:
        json.dump(initial_archived, f)

    mgr._short_term_cache = [
        {"id": "item-to-evict", "date": "now", "content": "already archived", "status": "resolved"},
        {"id": "staying-item", "date": "now", "content": "staying active", "status": "active"}
    ]
    mgr._save_short_term_memories(mgr._short_term_cache)

    with patch("core.mempalace_manager.SettingsManager") as mock_settings:
        mock_settings.return_value.get.side_effect = lambda k, d=None: 2 if k == "core.agent.max-short-term-memories" else d
        res = mgr.add_short_term_memory("brand new item", status="active")

    # Item was evicted
    assert res.get("success") is True
    # Deduplication check: item-to-evict must only appear once in archive
    archived = mgr.get_archived_short_term_memories()
    evicted_in_archive = [m for m in archived if m.get("id") == "item-to-evict"]
    assert len(evicted_in_archive) == 1

    # Inspect-by-ID check
    inspected = mgr.get_archived_short_term_memories("item-to-evict")
    assert len(inspected) == 1
    assert inspected[0]["id"] == "item-to-evict"

    inspected_missing = mgr.get_archived_short_term_memories("nonexistent-id")
    assert inspected_missing == []


