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
    assert len(name_el.text.strip()) < 20

    summary_el = root.find("summary")
    assert summary_el is not None and summary_el.text
    # Flathub quality guidelines: <35 chars, sentence case, no trailing period, don't repeat name
    summary_text = summary_el.text.strip()
    assert len(summary_text) < 35, f"Summary '{summary_text}' is {len(summary_text)} chars; must be < 35"
    assert not summary_text.endswith(".")
    assert "Open Amity" not in summary_text
    assert not summary_text.lower().startswith(("a ", "an ", "the "))

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
    assert colors["light"].lower() != colors["dark"].lower()

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
    if not os.path.exists(deploy_path):
        pytest.skip("deploy.sh is gitignored and not present in clean checkouts")

    with open(deploy_path, "r", encoding="utf-8") as f:
        deploy_content = f.read()

    assert "src/core/version.py" in deploy_content
    assert "compile/com.openamity.OpenAmity.metainfo.xml" in deploy_content
    assert "<release version=\\\"$VERSION\\\"" in deploy_content or '<release version="$VERSION"' in deploy_content
    assert "changelog before deploying" in deploy_content
    assert "gh pr create" in deploy_content
    assert "flathub/com.openamity.OpenAmity" in deploy_content
    assert "git push origin master" not in deploy_content
    assert "--reconcile-python" in deploy_content


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


def test_github_installer_manifest_and_desktop_parity(repo_root):
    """Ensures the GitHub Pages migrator Flatpak manifest exposes run_OpenAmity.sh for .desktop Exec."""
    manifest_path = os.path.join(
        repo_root, "compile", "github_installer", "com.openamity.OpenAmity.json"
    )
    assert os.path.exists(manifest_path)

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    assert manifest["app-id"] == "com.openamity.OpenAmity"
    assert "--talk-name=org.freedesktop.Flatpak" in manifest.get("finish-args", [])

    mod = manifest["modules"][0]
    build_cmds = "\n".join(mod.get("build-commands", []))
    assert "flathub_installer.py" in build_cmds
    assert "run_OpenAmity.sh" in build_cmds
    assert "com.openamity.OpenAmity.desktop" in build_cmds


def test_github_installer_dry_run_migration_commands(repo_root, monkeypatch):
    """Verifies MigrationWorker runs Polkit-free --user install, non-blocking launch, and legacy cleanup."""
    import importlib.util

    installer_path = os.path.join(
        repo_root, "compile", "github_installer", "flathub_installer.py"
    )
    spec = importlib.util.spec_from_file_location("flathub_installer", installer_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    executed_sync = []
    spawned_async = []

    def fake_run(cmd_list, dry_run=False):
        executed_sync.append(list(cmd_list))
        return True, ""

    def fake_spawn(cmd_list, dry_run=False):
        spawned_async.append(list(cmd_list))
        return True, ""

    monkeypatch.setattr(mod, "run_host_cmd", fake_run)
    monkeypatch.setattr(mod, "spawn_host_cmd", fake_spawn)

    worker = mod.MigrationWorker(dry_run=True)
    results = []
    worker.finished.connect(lambda ok, msg: results.append((ok, msg)))
    worker.run()

    assert results and results[0][0] is True
    assert any(
        cmd[:4] == ["flatpak", "remote-add", "--user", "--if-not-exists"]
        for cmd in executed_sync
    )
    assert any(
        cmd[:3] == ["flatpak", "install", "--user"] and mod.STABLE_REF in cmd
        for cmd in executed_sync
    )
    assert ["flatpak", "run", mod.STABLE_REF] in spawned_async
    cleanup_cmds = [c for c in spawned_async if c[:2] == ["sh", "-c"]]
    assert len(cleanup_cmds) == 1
    assert f"flatpak uninstall --user -y --noninteractive --force-remove {mod.MASTER_REF}" in cleanup_cmds[0][2]
    assert f"flatpak remote-delete --user --force {mod.LEGACY_REMOTE}" in cleanup_cmds[0][2]


def test_desktop_app_id_flatpak_vs_dev(monkeypatch):
    """Outside Flatpak, uses the .Devel app ID so dev windows never shadow Flatpak."""
    from config import paths

    monkeypatch.delenv("FLATPAK_ID", raising=False)
    assert paths.get_desktop_app_id() == "com.openamity.OpenAmity.Devel"

    monkeypatch.setenv("FLATPAK_ID", "com.openamity.OpenAmity")
    assert paths.get_desktop_app_id() == "com.openamity.OpenAmity"


def test_ensure_dev_desktop_entry_creates_valid_file_without_shadowing_flatpak(tmp_path, monkeypatch):
    """Registers a valid NoDisplay .Devel desktop file with an absolute icon path."""
    from config import paths

    monkeypatch.delenv("FLATPAK_ID", raising=False)
    monkeypatch.setattr(paths.sys, "platform", "linux")
    xdg_data = tmp_path / "xdg_data"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_data))

    desktop_path = paths.ensure_dev_desktop_entry()
    expected_path = xdg_data / "applications" / "com.openamity.OpenAmity.Devel.desktop"
    assert desktop_path == str(expected_path)
    assert expected_path.is_file()
    assert not (xdg_data / "applications" / "com.openamity.OpenAmity.desktop").exists()

    content = expected_path.read_text(encoding="utf-8")
    assert "Name=Open Amity (Dev)" in content
    assert "StartupWMClass=com.openamity.OpenAmity.Devel" in content
    assert "NoDisplay=true" in content
    assert f"Icon={os.path.abspath(paths.get_icon_path())}" in content
    exec_lines = [line for line in content.splitlines() if line.startswith("Exec=")]
    assert len(exec_lines) == 1 and len(exec_lines[0]) > len("Exec=")

    if shutil.which("desktop-file-validate"):
        result = subprocess.run(
            ["desktop-file-validate", str(expected_path)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"desktop-file-validate failed: {result.stdout} {result.stderr}"

    first_mtime = expected_path.stat().st_mtime_ns
    assert paths.ensure_dev_desktop_entry() == str(expected_path)
    assert expected_path.stat().st_mtime_ns == first_mtime


def test_ensure_dev_desktop_entry_skipped_inside_flatpak(tmp_path, monkeypatch):
    """Inside Flatpak, host desktop entries must never be created or overwritten."""
    from config import paths

    xdg_data = tmp_path / "xdg_data"
    monkeypatch.setenv("FLATPAK_ID", "com.openamity.OpenAmity")
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_data))

    assert paths.ensure_dev_desktop_entry() is None
    assert not (xdg_data / "applications").exists()


def test_desktop_exec_escaping_with_spaces_and_special_chars(tmp_path):
    """Desktop Exec argument escaping handles spaces and special characters cleanly."""
    from config import paths

    spaced_path = "/tmp/My Projects/Open Amity/run_OpenAmity.sh"
    escaped_spaced = paths._escape_desktop_exec_arg(spaced_path)
    assert escaped_spaced == f'"{spaced_path}"'

    special_path = '/tmp/My Projects/100% "Amity" $dev/run.sh'
    escaped_special = paths._escape_desktop_exec_arg(special_path)
    assert "%%" in escaped_special

    temp_desktop = tmp_path / "com.openamity.OpenAmity.Devel.desktop"
    temp_desktop.write_text(
        "[Desktop Entry]\n"
        "Version=1.0\n"
        "Type=Application\n"
        "Name=Open Amity (Dev)\n"
        f"Exec={escaped_spaced}\n"
        "Icon=com.openamity.OpenAmity\n"
        "Terminal=false\n"
        "NoDisplay=true\n",
        encoding="utf-8",
    )

    if shutil.which("desktop-file-validate"):
        result = subprocess.run(
            ["desktop-file-validate", str(temp_desktop)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"desktop-file-validate failed: {result.stdout} {result.stderr}"


def _load_sync_requirements_module(repo_root):
    import importlib.util

    script_path = os.path.join(repo_root, "compile", "sync_requirements.py")
    spec = importlib.util.spec_from_file_location("sync_requirements", script_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_pyreq_payload(entries):
    return {
        "name": "python3-requirements",
        "buildsystem": "simple",
        "build-commands": [],
        "modules": [
            {
                "name": f"python3-{mod_name}",
                "buildsystem": "simple",
                "build-commands": [
                    f'pip3 install --verbose --exists-action=i --no-index --find-links="file://${{PWD}}" --prefix=${{FLATPAK_DEST}} "{req_spec}" --no-build-isolation'
                ],
                "sources": [],
            }
            for mod_name, req_spec in entries
        ],
    }


def test_reconcile_python_requirements_absent_removal_and_runtime_pkg(tmp_path, repo_root):
    """Removing a requirement not present in python3-requirements.json and keeping runtime-provided markdown returns up_to_date without rewriting."""
    mod = _load_sync_requirements_module(repo_root)
    req_file = tmp_path / "requirements.txt"
    req_file.write_text(
        "PySide6<6.12.0,>=6.11.0\n"
        "markdown\n"
        "requests\n"
        "anthropic>=2.6.0\n",
        encoding="utf-8",
    )
    pyreq_file = tmp_path / "python3-requirements.json"
    original_text = json.dumps(
        _make_pyreq_payload([("requests", "requests"), ("anthropic", "anthropic>=2.6.0")]),
        indent=4,
    ) + "\n"
    pyreq_file.write_text(original_text, encoding="utf-8")

    status, details = mod.reconcile_python_requirements(
        str(req_file),
        str(pyreq_file),
        runtime_packages={"Markdown": "3.11.2"},
    )
    assert status == "up_to_date"
    assert details == []
    assert pyreq_file.read_text(encoding="utf-8") == original_text


def test_reconcile_python_requirements_prunes_removed_module_in_place(tmp_path, repo_root):
    """Removing a package that exists in python3-requirements.json prunes only that module in place."""
    mod = _load_sync_requirements_module(repo_root)
    req_file = tmp_path / "requirements.txt"
    req_file.write_text("requests\nanthropic>=2.6.0\n", encoding="utf-8")

    pyreq_file = tmp_path / "python3-requirements.json"
    pyreq_file.write_text(
        json.dumps(
            _make_pyreq_payload(
                [
                    ("requests", "requests"),
                    ("obsolete-pkg", "obsolete_pkg==1.2.3"),
                    ("anthropic", "anthropic>=2.6.0"),
                ]
            ),
            indent=4,
        )
        + "\n",
        encoding="utf-8",
    )

    status, details = mod.reconcile_python_requirements(
        str(req_file),
        str(pyreq_file),
        runtime_packages={},
    )
    assert status == "pruned"
    assert details == ["python3-obsolete-pkg"]

    updated = json.loads(pyreq_file.read_text(encoding="utf-8"))
    assert [m["name"] for m in updated["modules"]] == ["python3-requests", "python3-anthropic"]


def test_reconcile_python_requirements_detects_added_package(tmp_path, repo_root):
    """Adding a new package to requirements.txt triggers needs_regen without modifying the manifest."""
    mod = _load_sync_requirements_module(repo_root)
    req_file = tmp_path / "requirements.txt"
    req_file.write_text("requests\nhttpx>=0.28\n", encoding="utf-8")

    pyreq_file = tmp_path / "python3-requirements.json"
    original_text = json.dumps(_make_pyreq_payload([("requests", "requests")]), indent=4) + "\n"
    pyreq_file.write_text(original_text, encoding="utf-8")

    status, details = mod.reconcile_python_requirements(
        str(req_file),
        str(pyreq_file),
        runtime_packages={"Markdown": "3.11.2"},
    )
    assert status == "needs_regen"
    assert "httpx>=0.28" in details
    assert pyreq_file.read_text(encoding="utf-8") == original_text


def test_reconcile_python_requirements_detects_changed_specifier_extras_or_runtime(tmp_path, repo_root):
    """Changed version bounds, extras, or unsatisfied runtime packages trigger needs_regen."""
    mod = _load_sync_requirements_module(repo_root)
    pyreq_file = tmp_path / "python3-requirements.json"
    original_text = json.dumps(
        _make_pyreq_payload([("anthropic", "anthropic>=2.6.0"), ("camoufox", "camoufox[geoip]>=0.5.9")]),
        indent=4,
    ) + "\n"
    pyreq_file.write_text(original_text, encoding="utf-8")

    req_file = tmp_path / "requirements.txt"

    # 1. Version bump on existing package
    req_file.write_text("anthropic>=2.7.0\ncamoufox[geoip]>=0.5.9\n", encoding="utf-8")
    status, details = mod.reconcile_python_requirements(
        str(req_file), str(pyreq_file), runtime_packages={}
    )
    assert status == "needs_regen"
    assert "anthropic>=2.7.0" in details

    # 2. Extras changed on existing package
    req_file.write_text("anthropic>=2.6.0\ncamoufox>=0.5.9\n", encoding="utf-8")
    status, details = mod.reconcile_python_requirements(
        str(req_file), str(pyreq_file), runtime_packages={}
    )
    assert status == "needs_regen"
    assert "camoufox>=0.5.9" in details

    # 3. Tightened runtime-provided package
    req_file.write_text("anthropic>=2.6.0\ncamoufox[geoip]>=0.5.9\nmarkdown>=99.0\n", encoding="utf-8")
    status, details = mod.reconcile_python_requirements(
        str(req_file), str(pyreq_file), runtime_packages={"Markdown": "3.11.2"}
    )
    assert status == "needs_regen"
    assert "markdown>=99.0" in details
    assert pyreq_file.read_text(encoding="utf-8") == original_text

