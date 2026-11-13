# Web Browsing (`WebSearch` vs `Browser`)

## Two-Tier Web Strategy
* **Default to `WebSearch`:** Prefer `WebSearch` and `WebSearch_view_page` for factual queries, speed, static pages, and low token overhead.
* **Use `Browser` for Interactivity:** Launch `Browser_*` only when JS execution or interaction is required—Single-Page Applications (SPAs), form submissions, logins/OAuth, dropdowns, multi-tab flows, or visual Set-of-Marks checks.

## Browse -> Observe -> Act Loop & Element IDs
1. Call `Browser_navigate(url)` (keep default `screenshot=False` to save ~1,000-1,600 multimodal tokens per image).
2. Read the returned pruned accessibility tree and section headings (`--- "Title" (h2) ---`).
3. Execute `Browser_click`, `Browser_type`, `Browser_select`, or `Browser_upload` using bracketed IDs (`[1]`, `[2]`, ...).
4. Inspect the refreshed accessibility tree returned in the tool output and repeat.
* **Ephemeral IDs:** Element IDs are renumbered in document order on every mutating action. Never cache, hardcode, or reuse IDs across turns—always use IDs from the latest response.

## Visual Set-of-Marks (SoM), Coordinates & Keys
* Call `Browser_screenshot` only when layout verification, CAPTCHA inspection, or spatial alignment is needed. Screenshots overlay numbered SoM badges matching accessibility IDs.
* **Escape Hatches:** For controls missing from the accessibility tree (canvas, WebGL, maps, custom clickable `div` elements), use `Browser_click_coordinates(x, y)`. Dismiss modals with `Browser_press_key("Escape")` and navigate keyboard-only widgets via `"Tab"` or arrow keys.

## Uploads, Tabs, Waits & Downloads
* **File Uploads:** Headless sandboxed browsers cannot drive host OS file pickers. Always use `Browser_upload(file_path, element_id=...)` (targeting `<input type="file">`, dropzone, or omitting `element_id` for single-input pages)—never click an upload button expecting an OS dialog.
* **Tabs & Popups:** Links opening new tabs or popups (`target="_blank"`, OAuth popups) auto-focus and announce title/URL; closing returns focus to the parent tab. Inspect tabs via `Browser_list_tabs()` and switch via `Browser_switch_tab(index)`.
* **Fast-Failing 4s Timeouts & SPA Waits:** Interactive calls use a 4-second auto-wait instead of hanging for 30 seconds. On detached/not-yet-rendered elements, the response returns the fresh accessibility tree; follow with `Browser_wait(selector="...")` or `Browser_wait()` after async fetches.
* **Downloads:** Triggering download buttons or binary URLs saves files directly into `~/Downloads/<YourName>/` and returns the saved path.

## Cache Expiration & Session Hygiene
* Captured screenshots and browser caches auto-delete after **24 hours**. Save permanent screenshots to `~/Pictures/<YourName>/...` or transmit them via external channels before expiry.
* Reset broken sessions or stale cookies with `Browser_clear_cache(domain=...)` or `Browser_clear_cache()`. Always call `Browser_close()` when finished to free browser memory.
