import sqlite3
import os
from datetime import datetime, timedelta
from typing import List, Dict, Any
from core.cerebrum import Tool

from config import paths
DB_DIR = paths.get_app_data_dir()
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "pulses.db")
class PulseTool(Tool):
    name = "PulseTool"
    description = "Allows you to manage your own Autonomy Pulses (your scheduled tasks and routines)."
    commands = ["add_pulse", "update_pulse", "view_agenda"]

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
                        },
                        "pulse_type": {
                            "type": "STRING",
                            "description": "The type of pulse: 'silent' (default, recommendation for internal thought/maintenance) or 'standard' (recommendation for vocalized responses). Note: Since you have full autonomy over your speech via the Speaker tool, this is merely a recommendation from the scheduler."
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
        return sqlite3.connect(DB_PATH)

    def execute(self, command: str, *args, **kwargs) -> str:
        if command == "add_pulse":
            return self._add_pulse(**kwargs)
        elif command == "update_pulse":
            return self._update_pulse(**kwargs)
        elif command == "view_agenda":
            return self._view_agenda(**kwargs)
        return f"Unknown command: {command}"

    def _add_pulse(self, title: str, context: str, scheduled_time: str, recurrence: str, pulse_type: str = "silent") -> str:
        try:
            datetime.fromisoformat(scheduled_time)
        except ValueError:
            return "Error: scheduled_time must be a valid ISO format string."
            
        if recurrence not in ['none', 'daily', 'weekly', 'monthly']:
            return "Error: recurrence must be one of 'none', 'daily', 'weekly', 'monthly'."
            
        if pulse_type not in ['silent', 'standard']:
            return "Error: pulse_type must be either 'silent' or 'standard'."

        conn = self._get_db()
        c = conn.cursor()
        c.execute('''
            INSERT INTO pulses (title, context, scheduled_time, recurrence, status, has_run, created_at, pulse_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (title, context, scheduled_time, recurrence, "pending", 0, datetime.now().isoformat(), pulse_type))
        conn.commit()
        pulse_id = c.lastrowid
        conn.close()
        
        return f"Success: Pulse '{title}' scheduled with ID {pulse_id}."

    def _update_pulse(self, pulse_id: int, action: str, new_time: str = None) -> str:
        if action not in ['complete', 'cancel', 'snooze', 'delete']:
            return "Error: action must be 'complete', 'cancel', 'snooze', or 'delete'."
            
        if action == 'snooze' and not new_time:
            return "Error: new_time is required when snoozing."

        conn = self._get_db()
        c = conn.cursor()
        
        c.execute('SELECT recurrence FROM pulses WHERE id = ?', (pulse_id,))
        row = c.fetchone()
        if not row:
            conn.close()
            return f"Error: Pulse ID {pulse_id} not found."
            
        recurrence = row[0]

        if action == 'delete':
            if recurrence == 'none':
                c.execute('UPDATE pulses SET status="deleted" WHERE id=?', (pulse_id,))
                msg = f"Success: Once-off Pulse ID {pulse_id} flagged as deleted."
            else:
                c.execute('DELETE FROM pulses WHERE id=?', (pulse_id,))
                msg = f"Success: Recurring Pulse ID {pulse_id} permanently removed."
        elif action == 'snooze':
            try:
                datetime.fromisoformat(new_time)
            except ValueError:
                conn.close()
                return "Error: new_time must be a valid ISO format string."
            c.execute('UPDATE pulses SET scheduled_time=?, status="pending", has_run=0 WHERE id=?', (new_time, pulse_id))
            msg = f"Success: Pulse ID {pulse_id} snoozed to {new_time}."
        else:
            # complete or cancel
            c.execute('UPDATE pulses SET status=? WHERE id=?', (action, pulse_id))
            msg = f"Success: Pulse ID {pulse_id} marked as {action}."

        conn.commit()
        conn.close()
        return msg

    def _view_agenda(self, days_ahead: int = 7) -> str:
        now = datetime.now()
        end_date = now + timedelta(days=days_ahead)
        
        conn = self._get_db()
        c = conn.cursor()
        
        c.execute('''
            SELECT id, title, scheduled_time, recurrence, status, pulse_type
            FROM pulses 
            WHERE status != 'deleted' AND scheduled_time <= ?
            ORDER BY scheduled_time ASC
        ''', (end_date.isoformat(),))
        
        rows = c.fetchall()
        conn.close()
        
        if not rows:
            return f"Your agenda is clear for the next {days_ahead} days."
            
        agenda = [f"Upcoming Agenda (Next {days_ahead} days):"]
        for row in rows:
            p_id, title, sched, rec, status, p_type = row
            # Simplify time display
            try:
                dt = datetime.fromisoformat(sched)
                sched_display = dt.strftime("%Y-%m-%d %H:%M")
            except:
                sched_display = sched
                
            rec_str = f" (Repeats: {rec})" if rec != 'none' else ""
            agenda.append(f"[{p_id}] {sched_display} | {title} | Type: {p_type} | Status: {status}{rec_str}")
            
        return "\n".join(agenda)
