import os
import sys
import logging
import pytest
import requests
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from tools.web_search_tool import WebSearchSkill
from core.logger_config import (
    agent_id_var,
    set_active_websearch_agent_id,
    get_active_websearch_agent_id,
    ColorFormatter,
    FileFormatter,
    BaseFormatter,
    VerbosityFilter,
    AgentDispatchingHandler,
)
import concurrent.futures

try:
    from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException
except ImportError:
    class DDGSException(Exception):
        pass
    class RatelimitException(DDGSException):
        pass
    class TimeoutException(DDGSException):
        pass


class MockOrchestrator:
    def __init__(self, agent_id="agent-search-test"):
        self.agent_id = agent_id


@pytest.fixture
def mock_orch():
    return MockOrchestrator()


def test_web_search_zero_results_empty_list(mock_orch, caplog):
    tool = WebSearchSkill(orchestrator=mock_orch)
    caplog.set_level(logging.DEBUG)

    with patch("tools.web_search_tool.DDGS") as mock_ddgs:
        instance = MagicMock()
        instance.__enter__.return_value = instance
        instance.text.return_value = iter([])
        mock_ddgs.return_value = instance

        result = tool.execute("search", query="nonexistent query")
        assert result == "No results found for query: nonexistent query"

        # Check caplog for NO ERROR logs
        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(error_records) == 0

        # Verify info log was recorded
        info_records = [r for r in caplog.records if "No results found" in r.message]
        assert len(info_records) > 0


def test_web_search_zero_results_ddgs_exception(mock_orch, caplog):
    tool = WebSearchSkill(orchestrator=mock_orch)
    caplog.set_level(logging.DEBUG)

    with patch("tools.web_search_tool.DDGS") as mock_ddgs:
        instance = MagicMock()
        instance.__enter__.return_value = instance
        instance.text.side_effect = DDGSException("No results found.")
        mock_ddgs.return_value = instance

        result = tool.execute("search", query="obscure search term")
        assert result == "No results found for query: obscure search term"

        # Check caplog for NO ERROR logs
        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(error_records) == 0

        # Verify info log was recorded
        info_records = [r for r in caplog.records if "No results found" in r.message]
        assert len(info_records) > 0


def test_web_search_rate_limit_handling(mock_orch, caplog):
    tool = WebSearchSkill(orchestrator=mock_orch)
    caplog.set_level(logging.DEBUG)

    with patch("tools.web_search_tool.DDGS") as mock_ddgs:
        instance = MagicMock()
        instance.__enter__.return_value = instance
        instance.text.side_effect = RatelimitException("Rate limit 429 exceeded")
        mock_ddgs.return_value = instance

        result = tool.execute("search", query="test query")
        assert "rate limit reached" in result.lower()

        # Should be a warning, not an error
        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(error_records) == 0
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warning_records) > 0


def test_web_search_timeout_handling(mock_orch, caplog):
    tool = WebSearchSkill(orchestrator=mock_orch)
    caplog.set_level(logging.DEBUG)

    with patch("tools.web_search_tool.DDGS") as mock_ddgs:
        instance = MagicMock()
        instance.__enter__.return_value = instance
        instance.text.side_effect = TimeoutException("Connection timed out after 10s")
        mock_ddgs.return_value = instance

        result = tool.execute("search", query="slow query")
        assert "timed out" in result.lower()

        # Should be a warning, not an error
        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(error_records) == 0
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warning_records) > 0


def test_primp_log_rebinding_and_formatting():
    agent_id_var.set("agent-search-test")

    primp_logger = logging.getLogger("primp")
    record = primp_logger.makeRecord(
        "primp", logging.INFO, "test.py", 10, "response: https://example.com 200", (), None
    )

    # VerbosityFilter and PrimpLogFilter rebind name
    vf = VerbosityFilter()
    vf.filter(record)
    assert record.name == "tool.WebSearch"

    # BaseFormatter maps it to tool.WebSearch
    bf = BaseFormatter()
    source = bf.get_source(record)
    assert source == "tool.WebSearch"

    # ColorFormatter colors it and includes agent name
    cf = ColorFormatter()
    formatted = cf.format(record)
    assert "[tool.WebSearch]" in formatted
    # Cyan tool color code for WebSearch (#00BCD4 -> 0;188;212)
    assert "\x1b[38;2;0;188;212m" in formatted

    # FileFormatter formats with agent and tool
    ff = FileFormatter()
    file_formatted = ff.format(record)
    assert "[tool.WebSearch]" in file_formatted


def test_thread_pool_executor_context_propagation():
    test_id = "agent-threadpool-test"
    agent_id_var.set(test_id)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        future = executor.submit(lambda: agent_id_var.get())
        result = future.result()
        assert result == test_id


def test_view_page_request_exception_is_warning(mock_orch, caplog):
    tool = WebSearchSkill(orchestrator=mock_orch)
    caplog.set_level(logging.DEBUG)

    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError("422 Client Error: Unprocessable Entity")
        mock_get.return_value = mock_resp

        result = tool.execute("view_page", url="https://example.com/deadlink")
        assert "Failed to fetch page" in result

        # Must not be logged as ERROR
        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(error_records) == 0
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warning_records) > 0


def test_download_image_request_exception_is_warning(mock_orch, caplog):
    tool = WebSearchSkill(orchestrator=mock_orch)
    caplog.set_level(logging.DEBUG)

    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.exceptions.ConnectionError("Connection failed")
        mock_get.return_value = mock_resp

        result = tool.execute("download_image", url="https://example.com/bad.png")
        assert "Failed to download image" in result

        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(error_records) == 0
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warning_records) > 0
