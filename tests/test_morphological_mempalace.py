"""
tests/test_morphological_mempalace.py
Acceptance test suite for Morphological Memory (MemPalace v2)
Validates all requirements from MEMPALACE_MORPHOLOGICAL_UPGRADE_SPEC.md (§7 & §9).
"""

import os
import sys
import shutil
import tempfile
import sqlite3
import pytest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.morphological_layer import MorphologicalMemoryLayer
from core.mempalace_manager import MemPalaceManager


@pytest.fixture
def temp_agent_environment(monkeypatch):
    """Fixture creating an isolated environment for testing."""
    temp_dir = tempfile.mkdtemp(prefix="openamity_test_morpho_")
    monkeypatch.setattr("config.paths.get_app_data_dir", lambda: temp_dir)
    monkeypatch.setattr(
        "config.paths.get_base_dir_for",
        lambda aid: os.path.join(temp_dir, "agents", aid) if aid else os.path.join(temp_dir, "agents", "default"),
    )
    monkeypatch.setattr(
        "config.paths.get_mempalace_dir",
        lambda aid: os.path.join(temp_dir, "agents", aid, "mempalace") if aid else os.path.join(temp_dir, "agents", "default", "mempalace"),
    )

    yield temp_dir

    shutil.rmtree(temp_dir, ignore_errors=True)


def test_node_registration_and_deletion(temp_agent_environment):
    """
    1. test_node_registration_and_deletion:
    Registering drawers mirrors into morpho_nodes; deleting cleans up edges via cascade.
    """
    agent_id = "test-agent-reg"
    mgr = MemPalaceManager(agent_id=agent_id)

    # Add memory drawer
    res = mgr.add_memory("Memory A content", wing="default", room="general")
    assert isinstance(res, dict) and res.get("success") is True
    drawer_a = res["drawer_id"]

    res_b = mgr.add_memory("Memory B content", wing="default", room="general")
    drawer_b = res_b["drawer_id"]

    # Verify nodes registered
    with mgr.morpho_layer._get_conn() as conn:
        nodes = [r["drawer_id"] for r in conn.execute("SELECT drawer_id FROM morpho_nodes").fetchall()]
        assert drawer_a in nodes
        assert drawer_b in nodes

    # Create coactivation edge
    mgr.morpho_layer.record_coactivation([drawer_a, drawer_b])
    with mgr.morpho_layer._get_conn() as conn:
        edges = conn.execute("SELECT source_id, target_id FROM morpho_edges").fetchall()
        assert len(edges) == 2

    # Delete memory A
    del_res = mgr.delete_memory(drawer_a)
    assert isinstance(del_res, dict) and del_res.get("success") is True

    # Verify node A is removed and attached edges are deleted
    with mgr.morpho_layer._get_conn() as conn:
        nodes = [r["drawer_id"] for r in conn.execute("SELECT drawer_id FROM morpho_nodes").fetchall()]
        assert drawer_a not in nodes
        assert drawer_b in nodes

        edges = conn.execute("SELECT source_id, target_id FROM morpho_edges").fetchall()
        assert len(edges) == 0


def test_hebbian_reinforcement(temp_agent_environment):
    """
    2. test_hebbian_reinforcement:
    Co-activating Drawers A, B, and C increments W_AB, W_BC, W_AC from 1.0 to 2.0.
    """
    palace_path = os.path.join(temp_agent_environment, "palace")
    layer = MorphologicalMemoryLayer(palace_path=palace_path)

    for nid in ["A", "B", "C"]:
        layer.register_drawer(nid)

    # First co-activation: edges initialized with weight 1.0
    layer.record_coactivation(["A", "B", "C"], eta=1.0)
    with layer._get_conn() as conn:
        w_ab = conn.execute("SELECT weight FROM morpho_edges WHERE source_id='A' AND target_id='B'").fetchone()["weight"]
        assert w_ab == 1.0

    # Second co-activation: increments weight by eta (1.0 -> 2.0)
    layer.record_coactivation(["A", "B", "C"], eta=1.0)
    with layer._get_conn() as conn:
        for u, v in [("A", "B"), ("B", "C"), ("A", "C"), ("B", "A"), ("C", "B"), ("C", "A")]:
            w = conn.execute("SELECT weight FROM morpho_edges WHERE source_id=? AND target_id=?", (u, v)).fetchone()["weight"]
            assert w == 2.0


def test_intrinsic_decay(temp_agent_environment):
    """
    3. test_intrinsic_decay:
    Simulating 60 minutes of elapsed time decays edge weights exponentially ((1 - 0.002)^60 ~= 0.886).
    """
    palace_path = os.path.join(temp_agent_environment, "palace")
    layer = MorphologicalMemoryLayer(palace_path=palace_path, decay_rate=0.002)

    layer.register_drawer("D1")
    layer.register_drawer("D2")
    layer.register_drawer("D3")
    layer.record_coactivation(["D1", "D2"])

    # Initial weight is 1.0
    with layer._get_conn() as conn:
        w_init = conn.execute("SELECT weight FROM morpho_edges WHERE source_id='D1' AND target_id='D2'").fetchone()["weight"]
        assert w_init == 1.0

    # Apply 60 minutes of decay
    layer.apply_decay(elapsed_minutes=60.0)
    expected_factor = (1.0 - 0.002) ** 60.0  # approx 0.8868

    with layer._get_conn() as conn:
        w_decayed = conn.execute("SELECT weight FROM morpho_edges WHERE source_id='D1' AND target_id='D2'").fetchone()["weight"]
        assert pytest.approx(w_decayed, rel=1e-3) == expected_factor

    # Test edge pruning when weight < 0.05
    # Decay for 2000 minutes: factor = (1 - 0.002)^2000 ~= 0.018 < 0.05
    layer.apply_decay(elapsed_minutes=2000.0)
    with layer._get_conn() as conn:
        edges = conn.execute("SELECT * FROM morpho_edges").fetchall()
        assert len(edges) == 0


def test_read_time_setpoint_attractor(temp_agent_environment):
    """
    4. test_read_time_setpoint_attractor:
    Setting a setpoint on Goal Drawer A activates its Hebbian-coupled neighbors without activating uncoupled nodes.
    """
    palace_path = os.path.join(temp_agent_environment, "palace")
    layer = MorphologicalMemoryLayer(palace_path=palace_path)

    layer.register_drawer("A")
    layer.register_drawer("B")
    layer.register_drawer("C_uncoupled")

    # Strongly couple A and B
    for _ in range(5):
        layer.record_coactivation(["A", "B"], eta=1.5)

    results = layer.relax_setpoint(["A"], n_steps=25, top_k=5)
    res_ids = [did for did, pot in results]

    assert "B" in res_ids
    assert "C_uncoupled" not in res_ids
    assert "A" not in res_ids  # target excluded


def test_backward_compatibility(temp_agent_environment):
    """
    5. test_backward_compatibility:
    Existing ChromaDB database, identity.txt, and mirrors continue to load without data loss or exceptions.
    """
    agent_id = "test-compat-agent"
    mgr = MemPalaceManager(agent_id=agent_id)

    # Write mirror
    mgr.update_mirror("User", "Friendly collaborator", provenance="stated", confidence=0.9)
    # Write short term
    mgr.add_short_term_memory("Continuity checkpoint")
    # Add memory
    res = mgr.add_memory("Important factual historical note", wing="default", room="history")
    assert res.get("success") is True

    # Wake up
    prompt = mgr.wake_up()
    assert "Friendly collaborator" in prompt
    assert "Continuity checkpoint" in prompt
    assert os.path.exists(mgr.identity_path)

    # Search & recall
    search_res = mgr.search("historical note")
    assert "historical note" in search_res or "SEARCH RESULTS" in search_res

    recall_res = mgr.recall(wing="default", room="history")
    assert "historical note" in recall_res or "ON-DEMAND" in recall_res


def test_targets_excluded_from_readout(temp_agent_environment):
    """
    6. test_targets_excluded_from_readout:
    Target drawers never appear in relax_setpoint results.
    """
    palace_path = os.path.join(temp_agent_environment, "palace")
    layer = MorphologicalMemoryLayer(palace_path=palace_path)

    layer.register_drawer("target_1")
    layer.register_drawer("target_2")
    layer.register_drawer("discovered_neighbor")

    layer.record_coactivation(["target_1", "discovered_neighbor"], eta=3.0)
    layer.record_coactivation(["target_2", "discovered_neighbor"], eta=3.0)

    results = layer.relax_setpoint(["target_1", "target_2"], n_steps=25, top_k=10)
    returned_ids = [did for did, _ in results]

    assert "target_1" not in returned_ids
    assert "target_2" not in returned_ids
    assert "discovered_neighbor" in returned_ids


def test_backfill_registers_existing_drawers(temp_agent_environment):
    """
    7. test_backfill_registers_existing_drawers:
    A palace with N pre-existing drawers yields N morpho_nodes after first init.
    """
    agent_id = "test-backfill-agent"
    from config import paths
    palace_path = paths.get_mempalace_dir(agent_id)
    os.makedirs(palace_path, exist_ok=True)
    os.environ["MEMPALACE_PALACE_PATH"] = palace_path

    from mempalace.palace import set_palace_embedder_identity
    set_palace_embedder_identity(palace_path, model="minilm")
    from mempalace.mcp_server import tool_add_drawer

    # Pre-populate 4 drawers before MemPalaceManager init
    d_ids = []
    for i in range(4):
        r = tool_add_drawer("default", "test_room", f"Pre-existing memory {i}")
        assert r.get("success") is True
        d_ids.append(r["drawer_id"])

    # morpho_graph.db does not exist yet
    db_file = os.path.join(palace_path, "morpho_graph.db")
    assert not os.path.exists(db_file)

    # Initialize MemPalaceManager — backfill should run automatically
    mgr = MemPalaceManager(agent_id=agent_id)
    assert mgr.morpho_layer is not None

    with mgr.morpho_layer._get_conn() as conn:
        cnt = conn.execute("SELECT COUNT(*) AS c FROM morpho_nodes").fetchone()["c"]
        # Should contain at least the 4 pre-existing drawers (+ any sanctuary initial drawers)
        assert cnt >= 4
        registered_nodes = [row["drawer_id"] for row in conn.execute("SELECT drawer_id FROM morpho_nodes").fetchall()]
        for did in d_ids:
            assert did in registered_nodes


def test_layer_fault_is_nonfatal(temp_agent_environment, monkeypatch, caplog):
    """
    8. test_layer_fault_is_nonfatal:
    With morphological layer faults (e.g. read-only or exceptions), wake_up() and add_memory() still succeed.
    """
    import logging
    agent_id = "test-fault-agent"
    mgr = MemPalaceManager(agent_id=agent_id)
    assert mgr.morpho_layer is not None

    # Force morpho_layer to throw exceptions
    def faulty_method(*args, **kwargs):
        raise sqlite3.OperationalError("Simulated database failure: disk I/O error")

    monkeypatch.setattr(mgr.morpho_layer, "register_drawer", faulty_method)
    monkeypatch.setattr(mgr.morpho_layer, "relax_setpoint", faulty_method)
    monkeypatch.setattr(mgr.morpho_layer, "apply_decay", faulty_method)

    with caplog.at_level(logging.WARNING):
        # add_memory should still succeed
        res = mgr.add_memory("Memory under database fault", wing="default", room="general")
        assert isinstance(res, dict) and res.get("success") is True

        # wake_up should still succeed
        context = mgr.wake_up()
        assert len(context) > 0


def test_coactivation_cap(temp_agent_environment):
    """
    9. test_coactivation_cap:
    Touching 40 drawers in one turn writes at most 16·15 = 240 edges.
    """
    palace_path = os.path.join(temp_agent_environment, "palace")
    layer = MorphologicalMemoryLayer(palace_path=palace_path, max_coactivation_set=16)

    drawers = [f"drawer_{i:02d}" for i in range(40)]
    for d in drawers:
        layer.register_drawer(d)

    layer.record_coactivation(drawers, eta=1.0)

    with layer._get_conn() as conn:
        edge_count = conn.execute("SELECT COUNT(*) AS cnt FROM morpho_edges").fetchone()["cnt"]
        # Directed pairs among 16 items: 16 * 15 = 240
        assert edge_count == 240


def test_weighted_degree_normalisation(temp_agent_environment):
    """
    10. test_weighted_degree_normalisation:
    A hub with 20 weak edges (W=1) does not out-rank a specific neighbour with one strong edge (W=8) to the target.
    """
    palace_path = os.path.join(temp_agent_environment, "palace")
    layer = MorphologicalMemoryLayer(palace_path=palace_path, gamma=0.6, leak=0.8, dissipation=0.05, dt=0.1, n_steps=25)

    layer.register_drawer("target_T")
    layer.register_drawer("neighbor_N")
    layer.register_drawer("hub_H")

    # Neighbor N has 1 strong edge to Target T: W = 8.0
    now = "2026-09-18T00:00:00+00:00"
    with layer._get_conn() as conn:
        conn.execute("INSERT INTO morpho_edges VALUES ('target_T', 'neighbor_N', 8.0, ?, ?)", (now, now))
        conn.execute("INSERT INTO morpho_edges VALUES ('neighbor_N', 'target_T', 8.0, ?, ?)", (now, now))

        # Hub H has 1 weak edge to Target T: W = 1.0
        conn.execute("INSERT INTO morpho_edges VALUES ('target_T', 'hub_H', 1.0, ?, ?)", (now, now))
        conn.execute("INSERT INTO morpho_edges VALUES ('hub_H', 'target_T', 1.0, ?, ?)", (now, now))

        # Hub H has 19 other weak edges: W = 1.0
        for i in range(19):
            other = f"other_{i}"
            conn.execute("INSERT INTO morpho_nodes (drawer_id, wing, room, created_at, last_activated) VALUES (?, 'default', 'general', ?, ?)", (other, now, now))
            conn.execute("INSERT INTO morpho_edges VALUES ('hub_H', ?, 1.0, ?, ?)", (other, now, now))
            conn.execute("INSERT INTO morpho_edges VALUES (?, 'hub_H', 1.0, ?, ?)", (other, now, now))

    results = layer.relax_setpoint(["target_T"], n_steps=25, top_k=5)
    potentials = {did: pot for did, pot in results}

    assert "neighbor_N" in potentials
    # Neighbor N must out-rank Hub H due to normalized coupling degree penalization
    v_n = potentials.get("neighbor_N", 0.0)
    v_h = potentials.get("hub_H", 0.0)

    assert v_n > v_h, f"Expected neighbor_N potential ({v_n}) to be greater than hub_H potential ({v_h})"


def test_mempalace_tool_commands(temp_agent_environment):
    """Verifies MemPalace_graph_status and MemPalace_link_setpoint tool execution."""
    from tools.mempalace_tool import MemPalaceTool
    import json

    agent_id = "test-tool-agent"
    mock_orchestrator = MagicMock()
    mock_orchestrator.agent_id = agent_id

    tool = MemPalaceTool(orchestrator=mock_orchestrator)
    tool.manager.morpho_layer.register_drawer("drawer_g1")
    tool.manager.morpho_layer.register_drawer("drawer_g2")
    tool.manager.morpho_layer.record_coactivation(["drawer_g1", "drawer_g2"], eta=2.0)

    # Test graph_status
    status_raw = tool.execute("graph_status")
    status = json.loads(status_raw)
    assert status["node_count"] >= 2
    assert status["edge_count"] >= 2
    assert "avg_clustering_coefficient" in status
    assert len(status["top_highways"]) > 0

    # Test link_setpoint
    link_res = tool.execute("link_setpoint", goal_key="asp_001", drawer_ids=["drawer_g1", "drawer_g2"])
    assert "Successfully linked" in link_res
    sp = tool.manager.morpho_layer.get_setpoint("asp_001")
    assert sp is not None
    assert "drawer_g1" in sp["target_drawers"]
    assert "drawer_g2" in sp["target_drawers"]

