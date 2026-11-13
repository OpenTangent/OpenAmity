import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from core.file_ops import (
    canonicalize_user_path,
    edit_text_file,
    read_text_file,
)


class SubagentFilePolicy:
    """
    Immutable canonical allowlist governing which files and directories a subagent
    may read or edit. Checks run on realpath()-canonicalized paths before any disk I/O.
    """

    def __init__(
        self,
        allowed_files: Optional[List[str]] = None,
        allowed_dirs: Optional[List[str]] = None,
        raw_entries: Optional[List[str]] = None,
    ):
        self._allowed_files: List[str] = list(allowed_files or [])
        self._allowed_files_set = set(self._allowed_files)
        self._allowed_dirs: List[str] = list(allowed_dirs or [])
        self._raw_entries: List[str] = list(raw_entries or [])

    @classmethod
    def from_paths(cls, allowed_paths: Any) -> "SubagentFilePolicy":
        """Validates and canonicalizes an optional list of permitted files/directories."""
        if allowed_paths is None:
            return cls()

        if isinstance(allowed_paths, (str, bytes)) or not isinstance(allowed_paths, (list, tuple, set)):
            raise TypeError("allowed_files must be a list of file or directory path strings.")

        files: List[str] = []
        dirs: List[str] = []
        seen_files = set()
        seen_dirs = set()
        display_entries: List[str] = []
        fs_root = os.path.abspath(os.sep)

        for entry in allowed_paths:
            if not isinstance(entry, str) or not entry.strip() or "\x00" in entry:
                raise ValueError("Every entry in allowed_files must be a non-empty valid path string.")

            raw_stripped = entry.strip()
            real_path = canonicalize_user_path(raw_stripped)

            if real_path == fs_root:
                raise ValueError("Refusing to grant subagent unrestricted access to filesystem root '/'.")

            if os.path.exists(real_path):
                if os.path.isdir(real_path):
                    is_dir = True
                elif os.path.isfile(real_path):
                    is_dir = False
                else:
                    raise ValueError(f"Allowed path '{raw_stripped}' is not a regular file or directory.")
            else:
                is_dir = raw_stripped.endswith(("/", os.sep))

            if is_dir:
                if real_path not in seen_dirs:
                    seen_dirs.add(real_path)
                    dirs.append(real_path)
                    display_entries.append(f"{real_path}{os.sep} (dir)")
            else:
                if real_path not in seen_files:
                    seen_files.add(real_path)
                    files.append(real_path)
                    display_entries.append(real_path)

        return cls(allowed_files=files, allowed_dirs=dirs, raw_entries=display_entries)

    @property
    def has_permissions(self) -> bool:
        return bool(self._allowed_files or self._allowed_dirs)

    @property
    def allowed_files(self) -> Tuple[str, ...]:
        return tuple(self._allowed_files)

    @property
    def allowed_dirs(self) -> Tuple[str, ...]:
        return tuple(self._allowed_dirs)

    def authorize_path(self, file_path: Any) -> Tuple[bool, str, Optional[str]]:
        """
        Canonicalizes file_path and verifies whether it falls within the allowlist.
        Must be called BEFORE any filesystem existence or stat checks.
        """
        if not isinstance(file_path, str) or not file_path.strip() or "\x00" in file_path:
            return (False, "", "Error: Invalid file_path parameter.")

        real_target = canonicalize_user_path(file_path)

        if not self.has_permissions:
            return (
                False,
                real_target,
                f"Error: Access denied. Subagent is not permitted to interact with '{file_path}' (no permitted files configured).",
            )

        if real_target in self._allowed_files_set:
            return (True, real_target, None)

        for allowed_dir in self._allowed_dirs:
            try:
                if os.path.commonpath([real_target, allowed_dir]) == allowed_dir:
                    return (True, real_target, None)
            except ValueError:
                continue

        return (
            False,
            real_target,
            f"Error: Access denied. Subagent is not permitted to interact with '{file_path}'.",
        )

    def summary(self, max_items: int = 3) -> str:
        if not self.has_permissions:
            return "no file access"
        shown = self._raw_entries[:max_items]
        extra = len(self._raw_entries) - len(shown)
        text = ", ".join(shown)
        if extra > 0:
            text += f", +{extra} more"
        return text

    def describe_for_prompt(self) -> str:
        if not self.has_permissions:
            return (
                "[FILE ACCESS PERMISSIONS: None]\n"
                "You do NOT have permission to read, write, or edit any local files."
            )

        lines = [
            "[FILE ACCESS PERMISSIONS]",
            "You have File_read and File_edit access ONLY to the following permitted path(s):",
        ]
        for entry in self._raw_entries:
            lines.append(f"- {entry}")
        lines.append(
            "STRICT BOUNDARY: Any attempt to read or edit a path outside this list will be blocked. "
            "Always read a file with File_read before editing it with File_edit."
        )
        return "\n".join(lines)


class SubagentFileToolset:
    """Subagent-only file reading and editing dispatcher bound to a SubagentFilePolicy."""

    TOOL_NAMES = frozenset({"File_read", "File_edit"})

    def __init__(
        self,
        policy: Optional[SubagentFilePolicy] = None,
        orchestrator: Any = None,
        subagent_id: Optional[str] = None,
    ):
        self.policy = policy if policy is not None else SubagentFilePolicy()
        self.orchestrator = orchestrator
        self.subagent_id = subagent_id
        self.logger = logging.getLogger(
            f"subagent.{subagent_id}" if subagent_id else "subagent.files"
        )

    def get_tool_declarations(self) -> List[Dict[str, Any]]:
        """Returns tool schemas only when the subagent has explicit file permissions."""
        if not self.policy.has_permissions:
            return []

        return [
            {
                "name": "File_read",
                "description": (
                    "Reads a permitted text file with line numbers, line slicing/pagination, and query search. "
                    "Only files and directories explicitly granted to this subagent may be accessed."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "file_path": {
                            "type": "STRING",
                            "description": "Path to the permitted file to inspect (absolute, relative to ~/Documents, or starting with '~').",
                        },
                        "start_line": {
                            "type": "INTEGER",
                            "description": "1-indexed line number to start reading from (default: 1).",
                        },
                        "end_line": {
                            "type": "INTEGER",
                            "description": "1-indexed line number to stop reading at (inclusive).",
                        },
                        "max_lines": {
                            "type": "INTEGER",
                            "description": "Maximum number of lines to return (default: 500; max: 1000).",
                        },
                        "show_line_numbers": {
                            "type": "BOOLEAN",
                            "description": "Prefix lines with 1-indexed line numbers (default: true).",
                        },
                        "query": {
                            "type": "STRING",
                            "description": "Optional case-insensitive search pattern to locate matching lines with context.",
                        },
                        "context_lines": {
                            "type": "INTEGER",
                            "description": "Surrounding context lines before/after each query match (default: 3).",
                        },
                        "jump_to_match": {
                            "type": "BOOLEAN",
                            "description": "When true with query, starts continuous reading from the first match (default: false).",
                        },
                        "max_line_length": {
                            "type": "INTEGER",
                            "description": "Optional character cap per line to avoid token spikes.",
                        },
                    },
                    "required": ["file_path"],
                },
            },
            {
                "name": "File_edit",
                "description": (
                    "Edits or creates a permitted text file. Supports either targeted search-and-replace "
                    "(old_text + new_text) on an existing file, OR full-file write/create (content). "
                    "Only files and directories explicitly granted to this subagent may be modified."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "file_path": {
                            "type": "STRING",
                            "description": "Path to the permitted file to edit or create (absolute, relative to ~/Documents, or starting with '~').",
                        },
                        "old_text": {
                            "type": "STRING",
                            "description": "Exact text block to find and replace in an existing file. Must match uniquely unless replace_all is true. Mutually exclusive with 'content'.",
                        },
                        "new_text": {
                            "type": "STRING",
                            "description": "Replacement text for 'old_text' (pass '' to delete matched text). Required when 'old_text' is provided.",
                        },
                        "replace_all": {
                            "type": "BOOLEAN",
                            "description": "Replace all occurrences of 'old_text' instead of requiring a unique match (default: false).",
                        },
                        "content": {
                            "type": "STRING",
                            "description": "Full text content to write (creates a new permitted file or overwrites an existing one). Mutually exclusive with 'old_text'/'new_text'.",
                        },
                    },
                    "required": ["file_path"],
                },
            },
        ]

    def _is_low_token_mode(self) -> bool:
        try:
            sm = getattr(self.orchestrator, "settings_manager", None)
            if sm is not None:
                val = sm.get("core.low-token-mode", False)
                return val is True
        except Exception:
            pass
        return False

    def execute_tool_call(self, function_name: str, args: Optional[Dict[str, Any]] = None) -> str:
        """Executes File_read or File_edit strictly within self.policy."""
        if function_name not in self.TOOL_NAMES:
            return f"Error: Unknown subagent file tool '{function_name}'."

        safe_args = dict(args or {})
        file_path = safe_args.get("file_path")
        if file_path is None:
            return "Error: Missing file_path parameter."

        allowed, canonical_path, denial_msg = self.policy.authorize_path(file_path)
        if not allowed:
            self.logger.warning(
                f"[Subagent {self.subagent_id or '?'}] Blocked {function_name} on unauthorized path: {file_path}"
            )
            return denial_msg or f"Error: Access denied for '{file_path}'."

        if function_name == "File_read":
            return read_text_file(
                file_path=file_path,
                start_line=safe_args.get("start_line", 1),
                end_line=safe_args.get("end_line"),
                max_lines=safe_args.get("max_lines"),
                show_line_numbers=safe_args.get("show_line_numbers", True),
                query=safe_args.get("query"),
                context_lines=safe_args.get("context_lines", 3),
                jump_to_match=safe_args.get("jump_to_match", False),
                max_line_length=safe_args.get("max_line_length"),
                is_low_token=self._is_low_token_mode(),
                resolved_path=canonical_path,
                tool_name="File_read",
                dir_hint="",
                binary_hint="",
                permission_hint="",
            )

        return edit_text_file(
            file_path=file_path,
            old_text=safe_args.get("old_text"),
            new_text=safe_args.get("new_text"),
            replace_all=safe_args.get("replace_all", False),
            content=safe_args.get("content"),
            resolved_path=canonical_path,
            read_tool_name="File_read",
        )
