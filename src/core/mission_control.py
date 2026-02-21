
import sqlite3
import os
import datetime
from typing import List, Dict, Optional

class MissionControl:
    def __init__(self, db_path="src/memory/data/mission_control.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initialize the SQLite database."""
        # Ensure directory exists
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Goals table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL, -- overarching, long_term, short_term, task
                description TEXT NOT NULL,
                status TEXT DEFAULT 'pending', -- pending, in_progress, completed, failed
                parent_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(parent_id) REFERENCES goals(id)
            )
        ''')
        conn.commit()
        conn.close()

    def add_goal(self, type: str, description: str, parent_id: Optional[int] = None) -> int:
        """Adds a new goal or task."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO goals (type, description, parent_id)
            VALUES (?, ?, ?)
        ''', (type, description, parent_id))
        goal_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return goal_id

    def update_status(self, goal_id: int, status: str):
        """Updates the status of a goal."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE goals SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (status, goal_id))
        conn.commit()
        conn.close()

    def get_goals(self, type: Optional[str] = None, status: Optional[str] = None, parent_id: Optional[int] = None) -> List[Dict]:
        """Retrieves goals matching criteria."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        query = "SELECT * FROM goals WHERE 1=1"
        params = []
        
        if type:
            query += " AND type = ?"
            params.append(type)
        if status:
            query += " AND status = ?"
            params.append(status)
        if parent_id:
            query += " AND parent_id = ?"
            params.append(parent_id)
            
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]

    def get_active_goals_summary(self) -> str:
        """Generates a summary of active goals for the system prompt."""
        summary = "### Mission Control (Active Goals)
"
        
        # Overarching
        overarching = self.get_goals(type="overarching", status="in_progress")
        if overarching:
            summary += "**Overarching Goals:**
"
            for g in overarching:
                summary += f"- [{g['id']}] {g['description']}
"
        
        # Long-Term
        long_term = self.get_goals(type="long_term", status="in_progress")
        if long_term:
            summary += "**Long-Term Goals:**
"
            for g in long_term:
                summary += f"- [{g['id']}] {g['description']}
"
                
        # Short-Term
        short_term = self.get_goals(type="short_term", status="in_progress")
        if short_term:
            summary += "**Current Objectives:**
"
            for g in short_term:
                summary += f"- [{g['id']}] {g['description']}
"
        
        return summary
