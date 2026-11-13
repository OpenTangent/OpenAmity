import os
import sys
import time
import shutil
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from config import paths
from core.cache_manager import CacheManager
from core.backup_manager import EXCLUDED_DIR_NAMES, EXCLUDED_EXTERNAL_DIR_NAMES
from tools.browser_tool import BrowserSkill
from core.browser_manager import BrowserManager


def test_backup_excludes_browser_screenshots_and_cache():
    """Verify that browser screenshots ('media') and web search cache ('web_cache') are excluded from .oaa backups."""
    assert "media" in EXCLUDED_DIR_NAMES
    assert "media" in EXCLUDED_EXTERNAL_DIR_NAMES
    assert "browser_data" in EXCLUDED_DIR_NAMES
    assert "browser_data" in EXCLUDED_EXTERNAL_DIR_NAMES
    assert "web_cache" in EXCLUDED_DIR_NAMES
    assert "web_cache" in EXCLUDED_EXTERNAL_DIR_NAMES


def test_browser_screenshot_tool_declaration_notice():
    """Verify that Browser_screenshot declaration explicitly documents the 24h temporary cache expiration."""
    tool = BrowserSkill()
    declarations = tool.get_tool_declarations()
    decl_map = {d["name"]: d for d in declarations}

    assert "Browser_screenshot" in decl_map
    desc = decl_map["Browser_screenshot"]["description"]
    assert "24 hours" in desc
    assert "temporary" in desc.lower()


def test_cache_manager_recursive_cleanup(tmp_path):
    """Verify CacheManager.clear_expired with recursive=True removes stale files in nested subdirectories."""
    cache_root = tmp_path / "test_cache"
    nested_dir = cache_root / "sub1" / "sub2"
    nested_dir.mkdir(parents=True, exist_ok=True)

    fresh_file = nested_dir / "fresh.dat"
    fresh_file.write_bytes(b"fresh_content")

    stale_file = nested_dir / "stale.dat"
    stale_file.write_bytes(b"stale_content")
    stale_time = time.time() - (25 * 3600)  # 25 hours old
    os.utime(stale_file, (stale_time, stale_time))

    CacheManager.clear_expired(str(cache_root), max_age_days=1.0, recursive=True)

    assert not stale_file.exists()
    assert fresh_file.exists()


def test_browser_manager_clear_expired_cache(tmp_path):
    """Verify BrowserManager.clear_expired_cache cleans stale screenshots (>24h) and browser caches."""
    agent_id = "agent_cleanup_test"

    with patch.dict(os.environ, {"XDG_DATA_HOME": str(tmp_path)}):
        agent_dir = tmp_path / "agents" / agent_id

        # Setup screenshots in media directory
        screenshot_dir = agent_dir / "media"
        screenshot_dir.mkdir(parents=True, exist_ok=True)

        fresh_shot = screenshot_dir / "browser_screenshot_fresh.png"
        fresh_shot.write_bytes(b"fake_png_fresh")

        stale_shot = screenshot_dir / "browser_screenshot_stale.png"
        stale_shot.write_bytes(b"fake_png_stale")
        stale_time = time.time() - (25 * 3600)  # 25 hours old
        os.utime(stale_shot, (stale_time, stale_time))

        # Setup browser cache in browser_data/cache
        browser_cache = agent_dir / "browser_data" / "cache"
        browser_cache.mkdir(parents=True, exist_ok=True)

        fresh_cache = browser_cache / "fresh.cache"
        fresh_cache.write_bytes(b"fresh_cache")

        stale_cache = browser_cache / "stale.cache"
        stale_cache.write_bytes(b"stale_cache")
        os.utime(stale_cache, (stale_time, stale_time))

        # Setup Camoufox cache2/entries
        cache2_entries = agent_dir / "browser_data" / "cache2" / "entries"
        cache2_entries.mkdir(parents=True, exist_ok=True)

        stale_http_cache = cache2_entries / "stale_entry"
        stale_http_cache.write_bytes(b"stale_http")
        os.utime(stale_http_cache, (stale_time, stale_time))

        mock_orch = MagicMock()
        mock_orch.agent_id = agent_id
        mgr = BrowserManager(orchestrator=mock_orch)

        # Trigger cleanup
        mgr.clear_expired_cache(max_age_days=1.0)

        # Verify stale files are purged, fresh files kept
        assert not stale_shot.exists()
        assert fresh_shot.exists()

        assert not stale_cache.exists()
        assert fresh_cache.exists()

        assert not stale_http_cache.exists()


def test_clear_all_agent_expired_caches(tmp_path):
    """Verify CacheManager.clear_all_agent_expired_caches prunes all agent cache dirs."""
    agent_id = "agent_all_caches_test"

    with patch.dict(os.environ, {"XDG_DATA_HOME": str(tmp_path)}):
        agent_dir = tmp_path / "agents" / agent_id
        stale_time = time.time() - (26 * 3600)

        # 1. Media directory (screenshots)
        shot_dir = agent_dir / "media"
        shot_dir.mkdir(parents=True, exist_ok=True)
        stale_shot = shot_dir / "stale_shot.png"
        stale_shot.write_bytes(b"shot")
        os.utime(stale_shot, (stale_time, stale_time))

        # 2. Generated media
        gen_dir = agent_dir / "generated_media"
        gen_dir.mkdir(parents=True, exist_ok=True)
        stale_img = gen_dir / "stale_img.jpg"
        stale_img.write_bytes(b"img")
        os.utime(stale_img, (stale_time, stale_time))

        # 3. Audio cache
        audio_dir = agent_dir / "audio_cache"
        audio_dir.mkdir(parents=True, exist_ok=True)
        stale_audio = audio_dir / "stale_audio.opus"
        stale_audio.write_bytes(b"audio")
        os.utime(stale_audio, (stale_time, stale_time))

        # 4. Web cache
        web_cache = agent_dir / "web_cache"
        web_cache.mkdir(parents=True, exist_ok=True)
        stale_web = web_cache / "stale_query.json"
        stale_web.write_bytes(b"web")
        os.utime(stale_web, (stale_time, stale_time))

        # Fresh file in generated_media to ensure fresh files are preserved
        fresh_img = gen_dir / "fresh_img.jpg"
        fresh_img.write_bytes(b"fresh_img")

        CacheManager.clear_all_agent_expired_caches(agent_id, max_age_days=1.0)

        assert not stale_shot.exists()
        assert not stale_img.exists()
        assert not stale_audio.exists()
        assert not stale_web.exists()
        assert fresh_img.exists()
