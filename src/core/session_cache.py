import os
import json
import time
import logging
from typing import List, Dict, Any, Optional

from config import paths
from core.file_utils import atomic_json_write

logger = logging.getLogger("core.session_cache")


def normalize_session_messages(
    messages: List[Dict[str, Any]],
    in_flight_prompt: Optional[str] = None,
    close_trailing_user: bool = False
) -> List[Dict[str, str]]:
    """
    Normalizes a list of message turns across provider formats into standard
    'user' and 'assistant' text turns with strict role alternation.
    
    - Converts all provider-specific role variants to 'user' or 'assistant'.
    - Replaces inline base64 media blocks with concise text placeholders.
    - Appends any in-flight prompt if not already present.
    - Merges consecutive messages with the same role to respect alternation.
    - If close_trailing_user=True and the final turn is 'user', appends an assistant closure
      so models with strict alternating role constraints do not reject the history.
    """
    if not messages and not in_flight_prompt:
        return []

    raw_turns: List[Dict[str, str]] = []

    for msg in messages:
        if not isinstance(msg, dict):
            continue

        raw_role = str(msg.get("role", "")).strip().lower()
        if raw_role in ("model", "assistant"):
            role = "assistant"
        else:
            role = "user"

        content = msg.get("content", "")
        text_parts = []

        if isinstance(content, str):
            if content.strip():
                text_parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, str):
                    if block.strip():
                        text_parts.append(block)
                elif isinstance(block, dict):
                    b_type = block.get("type", "")
                    if b_type == "text":
                        t = block.get("text", "")
                        if t and t.strip():
                            text_parts.append(t)
                    elif b_type in ("image", "image_url", "file", "document", "input_audio"):
                        text_parts.append("[Media attachment from earlier in session]")
                    elif b_type == "tool_use":
                        name = block.get("name", "")
                        args = block.get("input", {})
                        text_parts.append(f"[Tool invocation: {name}({json.dumps(args, default=str)})]")
                    elif b_type == "tool_result":
                        c = block.get("content", "")
                        text_parts.append(f"[Tool Result: {json.dumps(c, default=str) if isinstance(c, (dict, list)) else str(c)}]")
                    elif "text" in block:
                        t = block.get("text", "")
                        if t and t.strip():
                            text_parts.append(t)
                else:
                    # Generic object (e.g. Anthropic TextBlock, ToolUseBlock, etc.)
                    b_type = getattr(block, "type", None)
                    if b_type == "text":
                        t = getattr(block, "text", "")
                        if t and t.strip():
                            text_parts.append(t)
                    elif b_type == "thinking":
                        th = getattr(block, "thinking", "")
                        if th and th.strip():
                            text_parts.append(th)
                    elif b_type == "tool_use":
                        name = getattr(block, "name", "")
                        args = getattr(block, "input", {})
                        text_parts.append(f"[Tool invocation: {name}({json.dumps(args, default=str)})]")
                    elif b_type in ("image", "document"):
                        text_parts.append("[Media attachment from earlier in session]")

        # Tool calls on assistant message (OpenAI / DeepSeek / OpenRouter style)
        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                if isinstance(tc, dict):
                    fn = tc.get("function", {})
                    name = fn.get("name", "")
                    args = fn.get("arguments", "")
                    text_parts.append(f"[Tool invocation: {name}({args})]")
                else:
                    fn = getattr(tc, "function", None)
                    if fn:
                        name = getattr(fn, "name", "")
                        args = getattr(fn, "arguments", "")
                        text_parts.append(f"[Tool invocation: {name}({args})]")

        joined_text = "\n".join(text_parts).strip()
        if joined_text:
            raw_turns.append({"role": role, "content": joined_text})

    if in_flight_prompt and in_flight_prompt.strip():
        prompt_str = in_flight_prompt.strip()
        if not raw_turns or raw_turns[-1]["content"] != prompt_str:
            raw_turns.append({"role": "user", "content": prompt_str})

    if not raw_turns:
        return []

    # Merge consecutive turns with the same role to enforce alternation
    merged_turns: List[Dict[str, str]] = []
    for turn in raw_turns:
        if merged_turns and merged_turns[-1]["role"] == turn["role"]:
            merged_turns[-1]["content"] = f"{merged_turns[-1]['content']}\n\n{turn['content']}"
        else:
            merged_turns.append(dict(turn))

    # Close trailing user turn if requested for restoration
    if close_trailing_user and merged_turns and merged_turns[-1]["role"] == "user":
        merged_turns.append({
            "role": "assistant",
            "content": "[Turn interrupted by application shutdown or restart]"
        })

    return merged_turns


def save_session_cache(agent_id: str, data: Dict[str, Any]) -> bool:
    """
    Atomically writes the active session state to session_cache.json for an agent.
    Returns True on success, False on failure.
    """
    if not agent_id:
        return False
    try:
        cache_file = paths.get_session_cache_file(agent_id)
        payload = dict(data)
        payload["version"] = 1
        payload["updated_at"] = time.time()
        atomic_json_write(cache_file, payload, indent=2)
        return True
    except Exception as e:
        logger.warning(f"Failed to save session cache for agent {agent_id}: {e}")
        return False


def load_session_cache(agent_id: str) -> Optional[Dict[str, Any]]:
    """
    Loads and validates the active session cache for an agent.
    If the cache file is corrupted, it is quarantined to .corrupt.<timestamp> and None is returned.
    """
    if not agent_id:
        return None
    cache_file = paths.get_session_cache_file(agent_id)
    if not os.path.exists(cache_file):
        return None

    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError(f"Root object in {cache_file} must be a JSON object, got {type(data).__name__}")
        return data
    except Exception as e:
        import uuid
        ts = f"{int(time.time())}_{uuid.uuid4().hex[:6]}"
        corrupt_path = f"{cache_file}.corrupt.{ts}"
        try:
            os.replace(cache_file, corrupt_path)
            logger.error(
                f"Quarantined corrupted session cache for agent {agent_id} to {corrupt_path}: {e}"
            )
        except Exception as q_err:
            logger.error(
                f"Failed to quarantine corrupted session cache {cache_file}: {q_err}"
            )
        return None


def clear_session_cache(agent_id: str) -> bool:
    """
    Safely removes the session cache file for an agent upon successful memory consolidation.
    """
    if not agent_id:
        return False
    cache_file = paths.get_session_cache_file(agent_id)
    if os.path.exists(cache_file):
        try:
            os.remove(cache_file)
            logger.debug(f"Cleared session cache for agent {agent_id}")
            return True
        except Exception as e:
            logger.warning(f"Failed to remove session cache {cache_file}: {e}")
            return False
    return True
