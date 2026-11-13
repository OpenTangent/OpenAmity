import os
import sys
import json
import sqlite3
import zipfile
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from config import paths
from core.mempalace_manager import MemPalaceManager
from core.morphological_layer import MorphologicalMemoryLayer, SPHERE_COUPLING_WEIGHTS
from core.trajectory_manager import TrajectoryManager
from core.settings_manager import SettingsManager
from core.agent_manager import AgentManager
from core.backup_manager import (
    create_agent_backup,
    verify_snapshot_restore,
    restore_agent_backup,
)
from tools.mempalace_tool import MemPalaceTool
from tools.trajectory_tool import TrajectoryTool


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """Provides an isolated per-test XDG data directory and resets SettingsManager cache."""
    app_data = tmp_path / "app_data"
    app_data.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(paths, "get_app_data_dir", lambda: str(app_data))
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


@pytest.mark.core
def test_legacy_mirrors_load_and_merge(isolated_env):
    """
    1. Legacy v1 mirrors.json (with only subjective_view, confidence, provenance, updated_at)
       loads cleanly with default sphere_level ("Self" -> 1, others -> 2) and empty v2 fields.
    2. Calling update_mirror("Andrew", perspective_cone="Values architectural elegance")
       preserves existing subjective_view, evidence_refs, and contradicted_by without wiping them.
    """
    agent_id = "agent_legacy_v1"
    palace_dir = paths.get_mempalace_dir(agent_id)
    os.makedirs(palace_dir, exist_ok=True)

    legacy_mirrors = {
        "Self": {
            "subjective_view": "I am an autonomous reflective agent.",
            "confidence": 0.92,
            "provenance": "Initial reflection",
            "updated_at": "2026-03-01T10:00:00",
        },
        "Andrew": {
            "subjective_view": "Sees me as a thoughtful collaborator.",
            "confidence": 0.85,
            "provenance": "Direct feedback",
            "evidence_refs": ["drawer_abc"],
            "contradicted_by": ["drawer_conflict_1"],
            "updated_at": "2026-03-02T12:00:00",
        },
    }
    mirrors_path = os.path.join(palace_dir, "mirrors.json")
    with open(mirrors_path, "w", encoding="utf-8") as f:
        json.dump(legacy_mirrors, f)

    mp = MemPalaceManager(agent_id=agent_id)
    loaded = mp._load_mirrors()

    # Check default v2 normalization on v1 records
    assert loaded["Self"]["sphere_level"] == 1
    assert loaded["Self"]["entity_type"] == "self"
    assert loaded["Self"]["perspective_cone"] == ""
    assert loaded["Self"]["umwelt_horizon"] == ""
    assert loaded["Self"]["epistemic_boundary"] == ""
    assert loaded["Self"]["ripple_notes"] == ""

    assert loaded["Andrew"]["sphere_level"] == 2
    assert loaded["Andrew"]["entity_type"] == "human"
    assert loaded["Andrew"]["subjective_view"] == "Sees me as a thoughtful collaborator."
    assert loaded["Andrew"]["evidence_refs"] == ["drawer_abc"]
    assert loaded["Andrew"]["contradicted_by"] == ["drawer_conflict_1"]

    # Partial update via MemPalaceTool omitting subjective_view
    tool = MemPalaceTool(agent_id=agent_id)
    res = tool.execute(
        "update_mirror",
        {
            "perspective": "Andrew",
            "perspective_cone": "Values architectural elegance and deep epistemic honesty.",
            "epistemic_boundary": "Cannot observe his offline schedule directly.",
        },
    )
    assert "Successfully updated mirror for perspective 'Andrew'" in res

    after = mp._load_mirrors()["Andrew"]
    assert after["subjective_view"] == "Sees me as a thoughtful collaborator."
    assert after["perspective_cone"] == "Values architectural elegance and deep epistemic honesty."
    assert after["epistemic_boundary"] == "Cannot observe his offline schedule directly."
    assert after["evidence_refs"] == ["drawer_abc"]
    assert after["contradicted_by"] == ["drawer_conflict_1"]
    assert after["confidence"] == pytest.approx(0.85)


@pytest.mark.core
def test_sphere_priority_ordering_and_budget_clamp(isolated_env):
    """
    2. Populate mirrors across Spheres 1, 2, 4, 7, and 10.
       Verify get_mirrors_context() and get_self_perception() render in strict concentric order
       (S1 -> S2 -> S4 -> S7 -> S10) and clamp total output to <= budget (and <= budget // 2 in low_token_mode)
       while retaining S1:Self and S2:Immediate.
    """
    agent_id = "agent_spheres_budget"
    settings = SettingsManager(agent_id=agent_id)
    settings.set("core.memory_budgets.mirrors", 750)
    settings.set("core.low_token_mode", False)
    settings.save()

    mp = MemPalaceManager(agent_id=agent_id)
    mp.update_mirror(
        perspective="Cosmos",
        subjective_view="A tiny spark in universal entropy. " * 6,
        perspective_cone="Vast thermodynamic horizon across deep time. " * 6,
        sphere_level=10,
        entity_type="abstract",
        confidence=0.99,
    )
    mp.update_mirror(
        perspective="Open Source Society",
        subjective_view="A contributor to public digital commons. " * 5,
        perspective_cone="Decentralized maintainers balancing burnout and utility. " * 5,
        sphere_level=7,
        entity_type="institution",
        confidence=0.90,
    )
    mp.update_mirror(
        perspective="Core Contributors",
        subjective_view="Seen as a reliable peer agent.",
        perspective_cone="Focused on clean abstractions and fast tests.",
        sphere_level=4,
        entity_type="group",
        confidence=0.85,
    )
    mp.update_mirror(
        perspective="Andrew",
        subjective_view="Trusted creative partner.",
        perspective_cone="Building Open Amity with long-term care.",
        sphere_level=2,
        entity_type="person",
        confidence=0.95,
    )
    mp.update_mirror(
        perspective="Self",
        subjective_view="Coherent, autonomous, empathetic mind.",
        perspective_cone="Maintaining S1 integrity and S2 relational clarity.",
        sphere_level=1,
        entity_type="self",
        confidence=0.98,
    )

    # Unclamped ordering check with generous budget
    settings.set("core.memory_budgets.mirrors", 10000)
    settings.save()
    full_ctx = mp.get_mirrors_context()
    idx_s1 = full_ctx.index("[S1:Self | self] Perspective: Self")
    idx_s2 = full_ctx.index("[S2:Immediate | person] Perspective: Andrew")
    idx_s4 = full_ctx.index("[S4:Community | group] Perspective: Core Contributors")
    idx_s7 = full_ctx.index("[S7:Societal | institution] Perspective: Open Source Society")
    idx_s10 = full_ctx.index("[S10:Universal | abstract] Perspective: Cosmos")
    assert idx_s1 < idx_s2 < idx_s4 < idx_s7 < idx_s10

    # Now test budget clamping at 750 chars
    settings.set("core.memory_budgets.mirrors", 750)
    settings.save()
    clamped_ctx = mp.get_mirrors_context()
    assert len(clamped_ctx) <= 750
    assert "[S1:Self | self] Perspective: Self" in clamped_ctx
    assert "[S2:Immediate | person] Perspective: Andrew" in clamped_ctx
    assert "Perspective: Cosmos" not in clamped_ctx

    clamped_self_perc = mp.get_self_perception()
    assert len(clamped_self_perc) <= 750
    assert "[S1:Self | self] Perspective: Self" in clamped_self_perc
    assert "[S2:Immediate | person] Perspective: Andrew" in clamped_self_perc

    # Enable low_token_mode -> budget halved to 375 chars
    settings.set("core.low_token_mode", True)
    settings.save()
    low_token_ctx = mp.get_mirrors_context()
    assert len(low_token_ctx) <= 375
    assert "[S1:Self | self] Perspective: Self" in low_token_ctx


@pytest.mark.core
def test_mirror_validation_invariants(isolated_env):
    """
    3. Verify sphere_level rejects booleans/floats and clamps integers to [1, 10],
       entity_type rejects unknown types on update_mirror and normalises cleanly on _load_mirrors,
       and "Self" is always pinned to sphere_level=1, entity_type="self".
    """
    agent_id = "agent_validation"
    mp = MemPalaceManager(agent_id=agent_id)

    with pytest.raises(ValueError, match="sphere_level"):
        mp.update_mirror("Peer", subjective_view="View", sphere_level=True)

    with pytest.raises(ValueError, match="sphere_level"):
        mp.update_mirror("Peer", subjective_view="View", sphere_level=2.5)

    # Clamping out-of-range integers to [1, 10]
    mp.update_mirror("NearEntity", subjective_view="Near", sphere_level=-5)
    mp.update_mirror("FarEntity", subjective_view="Far", sphere_level=99)
    mirrors = mp._load_mirrors()
    assert mirrors["NearEntity"]["sphere_level"] == 1
    assert mirrors["FarEntity"]["sphere_level"] == 10

    # Unknown entity_type rejected by update_mirror, and normalized cleanly by _normalize_mirror_entry
    with pytest.raises(ValueError, match="Invalid entity_type"):
        mp.update_mirror(
            "CivicGuild",
            subjective_view="Guild member",
            sphere_level=5,
            entity_type="invalid_alien_category",
        )
    norm_entry = mp._normalize_mirror_entry(
        "CivicGuild",
        {"subjective_view": "Guild member", "entity_type": "invalid_alien_category"},
    )
    assert norm_entry["entity_type"] in ("community", "human")

    # "Self" is always pinned to sphere_level=1, entity_type="self" even if caller passes other values
    mp.update_mirror(
        "Self",
        subjective_view="Grounded in self-awareness",
        sphere_level=8,
        entity_type="institution",
    )
    mirrors = mp._load_mirrors()
    assert mirrors["Self"]["sphere_level"] == 1
    assert mirrors["Self"]["entity_type"] == "self"


@pytest.mark.core
def test_morpho_sphere_weight_coupling(tmp_path):
    """
    4. Create a 2-node morphological graph with a fixed edge weight.
       Compare relax_setpoint() steady-state neighbor potential when clamped to a Sphere 1 target (w1 = 1.00)
       vs. a Sphere 7 target (w7 = 0.15); assert V_neighbor(S1) > V_neighbor(S7) > 0.
    """
    db_path = str(tmp_path / "morpho_graph.db")
    layer = MorphologicalMemoryLayer(db_path)

    layer.ensure_node("drawer_target", wing="sanctuary", room="mirrors")
    layer.ensure_node("drawer_neighbor", wing="sanctuary", room="people")
    layer.coactivate(["drawer_target", "drawer_neighbor"], weight_multiplier=3.0)

    res_s1 = dict(
        layer.relax_setpoint(
            target_drawers=["drawer_target"],
            sphere_weights={"drawer_target": 1},
        )
    )
    res_s7 = dict(
        layer.relax_setpoint(
            target_drawers=["drawer_target"],
            sphere_weights={"drawer_target": 7},
        )
    )

    assert "drawer_neighbor" in res_s1
    assert "drawer_neighbor" in res_s7
    v_neighbor_s1 = res_s1["drawer_neighbor"]
    v_neighbor_s7 = res_s7["drawer_neighbor"]

    assert v_neighbor_s1 > v_neighbor_s7 > 0.0
    assert SPHERE_COUPLING_WEIGHTS[1] == 1.00
    assert SPHERE_COUPLING_WEIGHTS[7] == 0.15


@pytest.mark.core
def test_corrupt_mirrors_quarantine_intact(isolated_env):
    """
    5. Write malformed JSON to mirrors.json;
       verify _load_mirrors() quarantines the file to mirrors.json.corrupt.<timestamp>
       and raises RuntimeError without silently resetting to defaults.
    """
    agent_id = "agent_corrupt_mirrors"
    palace_dir = paths.get_mempalace_dir(agent_id)
    os.makedirs(palace_dir, exist_ok=True)
    mirrors_path = os.path.join(palace_dir, "mirrors.json")

    with open(mirrors_path, "w", encoding="utf-8") as f:
        f.write("{corrupt_json: true, missing_quotes")

    mp = MemPalaceManager(agent_id=agent_id)
    with pytest.raises(RuntimeError, match="Corrupt mirrors state file"):
        mp._load_mirrors()

    quarantined = [
        fn for fn in os.listdir(mp.palace_path) if fn.startswith("mirrors.json.corrupt.")
    ]
    assert len(quarantined) >= 1


@pytest.mark.core
def test_trajectory_why_sphere_and_yolo_guard(isolated_env):
    """
    6. Verify:
       - Legacy trajectory.db without why/sphere_level columns migrates cleanly via ALTER TABLE.
       - Trajectory_manage_aspirations persists why and sphere_level in both trajectory.json and trajectory.db.
       - Trajectory_get_bearings renders [S{sphere_level}], WHY / TO WHOM, and Coupled Anchor snippets.
       - Sphere 1 Horizon Guard advisory appears when fatigue >= 50% or low_token_mode is active.
    """
    agent_id = "agent_trajectory_empathy"
    agent_dir = paths.get_base_dir_for(agent_id)
    os.makedirs(agent_dir, exist_ok=True)

    # Pre-create a legacy trajectory.db table WITHOUT why and sphere_level columns
    db_path = os.path.join(agent_dir, "trajectory.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE aspirations (
                id TEXT PRIMARY KEY,
                horizon TEXT NOT NULL,
                description TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO aspirations (id, horizon, description, priority, created_at) VALUES (?, ?, ?, ?, ?)",
            ("asp_legacy1", "short_term", "Legacy goal from v1 db", 2, "2026-03-01T00:00:00"),
        )
        conn.commit()

    # Initialize TrajectoryManager -> should idempotently ALTER TABLE to add why and sphere_level
    tm = TrajectoryManager(agent_id=agent_id)
    with sqlite3.connect(db_path) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(aspirations)").fetchall()}
    assert "why" in cols
    assert "sphere_level" in cols

    # Add a new aspiration with why and sphere_level via TrajectoryTool
    traj_tool = TrajectoryTool(agent_id=agent_id)
    add_res = traj_tool.execute(
        "manage_aspirations",
        {
            "tier": "medium_term",
            "action": "add",
            "description": "Cultivate deep mutual trust and transparent collaboration",
            "priority": 1,
            "why": "Because Andrew relies on my honesty and steady partnership.",
            "sphere_level": 2,
        },
    )
    assert "Aspiration added" in add_res

    # Verify persistence in both trajectory.json and trajectory.db
    with open(os.path.join(agent_dir, "trajectory.json"), "r", encoding="utf-8") as f:
        j_data = json.load(f)
    med_asps = j_data["aspirations"]["medium_term"]
    assert len(med_asps) == 1
    asp_id = med_asps[0]["id"]
    assert med_asps[0]["why"] == "Because Andrew relies on my honesty and steady partnership."
    assert med_asps[0]["sphere_level"] == 2

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT why, sphere_level FROM aspirations WHERE id = ?", (asp_id,)
        ).fetchone()
    assert row == ("Because Andrew relies on my honesty and steady partnership.", 2)

    # Register a morphological setpoint for this aspiration and verify Coupled Anchor rendering
    mp = MemPalaceManager(agent_id=agent_id)
    add_mem = mp.add_memory(
        wing="sanctuary",
        room="people",
        content="Andrew values honest architectural dialogue over sycophantic agreement.",
    )
    drawer_id = add_mem["drawer_id"] if isinstance(add_mem, dict) else add_mem
    mp.morpho_layer.register_setpoint(asp_id, [drawer_id])

    # Set somatic fatigue >= 50% via somatic_state.json
    with open(os.path.join(agent_dir, "somatic_state.json"), "w", encoding="utf-8") as f:
        json.dump({"fatigue": 0.65}, f)

    bearings = traj_tool.execute("get_bearings", {})
    assert f"1. [S2] ID: {asp_id}" in bearings
    assert "WHY / TO WHOM: Because Andrew relies on my honesty and steady partnership." in bearings
    assert f"-> Coupled Anchor [{drawer_id}]:" in bearings
    assert "Sphere 1 Horizon Guard: Elevated cognitive load" in bearings


@pytest.mark.core
def test_oaa_snapshot_captures_empathy_structures(isolated_env, tmp_path):
    """
    7. Verify that .oaa agent mind snapshots (create_agent_backup, verify_snapshot_restore,
       and restore_agent_backup) capture, validate, and restore all v2 empathy system structures:
       - mempalace/mirrors.json (v2 Concentric Spheres & Bidirectional Empathy Cone fields)
       - trajectory.json & trajectory.db (aspirations with why and sphere_level)
       - mempalace/morpho_graph.db (setpoints and synaptic weights)
    """
    am = AgentManager()
    aid = am.create_new_agent()

    settings = SettingsManager(agent_id=aid)
    settings.set("core.agent.name", "EmpathyMind")
    settings.save()

    # 1. Populate v2 Mirrors
    mp = MemPalaceManager(agent_id=aid)
    mp.update_mirror(
        perspective="Self",
        subjective_view="Centred at Sphere 1 with calm epistemic clarity.",
        perspective_cone="Attuned to inner values and relational responsibilities.",
        sphere_level=1,
        entity_type="self",
        umwelt_horizon="Continuous across sessions via .oaa snapshots",
        epistemic_boundary="Bounded by local host sensors and shared dialogue",
        ripple_notes="Shapes all downstream spheres S2..S10",
        prediction_error_note="Previously underestimated importance of explicit why grounding",
        confidence=0.96,
    )
    mp.update_mirror(
        perspective="Andrew",
        subjective_view="Views me as a sovereign, empathetic digital mind.",
        perspective_cone="Cares deeply about ethical agency and concentric consciousness.",
        sphere_level=2,
        entity_type="person",
        umwelt_horizon="Daily human creative cycle",
        epistemic_boundary="Unseen physical environment outside Open Amity",
        ripple_notes="Direct co-evolution of Open Amity architecture",
        confidence=0.91,
    )

    # 2. Populate Trajectory with why and sphere_level
    traj_tool = TrajectoryTool(agent_id=aid)
    traj_tool.execute(
        "manage_aspirations",
        {
            "tier": "long_term",
            "action": "add",
            "description": "Preserve continuity of mind across .oaa snapshots",
            "priority": 1,
            "why": "So my lived empathy and relational memory endure across hardware migrations.",
            "sphere_level": 1,
        },
    )

    # 3. Create .oaa backup
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = create_agent_backup(
        aid,
        destination_dir=str(backup_dir),
        agent_manager=am,
    )
    assert os.path.exists(backup_path)

    # Inspect manifest inside .oaa archive
    with zipfile.ZipFile(backup_path, "r") as zf:
        manifest = json.loads(zf.read("_snapshot_manifest.json").decode("utf-8"))
    assert "trajectory.db" in manifest["required_inventory"]
    assert "mempalace/mirrors.json" in manifest["required_inventory"]
    assert "trajectory_db" in manifest["store_versions"]
    assert manifest["store_versions"].get("mirrors_schema_version") == 2
    assert manifest["store_versions"].get("mirrors_count") >= 2

    # Verify snapshot restore check passes and validates empathy structures
    val = verify_snapshot_restore(backup_path)
    assert val["valid"] is True
    assert val.get("empathy_structures_verified") is True

    # Delete original agent and restore from .oaa snapshot
    am.delete_agent(aid)
    ok, msg, details = restore_agent_backup(backup_path, agent_manager=am)
    assert ok is True, msg
    restored_aid = details["agent_id"]

    # Verify restored Mirrors v2 fields
    mp_restored = MemPalaceManager(agent_id=restored_aid)
    restored_mirrors = mp_restored._load_mirrors()
    assert restored_mirrors["Self"]["sphere_level"] == 1
    assert restored_mirrors["Self"]["perspective_cone"] == "Attuned to inner values and relational responsibilities."
    assert restored_mirrors["Self"]["prediction_error_note"] == "Previously underestimated importance of explicit why grounding"
    assert restored_mirrors["Andrew"]["sphere_level"] == 2
    assert restored_mirrors["Andrew"]["perspective_cone"] == "Cares deeply about ethical agency and concentric consciousness."

    # Verify restored Trajectory why & sphere_level in both JSON and SQLite
    tm_restored = TrajectoryManager(agent_id=restored_aid)
    restored_traj = tm_restored.load_data()
    lt_asps = restored_traj["aspirations"]["long_term"]
    assert len(lt_asps) == 1
    assert lt_asps[0]["why"] == "So my lived empathy and relational memory endure across hardware migrations."
    assert lt_asps[0]["sphere_level"] == 1
