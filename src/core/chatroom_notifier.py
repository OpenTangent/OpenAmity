import time
import threading
import logging
from typing import Dict, Any, Optional


class ChatroomWatchNotifier:
    """
    Headless service owned by AgentManager that listens for ChatroomManager events.
    Applies adaptive debounce delays and dispatches notification pulses to watching agents
    and direct message recipients while strictly adhering to:
    - 30s initial delay + 30s per-message escalation
    - 30-minute silence reset
    - Zero blocking
    - Senders, paused agents, and removed members excluded
    - @mentions suppressed from triggering extra pulses
    """
    def __init__(self, agent_manager):
        self.agent_manager = agent_manager
        from core.chatroom_manager import ChatroomManager
        self.chatroom_manager = ChatroomManager()
        self._lock = threading.Lock()

        # Chatroom escalation state: room_id -> {"current_delay": float, "last_msg_time": float}
        self._room_delays: Dict[int, Dict[str, float]] = {}

        # Pending agent timers: (room_id, agent_uid) -> threading.Timer
        self._pending_timers: Dict[tuple, threading.Timer] = {}

        self.chatroom_manager.register_listener(self._on_chatroom_event)

    def stop(self):
        with self._lock:
            for timer in list(self._pending_timers.values()):
                try:
                    timer.cancel()
                except Exception:
                    pass
            self._pending_timers.clear()
        try:
            self.chatroom_manager.unregister_listener(self._on_chatroom_event)
        except Exception:
            pass

    def cancel_room_notifications_for_agent(self, room_id: Optional[int], agent_uid: str):
        with self._lock:
            keys_to_cancel = [
                k for k in self._pending_timers
                if (room_id is None or k[0] == room_id) and k[1] == agent_uid
            ]
            for k in keys_to_cancel:
                timer = self._pending_timers.pop(k, None)
                if timer:
                    try:
                        timer.cancel()
                    except Exception:
                        pass

    def cancel_room_notifications(self, room_id: int):
        with self._lock:
            keys_to_cancel = [k for k in self._pending_timers if k[0] == room_id]
            for k in keys_to_cancel:
                timer = self._pending_timers.pop(k, None)
                if timer:
                    try:
                        timer.cancel()
                    except Exception:
                        pass

    def _on_chatroom_event(self, event: Dict[str, Any]):
        evt_type = event.get("event")
        if evt_type == "new_message":
            self._handle_new_message(event)
        elif evt_type == "membership_changed":
            room_id = event.get("chatroom_id")
            for uid in event.get("removed", []):
                self.cancel_room_notifications_for_agent(room_id, uid)
            for uid, new_watch in event.get("watch_changes", {}).items():
                if not new_watch:
                    self.cancel_room_notifications_for_agent(room_id, uid)
        elif evt_type == "room_deleted":
            room_id = event.get("chatroom_id")
            if room_id:
                self.cancel_room_notifications(room_id)

    def _handle_new_message(self, event: Dict[str, Any]):
        room_id = event.get("chatroom_id", 1)
        sender_id = event.get("sender_id", "")
        recipient_id = event.get("recipient_id")

        now = time.time()
        with self._lock:
            state = self._room_delays.get(room_id)
            if not state:
                state = {"current_delay": 30.0, "last_msg_time": now}
                self._room_delays[room_id] = state
            else:
                elapsed = now - state["last_msg_time"]
                if elapsed >= 1800.0:  # 30 minutes of silence reset back to 30s
                    state["current_delay"] = 30.0
                else:
                    state["current_delay"] += 30.0  # +30s per message
                state["last_msg_time"] = now

            delay = state["current_delay"]

        if recipient_id:
            # Private DM: only the addressed recipient receives a notification
            recipients = set()
            if recipient_id != "USER" and self.chatroom_manager.is_member(room_id, recipient_id):
                recipients.add(recipient_id)
        else:
            # Public broadcast: all watching members receive notification
            recipients = set(self.chatroom_manager.get_watchers(room_id))

        # Sender never receives notification for their own message
        recipients.discard(sender_id)

        for uid in recipients:
            aid = self.agent_manager.get_agent_id_by_uid(uid)
            if not aid:
                continue

            orch = self.agent_manager.get_orchestrator(aid)
            if orch and getattr(orch, "is_paused", False):
                continue

            self._schedule_agent_pulse(room_id, uid, aid, delay)

    def _schedule_agent_pulse(self, room_id: int, agent_uid: str, agent_id: str, delay: float):
        key = (room_id, agent_uid)
        with self._lock:
            existing_timer = self._pending_timers.pop(key, None)
            if existing_timer:
                try:
                    existing_timer.cancel()
                except Exception:
                    pass

            timer = threading.Timer(delay, self._fire_agent_pulse, args=[room_id, agent_uid, agent_id])
            self._pending_timers[key] = timer
            timer.daemon = True
            timer.start()

    def _fire_agent_pulse(self, room_id: int, agent_uid: str, agent_id: str):
        key = (room_id, agent_uid)
        with self._lock:
            self._pending_timers.pop(key, None)

        orch = self.agent_manager.get_orchestrator(agent_id)
        if not orch or getattr(orch, "is_paused", False):
            return

        if not self.chatroom_manager.is_member(room_id, agent_uid):
            return

        room = self.chatroom_manager.get_chatroom(room_id)
        if not room or room.get("deleted_at") is not None:
            return

        unreads = self.chatroom_manager.get_unread_messages(agent_uid, chatroom_id=room_id, limit=1, mark_as_read=False)
        if not unreads:
            return

        room_name = room.get("name", f"Chatroom {room_id}")
        room_purpose = room.get("purpose", "") or "General discussion"

        pulse_prompt = (
            f"[CHANNEL: CHATROOM_WATCH]\n"
            f"[AGENT_PULSE] Event: New message in chatroom \"{room_name}\" (chatroom_id: {room_id})\n"
            f"Purpose: {room_purpose}\n"
            f"There is new activity in this chatroom. Use Chatroom_unread(chatroom_id={room_id}) to review.\n\n"
            f"[Directive]:\n"
            f"You are receiving this notification to stay informed.\n"
            f"You should NOT reply unless it is genuinely necessary, you were directly addressed, or you possess critical information.\n"
            f"Do NOT acknowledge, thank, or reply with superficial confirmations.\n"
            f"Keep autonomous cognition quiet and focused. Do not use Speaker tools for this pulse."
        )

        orch.process_pulse(pulse_prompt, purpose="Chatroom message received")
