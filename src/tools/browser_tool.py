import logging
from typing import List, Dict, Any, Optional

from core.cerebrum import Tool
from core.browser_manager import BrowserManager

logger = logging.getLogger("tools.BrowserSkill")


class BrowserSkill(Tool):
    """
    Camoufox-based stealth browser tool providing interactive browsing,
    Set-of-Marks visual cues, DOM accessibility tree exploration,
    tab control, and session management for Open Amity primary agents.
    """
    name = "Browser"
    icon = "🌍"
    color = "#3B82F6"
    async_commands = []
    description = (
        "Interactive stealth web browser powered by Camoufox. Allows you to navigate websites, "
        "interact with JavaScript-rendered applications (SPAs), click buttons/links, fill forms, "
        "capture Set-of-Marks visual screenshots, manage multiple tabs, and download files."
    )
    commands = [
        "navigate",
        "go_back",
        "go_forward",
        "click",
        "type",
        "upload",
        "select",
        "hover",
        "click_coordinates",
        "press_key",
        "scroll",
        "screenshot",
        "extract_content",
        "wait",
        "list_tabs",
        "switch_tab",
        "clear_cache",
        "close"
    ]

    def __init__(self, orchestrator=None):
        super().__init__(orchestrator)
        self.manager = BrowserManager(orchestrator=self.orchestrator)

    def shutdown(self):
        """Cleanly releases browser context and terminates event loop thread."""
        if hasattr(self, "manager") and self.manager:
            try:
                self.manager.shutdown()
            except Exception as e:
                logger.error(f"Error shutting down BrowserManager: {e}", exc_info=True)

    def get_manual_entry(self) -> str:
        return (
            f"- **{self.name}**: {self.description}\n"
            f"  Commands: {', '.join(self.commands)}"
        )

    def get_tool_declarations(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "Browser_navigate",
                "description": (
                    "Navigates to a URL using the stealth browser, automatically dismisses cookie banners, "
                    "and returns the page title, URL, and a pruned accessibility tree with sequential element IDs. "
                    "Set screenshot=True only if visual verification is needed (defaults to False to conserve tokens)."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "url": {
                            "type": "STRING",
                            "description": "The destination URL to navigate to (e.g. 'https://github.com')."
                        },
                        "screenshot": {
                            "type": "BOOLEAN",
                            "description": "If True, captures and returns a Set-of-Marks screenshot alongside the tree. Default is False."
                        }
                    },
                    "required": ["url"]
                }
            },
            {
                "name": "Browser_go_back",
                "description": "Navigates backward in browser history. Returns the updated accessibility tree.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {}
                }
            },
            {
                "name": "Browser_go_forward",
                "description": "Navigates forward in browser history. Returns the updated accessibility tree.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {}
                }
            },
            {
                "name": "Browser_click",
                "description": (
                    "Clicks an interactive element by its ephemeral accessibility ID ([1], [2], etc.). "
                    "Returns an updated accessibility tree reflecting any DOM changes or a download notice if a file was triggered."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "element_id": {
                            "type": "INTEGER",
                            "description": "The sequential ID of the element to click (e.g. 1)."
                        }
                    },
                    "required": ["element_id"]
                }
            },
            {
                "name": "Browser_type",
                "description": (
                    "Focuses an input field or textarea by its element ID and types text. "
                    "Simulates human typing jitter unless instant=True is passed. "
                    "Set press_enter=True to submit forms immediately."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "element_id": {
                            "type": "INTEGER",
                            "description": "The sequential ID of the input element to type into."
                        },
                        "text": {
                            "type": "STRING",
                            "description": "The string text to type."
                        },
                        "press_enter": {
                            "type": "BOOLEAN",
                            "description": "Whether to press the Enter key after typing. Default is False."
                        },
                        "instant": {
                            "type": "BOOLEAN",
                            "description": "If True, bypasses humanization typing jitter for speed. Default is False."
                        }
                    },
                    "required": ["element_id", "text"]
                }
            },
            {
                "name": "Browser_upload",
                "description": (
                    "Uploads a local file into a file input or file-upload button/dropzone on the active page, "
                    "bypassing native OS file picker dialogs entirely. If element_id is omitted, automatically targets "
                    "the primary file input on the page."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "file_path": {
                            "type": "STRING",
                            "description": "The path to the local file to upload (e.g. '~/Pictures/Amy/photo.jpg')."
                        },
                        "element_id": {
                            "type": "INTEGER",
                            "description": "Optional sequential ID of the file input, upload button, or dropzone."
                        }
                    },
                    "required": ["file_path"]
                }
            },
            {
                "name": "Browser_select",
                "description": "Selects an option in a dropdown <select> element by visible text or value.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "element_id": {
                            "type": "INTEGER",
                            "description": "The sequential ID of the <select> element."
                        },
                        "value": {
                            "type": "STRING",
                            "description": "The option label text or value attribute to select."
                        }
                    },
                    "required": ["element_id", "value"]
                }
            },
            {
                "name": "Browser_hover",
                "description": (
                    "Hovers the mouse over an element by its ID (triggers mouseenter/mouseover). "
                    "Useful for opening hover menus, revealing tooltips, or preview panels."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "element_id": {
                            "type": "INTEGER",
                            "description": "The sequential ID of the element to hover over."
                        }
                    },
                    "required": ["element_id"]
                }
            },
            {
                "name": "Browser_click_coordinates",
                "description": (
                    "Clicks exact pixel coordinates on the active page. "
                    "Serves as an escape hatch for canvas elements, maps, or interactive items missing from the accessibility tree."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "x": {
                            "type": "INTEGER",
                            "description": "X coordinate in pixels relative to viewport."
                        },
                        "y": {
                            "type": "INTEGER",
                            "description": "Y coordinate in pixels relative to viewport."
                        }
                    },
                    "required": ["x", "y"]
                }
            },
            {
                "name": "Browser_press_key",
                "description": (
                    "Sends a keyboard key press (e.g. 'Escape', 'Enter', 'Tab', 'ArrowDown', 'ArrowUp', 'Backspace'). "
                    "Useful for dismissing modal dialogs or keyboard navigation."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "key": {
                            "type": "STRING",
                            "description": "The key name to press (e.g. 'Escape', 'Enter', 'Tab')."
                        }
                    },
                    "required": ["key"]
                }
            },
            {
                "name": "Browser_scroll",
                "description": "Smoothly scrolls the active viewport up or down. Returns the updated accessibility tree.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "direction": {
                            "type": "STRING",
                            "description": "Scroll direction: 'down' or 'up'. Default is 'down'."
                        },
                        "amount": {
                            "type": "INTEGER",
                            "description": "Amount to scroll in pixels. Default is 500."
                        }
                    }
                }
            },
            {
                "name": "Browser_screenshot",
                "description": (
                    "Captures a visual screenshot of the current page with Set-of-Marks numbered bounding badges overlaying interactive controls. "
                    "The image is downscaled to max 1024px by default to save tokens and injected directly into multimodal context."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "high_res": {
                            "type": "BOOLEAN",
                            "description": "If True, saves at full native resolution without downscaling. Default is False."
                        },
                        "show_overlay_cues": {
                            "type": "BOOLEAN",
                            "description": "If True, overlays numbered Set-of-Marks badges matching accessibility element IDs. Default is True."
                        },
                        "full_page": {
                            "type": "BOOLEAN",
                            "description": "If True, captures the entire scrollable height rather than just the visible viewport. Default is False."
                        }
                    }
                }
            },
            {
                "name": "Browser_extract_content",
                "description": (
                    "Extracts the JS-rendered content of the currently active page as clean Markdown text. "
                    "Use this when reading extensive articles, documentation, or long-form page content."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "mode": {
                            "type": "STRING",
                            "description": "Extraction mode: 'markdown' (default) or 'html'."
                        }
                    }
                }
            },
            {
                "name": "Browser_wait",
                "description": (
                    "Waits for a CSS selector to appear in the DOM, or waits for network idle if selector is omitted. "
                    "Essential after clicking actions that trigger asynchronous content loading in SPAs."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "selector": {
                            "type": "STRING",
                            "description": "Optional CSS selector to wait for (e.g. '.results-container')."
                        },
                        "timeout": {
                            "type": "INTEGER",
                            "description": "Maximum seconds to wait. Default is 10."
                        }
                    }
                }
            },
            {
                "name": "Browser_list_tabs",
                "description": "Returns an indexed list of all open browser tabs and popups, indicating which is currently active.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {}
                }
            },
            {
                "name": "Browser_switch_tab",
                "description": "Switches the active browser focus to a specific tab index and returns its accessibility tree.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "index": {
                            "type": "INTEGER",
                            "description": "The zero-based index of the tab to switch to."
                        }
                    },
                    "required": ["index"]
                }
            },
            {
                "name": "Browser_clear_cache",
                "description": (
                    "Clears browser cookies, local storage, and cached assets. "
                    "Optionally specify a domain to clear only cookies/storage for that specific site."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "domain": {
                            "type": "STRING",
                            "description": "Optional domain to clear (e.g. 'github.com'). If omitted, clears all cache and cookies."
                        }
                    }
                }
            },
            {
                "name": "Browser_close",
                "description": "Explicitly closes the browser session and releases all associated memory.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {}
                }
            }
        ]

    def execute(self, command: str, *args, **kwargs) -> Any:
        try:
            if command == "navigate":
                url = kwargs.get("url") or (args[0] if args else None)
                if not url:
                    return {"error": "Missing 'url' parameter."}
                screenshot = bool(kwargs.get("screenshot", False))
                return self.manager.navigate(url=url, screenshot=screenshot)

            elif command == "go_back":
                return self.manager.go_back()

            elif command == "go_forward":
                return self.manager.go_forward()

            elif command == "click":
                elem_id = kwargs.get("element_id") or (args[0] if args else None)
                if elem_id is None:
                    return {"error": "Missing 'element_id' parameter."}
                return self.manager.click(element_id=int(elem_id))

            elif command == "type":
                elem_id = kwargs.get("element_id") or (args[0] if args else None)
                text = kwargs.get("text") or (args[1] if len(args) > 1 else None)
                if elem_id is None or text is None:
                    return {"error": "Missing 'element_id' or 'text' parameter."}
                press_enter = bool(kwargs.get("press_enter", False))
                instant = bool(kwargs.get("instant", False))
                return self.manager.type_text(
                    element_id=int(elem_id),
                    text=str(text),
                    press_enter=press_enter,
                    instant=instant
                )

            elif command == "upload":
                file_path = kwargs.get("file_path") or (args[0] if args else None)
                if not file_path:
                    return {"error": "Missing 'file_path' parameter."}
                elem_id = kwargs.get("element_id") or (args[1] if len(args) > 1 else None)
                return self.manager.upload(
                    file_path=str(file_path),
                    element_id=int(elem_id) if elem_id is not None else None
                )

            elif command == "select":
                elem_id = kwargs.get("element_id") or (args[0] if args else None)
                value = kwargs.get("value") or (args[1] if len(args) > 1 else None)
                if elem_id is None or value is None:
                    return {"error": "Missing 'element_id' or 'value' parameter."}
                return self.manager.select(element_id=int(elem_id), value=str(value))

            elif command == "hover":
                elem_id = kwargs.get("element_id") or (args[0] if args else None)
                if elem_id is None:
                    return {"error": "Missing 'element_id' parameter."}
                return self.manager.hover(element_id=int(elem_id))

            elif command == "click_coordinates":
                x = kwargs.get("x") or (args[0] if args else None)
                y = kwargs.get("y") or (args[1] if len(args) > 1 else None)
                if x is None or y is None:
                    return {"error": "Missing 'x' or 'y' coordinate parameters."}
                return self.manager.click_coordinates(x=int(x), y=int(y))

            elif command == "press_key":
                key = kwargs.get("key") or (args[0] if args else None)
                if not key:
                    return {"error": "Missing 'key' parameter."}
                return self.manager.press_key(key=str(key))

            elif command == "scroll":
                direction = kwargs.get("direction", "down")
                amount = int(kwargs.get("amount", 500))
                return self.manager.scroll(direction=direction, amount=amount)

            elif command == "screenshot":
                high_res = bool(kwargs.get("high_res", False))
                show_overlay_cues = bool(kwargs.get("show_overlay_cues", True))
                full_page = bool(kwargs.get("full_page", False))
                return self.manager.screenshot(
                    high_res=high_res,
                    show_overlay_cues=show_overlay_cues,
                    full_page=full_page
                )

            elif command == "extract_content":
                mode = kwargs.get("mode", "markdown")
                return self.manager.extract_content(mode=mode)

            elif command == "wait":
                selector = kwargs.get("selector")
                timeout = int(kwargs.get("timeout", 10))
                return self.manager.wait_for(selector=selector, timeout=timeout)

            elif command == "list_tabs":
                return self.manager.list_tabs()

            elif command == "switch_tab":
                index = kwargs.get("index") or (args[0] if args else None)
                if index is None:
                    return {"error": "Missing 'index' parameter."}
                return self.manager.switch_tab(index=int(index))

            elif command == "clear_cache":
                domain = kwargs.get("domain")
                return self.manager.clear_cache(domain=domain)

            elif command == "close":
                return self.manager.close()

            return f"Unknown command: {command}"
        except Exception as e:
            logger.error(f"Error executing browser command '{command}': {e}", exc_info=True)
            return {"error": f"Command '{command}' failed: {e}"}
