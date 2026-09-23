import sqlite3
import os
import calendar
from datetime import datetime, timedelta
from typing import List, Dict, Any
from core.cerebrum import Tool


class PulseTool(Tool):
    name = "Pulse"
    icon = "🫀"
    color = "#673AB7"
    async_commands = []
    description = "Allows you to manage your own Autonomy Pulses (your scheduled tasks and routines)."
    commands = ["add_pulse", "update_pulse", "view_agenda"]

    def __init__(self, orchestrator=None):
        super().__init__(orchestrator)

    def get_tool_declarations(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "PulseTool_add_pulse",
                "description": "Schedule a new Autonomy Pulse (a task, reminder, or routine) for yourself.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "title": {
                            "type": "STRING",
                            "description": "A brief title for the pulse."
                        },
                        "context": {
                            "type": "STRING",
                            "description": "The detailed instructions or context you will receive when the pulse triggers."
                        },
                        "scheduled_time": {
                            "type": "STRING",
                            "description": "The ISO format date and time to trigger the pulse (e.g., '2026-06-23T15:00:00')."
                        },
                        "recurrence": {
                            "type": "STRING",
                            "description": "How often the pulse repeats: 'none', 'daily', 'weekly', 'monthly'."
                        }
                    },
                    "required": ["title", "context", "scheduled_time", "recurrence"]
                }
            },
            {
                "name": "PulseTool_update_pulse",
                "description": "Update the status of an existing Autonomy Pulse, such as completing it, deleting it, or snoozing it.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "pulse_id": {
                            "type": "INTEGER",
                            "description": "The ID of the pulse."
                        },
                        "action": {
                            "type": "STRING",
                            "description": "The action to perform: 'complete', 'cancel', 'snooze', or 'delete'."
                        },
                        "new_time": {
                            "type": "STRING",
                            "description": "If action is 'snooze', provide the new ISO format date and time. Otherwise, omit."
                        }
                    },
                    "required": ["pulse_id", "action"]
                }
            },
            {
                "name": "PulseTool_view_agenda",
                "description": "View your upcoming scheduled pulses to manage your cognitive budget.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "days_ahead": {
                            "type": "INTEGER",
                            "description": "How many days ahead to view. Default is 7."
                        }
                    }
                }
            }
        ]

    def _get_db(self):
        from config import paths
        agent_id = self.orchestrator.agent_id if self.orchestrator else None
        db_dir = paths.get_base_dir_for(agent_id)
        os.makedirs(db_dir, exist_ok=True)
        db_path = os.path.join(db_dir, "pulses.db")
        conn = sqlite3.connect(db_path)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS pulses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                context TEXT,
                scheduled_time TEXT,
                recurrence TEXT,
                status TEXT,
                has_run BOOLEAN DEFAULT 0,
                created_at TEXT,
                pulse_type TEXT DEFAULT 'standard'
            )
        ''')
        return conn

    def execute(self, command: str, *args, **kwargs) -> str:
        clean_kwargs = {k: v for k, v in kwargs.items() if not k.startswith("_")}
        if command == "add_pulse":
            return self._add_pulse(**clean_kwargs)
        elif command in ["update_pulse", "manage_pulse"]:
            return self._update_pulse(**clean_kwargs)
        elif command == "view_agenda":
            return self._view_agenda(**clean_kwargs)
        return f"Unknown command: {command}"

    def _add_pulse(self, title: str, context: str, scheduled_time: str, recurrence: str, **kwargs) -> str:
        try:
            parsed_dt = datetime.fromisoformat(scheduled_time)
            if parsed_dt.tzinfo is not None:
                scheduled_time = parsed_dt.astimezone().replace(tzinfo=None).isoformat()
        except ValueError:
            return "Error: scheduled_time must be a valid ISO format string."

        if recurrence not in ['none', 'daily', 'weekly', 'monthly']:
            return "Error: recurrence must be one of 'none', 'daily', 'weekly', 'monthly'."

        conn = self._get_db()
        try:
            c = conn.cursor()
            c.execute('''
                INSERT INTO pulses (title, context, scheduled_time, recurrence, status, has_run, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (title, context, scheduled_time, recurrence, "pending", 0, datetime.now().isoformat()))
            conn.commit()
            pulse_id = c.lastrowid
        finally:
            conn.close()

        return f"Success: Pulse '{title}' scheduled with ID {pulse_id}."

    def _update_pulse(self, pulse_id: int, action: str, new_time: str = None) -> str:
        if action not in ['complete', 'cancel', 'snooze', 'delete']:
            return "Error: action must be 'complete', 'cancel', 'snooze', or 'delete'."

        if action == 'snooze' and not new_time:
            return "Error: new_time is required when snoozing."

        conn = self._get_db()
        try:
            c = conn.cursor()

            c.execute('SELECT recurrence FROM pulses WHERE id = ?', (pulse_id,))
            row = c.fetchone()
            if not row:
                return f"Error: Pulse ID {pulse_id} not found."

            recurrence = row[0]

            if action == 'delete':
                if recurrence == 'none':
                    c.execute(
                        'UPDATE pulses SET status="deleted" WHERE id=?', (pulse_id,))
                    cutoff = (datetime.now() - timedelta(days=30)).isoformat()
                    c.execute(
                        'DELETE FROM pulses WHERE status="deleted" AND scheduled_time < ?', (cutoff,))
                    msg = f"Success: Once-off Pulse ID {pulse_id} flagged as deleted."
                else:
                    c.execute('DELETE FROM pulses WHERE id=?', (pulse_id,))
                    msg = f"Success: Recurring Pulse ID {pulse_id} permanently removed."
            elif action == 'snooze':
                try:
                    parsed_dt = datetime.fromisoformat(new_time)
                    if parsed_dt.tzinfo is not None:
                        new_time = parsed_dt.astimezone().replace(tzinfo=None).isoformat()
                except ValueError:
                    return "Error: new_time must be a valid ISO format string."
                c.execute(
                    'UPDATE pulses SET scheduled_time=?, status="pending", has_run=0 WHERE id=?', (new_time, pulse_id))
                msg = f"Success: Pulse ID {pulse_id} snoozed to {new_time}."
            else:
                # complete or cancel
                c.execute('UPDATE pulses SET status=? WHERE id=?',
                          (action, pulse_id))
                msg = f"Success: Pulse ID {pulse_id} marked as {action}."

            conn.commit()
            return msg
        finally:
            conn.close()

    _manage_pulse = _update_pulse

    @staticmethod
    def _next_recurrence_dt(dt: datetime, recurrence: str, target_day: int) -> datetime:
        if recurrence == 'daily':
            return dt + timedelta(days=1)
        elif recurrence == 'weekly':
            return dt + timedelta(days=7)
        elif recurrence == 'monthly':
            month = dt.month % 12 + 1
            year = dt.year + (dt.month // 12)
            max_days = calendar.monthrange(year, month)[1]
            d = min(target_day, max_days)
            return dt.replace(year=year, month=month, day=d)
        else:
            return dt + timedelta(days=1)

    def _view_agenda(self, days_ahead: int = 7, **kwargs) -> str:
        try:
            days_ahead = int(days_ahead)
        except (ValueError, TypeError):
            days_ahead = 7

        now = datetime.now()
        end_date = now + timedelta(days=days_ahead)

        conn = self._get_db()
        try:
            c = conn.cursor()
            c.execute('''
                SELECT id, title, scheduled_time, recurrence, status
                FROM pulses 
                WHERE status != 'deleted' AND scheduled_time <= ?
                ORDER BY scheduled_time ASC
            ''', (end_date.isoformat(),))
            rows = c.fetchall()
        finally:
            conn.close()

        items = []
        for row in rows:
            p_id, title, sched, rec, status = row
            try:
                dt = datetime.fromisoformat(sched)
                if dt.tzinfo is not None:
                    dt = dt.astimezone().replace(tzinfo=None)
            except Exception:
                continue

            if rec == 'none':
                if (now - timedelta(minutes=15) <= dt <= end_date) or (status == 'pending' and dt <= end_date):
                    items.append((dt, p_id, title, status, rec))
            else:
                target_day = dt.day
                curr = dt

                # If curr is in the past, advance it toward the horizon
                while curr < now - timedelta(minutes=15) and curr < end_date:
                    next_curr = self._next_recurrence_dt(curr, rec, target_day)
                    if next_curr <= curr:
                        break
                    curr = next_curr

                # Project occurrences up to end_date
                counter = 0
                while curr <= end_date and counter < 1000:
                    if curr >= now - timedelta(minutes=15) or curr == dt:
                        items.append((curr, p_id, title, status, rec))
                    next_curr = self._next_recurrence_dt(curr, rec, target_day)
                    if next_curr <= curr:
                        break
                    curr = next_curr
                    counter += 1

        if not items:
            return f"Your agenda is clear for the next {days_ahead} days."

        # Chronological sort: by occurrence datetime, then pulse ID
        items.sort(key=lambda x: (x[0], x[1]))

        agenda = [f"Upcoming Agenda (Next {days_ahead} days):"]
        for dt, p_id, title, status, rec in items:
            sched_display = dt.strftime("%Y-%m-%d %H:%M")
            rec_str = f" (Repeats: {rec})" if rec != 'none' else ""
            agenda.append(
                f"[{p_id}] {sched_display} | {title} | Status: {status}{rec_str}")

        return "\n".join(agenda)
