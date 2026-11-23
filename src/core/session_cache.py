import os
import re
import json
import time
import logging
from typing import List, Dict, Any, Optional, Tuple

from config import paths
from core.file_utils import atomic_json_write

logger = logging.getLogger("core.session_cache")

_TOOL_INVOCATION_START_RE = re.compile(r"\[Tool invocation:\s*([A-Za-z0-9_]+)\s*\(")
_FAKE_CALLED_LINE_RE = re.compile(r"🔧\s*Called\b")
_FAKE_CALLED_STRIP_LINE_RE = re.compile(r"(?m)^[ \t]*🔧\s*Called\b[^\n]*\n?")
_FAKE_CALLED_INLINE_RE = re.compile(r"🔧\s*Called\b[^\n]*")
_TOOL_RESULT_ARROW_RE = re.compile(r"([A-Za-z0-9_]+)\s*->\s*")
_EMPTY_ASSISTANT_FALLBACK = "[Attempted plain-text tool imitation; no native tool call emitted]"


def has_fake_tool_invocation_text(text: str) -> bool:
    """Return True if assistant text contains plain-text tool-call imitations."""
    if not text or not isinstance(text, str):
        return False
    return "[Tool invocation:" in text or bool(_FAKE_CALLED_LINE_RE.search(text))


def _scan_balanced_paren_end(text: str, open_paren_idx: int) -> Optional[int]:
    """Return index of matching ')' for '(' at open_paren_idx, or None if unbalanced."""
    n = len(text)
    depth = 1
    i = open_paren_idx + 1
    while i < n and text[i].isspace():
        i += 1
    json_style = i < n and text[i] in ("{", "[", '"')
    quote_char: Optional[str] = None
    escape = False

    i = open_paren_idx + 1
    while i < n:
        ch = text[i]
        if quote_char is not None:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote_char:
                quote_char = None
        else:
            if ch == '"' or (not json_style and ch == "'"):
                quote_char = ch
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    return None


def _parse_tool_args(raw_args: str) -> Dict[str, Any]:
    """Parse captured argument text into a dictionary suitable for native tool schemas."""
    stripped = (raw_args or "").strip()
    if not stripped:
        return {}
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
        return {"value": parsed}
    except Exception:
        return {"_raw": stripped}


def extract_tool_invocations_from_text(text: str) -> Tuple[str, List[Dict[str, Any]]]:
    """Strip `[Tool invocation: ...]` and `🔧Called ...` blocks from text and return parsed invocations."""
    if not text or not isinstance(text, str):
        return "", []

    invocations: List[Dict[str, Any]] = []
    kept_parts: List[str] = []
    n = len(text)
    pos = 0

    while pos < n:
        match = _TOOL_INVOCATION_START_RE.search(text, pos)
        if not match:
            kept_parts.append(text[pos:])
            break

        kept_parts.append(text[pos:match.start()])
        tool_name = match.group(1)
        open_paren_idx = match.end() - 1
        close_paren_idx = _scan_balanced_paren_end(text, open_paren_idx)

        if close_paren_idx is None:
            # Truncated/unclosed block: drop only up to end of line to avoid swallowing prose.
            nl_idx = text.find("\n", match.start())
            pos = nl_idx + 1 if nl_idx != -1 else n
            continue

        raw_args = text[open_paren_idx + 1:close_paren_idx]
        args_dict = _parse_tool_args(raw_args)
        invocations.append({
            "name": tool_name,
            "args": args_dict,
            "arguments": json.dumps(args_dict, ensure_ascii=False),
        })

        end_pos = close_paren_idx + 1
        rest = text[end_pos:]
        ws_len = len(rest) - len(rest.lstrip(" \t"))
        if end_pos + ws_len < n and text[end_pos + ws_len] == "]":
            end_pos = end_pos + ws_len + 1
        if end_pos < n and text[end_pos] == "\n":
            end_pos += 1
        pos = end_pos

    cleaned = "".join(kept_parts)
    cleaned = _FAKE_CALLED_STRIP_LINE_RE.sub("", cleaned)
    cleaned = _FAKE_CALLED_INLINE_RE.sub("", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, invocations


def strip_fake_tool_invocation_text(text: str) -> str:
    """Remove plain-text tool-call imitations from assistant text."""
    cleaned, _ = extract_tool_invocations_from_text(text)
    return cleaned


def sanitize_fake_tool_assistant_text(text: str, fallback: str = _EMPTY_ASSISTANT_FALLBACK) -> str:
    """Strip fake plain-text tool invocations if present, returning fallback when empty."""
    if not has_fake_tool_invocation_text(text):
        return text
    cleaned = strip_fake_tool_invocation_text(text)
    return cleaned or fallback


def _normalize_tool_result_payload(parsed_obj: Any) -> str:
    """Normalize a parsed tool result payload into a JSON object string."""
    if isinstance(parsed_obj, dict):
        return json.dumps(parsed_obj, ensure_ascii=False)
    if isinstance(parsed_obj, str):
        try:
            nested = json.loads(parsed_obj)
            if isinstance(nested, dict):
                return json.dumps(nested, ensure_ascii=False)
        except Exception:
            pass
        return json.dumps({"result": parsed_obj}, ensure_ascii=False)
    return json.dumps({"result": parsed_obj}, ensure_ascii=False)


def extract_tool_results_from_user_text(text: str) -> Tuple[List[str], str]:
    """Extract leading `[Tool Result: ...]` blocks from a user turn and return remaining text."""
    if not text or not isinstance(text, str):
        return [], ""

    results: List[str] = []
    decoder = json.JSONDecoder()
    n = len(text)
    pos = 0

    while pos < n:
        while pos < n and text[pos].isspace():
            pos += 1
        if not text.startswith("[Tool Result:", pos):
            break

        inner_pos = pos + len("[Tool Result:")
        while inner_pos < n and text[inner_pos].isspace():
            inner_pos += 1

        arrow_match = _TOOL_RESULT_ARROW_RE.match(text, inner_pos)
        if arrow_match:
            inner_pos = arrow_match.end()

        parsed_block = False
        if inner_pos < n and text[inner_pos] in ("{", "[", '"'):
            try:
                parsed_obj, end_idx = decoder.raw_decode(text, inner_pos)
                after = text[end_idx:]
                ws_len = len(after) - len(after.lstrip())
                if end_idx + ws_len < n and text[end_idx + ws_len] == "]":
                    results.append(_normalize_tool_result_payload(parsed_obj))
                    pos = end_idx + ws_len + 1
                    parsed_block = True
            except Exception:
                pass

        if parsed_block:
            continue

        # Fallback balanced-bracket scan for legacy or plain-text tool results.
        depth = 1
        quote_char: Optional[str] = None
        escape = False
        scan = inner_pos
        close_idx: Optional[int] = None
        while scan < n:
            ch = text[scan]
            if quote_char is not None:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == quote_char:
                    quote_char = None
            else:
                if ch == '"':
                    quote_char = ch
                elif ch == "[":
                    depth += 1
                elif ch == "]":
                    depth -= 1
                    if depth == 0:
                        close_idx = scan
                        break
            scan += 1

        if close_idx is None:
            break

        raw_result = text[inner_pos:close_idx].strip()
        try:
            parsed_raw = json.loads(raw_result)
            results.append(_normalize_tool_result_payload(parsed_raw))
        except Exception:
            results.append(json.dumps({"result": raw_result}, ensure_ascii=False))
        pos = close_idx + 1

    remaining = text[pos:].strip() if results else text.strip()
    return results, remaining


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
    - Strips unpaired/failed plain-text tool imitations from assistant turns.
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

    # Sanitize unpaired/failed plain-text tool imitations in assistant turns so they never poison future turns
    for idx, turn in enumerate(merged_turns):
        if turn["role"] != "assistant" or not has_fake_tool_invocation_text(turn["content"]):
            continue
        clean_body, _ = extract_tool_invocations_from_text(turn["content"])
        next_has_results = (
            idx + 1 < len(merged_turns)
            and merged_turns[idx + 1]["role"] == "user"
            and bool(extract_tool_results_from_user_text(merged_turns[idx + 1]["content"])[0])
        )
        if next_has_results:
            # Strip any stray 🔧Called lines while preserving paired [Tool invocation: ...] for reconstruction
            cleaned_called = _FAKE_CALLED_STRIP_LINE_RE.sub("", turn["content"])
            cleaned_called = _FAKE_CALLED_INLINE_RE.sub("", cleaned_called).strip()
            if cleaned_called != turn["content"]:
                turn["content"] = cleaned_called
        else:
            turn["content"] = clean_body or "[Turn completed without native tool calls]"

    # Close trailing user turn if requested for restoration
    if close_trailing_user and merged_turns and merged_turns[-1]["role"] == "user":
        merged_turns.append({
            "role": "assistant",
            "content": "[Turn interrupted by application shutdown or restart]"
        })

    return merged_turns


def reconstruct_structured_session_turns(
    messages: List[Dict[str, Any]],
    in_flight_prompt: Optional[str] = None,
    close_trailing_user: bool = True,
) -> List[Dict[str, Any]]:
    """
    Reconstructs normalized session messages into structured native tool-call / tool-result turns
    so restored sessions render real API tool structures instead of plain-text `[Tool invocation: ...]`
    strings inside assistant turns.
    """
    norm_messages = normalize_session_messages(
        messages,
        in_flight_prompt=in_flight_prompt,
        close_trailing_user=False,
    )
    structured: List[Dict[str, Any]] = []
    i = 0
    n = len(norm_messages)

    while i < n:
        curr = norm_messages[i]
        if curr["role"] == "assistant":
            clean_text, invocations = extract_tool_invocations_from_text(curr["content"])
            results: List[str] = []
            remaining_user = ""
            if invocations and i + 1 < n and norm_messages[i + 1]["role"] == "user":
                results, remaining_user = extract_tool_results_from_user_text(norm_messages[i + 1]["content"])

            if invocations and results:
                paired_calls = []
                for j, inv in enumerate(invocations):
                    res_str = results[j] if j < len(results) else json.dumps({"result": "[Completed]"})
                    paired_calls.append({
                        "id": f"call_restored_{i}_{j}",
                        "name": inv["name"],
                        "args": inv["args"],
                        "arguments": inv["arguments"],
                        "result": res_str,
                    })
                structured.append({
                    "role": "assistant",
                    "content": clean_text,
                    "tool_calls": paired_calls,
                })
                structured.append({
                    "role": "tool_results",
                    "tool_calls": paired_calls,
                })
                if remaining_user:
                    structured.append({
                        "role": "assistant",
                        "content": "[Completed tool execution]",
                    })
                    structured.append({
                        "role": "user",
                        "content": remaining_user,
                    })
                i += 2
                continue

            final_text = clean_text if invocations else curr["content"]
            structured.append({
                "role": "assistant",
                "content": final_text or "[Turn completed without native tool calls]",
            })
            i += 1
        else:
            structured.append({"role": "user", "content": curr["content"]})
            i += 1

    if close_trailing_user and structured and structured[-1]["role"] in ("user", "tool_results"):
        structured.append({
            "role": "assistant",
            "content": "[Turn interrupted by application shutdown or restart]",
        })

    return structured


def restore_openai_session_history(
    messages: List[Dict[str, Any]],
    close_trailing_user: bool = True,
) -> Tuple[List[Dict[str, Any]], set]:
    """Build OpenAI-style chat history with native `tool_calls` and `tool` messages from cache."""
    structured = reconstruct_structured_session_turns(messages, close_trailing_user=close_trailing_user)
    history: List[Dict[str, Any]] = []
    consumed_ids = set()

    for msg in structured:
        role = msg.get("role", "user")
        if role == "assistant" and msg.get("tool_calls"):
            history.append({
                "role": "assistant",
                "content": msg.get("content") or "",
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": tc["arguments"],
                        },
                    }
                    for tc in msg["tool_calls"]
                ],
            })
        elif role == "tool_results":
            for tc in msg.get("tool_calls", []):
                consumed_ids.add(tc["id"])
                history.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": tc["result"],
                })
        else:
            content = msg.get("content", "")
            if content:
                history.append({"role": role, "content": content})

    return history, consumed_ids


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
