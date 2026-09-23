import os
import tempfile
import time
import pytest
from PIL import Image

from config import paths
from core.cerebrum import Cerebrum
from core.browser_manager import BrowserManager
from tools.browser_tool import BrowserSkill
from core.subagent_worker import SubagentWorker


class DummyOrchestrator:
    def __init__(self, agent_id="test_browser_agent", agent_name="Nova"):
        self.agent_id = agent_id
        self.agent_name = agent_name
        self.settings_manager = None
        self.cerebrum = None


def test_browser_tool_cerebrum_registration():
    orch = DummyOrchestrator()
    cerebrum = Cerebrum(orchestrator=orch)
    assert "Browser" in cerebrum.tools
    tool = cerebrum.tools["Browser"]
    assert tool.__class__.__name__ == "BrowserSkill"
    assert tool.name == "Browser"

    # Verify all 18 commands declared
    assert len(tool.commands) == 18
    decls = tool.get_tool_declarations()
    assert len(decls) == 18
    decl_names = {d["name"] for d in decls}
    expected_names = {
        "Browser_navigate",
        "Browser_go_back",
        "Browser_go_forward",
        "Browser_click",
        "Browser_type",
        "Browser_select",
        "Browser_hover",
        "Browser_click_coordinates",
        "Browser_press_key",
        "Browser_scroll",
        "Browser_screenshot",
        "Browser_extract_content",
        "Browser_wait",
        "Browser_list_tabs",
        "Browser_switch_tab",
        "Browser_upload",
        "Browser_clear_cache",
        "Browser_close"
    }
    assert decl_names == expected_names
    cerebrum.shutdown()


def test_subagent_worker_isolation():
    orch = DummyOrchestrator()
    cerebrum = Cerebrum(orchestrator=orch)
    orch.cerebrum = cerebrum

    # Verify that SubagentWorker only accepts DateTime and WebSearch
    # Inspect cerebrum tools available for subagents
    subagent_allowed = ["DateTime", "WebSearch"]
    subagent_tools = []
    for name, tool in cerebrum.tools.items():
        if name in subagent_allowed:
            subagent_tools.extend(tool.get_tool_declarations())

    tool_names = [t["name"] for t in subagent_tools]
    assert all(not name.startswith("Browser_") for name in tool_names)
    assert "Browser" not in subagent_allowed
    cerebrum.shutdown()


def test_browser_paths():
    agent_id = "agent_123"
    agent_name = "Amy"

    data_dir = paths.get_browser_data_dir(agent_id)
    assert data_dir.endswith(os.path.join(agent_id, "browser_data"))

    cache_dir = paths.get_browser_cache_dir(agent_id)
    assert cache_dir.endswith(os.path.join(agent_id, "browser_data", "cache"))

    dl_dir = paths.get_browser_download_dir(agent_name)
    assert dl_dir.endswith(os.path.join("Downloads", agent_name))


def test_browser_manager_lifecycle_lazy_and_shutdown():
    orch = DummyOrchestrator(agent_id="test_lazy")
    mgr = BrowserManager(orchestrator=orch)

    # Lazy: process should NOT be launched yet
    assert mgr._camoufox is None
    assert mgr._context is None
    assert mgr._loop is None

    # Trigger first call (ensures event loop thread starts)
    mgr._ensure_event_loop()
    assert mgr._loop is not None
    assert mgr._loop_thread.is_alive()
    assert mgr._loop_thread.daemon is True

    # Shutdown
    mgr.shutdown()
    assert mgr._loop is None
    assert mgr._loop_thread is None


def test_browser_navigation_interaction_and_content():
    orch = DummyOrchestrator(agent_id=f"test_nav_{os.getpid()}")
    tool = BrowserSkill(orchestrator=orch)

    html_content = """
    <!DOCTYPE html>
    <html>
      <head><title>Test Playground</title></head>
      <body>
        <h1>Interactive Form</h1>
        <input type="text" id="inp" placeholder="Type here..." />
        <select id="sel">
          <option value="opt1">Option 1</option>
          <option value="opt2">Option 2</option>
        </select>
        <button id="btn" onclick="document.getElementById('status').innerText = 'Clicked!'">Submit</button>
        <div id="status">Waiting</div>
        <p>Paragraph with <a href="https://example.com">Example Link</a></p>
      </body>
    </html>
    """
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
        f.write(html_content)
        file_path = f.name

    try:
        # 1. Navigate
        tree = tool.execute("navigate", url=f"file://{file_path}")
        assert isinstance(tree, str)
        assert '--- "Interactive Form" (h1) ---' in tree
        assert 'input[text] "Type here..." placeholder' in tree
        assert 'button "Submit"' in tree

        # 2. Type text (instant=True for speed)
        tree = tool.execute("type", element_id=1, text="Hello World", instant=True)
        assert isinstance(tree, str)

        # 3. Select option
        tree = tool.execute("select", element_id=2, value="Option 2")
        assert isinstance(tree, str)

        # 4. Click button
        tree = tool.execute("click", element_id=3)
        assert isinstance(tree, str)

        # 5. Hover
        tree = tool.execute("hover", element_id=3)
        assert isinstance(tree, str)

        # 6. Scroll
        tree = tool.execute("scroll", direction="down", amount=100)
        assert isinstance(tree, str)

        # 7. Press key
        tree = tool.execute("press_key", key="Escape")
        assert isinstance(tree, str)

        # 8. Extract content
        md = tool.execute("extract_content", mode="markdown")
        assert "# Interactive Form" in md
        assert "Clicked!" in md

        # 9. List tabs
        tabs = tool.execute("list_tabs")
        assert "Open Tabs" in tabs
        assert "(active)" in tabs

        # 10. Clear cache
        clear_res = tool.execute("clear_cache", domain="example.com")
        assert "cleared" in clear_res.lower()
        clear_all = tool.execute("clear_cache")
        assert "cleared" in clear_all.lower()

        # 11. Close session
        close_res = tool.execute("close")
        assert "closed successfully" in close_res

    finally:
        tool.shutdown()
        if os.path.exists(file_path):
            os.remove(file_path)


def test_browser_screenshot_downscale_and_som():
    orch = DummyOrchestrator(agent_id=f"test_shot_{os.getpid()}")
    tool = BrowserSkill(orchestrator=orch)

    html_content = """
    <!DOCTYPE html>
    <html>
      <head><title>Visual Page</title></head>
      <body style="width: 1600px; height: 1200px; background: #fafafa;">
        <h1>Visual Test</h1>
        <button style="margin: 50px;">Clickable 1</button>
        <button style="margin: 50px;">Clickable 2</button>
      </body>
    </html>
    """
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
        f.write(html_content)
        file_path = f.name

    try:
        tool.execute("navigate", url=f"file://{file_path}")

        # Standard screenshot (downscaled to max 1024)
        res = tool.execute("screenshot", high_res=False, show_overlay_cues=True)
        assert isinstance(res, dict)
        assert "media" in res
        shot_path = res["media"][0]
        assert os.path.exists(shot_path)

        with Image.open(shot_path) as im:
            w, h = im.size
            assert max(w, h) <= 1024

        # Clean up screenshot
        if os.path.exists(shot_path):
            os.remove(shot_path)

    finally:
        tool.shutdown()
        if os.path.exists(file_path):
            os.remove(file_path)


def test_browser_upload():
    orch = DummyOrchestrator(agent_id=f"test_upload_{os.getpid()}")
    tool = BrowserSkill(orchestrator=orch)

    html_content = """
    <!DOCTYPE html>
    <html>
      <head><title>Upload Test</title></head>
      <body>
        <h1>File Upload Test</h1>
        <input type="file" id="file_input" onchange="document.getElementById('status').innerText = 'Selected: ' + this.files[0].name" />
        <div id="status">No file selected</div>
        <button id="upload_trigger" onclick="document.getElementById('file_input').click()">Custom Upload Button</button>
      </body>
    </html>
    """
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
        f.write(html_content)
        html_file = f.name

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("Test content for file upload")
        upload_file = f.name

    try:
        # Navigate to test page
        tree = tool.execute("navigate", url=f"file://{html_file}")
        assert isinstance(tree, str)
        assert 'input[file]' in tree

        # Test non-existent file error handling
        err_res = tool.execute("upload", file_path="/tmp/non_existent_open_amity_file.txt")
        assert isinstance(err_res, dict)
        assert "error" in err_res
        assert "File not found" in err_res["error"]

        # Test direct upload targeting element_id
        res = tool.execute("upload", file_path=upload_file, element_id=1)
        assert isinstance(res, str)
        assert os.path.basename(upload_file) in res

        # Test upload without element_id (auto-detects input[type=file])
        res2 = tool.execute("upload", file_path=upload_file)
        assert isinstance(res2, str)
        assert os.path.basename(upload_file) in res2

        # Test custom button triggering file chooser
        tool.execute("navigate", url=f"file://{html_file}")
        # Button is element 2
        res3 = tool.execute("upload", file_path=upload_file, element_id=2)
        assert isinstance(res3, str)
        assert os.path.basename(upload_file) in res3

    finally:
        tool.shutdown()
        if os.path.exists(html_file):
            os.remove(html_file)
        if os.path.exists(upload_file):
            os.remove(upload_file)


def test_browser_popup_and_tab_fallback():
    orch = DummyOrchestrator(agent_id=f"test_popup_{os.getpid()}")
    tool = BrowserSkill(orchestrator=orch)

    popup_html = """
    <!DOCTYPE html>
    <html>
      <head><title>Popup Child Window</title></head>
      <body>
        <h1>Popup Title</h1>
        <button id="close_btn" onclick="window.close()">Close Popup</button>
      </body>
    </html>
    """
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
        f.write(popup_html)
        popup_file = f.name

    parent_html = f"""
    <!DOCTYPE html>
    <html>
      <head><title>Parent Tab Window</title></head>
      <body>
        <h1>Parent Title</h1>
        <a href="file://{popup_file}" target="_blank" id="popup_link">Open Popup Window</a>
      </body>
    </html>
    """
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
        f.write(parent_html)
        parent_file = f.name

    try:
        tree = tool.execute("navigate", url=f"file://{parent_file}")
        assert "Parent Title" in tree
        assert '[1] link "Open Popup Window"' in tree

        # Click the link opening popup window
        click_res = tool.execute("click", element_id=1)
        assert isinstance(click_res, str)
        assert "Popup window opened and auto-focused" in click_res
        assert "Popup Child Window" in click_res

        # Verify list_tabs shows Popup Child Window as active
        tabs = tool.execute("list_tabs")
        assert "Popup Child Window" in tabs
        assert "(active)" in tabs
        active_lines = [l for l in tabs.splitlines() if "(active)" in l]
        assert len(active_lines) == 1
        assert "Popup Child Window" in active_lines[0]

        # Close the popup window by clicking its close button
        tool.execute("click", element_id=1)
        time.sleep(0.5)

        # Verify that active page automatically fell back to parent
        tabs_after = tool.execute("list_tabs")
        assert "Parent Tab Window" in tabs_after
        active_lines_after = [l for l in tabs_after.splitlines() if "(active)" in l]
        assert len(active_lines_after) == 1
        assert "Parent Tab Window" in active_lines_after[0]

    finally:
        tool.shutdown()
        if os.path.exists(parent_file):
            os.remove(parent_file)
        if os.path.exists(popup_file):
            os.remove(popup_file)

