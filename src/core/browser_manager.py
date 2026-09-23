import asyncio
import os
import random
import logging
import threading
import datetime
from typing import Optional, Dict, Any, List, Tuple
from PIL import Image

from config import paths

logger = logging.getLogger("core.BrowserManager")

# DOM extraction & ARIA pruning script executed inside the browser page
DOM_SCANNER_SCRIPT = r"""() => {
    // Remove any previous Open Amity IDs
    document.querySelectorAll('[data-oa-id]').forEach(el => el.removeAttribute('data-oa-id'));

    const elements = [];
    let currentId = 1;

    // Selector targeting semantic headings, interactive controls, accessible roles, and file inputs
    const selector = [
        'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'a[href]', 'button', 'input', 'select', 'textarea',
        '[role="button"]', '[role="link"]', '[role="checkbox"]', '[role="radio"]',
        '[role="menuitem"]', '[role="tab"]', '[role="combobox"]', '[role="switch"]',
        '[onclick]', '[tabindex]:not([tabindex="-1"])'
    ].join(', ');

    const candidates = Array.from(document.querySelectorAll(selector));
    const viewportHeight = window.innerHeight;
    const viewportWidth = window.innerWidth;

    for (const el of candidates) {
        const tagName = el.tagName.toLowerCase();
        const isHeading = /^h[1-6]$/.test(tagName);
        const isFileInput = (tagName === 'input' && el.type === 'file');

        const style = window.getComputedStyle(el);

        // File inputs can be styled with display:none or opacity:0 while triggered via label/button;
        // retain them so the agent can target them directly with Browser_upload.
        if (!isFileInput) {
            if (style.display === 'none' || style.visibility === 'hidden' || el.getAttribute('aria-hidden') === 'true') {
                continue;
            }

            const rect = el.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) continue;

            // Viewport-aware filtering: retain elements with a 100px boundary margin
            if (rect.bottom < -100 || rect.top > viewportHeight + 100 ||
                rect.right < -100 || rect.left > viewportWidth + 100) {
                continue;
            }
        }

        let role = el.getAttribute('role') || tagName;
        if (role === 'a') role = 'link';

        // Extract accessible name
        let name = el.getAttribute('aria-label') ||
                   el.getAttribute('placeholder') ||
                   el.getAttribute('title') ||
                   el.innerText ||
                   el.value || '';

        if (isFileInput && !name) {
            name = el.getAttribute('name') || el.id || 'Choose file';
        }

        name = name.trim().replace(/\s+/g, ' ');
        if (name.length > 80) {
            name = name.substring(0, 77) + '...';
        }

        if (isHeading) {
            const level = parseInt(tagName.charAt(1), 10);
            elements.push({
                is_heading: true,
                level: level,
                name: name
            });
        } else {
            const elemId = currentId++;
            el.setAttribute('data-oa-id', elemId.toString());

            let extras = [];
            if (document.activeElement === el) extras.push('focused');
            if (el.disabled) extras.push('disabled');
            if (el.checked) extras.push('checked');
            if (el.getAttribute('placeholder') && !el.value) extras.push('placeholder');

            const rect = el.getBoundingClientRect();
            elements.push({
                is_heading: false,
                id: elemId,
                role: role,
                name: name,
                type: el.type || null,
                extra: extras.join(' '),
                rect: {
                    x: Math.round(rect.x),
                    y: Math.round(rect.y),
                    width: Math.round(rect.width),
                    height: Math.round(rect.height)
                }
            });
        }
    }
    return elements;
}"""

# Set-of-Marks overlay injection script
SOM_OVERLAY_SCRIPT = r"""() => {
    let overlay = document.getElementById('oa-som-overlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'oa-som-overlay';
        overlay.style.position = 'absolute';
        overlay.style.top = '0';
        overlay.style.left = '0';
        overlay.style.width = '100%';
        overlay.style.height = '100%';
        overlay.style.pointerEvents = 'none';
        overlay.style.zIndex = '2147483647';
        document.body.appendChild(overlay);
    }
    overlay.innerHTML = '';

    const elements = document.querySelectorAll('[data-oa-id]');
    elements.forEach(el => {
        const id = el.getAttribute('data-oa-id');
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) return;

        const box = document.createElement('div');
        box.style.position = 'absolute';
        box.style.left = (window.scrollX + rect.left) + 'px';
        box.style.top = (window.scrollY + rect.top) + 'px';
        box.style.width = rect.width + 'px';
        box.style.height = rect.height + 'px';
        box.style.border = '2px solid #e63946';
        box.style.boxSizing = 'border-box';
        box.style.pointerEvents = 'none';

        const badge = document.createElement('span');
        badge.innerText = id;
        badge.style.position = 'absolute';
        badge.style.left = '-2px';
        badge.style.top = '-18px';
        badge.style.backgroundColor = '#e63946';
        badge.style.color = '#ffffff';
        badge.style.fontSize = '11px';
        badge.style.fontWeight = 'bold';
        badge.style.padding = '0 4px';
        badge.style.borderRadius = '2px';
        badge.style.lineHeight = '15px';
        badge.style.fontFamily = 'monospace';

        box.appendChild(badge);
        overlay.appendChild(box);
    });
}"""

# Markdown text extraction script for JS-rendered DOM
DOM_MARKDOWN_SCRIPT = r"""() => {
    function nodeToMd(node) {
        if (!node) return '';
        if (node.nodeType === Node.TEXT_NODE) {
            return node.textContent.replace(/\s+/g, ' ');
        }
        if (node.nodeType !== Node.ELEMENT_NODE) return '';

        const tag = node.tagName.toLowerCase();
        const style = window.getComputedStyle(node);
        if (style.display === 'none' || style.visibility === 'hidden') return '';

        let inner = Array.from(node.childNodes).map(nodeToMd).join('');

        switch (tag) {
            case 'h1': return '\n\n# ' + inner.trim() + '\n\n';
            case 'h2': return '\n\n## ' + inner.trim() + '\n\n';
            case 'h3': return '\n\n### ' + inner.trim() + '\n\n';
            case 'h4': return '\n\n#### ' + inner.trim() + '\n\n';
            case 'h5': return '\n\n##### ' + inner.trim() + '\n\n';
            case 'h6': return '\n\n###### ' + inner.trim() + '\n\n';
            case 'p': return '\n\n' + inner.trim() + '\n\n';
            case 'br': return '\n';
            case 'hr': return '\n\n---\n\n';
            case 'a': {
                const href = node.getAttribute('href');
                const text = inner.trim();
                return href && text ? '[' + text + '](' + href + ')' : text;
            }
            case 'b':
            case 'strong': return '**' + inner.trim() + '**';
            case 'i':
            case 'em': return '*' + inner.trim() + '*';
            case 'code': return '`' + inner.trim() + '`';
            case 'pre': return '\n\n```\n' + node.innerText + '\n```\n\n';
            case 'li': return '\n- ' + inner.trim();
            case 'ul':
            case 'ol': return '\n' + inner + '\n';
            case 'script':
            case 'style':
            case 'noscript':
            case 'svg': return '';
            default: return inner;
        }
    }
    const md = nodeToMd(document.body);
    return md.replace(/\n{3,}/g, '\n\n').trim();
}"""

# Auto-dismiss cookie banners script
COOKIE_DISMISS_SCRIPT = r"""() => {
    const patterns = /^(accept\s*all|agree|allow\s*all|allow|accept|i\s*agree|ok|got\s*it|enable\s*all)$/i;
    const candidates = Array.from(document.querySelectorAll('button, a, [role="button"], input[type="button"], input[type="submit"]'));
    for (const btn of candidates) {
        const text = (btn.innerText || btn.value || btn.getAttribute('aria-label') || '').trim();
        if (patterns.test(text)) {
            const style = window.getComputedStyle(btn);
            if (style.display !== 'none' && style.visibility !== 'hidden') {
                try {
                    btn.click();
                    return true;
                } catch (e) {}
            }
        }
    }
    return false;
}"""


class BrowserManager:
    """
    Central thread-safe manager controlling Camoufox stealth browser instances
    via a dedicated persistent asyncio event loop thread.
    """

    def __init__(self, orchestrator=None):
        self.orchestrator = orchestrator
        self.agent_id = orchestrator.agent_id if orchestrator and hasattr(orchestrator, "agent_id") else "default"
        self.agent_name = orchestrator.agent_name if orchestrator and hasattr(orchestrator, "agent_name") else "Agent"
        self.settings_manager = orchestrator.settings_manager if orchestrator and hasattr(orchestrator, "settings_manager") else None

        self._lock = threading.RLock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None

        self._camoufox = None
        self._context = None
        self._active_page = None
        self._tab_history: List[Any] = []
        self._page_parents: Dict[Any, Any] = {}
        self._recently_opened_page = None

        self._idle_timer: Optional[threading.Timer] = None
        self._last_download_path: Optional[str] = None
        self._current_mouse_pos = (100, 100)

        # Register orchestrator pause signal listener if available
        if self.orchestrator and hasattr(self.orchestrator, "on_pause"):
            try:
                self.orchestrator.on_pause.connect(self._on_pause)
            except Exception as e:
                logger.warning(f"Could not connect to orchestrator on_pause signal: {e}")

    # =========================================================================
    # Configuration Helpers
    # =========================================================================

    def _get_setting(self, key: str, default: Any) -> Any:
        if self.settings_manager:
            return self.settings_manager.get(f"core.browser.{key}", default)
        return default

    @property
    def is_headless(self) -> bool:
        return bool(self._get_setting("headless", True))

    @property
    def is_humanize(self) -> bool:
        return bool(self._get_setting("humanize", True))

    @property
    def idle_timeout_seconds(self) -> int:
        return int(self._get_setting("idle-timeout-minutes", 5)) * 60

    @property
    def page_timeout_seconds(self) -> int:
        return int(self._get_setting("page-timeout-seconds", 30))

    @property
    def action_timeout_seconds(self) -> int:
        return int(self._get_setting("action-timeout-seconds", 4))

    @property
    def action_timeout_ms(self) -> int:
        return self.action_timeout_seconds * 1000

    @property
    def max_accessibility_nodes(self) -> int:
        return int(self._get_setting("max-accessibility-nodes", 150))

    @property
    def screenshot_max_dim(self) -> int:
        return int(self._get_setting("screenshot-max-dimension", 1024))

    # =========================================================================
    # Async Event Loop Thread & Bridge
    # =========================================================================

    def _ensure_event_loop(self):
        with self._lock:
            if self._loop is None or not self._loop.is_running():
                self._loop = asyncio.new_event_loop()
                self._loop_thread = threading.Thread(
                    target=self._run_loop,
                    name=f"BrowserEventLoop-{self.agent_id}",
                    daemon=True
                )
                self._loop_thread.start()

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _run_async(self, coro, timeout: Optional[float] = None) -> Any:
        """Executes a coroutine on the dedicated background event loop and blocks for result."""
        self._ensure_event_loop()
        effective_timeout = timeout if timeout is not None else (self.page_timeout_seconds + 10)
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=effective_timeout)
        except TimeoutError:
            future.cancel()
            raise TimeoutError(f"Browser operation timed out after {effective_timeout}s")

    # =========================================================================
    # Lifecycle & Persistent Context
    # =========================================================================

    def _reset_idle_watchdog(self):
        with self._lock:
            if self._idle_timer:
                self._idle_timer.cancel()
                self._idle_timer = None
            timeout = self.idle_timeout_seconds
            if timeout > 0:
                self._idle_timer = threading.Timer(timeout, self._on_idle_timeout)
                self._idle_timer.daemon = True
                self._idle_timer.start()

    def _on_idle_timeout(self):
        logger.info(f"BrowserManager: Idle timeout reached ({self.idle_timeout_seconds}s). Closing browser process...")
        try:
            self._run_async(self._close_browser_async(), timeout=15)
        except Exception as e:
            logger.error(f"BrowserManager: Error closing browser on idle timeout: {e}")

    def _on_pause(self):
        logger.info("BrowserManager: Pause signal received. Closing browser process to reclaim memory...")
        try:
            self._run_async(self._close_browser_async(), timeout=15)
        except Exception as e:
            logger.error(f"BrowserManager: Error closing browser on pause: {e}")

    def _resolve_executable_path(self) -> Optional[str]:
        # 1. Environment variable override
        env_path = os.environ.get("CAMOUFOX_BINARY_PATH")
        if env_path and os.path.exists(env_path):
            return env_path
        # 2. Bundled Flatpak location
        flatpak_candidates = [
            "/app/share/camoufox/camoufox-bin",
            "/app/share/camoufox/camoufox",
            "/app/share/camoufox/firefox"
        ]
        for p in flatpak_candidates:
            if os.path.exists(p):
                return p
        return None

    def _attach_page_listeners(self, page, parent_page=None):
        """Attaches download and close listeners to pages for tab stack management."""
        if parent_page:
            self._page_parents[page] = parent_page

        if page not in self._tab_history:
            self._tab_history.append(page)

        page.on("download", self._on_download_async)
        page.on("close", lambda p=page: self._on_page_close(p))

    def _on_page_close(self, page):
        """Auto-falls back to parent tab or previous active tab when a tab/popup closes."""
        logger.info(f"BrowserManager: Page closed: {page}. Auto-managing active tab...")
        if page in self._tab_history:
            self._tab_history.remove(page)

        parent = self._page_parents.pop(page, None)

        if self._active_page == page or (self._active_page and self._active_page.is_closed()):
            # Fall back to parent if still open
            if parent and not parent.is_closed() and self._context and parent in self._context.pages:
                self._active_page = parent
            else:
                remaining = [p for p in self._tab_history if not p.is_closed()]
                if not remaining and self._context and self._context.pages:
                    remaining = [p for p in self._context.pages if not p.is_closed()]
                if remaining:
                    self._active_page = remaining[-1]
                else:
                    self._active_page = None

            if self._active_page:
                logger.info(f"BrowserManager: Auto-switched active focus to: {self._active_page.url}")
                try:
                    if self._loop and self._loop.is_running():
                        asyncio.run_coroutine_threadsafe(self._active_page.bring_to_front(), self._loop)
                except Exception:
                    pass

    async def _ensure_browser_async(self):
        """Ensures the Camoufox browser process and active page are running."""
        if self._context is not None:
            # Auto-heal active page if closed or invalid
            if self._active_page is None or self._active_page.is_closed():
                open_pages = [p for p in self._context.pages if not p.is_closed()]
                if open_pages:
                    self._active_page = open_pages[-1]
                else:
                    self._active_page = await self._context.new_page()
                    self._attach_page_listeners(self._active_page)

            try:
                # Test active page responsiveness
                await self._active_page.evaluate("1 + 1")
                return
            except Exception:
                logger.warning("BrowserManager: Active page unresponsive. Rebuilding context...")
                await self._close_browser_async()

        from camoufox import AsyncCamoufox

        data_dir = paths.get_browser_data_dir(self.agent_id)
        os.makedirs(data_dir, exist_ok=True)

        exec_path = self._resolve_executable_path()
        viewport_w = int(self._get_setting("default-viewport-width", 1280))
        viewport_h = int(self._get_setting("default-viewport-height", 800))

        launch_kwargs = {
            "user_data_dir": data_dir,
            "persistent_context": True,
            "headless": self.is_headless,
            "humanize": self.is_humanize,
        }
        if exec_path:
            launch_kwargs["executable_path"] = exec_path

        logger.info(f"BrowserManager: Launching Camoufox (headless={self.is_headless}, user_data_dir={data_dir})...")
        self._camoufox = AsyncCamoufox(**launch_kwargs)
        self._context = await self._camoufox.__aenter__()

        # Register tab/popup listener
        self._context.on("page", self._on_new_page_async)

        if self._context.pages:
            self._active_page = self._context.pages[0]
        else:
            self._active_page = await self._context.new_page()

        self._attach_page_listeners(self._active_page)

        # Set default viewport
        try:
            await self._active_page.set_viewport_size({"width": viewport_w, "height": viewport_h})
        except Exception as e:
            logger.debug(f"Could not set viewport size: {e}")

    def _on_new_page_async(self, page):
        """Auto-focuses newly opened popups or tabs."""
        logger.info(f"BrowserManager: New tab/popup detected: {page.url}. Auto-focusing...")
        self._recently_opened_page = page
        self._attach_page_listeners(page, parent_page=self._active_page)
        self._active_page = page

    async def _on_download_async(self, download):
        """Handles browser-initiated downloads to the agent downloads directory."""
        try:
            dl_dir = paths.get_browser_download_dir(self.agent_name)
            os.makedirs(dl_dir, exist_ok=True)
            filename = download.suggested_filename
            save_path = os.path.join(dl_dir, filename)

            # Avoid collision
            base, ext = os.path.splitext(filename)
            counter = 1
            while os.path.exists(save_path):
                save_path = os.path.join(dl_dir, f"{base}_{counter}{ext}")
                counter += 1

            await download.save_as(save_path)
            self._last_download_path = save_path
            logger.info(f"BrowserManager: Downloaded file saved to {save_path}")
        except Exception as e:
            logger.error(f"BrowserManager: Download failed: {e}", exc_info=True)

    async def _close_browser_async(self):
        """Closes browser context and frees resources."""
        with self._lock:
            if self._idle_timer:
                self._idle_timer.cancel()
                self._idle_timer = None

        if self._camoufox:
            try:
                await self._camoufox.__aexit__(None, None, None)
            except Exception as e:
                logger.debug(f"BrowserManager: Error during browser teardown: {e}")
            self._camoufox = None
            self._context = None
            self._active_page = None
            self._tab_history.clear()
            self._page_parents.clear()
            self._recently_opened_page = None

    def shutdown(self):
        """Synchronously shuts down the browser and stops the event loop thread."""
        logger.info("BrowserManager: Shutting down...")
        with self._lock:
            if self._idle_timer:
                self._idle_timer.cancel()
                self._idle_timer = None

        if self._loop and self._loop.is_running():
            try:
                self._run_async(self._close_browser_async(), timeout=10)
            except Exception as e:
                logger.debug(f"BrowserManager: Error closing browser during shutdown: {e}")

            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._loop_thread and self._loop_thread.is_alive():
                self._loop_thread.join(timeout=3)
            self._loop = None
            self._loop_thread = None

    # =========================================================================
    # Accessibility Tree & Formatting
    # =========================================================================

    async def _extract_pruned_tree_async(self) -> Tuple[str, List[Dict[str, Any]]]:
        """Scans the DOM and formats the pruned accessibility tree for agent consumption."""
        if not self._active_page or self._active_page.is_closed():
            return "No active page.", []

        try:
            title = await self._active_page.title()
            url = self._active_page.url
            nodes = await self._active_page.evaluate(DOM_SCANNER_SCRIPT)
        except Exception as e:
            return f"Error extracting accessibility tree: {e}", []

        output_lines = [f'Page: "{title}" | {url}\n']

        interactive_nodes = [n for n in nodes if not n.get("is_heading")]
        total_interactive = len(interactive_nodes)
        max_nodes = self.max_accessibility_nodes

        visible_count = 0
        truncated = False

        for node in nodes:
            if node.get("is_heading"):
                level = node.get("level", 2)
                name = node.get("name", "")
                output_lines.append(f'--- "{name}" (h{level}) ---')
            else:
                if visible_count >= max_nodes:
                    truncated = True
                    break

                elem_id = node.get("id")
                role = node.get("role", "element")
                name = node.get("name", "")
                type_attr = node.get("type")
                extra = node.get("extra", "")

                role_str = role
                if role == "input" and type_attr:
                    role_str = f"input[{type_attr}]"

                line = f'[{elem_id}] {role_str} "{name}"'
                if extra:
                    line += f" {extra}"
                output_lines.append(line)
                visible_count += 1

        if truncated:
            remaining = total_interactive - visible_count
            output_lines.append(f"[... {remaining} more elements. Scroll or narrow your focus.]")

        return "\n".join(output_lines), interactive_nodes

    # =========================================================================
    # Human-Like Interaction Simulation
    # =========================================================================

    async def _bezier_move_mouse(self, target_x: float, target_y: float, steps: int = 15):
        """Generates a cubic Bézier curve to simulate human mouse movement."""
        start_x, start_y = self._current_mouse_pos
        dx = target_x - start_x
        dy = target_y - start_y

        # Control points with random natural curvature
        ctrl1_x = start_x + dx * 0.25 + random.uniform(-20, 20)
        ctrl1_y = start_y + dy * 0.25 + random.uniform(-20, 20)
        ctrl2_x = start_x + dx * 0.75 + random.uniform(-20, 20)
        ctrl2_y = start_y + dy * 0.75 + random.uniform(-20, 20)

        for i in range(1, steps + 1):
            t = i / steps
            # Cubic Bézier formula
            bx = ((1 - t) ** 3) * start_x + 3 * ((1 - t) ** 2) * t * ctrl1_x + 3 * (1 - t) * (t ** 2) * ctrl2_x + (t ** 3) * target_x
            by = ((1 - t) ** 3) * start_y + 3 * ((1 - t) ** 2) * t * ctrl1_y + 3 * (1 - t) * (t ** 2) * ctrl2_y + (t ** 3) * target_y
            await self._active_page.mouse.move(bx, by)
            await asyncio.sleep(random.uniform(0.005, 0.015))

        self._current_mouse_pos = (target_x, target_y)

    # =========================================================================
    # Command Implementations
    # =========================================================================

    def navigate(self, url: str, screenshot: bool = False) -> Any:
        self._reset_idle_watchdog()
        return self._run_async(self._navigate_async(url, screenshot))

    async def _navigate_async(self, url: str, screenshot: bool = False) -> Any:
        await self._ensure_browser_async()
        if not (url.startswith("http://") or url.startswith("https://") or url.startswith("file://") or url.startswith("about:") or url.startswith("data:")):
            url = "https://" + url

        self._last_download_path = None
        try:
            await self._active_page.goto(url, timeout=self.page_timeout_seconds * 1000, wait_until="load")
        except Exception as e:
            # Check if navigation triggered a download
            if self._last_download_path:
                dl = self._last_download_path
                self._last_download_path = None
                return {"result": f"Downloaded file: {dl}", "media": [dl]}
            return {"error": f"Navigation failed: {e}", "url": url}

        # Check for binary / download response
        if self._last_download_path:
            dl = self._last_download_path
            self._last_download_path = None
            return {"result": f"Downloaded file: {dl}", "media": [dl]}

        # Auto-dismiss cookie consent banner if configured
        if self._get_setting("auto-dismiss-cookies", True):
            try:
                await self._active_page.evaluate(COOKIE_DISMISS_SCRIPT)
            except Exception:
                pass

        tree_text, _ = await self._extract_pruned_tree_async()

        if screenshot:
            screenshot_res = await self._screenshot_async()
            if isinstance(screenshot_res, dict) and "media" in screenshot_res:
                return {
                    "result": f"{tree_text}\n\n[Screenshot captured: {screenshot_res.get('result')}]",
                    "media": screenshot_res["media"]
                }
        return tree_text

    def go_back(self) -> str:
        self._reset_idle_watchdog()
        return self._run_async(self._go_back_async())

    async def _go_back_async(self) -> str:
        await self._ensure_browser_async()
        try:
            await self._active_page.go_back(timeout=self.page_timeout_seconds * 1000)
        except Exception as e:
            return f"Error navigating back: {e}"
        tree_text, _ = await self._extract_pruned_tree_async()
        return tree_text

    def go_forward(self) -> str:
        self._reset_idle_watchdog()
        return self._run_async(self._go_forward_async())

    async def _go_forward_async(self) -> str:
        await self._ensure_browser_async()
        try:
            await self._active_page.go_forward(timeout=self.page_timeout_seconds * 1000)
        except Exception as e:
            return f"Error navigating forward: {e}"
        tree_text, _ = await self._extract_pruned_tree_async()
        return tree_text

    def click(self, element_id: int) -> Any:
        self._reset_idle_watchdog()
        return self._run_async(self._click_async(element_id))

    async def _click_async(self, element_id: int) -> Any:
        await self._ensure_browser_async()
        locator = self._active_page.locator(f'[data-oa-id="{element_id}"]')

        count = await locator.count()
        if count == 0:
            tree_text, _ = await self._extract_pruned_tree_async()
            return {
                "error": f"Element [{element_id}] not found on the page. The page may have changed or the element ID is stale. Please review the updated accessibility tree.",
                "tree": tree_text
            }

        try:
            # Smart Auto-Wait: wait for actionable state with fast-fail timeout
            await locator.wait_for(state="visible", timeout=self.action_timeout_ms)

            box = await locator.bounding_box(timeout=self.action_timeout_ms)

            self._recently_opened_page = None

            if box and self.is_humanize:
                center_x = box["x"] + box["width"] / 2
                center_y = box["y"] + box["height"] / 2
                await self._bezier_move_mouse(center_x, center_y)
                await self._active_page.mouse.click(center_x, center_y)
            else:
                await locator.click(timeout=self.action_timeout_ms)

            # Check if click initiated a download
            if self._last_download_path:
                dl = self._last_download_path
                self._last_download_path = None
                return {"result": f"Downloaded file: {dl}", "media": [dl]}

            # Check if click spawned a popup / new tab
            for _ in range(10):
                if self._recently_opened_page:
                    break
                await asyncio.sleep(0.1)

            if self._recently_opened_page and not self._recently_opened_page.is_closed():
                popup = self._recently_opened_page
                self._recently_opened_page = None
                try:
                    await popup.wait_for_load_state("domcontentloaded", timeout=4000)
                except Exception:
                    pass
                title = await popup.title()
                url = popup.url
                tree_text, _ = await self._extract_pruned_tree_async()
                return f"[Popup window opened and auto-focused: \"{title}\" | {url}]\n\n{tree_text}"

            # Brief pause for DOM updates
            await asyncio.sleep(0.3)
            tree_text, _ = await self._extract_pruned_tree_async()
            return tree_text
        except Exception as e:
            if self._last_download_path:
                dl = self._last_download_path
                self._last_download_path = None
                return {"result": f"Downloaded file: {dl}", "media": [dl]}

            tree_text, _ = await self._extract_pruned_tree_async()
            return {
                "error": f"Click on [{element_id}] failed or element not actionable after {self.action_timeout_seconds}s: {e}",
                "tree": tree_text
            }

    def type_text(self, element_id: int, text: str, press_enter: bool = False, instant: bool = False) -> Any:
        self._reset_idle_watchdog()
        return self._run_async(self._type_text_async(element_id, text, press_enter, instant))

    async def _type_text_async(self, element_id: int, text: str, press_enter: bool = False, instant: bool = False) -> Any:
        await self._ensure_browser_async()
        locator = self._active_page.locator(f'[data-oa-id="{element_id}"]')

        count = await locator.count()
        if count == 0:
            tree_text, _ = await self._extract_pruned_tree_async()
            return {
                "error": f"Element [{element_id}] not found on the page. The page may have changed or the element ID is stale. Please review the updated accessibility tree.",
                "tree": tree_text
            }

        try:
            # Smart Auto-Wait: wait for actionable state with fast-fail timeout
            await locator.wait_for(state="visible", timeout=self.action_timeout_ms)

            await locator.focus(timeout=self.action_timeout_ms)

            if self.is_humanize and not instant:
                # Type with natural keystroke jitter
                for ch in text:
                    await self._active_page.keyboard.type(ch)
                    await asyncio.sleep(random.uniform(0.04, 0.11))
            else:
                await locator.fill(text, timeout=self.action_timeout_ms)

            if press_enter:
                await self._active_page.keyboard.press("Enter")

            await asyncio.sleep(0.3)
            tree_text, _ = await self._extract_pruned_tree_async()
            return tree_text
        except Exception as e:
            tree_text, _ = await self._extract_pruned_tree_async()
            return {
                "error": f"Type on [{element_id}] failed or element not actionable after {self.action_timeout_seconds}s: {e}",
                "tree": tree_text
            }

    def upload(self, file_path: str, element_id: Optional[int] = None) -> Any:
        self._reset_idle_watchdog()
        return self._run_async(self._upload_async(file_path, element_id))

    async def _upload_async(self, file_path: str, element_id: Optional[int] = None) -> Any:
        """Uploads a local file directly into a file input or via file chooser dialog."""
        await self._ensure_browser_async()

        expanded_path = os.path.abspath(os.path.expanduser(file_path))
        if not os.path.exists(expanded_path):
            return {"error": f"File not found at '{file_path}' (expanded: '{expanded_path}')"}

        if not os.path.isfile(expanded_path):
            return {"error": f"Path is not a regular file: '{expanded_path}'"}

        try:
            if element_id is not None:
                locator = self._active_page.locator(f'[data-oa-id="{element_id}"]')
                count = await locator.count()
                if count == 0:
                    tree_text, _ = await self._extract_pruned_tree_async()
                    return {"error": f"Element [{element_id}] not found on the page. Current elements:", "tree": tree_text}

                tag_name = await locator.evaluate("el => el.tagName.toLowerCase()")
                input_type = await locator.evaluate("el => el.getAttribute('type') || ''")

                if tag_name == "input" and input_type.lower() == "file":
                    await locator.set_input_files(expanded_path)
                else:
                    # Target is an upload button or dropzone that triggers native file chooser
                    try:
                        async with self._active_page.expect_file_chooser(timeout=self.action_timeout_ms) as fc_info:
                            box = await locator.bounding_box(timeout=self.action_timeout_ms)
                            if box and self.is_humanize:
                                center_x = box["x"] + box["width"] / 2
                                center_y = box["y"] + box["height"] / 2
                                await self._bezier_move_mouse(center_x, center_y)
                                await self._active_page.mouse.click(center_x, center_y)
                            else:
                                await locator.click(timeout=self.action_timeout_ms)
                        file_chooser = await fc_info.value
                        await file_chooser.set_files(expanded_path)
                    except Exception as chooser_err:
                        # Fallback: find any file input on the page
                        file_inputs = self._active_page.locator('input[type="file"]')
                        if await file_inputs.count() > 0:
                            await file_inputs.first.set_input_files(expanded_path)
                        else:
                            raise chooser_err
            else:
                # element_id is None: find the first input[type="file"] on page
                file_inputs = self._active_page.locator('input[type="file"]')
                if await file_inputs.count() == 0:
                    tree_text, _ = await self._extract_pruned_tree_async()
                    return {
                        "error": "No file input (<input type='file'>) found on the page to receive the file. Try targeting an upload button element_id.",
                        "tree": tree_text
                    }
                await file_inputs.first.set_input_files(expanded_path)

            await asyncio.sleep(0.5)
            tree_text, _ = await self._extract_pruned_tree_async()
            return f"Successfully uploaded '{os.path.basename(expanded_path)}'. Current page state:\n\n{tree_text}"
        except Exception as e:
            tree_text, _ = await self._extract_pruned_tree_async()
            return {
                "error": f"Upload failed: {e}",
                "tree": tree_text
            }

    def select(self, element_id: int, value: str) -> Any:
        self._reset_idle_watchdog()
        return self._run_async(self._select_async(element_id, value))

    async def _select_async(self, element_id: int, value: str) -> Any:
        await self._ensure_browser_async()
        locator = self._active_page.locator(f'[data-oa-id="{element_id}"]')

        count = await locator.count()
        if count == 0:
            tree_text, _ = await self._extract_pruned_tree_async()
            return {
                "error": f"Element [{element_id}] not found on the page. The page may have changed or the element ID is stale. Please review the updated accessibility tree.",
                "tree": tree_text
            }

        try:
            # Smart Auto-Wait: wait for actionable state with fast-fail timeout
            await locator.wait_for(state="visible", timeout=self.action_timeout_ms)

            try:
                await locator.select_option(label=value, timeout=self.action_timeout_ms)
            except Exception:
                await locator.select_option(value=value, timeout=self.action_timeout_ms)

            await asyncio.sleep(0.3)
            tree_text, _ = await self._extract_pruned_tree_async()
            return tree_text
        except Exception as e:
            tree_text, _ = await self._extract_pruned_tree_async()
            return {
                "error": f"Select on [{element_id}] failed or element not actionable after {self.action_timeout_seconds}s: {e}",
                "tree": tree_text
            }

    def hover(self, element_id: int) -> Any:
        self._reset_idle_watchdog()
        return self._run_async(self._hover_async(element_id))

    async def _hover_async(self, element_id: int) -> Any:
        await self._ensure_browser_async()
        locator = self._active_page.locator(f'[data-oa-id="{element_id}"]')

        count = await locator.count()
        if count == 0:
            tree_text, _ = await self._extract_pruned_tree_async()
            return {
                "error": f"Element [{element_id}] not found on the page. The page may have changed or the element ID is stale. Please review the updated accessibility tree.",
                "tree": tree_text
            }

        try:
            # Smart Auto-Wait: wait for actionable state with fast-fail timeout
            await locator.wait_for(state="visible", timeout=self.action_timeout_ms)

            box = await locator.bounding_box(timeout=self.action_timeout_ms)
            if box and self.is_humanize:
                center_x = box["x"] + box["width"] / 2
                center_y = box["y"] + box["height"] / 2
                await self._bezier_move_mouse(center_x, center_y)
            else:
                await locator.hover(timeout=self.action_timeout_ms)

            await asyncio.sleep(0.2)
            tree_text, _ = await self._extract_pruned_tree_async()
            return tree_text
        except Exception as e:
            tree_text, _ = await self._extract_pruned_tree_async()
            return {
                "error": f"Hover on [{element_id}] failed or element not actionable after {self.action_timeout_seconds}s: {e}",
                "tree": tree_text
            }

    def click_coordinates(self, x: int, y: int) -> Any:
        self._reset_idle_watchdog()
        return self._run_async(self._click_coordinates_async(x, y))

    async def _click_coordinates_async(self, x: int, y: int) -> Any:
        await self._ensure_browser_async()
        try:
            if self.is_humanize:
                await self._bezier_move_mouse(x, y)
                await self._active_page.mouse.click(x, y)
            else:
                await self._active_page.mouse.click(x, y)

            if self._last_download_path:
                dl = self._last_download_path
                self._last_download_path = None
                return {"result": f"Downloaded file: {dl}", "media": [dl]}

            await asyncio.sleep(0.3)
            tree_text, _ = await self._extract_pruned_tree_async()
            return tree_text
        except Exception as e:
            return {"error": f"Click coordinates ({x}, {y}) failed: {e}"}

    def press_key(self, key: str) -> Any:
        self._reset_idle_watchdog()
        return self._run_async(self._press_key_async(key))

    async def _press_key_async(self, key: str) -> Any:
        await self._ensure_browser_async()
        try:
            await self._active_page.keyboard.press(key)
            await asyncio.sleep(0.3)
            tree_text, _ = await self._extract_pruned_tree_async()
            return tree_text
        except Exception as e:
            return {"error": f"Key press '{key}' failed: {e}"}

    def scroll(self, direction: str = "down", amount: int = 500) -> Any:
        self._reset_idle_watchdog()
        return self._run_async(self._scroll_async(direction, amount))

    async def _scroll_async(self, direction: str = "down", amount: int = 500) -> Any:
        await self._ensure_browser_async()
        dy = amount if direction.lower() == "down" else -amount

        try:
            if self.is_humanize:
                steps = 8
                step_dy = dy / steps
                for _ in range(steps):
                    await self._active_page.evaluate(f"window.scrollBy(0, {step_dy})")
                    await asyncio.sleep(0.02)
            else:
                await self._active_page.evaluate(f"window.scrollBy(0, {dy})")

            await asyncio.sleep(0.2)
            tree_text, _ = await self._extract_pruned_tree_async()
            return tree_text
        except Exception as e:
            return {"error": f"Scroll failed: {e}"}

    def screenshot(self, high_res: bool = False, show_overlay_cues: bool = True, full_page: bool = False) -> Any:
        self._reset_idle_watchdog()
        return self._run_async(self._screenshot_async(high_res, show_overlay_cues, full_page))

    async def _screenshot_async(self, high_res: bool = False, show_overlay_cues: bool = True, full_page: bool = False) -> Any:
        await self._ensure_browser_async()
        media_dir = os.path.join(paths.get_agent_data_dir(self.agent_id), "media")
        os.makedirs(media_dir, exist_ok=True)

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:19]
        file_path = os.path.join(media_dir, f"browser_screenshot_{timestamp}.png")

        try:
            # Ensure elements are tagged with data-oa-id
            await self._active_page.evaluate(DOM_SCANNER_SCRIPT)

            if show_overlay_cues:
                await self._active_page.evaluate(SOM_OVERLAY_SCRIPT)

            await self._active_page.screenshot(path=file_path, full_page=full_page)

            if show_overlay_cues:
                # Remove overlay from DOM
                await self._active_page.evaluate("() => { const o = document.getElementById('oa-som-overlay'); if (o) o.remove(); }")

            # Check low token mode
            is_low_token = False
            if self.settings_manager:
                is_low_token = self.settings_manager.get("core.low-token-mode", False)

            # Process / downscale image
            if not high_res or is_low_token:
                try:
                    with Image.open(file_path) as im:
                        max_dim = self.screenshot_max_dim
                        if max(im.size) > max_dim:
                            im.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
                            im.save(file_path, "PNG")
                except Exception as e:
                    logger.warning(f"Failed to downscale screenshot: {e}")

            if is_low_token:
                return f"Screenshot saved to {file_path} (Low Token Mode active, media attachment omitted)."

            return {
                "result": f"Screenshot successfully saved to {file_path} (Set-of-Marks cues={'enabled' if show_overlay_cues else 'disabled'}).",
                "media": [file_path]
            }
        except Exception as e:
            return {"error": f"Screenshot failed: {e}"}

    def extract_content(self, mode: str = "markdown") -> str:
        self._reset_idle_watchdog()
        return self._run_async(self._extract_content_async(mode))

    async def _extract_content_async(self, mode: str = "markdown") -> str:
        await self._ensure_browser_async()
        try:
            if mode.lower() == "html":
                return await self._active_page.content()
            return await self._active_page.evaluate(DOM_MARKDOWN_SCRIPT)
        except Exception as e:
            return f"Error extracting content: {e}"

    def wait_for(self, selector: Optional[str] = None, timeout: int = 10) -> Any:
        self._reset_idle_watchdog()
        return self._run_async(self._wait_for_async(selector, timeout))

    async def _wait_for_async(self, selector: Optional[str] = None, timeout: int = 10) -> Any:
        await self._ensure_browser_async()
        ms = timeout * 1000
        try:
            if selector:
                await self._active_page.wait_for_selector(selector, timeout=ms)
            else:
                await self._active_page.wait_for_load_state("networkidle", timeout=ms)
            tree_text, _ = await self._extract_pruned_tree_async()
            return tree_text
        except Exception as e:
            return {"error": f"Wait condition failed: {e}"}

    def list_tabs(self) -> str:
        self._reset_idle_watchdog()
        return self._run_async(self._list_tabs_async())

    async def _list_tabs_async(self) -> str:
        await self._ensure_browser_async()
        pages = [p for p in self._context.pages if not p.is_closed()]
        if not pages:
            return "No tabs open."

        lines = [f"Open Tabs ({len(pages)}):"]
        for idx, page in enumerate(pages):
            is_active = (page == self._active_page)
            try:
                title = await page.title()
            except Exception:
                title = "Untitled"
            url = page.url
            active_mark = " (active)" if is_active else ""
            lines.append(f'[{idx}] "{title}" - {url}{active_mark}')

        return "\n".join(lines)

    def switch_tab(self, index: int) -> Any:
        self._reset_idle_watchdog()
        return self._run_async(self._switch_tab_async(index))

    async def _switch_tab_async(self, index: int) -> Any:
        await self._ensure_browser_async()
        pages = [p for p in self._context.pages if not p.is_closed()]
        if not (0 <= index < len(pages)):
            return {"error": f"Tab index {index} out of range (0 to {len(pages) - 1})."}

        self._active_page = pages[index]
        await self._active_page.bring_to_front()
        tree_text, _ = await self._extract_pruned_tree_async()
        return f"Switched to tab [{index}]:\n\n{tree_text}"

    def clear_cache(self, domain: Optional[str] = None) -> str:
        self._reset_idle_watchdog()
        return self._run_async(self._clear_cache_async(domain))

    async def _clear_cache_async(self, domain: Optional[str] = None) -> str:
        await self._ensure_browser_async()
        try:
            if domain:
                # Clear cookies matching domain
                cookies = await self._context.cookies()
                matching_cookies = [c for c in cookies if domain in c.get("domain", "")]
                if matching_cookies:
                    await self._context.clear_cookies(domain=domain)

                # Clear local storage on active page if same domain
                if self._active_page and domain in self._active_page.url:
                    await self._active_page.evaluate("() => { try { localStorage.clear(); sessionStorage.clear(); } catch(e){} }")
                return f"Browser cache and cookies cleared for domain: {domain}"
            else:
                await self._context.clear_cookies()
                cache_dir = paths.get_browser_cache_dir(self.agent_id)
                if os.path.exists(cache_dir):
                    import shutil
                    shutil.rmtree(cache_dir, ignore_errors=True)
                return "All browser cookies and persistent cache cleared."
        except Exception as e:
            return f"Error clearing cache: {e}"

    def close(self) -> str:
        """Closes the browser session to immediately reclaim memory."""
        self._run_async(self._close_browser_async(), timeout=10)
        return "Browser session closed successfully."
