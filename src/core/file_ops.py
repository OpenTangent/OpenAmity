import os
import shutil
import tempfile
from typing import Any, List, Optional

from core.file_utils import _get_lock_for_path

MAX_EDIT_FILE_BYTES = 5 * 1024 * 1024  # 5 MB safety limit for in-memory text edits


def format_file_size(num_bytes: int) -> str:
    """Formats a byte count into a human-readable string."""
    size = float(num_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024.0 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024.0
    return f"{size:.1f} PB"


def resolve_user_path(file_path: str) -> str:
    """Expands '~' and resolves relative paths against ~/Documents."""
    expanded = os.path.expanduser(str(file_path).strip())
    if not os.path.isabs(expanded):
        return os.path.abspath(os.path.join(os.path.expanduser("~/Documents"), expanded))
    return os.path.abspath(expanded)


def canonicalize_user_path(file_path: str) -> str:
    """Resolves a user path and follows all symlinks to a canonical path."""
    return os.path.realpath(resolve_user_path(file_path))


def _parse_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes"):
            return True
        if lowered in ("false", "0", "no"):
            return False
        return default
    return bool(value)


def _is_binary_probe(full_path: str, probe_bytes: int = 8192) -> bool:
    with open(full_path, "rb") as f:
        return b"\x00" in f.read(probe_bytes)


def _validate_readable_regular_file(
    full_path: str,
    dir_hint: str = "",
    permission_hint: str = "",
) -> Optional[str]:
    if not os.path.exists(full_path):
        return f"Error: File not found at '{full_path}'."
    if os.path.isdir(full_path):
        suffix = f" {dir_hint.strip()}" if dir_hint and dir_hint.strip() else ""
        return f"Error: Path '{full_path}' is a directory, not a regular file.{suffix}"
    if not os.path.isfile(full_path):
        return f"Error: Path '{full_path}' is not a regular file."
    return None


def _format_query_matches(
    lines: List[str],
    file_path_str: str,
    file_size: int,
    query_str: str,
    matching_line_nums: List[int],
    context_lines: int,
    show_numbers: bool,
    truncate_fn,
    char_limit: int,
    tool_name: str,
) -> str:
    total_lines = len(lines)
    try:
        ctx_val = max(0, int(context_lines)) if context_lines is not None else 3
    except (ValueError, TypeError):
        ctx_val = 3

    hunks = []
    for match_line in matching_line_nums:
        h_start = max(1, match_line - ctx_val)
        h_end = min(total_lines, match_line + ctx_val)
        if hunks and h_start <= hunks[-1][1] + 1:
            hunks[-1] = (hunks[-1][0], max(hunks[-1][1], h_end))
        else:
            hunks.append((h_start, h_end))

    pad_width = len(str(total_lines))
    match_set = set(matching_line_nums)

    match_summary_lines = ", ".join(map(str, matching_line_nums[:15]))
    if len(matching_line_nums) > 15:
        match_summary_lines += f", ... ({len(matching_line_nums)} total)"

    plural_es = "es" if len(matching_line_nums) != 1 else ""
    plural_s = "s" if len(matching_line_nums) != 1 else ""
    header = (
        f"=== File: {file_path_str} (Found {len(matching_line_nums)} "
        f"match{plural_es} for query '{query_str}' "
        f"at line{plural_s} [{match_summary_lines}], "
        f"Size: {format_file_size(file_size)}) ===\n"
    )

    current_char_count = len(header)
    truncated_due_to_chars = False
    last_rendered_line = 0
    formatted_hunk_blocks = []

    for h_idx, (h_start, h_end) in enumerate(hunks):
        if current_char_count >= char_limit:
            truncated_due_to_chars = True
            break

        hunk_lines = []
        if h_idx > 0:
            sep = f"{' ' * pad_width}   ---\n"
            hunk_lines.append(sep)
            current_char_count += len(sep)

        for line_num in range(h_start, h_end + 1):
            clean_line = truncate_fn(lines[line_num - 1])
            is_match = line_num in match_set
            marker = ">" if is_match else "|"

            if show_numbers:
                rendered_line = f"{line_num:>{pad_width}} {marker} {clean_line}\n"
            else:
                prefix = "> " if is_match else "  "
                rendered_line = f"{prefix}{clean_line}\n"

            if current_char_count + len(rendered_line) > char_limit:
                truncated_due_to_chars = True
                break

            hunk_lines.append(rendered_line)
            current_char_count += len(rendered_line)
            last_rendered_line = line_num

        formatted_hunk_blocks.append("".join(hunk_lines))
        if truncated_due_to_chars:
            break

    output = header + "".join(formatted_hunk_blocks)
    if truncated_due_to_chars:
        output += (
            f"\n... [Output truncated at {char_limit} characters limit. "
            f"Call {tool_name} with start_line={last_rendered_line + 1} to inspect further]"
        )
    else:
        output += (
            f"\n... [To read continuously around any match, call {tool_name} "
            f"with start_line=<line_number>]"
        )

    return output.strip()


def read_text_file(
    file_path: Optional[str] = None,
    start_line: int = 1,
    end_line: Optional[int] = None,
    max_lines: Optional[int] = None,
    show_line_numbers: bool = True,
    query: Optional[str] = None,
    context_lines: int = 3,
    jump_to_match: bool = False,
    max_line_length: Optional[int] = None,
    is_low_token: bool = False,
    resolved_path: Optional[str] = None,
    tool_name: str = "Terminal_read_file",
    dir_hint: str = "To inspect directory contents, run 'ls -la' using Terminal_run.",
    binary_hint: str = "For multimedia assets (images, audio, PDF), use Media_read.",
    permission_hint: str = "If superuser privileges are required, use Terminal_run(as_sudo=True).",
) -> str:
    """Reads a text file with line numbers, pagination, query search, and truncation guards."""
    if not file_path:
        return "Error: Missing file_path parameter."

    file_path_str = str(file_path).strip()
    if not file_path_str:
        return "Error: file_path parameter cannot be empty."
    if "\x00" in file_path_str:
        return "Error: Invalid file_path parameter."

    full_path = resolved_path if resolved_path is not None else resolve_user_path(file_path_str)

    invalid_msg = _validate_readable_regular_file(
        full_path, dir_hint=dir_hint, permission_hint=permission_hint
    )
    if invalid_msg:
        return invalid_msg

    try:
        file_size = os.path.getsize(full_path)
    except Exception as e:
        return f"Error determining size of '{full_path}': {e}"

    if file_size == 0:
        return f"=== File: {file_path_str} (Empty file, 0 B) ==="

    perm_suffix = f" {permission_hint.strip()}" if permission_hint and permission_hint.strip() else ""
    bin_suffix = f" {binary_hint.strip()}" if binary_hint and binary_hint.strip() else ""

    try:
        if _is_binary_probe(full_path):
            return (
                f"Error: File '{full_path}' appears to be a binary file "
                f"({format_file_size(file_size)}).{bin_suffix}"
            )
    except PermissionError:
        return f"Error: Permission denied accessing '{full_path}'.{perm_suffix}"
    except Exception as e:
        return f"Error checking file '{full_path}': {e}"

    default_max_lines = 100 if is_low_token else 500
    max_line_cap = 250 if is_low_token else 1000
    char_limit = 5000 if is_low_token else 30000

    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except PermissionError:
        return f"Error: Permission denied reading '{full_path}'.{perm_suffix}"
    except Exception as e:
        return f"Error reading file '{full_path}': {e}"

    total_lines = len(lines)
    if total_lines == 0:
        return f"=== File: {file_path_str} (Empty file, 0 B) ==="

    max_len = None
    if max_line_length is not None:
        try:
            max_len = max(1, int(max_line_length))
        except (ValueError, TypeError):
            max_len = None
    elif is_low_token:
        max_len = 300

    def _truncate_line(raw_text: str) -> str:
        clean = raw_text.rstrip("\r\n")
        if max_len is not None and len(clean) > max_len:
            return clean[:max_len] + " ... [line truncated]"
        return clean

    show_numbers = _parse_bool(show_line_numbers, True)
    query_str = str(query).strip() if query is not None else ""
    jump_flag = _parse_bool(jump_to_match, False)

    jumped_note = ""
    if query_str:
        query_lower = query_str.lower()
        matching_line_nums = [
            idx for idx, line in enumerate(lines, start=1)
            if query_lower in line.lower()
        ]
        if not matching_line_nums:
            return (
                f"=== File: {file_path_str} (Size: {format_file_size(file_size)}) ===\n"
                f"Notice: No matches found for query '{query_str}' in {total_lines} lines."
            )

        if jump_flag:
            start_line = matching_line_nums[0]
            jumped_note = f"Jumped to first match for '{query_str}' at line {start_line}; "
        else:
            return _format_query_matches(
                lines=lines,
                file_path_str=file_path_str,
                file_size=file_size,
                query_str=query_str,
                matching_line_nums=matching_line_nums,
                context_lines=context_lines,
                show_numbers=show_numbers,
                truncate_fn=_truncate_line,
                char_limit=char_limit,
                tool_name=tool_name,
            )

    try:
        start_line_val = int(start_line) if start_line is not None else 1
    except (ValueError, TypeError):
        start_line_val = 1
    start_line_val = max(1, start_line_val)

    end_line_val = None
    if end_line is not None:
        try:
            end_line_val = int(end_line)
        except (ValueError, TypeError):
            end_line_val = None

    if end_line_val is not None and end_line_val < start_line_val:
        return f"Error: end_line ({end_line_val}) cannot be less than start_line ({start_line_val})."

    if start_line_val > total_lines:
        return f"Notice: start_line {start_line_val} exceeds total file lines ({total_lines}). File has {total_lines} lines in total."

    if max_lines is not None:
        try:
            effective_limit = min(max(1, int(max_lines)), max_line_cap)
        except (ValueError, TypeError):
            effective_limit = default_max_lines
    else:
        effective_limit = default_max_lines

    if end_line_val is not None:
        if (end_line_val - start_line_val + 1) > max_line_cap:
            end_line_val = start_line_val + max_line_cap - 1
        end_line_val = min(end_line_val, total_lines)
    else:
        end_line_val = min(start_line_val + effective_limit - 1, total_lines)

    selected_lines = lines[start_line_val - 1 : end_line_val]
    pad_width = len(str(end_line_val))
    formatted_lines = []
    header = (
        f"=== File: {file_path_str} ({jumped_note}Lines {start_line_val}-{end_line_val} "
        f"of {total_lines}, Size: {format_file_size(file_size)}) ===\n"
    )
    current_char_count = len(header)
    truncated_due_to_chars = False
    last_rendered_line = start_line_val - 1

    for idx, line_text in enumerate(selected_lines):
        current_line_num = start_line_val + idx
        clean_line = _truncate_line(line_text)
        marker = ">" if (jump_flag and current_line_num == start_line_val) else "|"
        if show_numbers:
            rendered_line = f"{current_line_num:>{pad_width}} {marker} {clean_line}\n"
        else:
            prefix = "> " if (jump_flag and current_line_num == start_line_val) else ""
            rendered_line = f"{prefix}{clean_line}\n"

        if current_char_count + len(rendered_line) > char_limit and idx > 0:
            truncated_due_to_chars = True
            break

        formatted_lines.append(rendered_line)
        current_char_count += len(rendered_line)
        last_rendered_line = current_line_num

    output = header + "".join(formatted_lines)
    if truncated_due_to_chars:
        next_line = last_rendered_line + 1
        output += (
            f"\n... [Output truncated at {char_limit} characters limit. "
            f"Call {tool_name} with start_line={next_line} to continue reading]"
        )
    elif end_line_val < total_lines:
        remaining = total_lines - end_line_val
        next_line = end_line_val + 1
        output += (
            f"\n... [{remaining} more lines in file. "
            f"Call {tool_name} with start_line={next_line} to continue reading]"
        )
    else:
        output += f"\n=== End of File ({total_lines} lines) ==="

    return output.strip()


def _find_occurrence_lines(text: str, needle: str) -> List[int]:
    lines = []
    start = 0
    while True:
        idx = text.find(needle, start)
        if idx == -1:
            break
        line_num = text.count("\n", 0, idx) + 1
        lines.append(line_num)
        start = idx + max(1, len(needle))
    return lines


def _atomic_write_text(full_path: str, content: str, preserve_mode_from: Optional[str] = None) -> None:
    parent_dir = os.path.dirname(full_path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=parent_dir or ".",
            prefix=".oa_edit_",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            temp_path = tmp.name
            tmp.write(content)
            tmp.flush()
            os.fsync(tmp.fileno())

        if preserve_mode_from and os.path.exists(preserve_mode_from):
            shutil.copymode(preserve_mode_from, temp_path)

        os.replace(temp_path, full_path)
        temp_path = None
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def edit_text_file(
    file_path: Optional[str] = None,
    old_text: Optional[str] = None,
    new_text: Optional[str] = None,
    replace_all: Any = False,
    content: Optional[str] = None,
    resolved_path: Optional[str] = None,
    read_tool_name: str = "File_read",
) -> str:
    """
    Atomically edits or creates a UTF-8 text file under a per-path lock.
    Supports either targeted search-and-replace (old_text + new_text) or full write/create (content).
    """
    if not file_path:
        return "Error: Missing file_path parameter."

    file_path_str = str(file_path).strip()
    if not file_path_str:
        return "Error: file_path parameter cannot be empty."
    if "\x00" in file_path_str:
        return "Error: Invalid file_path parameter."

    has_patch = (old_text is not None) or (new_text is not None)
    has_content = content is not None

    if has_patch and has_content:
        return (
            "Error: Provide either ('old_text' and 'new_text') for a targeted edit "
            "OR 'content' for a full write/create, not both."
        )
    if not has_patch and not has_content:
        return "Error: Must provide either ('old_text' and 'new_text') or 'content'."

    if has_content and not isinstance(content, str):
        return "Error: 'content' must be a string."

    if has_patch:
        if old_text is None or not isinstance(old_text, str):
            return "Error: 'old_text' must be a string when performing a targeted edit."
        if new_text is None or not isinstance(new_text, str):
            return "Error: 'new_text' is required as a string when 'old_text' is provided (pass '' to delete matched text)."
        if old_text == "":
            return "Error: 'old_text' cannot be empty. Provide 'content' to create or overwrite a file."

    replace_all_flag = _parse_bool(replace_all, False)
    full_path = resolved_path if resolved_path is not None else resolve_user_path(file_path_str)
    canonical_lock_path = os.path.realpath(full_path)

    with _get_lock_for_path(canonical_lock_path):
        exists = os.path.exists(full_path)
        if exists:
            if os.path.isdir(full_path):
                return f"Error: Path '{full_path}' is a directory, not a regular file."
            if not os.path.isfile(full_path):
                return f"Error: Path '{full_path}' is not a regular file."
            try:
                file_size = os.path.getsize(full_path)
            except OSError as e:
                return f"Error determining size of '{full_path}': {e}"
            if file_size > MAX_EDIT_FILE_BYTES:
                return (
                    f"Error: File '{full_path}' ({format_file_size(file_size)}) exceeds the "
                    f"{format_file_size(MAX_EDIT_FILE_BYTES)} text edit limit."
                )
            try:
                if file_size > 0 and _is_binary_probe(full_path):
                    return f"Error: File '{full_path}' appears to be a binary file and cannot be edited as text."
            except PermissionError:
                return f"Error: Permission denied accessing '{full_path}'."
            except OSError as e:
                return f"Error checking file '{full_path}': {e}"

        if has_content:
            byte_len = len(content.encode("utf-8"))
            if byte_len > MAX_EDIT_FILE_BYTES:
                return (
                    f"Error: Content ({format_file_size(byte_len)}) exceeds the "
                    f"{format_file_size(MAX_EDIT_FILE_BYTES)} text edit limit."
                )
            try:
                _atomic_write_text(
                    full_path,
                    content,
                    preserve_mode_from=full_path if exists else None,
                )
            except PermissionError:
                return f"Error: Permission denied writing '{full_path}'."
            except OSError as e:
                return f"Error writing file '{full_path}': {e}"

            line_count = len(content.splitlines())
            action_word = "overwrote" if exists else "created"
            return (
                f"Successfully {action_word} '{file_path_str}' "
                f"({line_count} line{'s' if line_count != 1 else ''}, {format_file_size(byte_len)})."
            )

        if not exists:
            return (
                f"Error: File not found at '{full_path}'. "
                "To create a new file, provide 'content' instead of 'old_text'."
            )

        try:
            with open(full_path, "r", encoding="utf-8", newline="") as f:
                existing_text = f.read()
        except UnicodeDecodeError:
            return f"Error: File '{full_path}' is not valid UTF-8 text and cannot be edited safely."
        except PermissionError:
            return f"Error: Permission denied reading '{full_path}'."
        except OSError as e:
            return f"Error reading file '{full_path}': {e}"

        match_count = existing_text.count(old_text)
        if match_count == 0:
            return (
                f"Error: 'old_text' was not found in '{full_path}'. "
                f"Use {read_tool_name} to inspect the exact file contents before editing."
            )

        if match_count > 1 and not replace_all_flag:
            match_lines = _find_occurrence_lines(existing_text, old_text)
            preview = ", ".join(map(str, match_lines[:10]))
            if len(match_lines) > 10:
                preview += f", ... ({len(match_lines)} total)"
            return (
                f"Error: 'old_text' matched {match_count} times in '{full_path}' "
                f"(at lines [{preview}]). Include more surrounding context in 'old_text' "
                "for a unique match, or set replace_all=true."
            )

        if old_text == new_text:
            return f"Notice: No changes made to '{file_path_str}'; 'new_text' is identical to 'old_text'."

        if replace_all_flag:
            updated_text = existing_text.replace(old_text, new_text)
            replaced_count = match_count
        else:
            updated_text = existing_text.replace(old_text, new_text, 1)
            replaced_count = 1

        try:
            _atomic_write_text(full_path, updated_text, preserve_mode_from=full_path)
        except PermissionError:
            return f"Error: Permission denied writing '{full_path}'."
        except OSError as e:
            return f"Error writing file '{full_path}': {e}"

        new_line_count = len(updated_text.splitlines())
        return (
            f"Successfully edited '{file_path_str}' "
            f"(replaced {replaced_count} occurrence{'s' if replaced_count != 1 else ''}; "
            f"file now has {new_line_count} line{'s' if new_line_count != 1 else ''})."
        )
