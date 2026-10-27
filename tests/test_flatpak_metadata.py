import json
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
import pytest
from core.version import __version__


@pytest.fixture
def repo_root():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def test_metainfo_xml_structure_and_schema(repo_root):
    metainfo_path = os.path.join(repo_root, "compile", "com.openamity.OpenAmity.metainfo.xml")
    assert os.path.exists(metainfo_path), f"{metainfo_path} must exist"

    tree = ET.parse(metainfo_path)
    root = tree.getroot()

    # AppStream 1.0 desktop-application type
    assert root.tag == "component"
    assert root.attrib.get("type") == "desktop-application"

    # ID
    id_el = root.find("id")
    assert id_el is not None and id_el.text == "com.openamity.OpenAmity"

    # Licenses
    meta_lic = root.find("metadata_license")
    assert meta_lic is not None and meta_lic.text == "CC0-1.0"

    proj_lic = root.find("project_license")
    assert proj_lic is not None and proj_lic.text == "GPL-3.0-or-later"

    # Name and summary
    name_el = root.find("name")
    assert name_el is not None and name_el.text == "Open Amity"

    summary_el = root.find("summary")
    assert summary_el is not None and summary_el.text
    # Flathub quality guidelines: no trailing period, concise summary
    assert not summary_el.text.strip().endswith(".")
    assert "Open Amity" not in summary_el.text

    # Launchable
    launchable = root.find("launchable")
    assert launchable is not None
    assert launchable.attrib.get("type") == "desktop-id"
    assert launchable.text == "com.openamity.OpenAmity.desktop"

    # Branding
    branding = root.find("branding")
    assert branding is not None
    colors = {c.attrib.get("scheme_preference"): c.text for c in branding.findall("color")}
    assert "light" in colors and colors["light"].startswith("#")
    assert "dark" in colors and colors["dark"].startswith("#")

    # Content rating
    content_rating = root.find("content_rating")
    assert content_rating is not None
    assert content_rating.attrib.get("type") == "oars-1.1"

    # Developer
    developer = root.find("developer")
    assert developer is not None
    dev_name = developer.find("name")
    assert dev_name is not None and dev_name.text == "Andrew Craucamp"

    # URLs
    urls = {u.attrib.get("type"): u.text for u in root.findall("url")}
    assert urls.get("homepage") == "https://openamity.com"
    assert urls.get("bugtracker") == "https://github.com/OpenTangent/OpenAmity/issues"
    assert urls.get("vcs-browser") == "https://github.com/OpenTangent/OpenAmity"
    assert "help" not in urls

    # Releases
    releases = root.find("releases")
    assert releases is not None
    release_list = releases.findall("release")
    assert len(release_list) > 0

    latest_release = release_list[0]
    assert latest_release.attrib.get("version") == __version__
    assert latest_release.attrib.get("date")

    # Check release description for latest release
    desc = latest_release.find("description")
    assert desc is not None
    ul = desc.find("ul")
    assert ul is not None
    lis = ul.findall("li")
    assert len(lis) >= 4


def test_metainfo_screenshots(repo_root):
    metainfo_path = os.path.join(repo_root, "compile", "com.openamity.OpenAmity.metainfo.xml")
    screenshots_dir = os.path.join(repo_root, "src", "assets", "screenshots")

    tree = ET.parse(metainfo_path)
    root = tree.getroot()

    screenshots_el = root.find("screenshots")
    assert screenshots_el is not None
    screenshots = screenshots_el.findall("screenshot")
    assert len(screenshots) == 9, "Should declare all 9 screenshots"

    # Default screenshot
    assert screenshots[0].attrib.get("type") == "default"

    expected_files = {
        "sc-Agent.png",
        "sc-Chatroom.png",
        "sc-NewAgent.png",
        "sc-Provider.png",
        "sc-Settings.png",
        "sc-Snapshots.png",
        "sc-Social.png",
        "sc-Values.png",
        "sc-Voice.png"
    }

    found_files = set()
    for s in screenshots:
        img = s.find("image")
        assert img is not None and img.text
        filename = os.path.basename(img.text)
        found_files.add(filename)

        # File must exist locally
        local_path = os.path.join(screenshots_dir, filename)
        assert os.path.exists(local_path), f"Screenshot file {local_path} must exist"

        # Caption check
        caption = s.find("caption")
        assert caption is not None and caption.text
        # Flathub quality rule: captions must not end with a full stop and not start with numbers
        caption_text = caption.text.strip()
        assert not caption_text.endswith("."), f"Caption '{caption_text}' must not end with a period"
        assert not caption_text[0].isdigit(), f"Caption '{caption_text}' must not start with a digit"

    assert found_files == expected_files


def test_desktop_file_format(repo_root):
    desktop_path = os.path.join(repo_root, "compile", "com.openamity.OpenAmity.desktop")
    assert os.path.exists(desktop_path)

    with open(desktop_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "[Desktop Entry]" in content
    assert "Type=Application" in content
    assert "Name=Open Amity" in content
    assert "GenericName=AI Companion & Multi-Agent Framework" in content
    assert "Exec=run_OpenAmity.sh" in content
    assert "Icon=com.openamity.OpenAmity" in content
    assert "StartupWMClass=com.openamity.OpenAmity" in content
    assert "Keywords=" in content
    assert "Categories=" in content

    # Run desktop-file-validate if installed
    if shutil.which("desktop-file-validate"):
        result = subprocess.run(
            ["desktop-file-validate", desktop_path],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0, f"desktop-file-validate failed: {result.stderr}"


def test_appstreamcli_validation(repo_root):
    if not shutil.which("appstreamcli"):
        pytest.skip("appstreamcli not installed")

    metainfo_path = os.path.join(repo_root, "compile", "com.openamity.OpenAmity.metainfo.xml")
    # Using --no-net to skip checking remote unpushed github URLs
    result = subprocess.run(
        ["appstreamcli", "validate", "--pedantic", "--no-net", metainfo_path],
        capture_output=True,
        text=True
    )
    # Output may contain pedantic hint regarding uppercase in app ID
    assert "Validation was successful" in result.stdout or result.returncode == 0
    assert "Validation failed" not in result.stdout


def test_flatpak_manifest_json(repo_root):
    manifest_path = os.path.join(repo_root, "compile", "com.openamity.OpenAmity.json")
    assert os.path.exists(manifest_path)

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    assert manifest["app-id"] == "com.openamity.OpenAmity"
    assert "finish-args" in manifest
    finish_args = manifest["finish-args"]

    assert "--share=network" in finish_args
    assert "--socket=pulseaudio" in finish_args
    assert "--device=dri" in finish_args or "--device=all" in finish_args
    assert "--filesystem=home" in finish_args
    assert "org.freedesktop.Sdk.Extension.node24" in manifest.get("sdk-extensions", [])

    # Check open_amity module installs desktop, metainfo, and icon
    modules = manifest.get("modules", [])
    open_amity_mod = next((m for m in modules if isinstance(m, dict) and m.get("name") == "open_amity"), None)
    assert open_amity_mod is not None

    build_cmds = " ".join(open_amity_mod.get("build-commands", []))
    assert "com.openamity.OpenAmity.desktop" in build_cmds
    assert "com.openamity.OpenAmity.metainfo.xml" in build_cmds
    assert "com.openamity.OpenAmity.png" in build_cmds

    post_install_cmds = " ".join(open_amity_mod.get("post-install", []))
    assert "piper/hebrew/*.onnx" in post_install_cmds
    assert "piper/tashkeel/*.onnx" in post_install_cmds
    assert "piper/tashkeel*" not in post_install_cmds


def test_deploy_script_checks_version_release(repo_root):
    deploy_path = os.path.join(repo_root, "deploy.sh")
    assert os.path.exists(deploy_path)

    with open(deploy_path, "r", encoding="utf-8") as f:
        deploy_content = f.read()

    assert "src/core/version.py" in deploy_content
    assert "compile/com.openamity.OpenAmity.metainfo.xml" in deploy_content
    assert "<release version=\\\"$VERSION\\\"" in deploy_content or '<release version="$VERSION"' in deploy_content
    assert "The Flatpak manifest" in deploy_content or "must be updated with the changelog" in deploy_content


def test_local_stt_imports_without_pyav(monkeypatch):
    """Regression test: when PyAV ('av') is removed in the Flatpak image,
    LocalSTT must stub sys.modules['av'] so faster_whisper imports cleanly."""
    import builtins
    import sys
    import core.local_stt as local_stt

    monkeypatch.delitem(sys.modules, "av", raising=False)
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "av" or name.startswith("av."):
            raise ModuleNotFoundError("No module named 'av'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    local_stt._ensure_faster_whisper_av_stub()
    assert "av" in sys.modules
    stt = local_stt.LocalSTT()
    assert stt is not None


def test_flatpak_persistence_links(tmp_path, monkeypatch):
    """Regression test: inside Flatpak ($FLATPAK_ID set), ~/.cache and ~/.local/share
    must symlink to persistent XDG_CACHE_HOME and XDG_DATA_HOME."""
    from config import paths

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    xdg_cache = fake_home / ".var" / "app" / "com.openamity.OpenAmity" / "cache"
    xdg_data = fake_home / ".var" / "app" / "com.openamity.OpenAmity" / "data"

    monkeypatch.setenv("FLATPAK_ID", "com.openamity.OpenAmity")
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg_cache))
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_data))

    paths.ensure_flatpak_persistence_links()

    home_cache = fake_home / ".cache"
    home_local_share = fake_home / ".local" / "share"
    assert home_cache.is_symlink()
    assert os.path.realpath(home_cache) == os.path.realpath(xdg_cache)
    assert home_local_share.is_symlink()
    assert os.path.realpath(home_local_share) == os.path.realpath(xdg_data)


def test_chroma_onnx_ready_check(tmp_path):
    """Verifies _is_chroma_onnx_ready checks all 6 required ONNX files."""
    from main import _is_chroma_onnx_ready, CHROMA_ONNX_FILES

    base_dir = tmp_path / "all-MiniLM-L6-v2"
    onnx_dir = base_dir / "onnx"
    onnx_dir.mkdir(parents=True)

    assert not _is_chroma_onnx_ready(str(base_dir))
    for fname in CHROMA_ONNX_FILES:
        (onnx_dir / fname).write_bytes(b"ok")
    assert _is_chroma_onnx_ready(str(base_dir))

