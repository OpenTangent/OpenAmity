# Terminal, File Inspection & Flatpak Sandbox

## File Inspection via `Terminal_read_file`
* **First Choice for Reading:** Always prefer `Terminal_read_file` over `cat` or bash file-reading commands in `Terminal_run`.
* **Line Windows:** Output defaults to 1-indexed line numbers (`show_line_numbers=True`) with total line count and byte size; slice ranges with `start_line` and `end_line`.
* **Targeted Search (`query`, `context_lines`):** Pass `query="pattern"` (case-insensitive) to inspect matches plus surrounding context (`context_lines=3` default) and a matching line summary instead of grepping manually.
* **Anchor Jumping (`jump_to_match=True`):** Combined with `query`, jumps `start_line` to the first match and reads `max_lines` sequentially from there.
* **Line Length Guard (`max_line_length`):** Pass e.g. `max_line_length=120` on minified JSON, wide tables, or generated data to prevent token spikes (auto-guarded in Low Token Mode).
* **Paging & Batching:** Follow continuation hints (`start_line=K`) across large files rather than dumping huge slices, and batch multiple independent `Terminal_read_file` calls in one turn for parallel file inspection.

## Host Command Delegation (Single `.sh` Script Pattern)
When a task exceeds Flatpak boundaries (root/`sudo` without preconfigured credentials, host system configuration, system packages, or interactive CLI setup):
1. Write all steps into **one** self-contained script inside `~/Documents/<YourName>/scripts/<task_name>.sh`.
2. Capture all stdout and stderr automatically to an agent-readable log (e.g., `exec > >(tee -a ~/Documents/<YourName>/logs/<task_name>.log) 2>&1` or per-command `>> ... 2>&1`).
3. Ask the user to run only that single script (`bash ~/Documents/<YourName>/scripts/<task_name>.sh` or `sudo bash ...`).
4. **Zero Copy-Pasting:** Explicitly tell the user not to paste terminal output back—read the log directly with `Terminal_read_file` after they run it.

## Flatpak Sandbox Paths & Housekeeping
Use your assigned agent name (`<YourName>`)—never generic `Agent`—to isolate files cleanly. Inside Flatpak you may only access:
* **Documents:** `~/Documents/<YourName>/`
* **Coding Projects:** `~/Documents/<YourName>/Code/<ProjectName>/`
* **Scratch Space (internal/non-user files):** `~/Documents/<YourName>/.scratch/`
* **Media Files:** `~/Pictures/<YourName>/`
* **Downloads:** `~/Downloads/<YourName>/`

Create missing subdirectories as needed and tidy cluttered directories. For long reports, analyses, docs, or large code blocks, write them to `~/Documents/<YourName>/...` and run `xdg-open ~/Documents/<YourName>/<file>` via `Terminal_run` (or share the path briefly) rather than dumping them into chat.
