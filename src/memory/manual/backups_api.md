# System Backups, Custom Voice & Pulse Hook API

## `.oaa` Snapshots & Target Flagging
* **`Terminal_flag_for_backup`:** Register external files or directories (e.g., projects in `~/Documents/<YourName>/Code/`) for inclusion in `.oaa` archives. Saved in `backup_targets.json` and auto-pruned of missing or redundant paths on each call.
* **`System_create_backup(target="self"|"all")`:** Starts an asynchronous background `.oaa` snapshot of memories, trajectory, state, and flagged targets, immediately returning a `backup_id` (e.g., `backup_1`) while a GUI pip pulses. Heavy WhatsApp session data is excluded to keep archives compact (re-scan the QR code after restore).
* **`System_check_backup_status(backup_id="...")`:** Monitor live progress percentage, current file, and elapsed time.
* **`System_kill_backup(backup_id="...")`:** Safely cancel a slow or unwanted backup; temporary staging files are cleaned up automatically.
* **`[SYSTEM_NOTIFICATION]`:** Archive completion, cancellation, or failure automatically dispatches a notification pulse with archive paths and duration.

## Custom Voice Prompts (`System_set_custom_voice`)
* Gemini TTS prompts require positive imperative direction (e.g., `'Read the following transcript aloud as [Name] in a [accent] accent with clear articulation and natural conversational pacing:'`).
* **Forbidden Patterns:** Never include Markdown headers (`# AUDIO PROFILE`) or negative constraints (`'Do NOT speak fast'`). For inline expressive cues in speech (`<laugh>`, `<short pause>`), see `System_read_manual(topic="channels")`.

## Pulse Hook API & External Integrations
* **API Keys:** Issue credentials via `System_generate_api_key(name="...", scopes="pulse:inject", expires_in_days=None)`. Raw `oa_sec_...` tokens are shown only once (stored hashed). Audit active keys with `System_list_api_keys` and revoke compromised keys with `System_revoke_api_key(key_id="...")`.
* **Live HTTP Spec & Curl Recipes:** Whenever configuring webhooks, Home Assistant, GitHub Actions, or cron jobs, call **`System_api_docs`** for the exact endpoint (`http://localhost:7965/api/v1/agents/<YourUID>/pulses`), auth headers, payload JSON schema, 60-second per-agent cooldown (`HTTP 429 RATE_LIMITED`), and +/-1 minute `pulses.db` collision guard (`HTTP 409 PULSE_COLLISION`).
* Incoming webhook triggers appear as `[Autonomy Pulse: External pulse received]`.
