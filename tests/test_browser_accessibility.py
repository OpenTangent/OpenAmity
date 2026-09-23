import os
import tempfile
import time
import pytest
from core.browser_manager import BrowserManager


@pytest.fixture
def browser_mgr():
    agent_id = f"test_aria_{os.getpid()}"
    mgr = BrowserManager()
    mgr.agent_id = agent_id
    yield mgr
    mgr.shutdown()


def test_pruning_retains_headings_and_interactive(browser_mgr):
    html = """
    <html>
      <body>
        <h1>Main Page Title</h1>
        <p>Some introductory text that is non-interactive.</p>
        <h2>Section One</h2>
        <a href="https://example.com/one">Link One</a>
        <button>Submit Button</button>
        <h3>Subsection</h3>
        <input type="text" placeholder="Enter username" />
      </body>
    </html>
    """
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
        f.write(html)
        file_path = f.name

    try:
        tree = browser_mgr.navigate(f"file://{file_path}")
        assert isinstance(tree, str)
        assert '--- "Main Page Title" (h1) ---' in tree
        assert '--- "Section One" (h2) ---' in tree
        assert '--- "Subsection" (h3) ---' in tree
        assert '[1] link "Link One"' in tree
        assert '[2] button "Submit Button"' in tree
        assert '[3] input[text] "Enter username" placeholder' in tree
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)


def test_pruning_excludes_hidden_and_decorative_elements(browser_mgr):
    html = """
    <html>
      <body>
        <h1>Visible Title</h1>
        <div style="display: none;"><button>Hidden Button 1</button></div>
        <button style="visibility: hidden;">Hidden Button 2</button>
        <button aria-hidden="true">Hidden Button 3</button>
        <button style="width: 0px; height: 0px; padding: 0; border: 0; overflow: hidden;">Zero Size Button</button>
        <div>Decorative Container Without Role</div>
        <button>Visible Action</button>
      </body>
    </html>
    """
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
        f.write(html)
        file_path = f.name

    try:
        tree = browser_mgr.navigate(f"file://{file_path}")
        assert "Hidden Button" not in tree
        assert "Zero Size" not in tree
        assert "Decorative Container" not in tree
        assert '[1] button "Visible Action"' in tree
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)


def test_max_accessibility_nodes_truncation(browser_mgr):
    buttons = "".join([f'<button>Button {i}</button>' for i in range(1, 35)])
    html = f"""
    <html>
      <body>
        <h1>Many Controls</h1>
        {buttons}
      </body>
    </html>
    """
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
        f.write(html)
        file_path = f.name

    try:
        # Override max nodes setting for testing
        browser_mgr._get_setting = lambda k, d: 10 if k == "max-accessibility-nodes" else d
        tree = browser_mgr.navigate(f"file://{file_path}")
        assert '[1] button "Button 1"' in tree
        assert '[10] button "Button 10"' in tree
        assert '[11] button "Button 11"' not in tree
        assert "[... 24 more elements. Scroll or narrow your focus.]" in tree
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)


def test_stale_element_id_error(browser_mgr):
    html = """
    <html>
      <body>
        <button>Only Button</button>
      </body>
    </html>
    """
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
        f.write(html)
        file_path = f.name

    try:
        browser_mgr.navigate(f"file://{file_path}")
        # Element 99 does not exist
        res = browser_mgr.click(99)
        assert isinstance(res, dict)
        assert "error" in res
        assert "Element [99] not found" in res["error"]
        assert "tree" in res
        assert '[1] button "Only Button"' in res["tree"]
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)


def test_action_timeout_fast_fail(browser_mgr):
    html = """
    <html>
      <body>
        <h1>Timeout Test</h1>
        <button id="fading_btn">Click Me</button>
      </body>
    </html>
    """
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
        f.write(html)
        file_path = f.name

    try:
        browser_mgr.navigate(f"file://{file_path}")
        # Configure action timeout to 1 second for fast test execution
        browser_mgr._get_setting = lambda k, d: 1 if k == "action-timeout-seconds" else d
        assert browser_mgr.action_timeout_seconds == 1
        assert browser_mgr.action_timeout_ms == 1000

        # Dynamically hide the button in the DOM so it is no longer visible
        browser_mgr._run_async(
            browser_mgr._active_page.evaluate("document.getElementById('fading_btn').style.display = 'none'")
        )

        start_time = time.time()
        # Element 1 was button in scanned tree, but is now hidden
        res = browser_mgr.click(1)
        elapsed = time.time() - start_time

        # Ensure it failed fast within ~1-2.5 seconds, NOT freezing for 30s
        assert elapsed < 4.0, f"Action took too long to fail: {elapsed}s"
        assert isinstance(res, dict)
        assert "error" in res
        assert "not actionable after 1s" in res["error"]
        assert "tree" in res
        assert "fading_btn" not in res["tree"]
        assert "Click Me" not in res["tree"]
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

