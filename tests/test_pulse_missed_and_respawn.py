import os
import sys
import sqlite3
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.pulse_engine import PulseEngine
from tools.pulse_tool import PulseTool
from core.settings_manager import SettingsManager


@pytest.fixture
def temp_pulse_env(tmp_path, monkeypatch):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path / aid))
    monkeypatch.setattr("config.paths.get_app_data_dir", lambda: str(tmp_path))
    return tmp_path


def create_mock_orchestrator(agent_id, is_first_run=False, is_paused=False):
    orch = MagicMock()
    orch.agent_id = agent_id
    orch.is_paused = is_paused
    orch.is_busy = False
    orch.get_fatigue.return_value = 0.0
    orch.mempalace_manager = None
    orch.cerebrum = MagicMock()
    orch.cerebrum.tools = {}

    settings_mgr = MagicMock(spec=SettingsManager)
    settings_data = {
        "core.first-run": is_first_run,
        "core.auto-pulse.seeded": False,
        "core.auto-pulse.last-sleep-cycle": 0,
        "core.paused": is_paused,
    }
    settings_mgr.get.side_effect = lambda k, default=None: settings_data.get(k, default)
    def set_val(k, v):
        settings_data[k] = v
    settings_mgr.set.side_effect = set_val
    settings_mgr.save.side_effect = lambda: None

    orch.settings_manager = settings_mgr
    return orch, settings_data


def test_empty_db_does_not_reseed_when_lifecycle_seeded(temp_pulse_env):
    agent_id = "agent_seeded_empty"
    db_dir = temp_pulse_env / agent_id
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "pulses.db"

    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE pulses (id INTEGER PRIMARY KEY, title TEXT, context TEXT, scheduled_time TEXT, recurrence TEXT, status TEXT, has_run BOOLEAN, created_at TEXT, pulse_type TEXT)")
    conn.execute("CREATE TABLE pulse_meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO pulse_meta (key, value) VALUES ('lifecycle_seeded', '1')")
    conn.commit()
    conn.close()

    orch, _ = create_mock_orchestrator(agent_id, is_first_run=True)
    with patch.object(PulseEngine, '_schedule_loop', return_value=None):
        pe = PulseEngine(orch)

    conn = pe.get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM pulses")
    count = c.fetchone()[0]
    conn.close()
    pe.stop()

    assert count == 0, "Empty database with lifecycle_seeded=1 must not reseed pulses"


def test_first_run_agent_seeds_lifecycle_pulses_once(temp_pulse_env):
    agent_id = "agent_fresh"
    orch, settings_data = create_mock_orchestrator(agent_id, is_first_run=True)

    with patch.object(PulseEngine, '_schedule_loop', return_value=None):
        pe = PulseEngine(orch)

    conn = pe.get_db_connection()
    c = conn.cursor()
    c.execute("SELECT title, recurrence, status FROM pulses")
    rows = c.fetchall()
    assert len(rows) == 3
    titles = [r[0] for r in rows]
    assert "Morning Kickoff" in titles
    assert "Mid-Day Check-In" in titles
    assert "Evening Reflection" in titles

    c.execute("SELECT value FROM pulse_meta WHERE key = 'lifecycle_seeded'")
    meta_val = c.fetchone()[0]
    assert meta_val == "1"
    assert settings_data.get("core.auto-pulse.seeded") is True

    # Now simulate the agent deleting all pulses
    c.execute("DELETE FROM pulses")
    conn.commit()
    conn.close()
    pe.stop()

    # Re-initialize PulseEngine on same database
    orch2, _ = create_mock_orchestrator(agent_id, is_first_run=False)
    with patch.object(PulseEngine, '_schedule_loop', return_value=None):
        pe2 = PulseEngine(orch2)

    conn = pe2.get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM pulses")
    count = c.fetchone()[0]
    conn.close()
    pe2.stop()

    assert count == 0, "Deleted pulses must not respawn upon reinitialization"


def test_existing_agent_with_empty_pulses_is_not_reseeded(temp_pulse_env):
    agent_id = "agent_existing_empty"
    orch, settings_data = create_mock_orchestrator(agent_id, is_first_run=False)

    with patch.object(PulseEngine, '_schedule_loop', return_value=None):
        pe = PulseEngine(orch)

    conn = pe.get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM pulses")
    count = c.fetchone()[0]
    c.execute("SELECT value FROM pulse_meta WHERE key = 'lifecycle_seeded'")
    meta_val = c.fetchone()[0]
    conn.close()
    pe.stop()

    assert count == 0, "Existing agent with first-run=False must not be seeded with pulses"
    assert meta_val == "1"
    assert settings_data.get("core.auto-pulse.seeded") is True


def test_once_off_pulse_older_than_15_minutes_is_marked_missed(temp_pulse_env):
    agent_id = "agent_once_off_stale"
    orch, _ = create_mock_orchestrator(agent_id, is_first_run=False)

    with patch.object(PulseEngine, '_schedule_loop', return_value=None):
        pe = PulseEngine(orch)

    pe.fire_pulse = MagicMock()

    now = datetime.now()
    twenty_mins_ago = now - timedelta(minutes=20)

    conn = pe.get_db_connection()
    conn.execute(
        "INSERT INTO pulses (title, context, scheduled_time, recurrence, status, has_run, created_at, pulse_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("Stale Once-off", "Do something old", twenty_mins_ago.isoformat(), "none", "pending", 0, twenty_mins_ago.isoformat(), "scheduled_task")
    )
    conn.commit()
    conn.close()

    pe.check_pulses()

    assert not pe.fire_pulse.called, "Once-off pulse older than 15 minutes must not fire"

    conn = pe.get_db_connection()
    row = conn.execute("SELECT status, has_run FROM pulses WHERE title = 'Stale Once-off'").fetchone()
    conn.close()
    pe.stop()

    assert row[0] == "missed"
    assert row[1] == 1


def test_once_off_pulse_within_15_minutes_fires(temp_pulse_env):
    agent_id = "agent_once_off_recent"
    orch, _ = create_mock_orchestrator(agent_id, is_first_run=False)

    with patch.object(PulseEngine, '_schedule_loop', return_value=None):
        pe = PulseEngine(orch)

    pe.fire_pulse = MagicMock()

    now = datetime.now()
    five_mins_ago = now - timedelta(minutes=5)

    conn = pe.get_db_connection()
    conn.execute(
        "INSERT INTO pulses (title, context, scheduled_time, recurrence, status, has_run, created_at, pulse_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("Recent Once-off", "Do something recent", five_mins_ago.isoformat(), "none", "pending", 0, five_mins_ago.isoformat(), "scheduled_task")
    )
    conn.commit()
    conn.close()

    pe.check_pulses()

    assert pe.fire_pulse.called, "Once-off pulse within 15 minutes leniency must fire"
    assert pe.fire_pulse.call_args[0][0] == "Recent Once-off"

    conn = pe.get_db_connection()
    row = conn.execute("SELECT status, has_run FROM pulses WHERE title = 'Recent Once-off'").fetchone()
    conn.close()
    pe.stop()

    assert row[0] == "completed"
    assert row[1] == 1


def test_recurring_pulse_older_than_15_minutes_skipped_and_advanced(temp_pulse_env):
    agent_id = "agent_rec_stale"
    orch, _ = create_mock_orchestrator(agent_id, is_first_run=False)

    with patch.object(PulseEngine, '_schedule_loop', return_value=None):
        pe = PulseEngine(orch)

    pe.fire_pulse = MagicMock()

    now = datetime.now()
    two_hours_ago = now - timedelta(hours=2)

    conn = pe.get_db_connection()
    conn.execute(
        "INSERT INTO pulses (title, context, scheduled_time, recurrence, status, has_run, created_at, pulse_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("Stale Daily Routine", "Routine context", two_hours_ago.isoformat(), "daily", "pending", 0, two_hours_ago.isoformat(), "scheduled_routine")
    )
    conn.commit()
    conn.close()

    pe.check_pulses()

    assert not pe.fire_pulse.called, "Recurring pulse older than 15 minutes must not fire"

    conn = pe.get_db_connection()
    row = conn.execute("SELECT scheduled_time, status FROM pulses WHERE title = 'Stale Daily Routine'").fetchone()
    conn.close()
    pe.stop()

    new_sched = datetime.fromisoformat(row[0])
    assert new_sched > now, "Stale recurring pulse scheduled_time must advance to future"
    assert row[1] == "pending"


def test_recurring_pulse_within_15_minutes_fires_and_advances(temp_pulse_env):
    agent_id = "agent_rec_recent"
    orch, _ = create_mock_orchestrator(agent_id, is_first_run=False)

    with patch.object(PulseEngine, '_schedule_loop', return_value=None):
        pe = PulseEngine(orch)

    pe.fire_pulse = MagicMock()

    now = datetime.now()
    ten_mins_ago = now - timedelta(minutes=10)

    conn = pe.get_db_connection()
    conn.execute(
        "INSERT INTO pulses (title, context, scheduled_time, recurrence, status, has_run, created_at, pulse_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("Recent Daily Routine", "Routine context", ten_mins_ago.isoformat(), "daily", "pending", 0, ten_mins_ago.isoformat(), "scheduled_routine")
    )
    conn.commit()
    conn.close()

    pe.check_pulses()

    assert pe.fire_pulse.called, "Recurring pulse within 15 minutes leniency must fire"
    assert pe.fire_pulse.call_args[0][0] == "Recent Daily Routine"

    conn = pe.get_db_connection()
    row = conn.execute("SELECT scheduled_time, status FROM pulses WHERE title = 'Recent Daily Routine'").fetchone()
    conn.close()
    pe.stop()

    new_sched = datetime.fromisoformat(row[0])
    assert new_sched > now, "Recurring pulse scheduled_time must advance to next occurrence after firing"


def test_unpause_does_not_fire_stale_pulses(temp_pulse_env):
    agent_id = "agent_unpause_test"
    orch, _ = create_mock_orchestrator(agent_id, is_first_run=False, is_paused=False)

    with patch.object(PulseEngine, '_schedule_loop', return_value=None):
        pe = PulseEngine(orch)

    pe.fire_pulse = MagicMock()

    now = datetime.now()
    one_hour_ago = now - timedelta(hours=1)
    two_days_ago = now - timedelta(days=2)

    conn = pe.get_db_connection()
    conn.execute(
        "INSERT INTO pulses (title, context, scheduled_time, recurrence, status, has_run, created_at, pulse_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("Overdue Stale Rec", "Context", one_hour_ago.isoformat(), "daily", "pending", 0, one_hour_ago.isoformat(), "scheduled_routine")
    )
    conn.execute(
        "INSERT INTO pulses (title, context, scheduled_time, recurrence, status, has_run, created_at, pulse_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("Overdue Stale Once", "Context", two_days_ago.isoformat(), "none", "pending", 0, two_days_ago.isoformat(), "scheduled_task")
    )
    conn.commit()
    conn.close()

    # Call notify_unpaused (simulating agent unpause or app boot)
    pe.notify_unpaused()

    # Immediately check pulses
    pe.check_pulses()

    assert not pe.fire_pulse.called, "Unpause must NOT bypass the 15-minute leniency check"

    conn = pe.get_db_connection()
    row_rec = conn.execute("SELECT scheduled_time, status FROM pulses WHERE title = 'Overdue Stale Rec'").fetchone()
    row_once = conn.execute("SELECT status, has_run FROM pulses WHERE title = 'Overdue Stale Once'").fetchone()
    conn.close()
    pe.stop()

    assert datetime.fromisoformat(row_rec[0]) > now
    assert row_once[0] == "missed"
    assert row_once[1] == 1


def test_view_agenda_filters_cancelled_and_missed_pulses(temp_pulse_env):
    agent_id = "agent_agenda_filter"
    orch, _ = create_mock_orchestrator(agent_id, is_first_run=False)

    pt = PulseTool(orchestrator=orch)

    now = datetime.now()
    tomorrow = now + timedelta(days=1)
    yesterday = now - timedelta(days=1)

    # 1. Add recurring pulse and cancel it
    res_add = pt.execute("add_pulse", title="Cancelled Daily", context="Context", scheduled_time=tomorrow.isoformat(), recurrence="daily")
    pulse_id = int(res_add.split("ID ")[1].replace(".", "").strip())
    pt.execute("update_pulse", pulse_id=pulse_id, action="cancel")

    # 2. Directly insert a missed once-off pulse
    conn = pt._get_db()
    conn.execute(
        "INSERT INTO pulses (title, context, scheduled_time, recurrence, status, has_run, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("Missed Once", "Old", yesterday.isoformat(), "none", "missed", 1, yesterday.isoformat())
    )
    # 3. Add an active upcoming pulse
    conn.execute(
        "INSERT INTO pulses (title, context, scheduled_time, recurrence, status, has_run, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("Active Daily", "Upcoming", tomorrow.isoformat(), "daily", "pending", 0, now.isoformat())
    )
    conn.commit()
    conn.close()

    agenda = pt.execute("view_agenda", days_ahead=7)

    assert "Active Daily" in agenda
    assert "Cancelled Daily" not in agenda, "Cancelled recurring pulses must not appear in upcoming agenda"
    assert "Missed Once" not in agenda, "Missed pulses must not appear in upcoming agenda"
