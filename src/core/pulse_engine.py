import time
import threading
import logging
import sqlite3
import os
from datetime import datetime, timedelta
from .events import Signal
try:
    from core.settings_manager import SettingsManager
    from core.address_book import AddressBookManager
except ImportError:
    from .settings_manager import SettingsManager
    from .address_book import AddressBookManager

from config import paths
DB_DIR = paths.get_app_data_dir()
DB_PATH = os.path.join(DB_DIR, "pulses.db")

class PulseEngine:
    def __init__(self, orchestrator):
        self.trigger_pulse = Signal()
        self.orchestrator = orchestrator
        self.settings_manager = SettingsManager()
        self.address_book_manager = AddressBookManager()
        self.last_interaction_time = time.time()
        
        self.init_db()
        
        # WhatsApp state
        self.last_pulse_time = 0
        self.whatsapp_timer = None
        self.pending_whatsapp_sender = None
        
        # Timer for time-based checking
        self.is_running = True
        self.schedule_thread = threading.Thread(target=self._schedule_loop, daemon=True)
        self.schedule_thread.start()

    def stop(self):
        self.is_running = False
        if self.whatsapp_timer:
            self.whatsapp_timer.cancel()

    def _schedule_loop(self):
        # Do an immediate check on boot (with a slight delay to let UI load)
        time.sleep(5)
        self.check_pulses()
        while self.is_running:
            time.sleep(60)
            self.check_pulses()

    def get_db_connection(self):
        return sqlite3.connect(DB_PATH)

    def init_db(self):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = self.get_db_connection()
        c = conn.cursor()
        
        c.execute('''
            CREATE TABLE IF NOT EXISTS pulses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                context TEXT,
                scheduled_time TEXT,
                recurrence TEXT,
                status TEXT,
                has_run BOOLEAN DEFAULT 0,
                created_at TEXT,
                pulse_type TEXT DEFAULT 'silent'
            )
        ''')
        
        self._migrate_db(c)
        
        # Migration: Check if table is empty
        c.execute('SELECT COUNT(*) FROM pulses')
        if c.fetchone()[0] == 0:
            logging.info("PulseEngine: Empty database detected. Migrating initial lifecycle pulses.")
            now = datetime.now()
            
            pulses_to_seed = [
                (
                    "Morning Kickoff",
                    "Get the ball rolling. Think about what needs to be done today to advance your trajectory. Divide the work into manageable chunks and use the PulseTool to schedule targeted pulses for them. If you lack context on the user's goals, make it your priority to find out.",
                    now.replace(hour=9, minute=0, second=0, microsecond=0)
                ),
                (
                    "Mid-Day Check-In",
                    "Review your trajectory data and recent short-term memories. Ensure that your current tasks and aspirations are actively moving you towards your intended targets. Course-correct if you have drifted or gotten distracted.",
                    now.replace(hour=13, minute=0, second=0, microsecond=0)
                ),
                (
                    "Evening Reflection",
                    "Reflect on what has been achieved today. Critically evaluate not just what was done, but how you reasoned. Update your Trajectory reflection state and record any meaningful lessons. Crucially, if you received feedback or formed new opinions about yourself or others, use MemPalace_update_mirror to update your Theory of Mind records in your Sanctuary.",
                    now.replace(hour=17, minute=0, second=0, microsecond=0)
                )
            ]
            
            for title, context, sched in pulses_to_seed:
                c.execute('''
                    INSERT INTO pulses (title, context, scheduled_time, recurrence, status, has_run, created_at, pulse_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (title, context, sched.isoformat(), "daily", "pending", 0, now.isoformat(), "silent"))
            
        conn.commit()
        conn.close()

    def _migrate_db(self, cursor):
        cursor.execute("PRAGMA table_info(pulses)")
        existing_columns = {info[1] for info in cursor.fetchall()}
        
        expected_columns = {
            "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
            "title": "TEXT",
            "context": "TEXT",
            "scheduled_time": "TEXT",
            "recurrence": "TEXT",
            "status": "TEXT",
            "has_run": "BOOLEAN DEFAULT 0",
            "created_at": "TEXT",
            "pulse_type": "TEXT DEFAULT 'silent'"
        }
        
        for col, col_def in expected_columns.items():
            if col not in existing_columns:
                logging.info(f"PulseEngine: Migrating DB, adding missing column '{col}'")
                try:
                    cursor.execute(f"ALTER TABLE pulses ADD COLUMN {col} {col_def}")
                except Exception as e:
                    logging.error(f"PulseEngine: Failed to add column '{col}': {e}")

    def user_interacted(self):
        self.last_interaction_time = time.time()
        
    def is_idle(self):
        sys_settings = self.settings_manager.get("core.auto-pulse", {})
        idle_timeout = sys_settings.get("idle-timeout-minutes", 1)
        return (time.time() - self.last_interaction_time) > (idle_timeout * 60)
        
    def is_deep_idle(self):
        return (time.time() - self.last_interaction_time) > (4 * 60 * 60) # 4 hours
        
    def calculate_next_recurrence(self, current_sched, recurrence, now):
        next_time = current_sched
        while next_time <= now:
            if recurrence == 'daily':
                next_time += timedelta(days=1)
            elif recurrence == 'weekly':
                next_time += timedelta(days=7)
            elif recurrence == 'monthly':
                month = next_time.month % 12 + 1
                year = next_time.year + (next_time.month // 12)
                d = next_time.day
                while d > 28:
                    try:
                        next_time = next_time.replace(year=year, month=month, day=d)
                        break
                    except ValueError:
                        d -= 1
                if d <= 28:
                     next_time = next_time.replace(year=year, month=month, day=d)
            else:
                next_time += timedelta(days=1)
        return next_time

    def check_pulses(self):
        if not self.is_idle() or self.orchestrator.is_busy:
            return
            
        # Check for sleep cycle (Memory Consolidation)
        if self.is_deep_idle():
            last_sleep = self.settings_manager.get("core.auto-pulse.last-sleep-cycle", 0)
            if (time.time() - last_sleep) > (8 * 60 * 60): # 8 hours rate limit
                self.settings_manager.set("core.auto-pulse.last-sleep-cycle", time.time())
                self.settings_manager.save()
                
                title = "Sleep Cycle (Memory Consolidation)"
                context = "You have been idle for over 4 hours. It is time for a Sleep Cycle. Review your active session history. Synthesize this episodic memory into generalized facts and store them in the Sanctuary or Deep Search (Chroma) if they are important. Then, update your short-term memory (using MemPalace) so that you have a condensed summary of your current state and ongoing tasks before this session is archived. Take your time to get this right."
                self.fire_pulse(title, context, "sleep_cycle")
                return # Give sleep cycle priority
            
        now = datetime.now()
        conn = self.get_db_connection()
        c = conn.cursor()
        
        # Fetch pending pulses scheduled in the past
        c.execute('SELECT id, title, context, scheduled_time, recurrence, has_run, pulse_type FROM pulses WHERE status="pending" AND scheduled_time <= ?', (now.isoformat(),))
        pending = c.fetchall()
        
        for p in pending:
            p_id, title, context, sched_str, recurrence, has_run, pulse_type = p
            sched = datetime.fromisoformat(sched_str)
            
            if recurrence == 'none':
                # Fire once-off pulse. It fires even if it was missed while offline.
                self.fire_pulse(title, context, pulse_type)
                c.execute('UPDATE pulses SET has_run=1, status="completed" WHERE id=?', (p_id,))
                conn.commit()
                break # Process one pulse at a time to prevent cognitive overload
            else:
                # It's a recurring pulse
                next_sched = self.calculate_next_recurrence(sched, recurrence, now)
                
                # Check if it was missed (offline or busy for more than 15 mins)
                delta = (now - sched).total_seconds()
                if delta > 900: # 15 minutes grace period
                    logging.info(f"PulseEngine: Skipped missed recurring pulse '{title}' (was scheduled for {sched_str})")
                    c.execute('UPDATE pulses SET scheduled_time=? WHERE id=?', (next_sched.isoformat(), p_id))
                    conn.commit()
                else:
                    # Within grace period, fire it
                    self.fire_pulse(title, context, pulse_type)
                    c.execute('UPDATE pulses SET scheduled_time=? WHERE id=?', (next_sched.isoformat(), p_id))
                    conn.commit()
                    break

        conn.close()

    def fire_pulse(self, title, context, pulse_type="standard"):
        if pulse_type == "silent":
            prompt = f"[CHANNEL: SYSTEM_CONTEMPLATION]\n[AGENT_PULSE] Event: {title}\nContext:\n{context}\n[Directive]: This is a silent contemplation cycle. The system will not automatically vocalize your internal monologue. You do not need to use the Speaker tool to summarize, but you may still explicitly use your voice (via Speaker or WhatsApp voice notes) if you have an urgent realization or deem it necessary to speak."
        else:
            prompt = f"[CHANNEL: SYSTEM_SCHEDULE]\n[AGENT_PULSE] Event: {title}\nContext:\n{context}"
        self.trigger_pulse.emit(prompt)

    # --- WhatsApp Handling Ported from WakeUpService ---
    def handle_whatsapp_message(self, sender_id, sender_name):
        if sender_id.endswith("@g.us"):
            return

        sys_settings = self.settings_manager.get("core.auto-pulse", {})
        whitelist = sys_settings.get("whitelist", [])

        clean_sender = sender_id.replace("@c.us", "").replace("@g.us", "").replace("@lid", "")
        contact = self.address_book_manager.lookup_by_number(clean_sender)
        
        if contact:
            rel = f" ({contact['relationship']})" if contact.get("relationship") else ""
            sender_name = f"{contact['name']}{rel}"

        matched = False
        for w_item in whitelist:
            w_clean = w_item.replace("+", "").replace(" ", "")
            if clean_sender.endswith(w_clean) or clean_sender == w_clean:
                matched = True
                break
            if sender_name.lower() == w_item.strip().lower():
                matched = True
                break

        if not matched:
            return            
            
        cooldown = sys_settings.get("ratelimit-minutes", 15)
        if (time.time() - self.last_pulse_time) < (cooldown * 60):
            logging.info("PulseEngine: WhatsApp pulse suppressed due to rate limiting.")
            return
            
        if not self.is_idle() or self.orchestrator.is_busy:
            logging.info("PulseEngine: WhatsApp pulse suppressed due to active system state.")
            return

        buffer_seconds = sys_settings.get("buffer-seconds", 30)
        self.pending_whatsapp_sender = sender_name or f"+{clean_sender}"
        self.pending_whatsapp_sender_id = sender_id
        self.pending_whatsapp_is_group = sender_id.endswith("@g.us")
        
        if self.whatsapp_timer:
            self.whatsapp_timer.cancel()
            
        self.whatsapp_timer = threading.Timer(buffer_seconds, self.execute_whatsapp_pulse, args=[sys_settings])
        self.whatsapp_timer.start()
        logging.debug(f"PulseEngine: WhatsApp message from {self.pending_whatsapp_sender} buffered for {buffer_seconds}s.")

    def execute_whatsapp_pulse(self, sys_settings):
        if not self.is_idle() or self.orchestrator.is_busy:
            logging.info("PulseEngine: WhatsApp pulse aborted, system no longer idle.")
            return
            
        self.last_pulse_time = time.time()
        
        channel = "WHATSAPP_GROUP" if getattr(self, 'pending_whatsapp_is_group', False) else "WHATSAPP_DM"
        prompt = f"[CHANNEL: {channel}]\n[SOURCE_ID: {getattr(self, 'pending_whatsapp_sender_id', '')}]\n[AGENT_PULSE] {self.pending_whatsapp_sender} has messaged you. Read their message and respond appropriately."
        self.trigger_pulse.emit(prompt)
