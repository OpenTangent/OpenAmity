#!/usr/bin/env python3
import argparse
import json
import os
import re
import shlex
import subprocess
import sys

# NOTE: For future Flathub submission:
# Flathub builds do not run this script. They require the generated 
# `python3-requirements.json` to be committed directly to your git repository.
# Continue to run this script locally only when requirements.txt changes, 
# and commit the updated .json file.

REQUIREMENTS_FILE = "../requirements.txt"
FLATPAK_REQS_FILE = "requirements-flatpak.txt"
GENERATOR_URL = "https://raw.githubusercontent.com/flatpak/flatpak-builder-tools/master/pip/flatpak-pip-generator.py"
GENERATOR_SCRIPT = "flatpak-pip-generator.py"

def _normalize_pkg_name(name: str) -> str:
    """Canonicalize a Python distribution name according to PEP 503."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _strip_inline_comment(line: str) -> str:
    """Strip full-line or whitespace-preceded inline comments from a requirements line."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return ""
    return re.sub(r"\s+#.*$", "", stripped).strip()


def _parse_requirements_txt(requirements_path: str):
    """Parse active non-PySide requirements from requirements.txt."""
    from packaging.requirements import InvalidRequirement, Requirement

    desired = {}
    unparseable = []
    with open(requirements_path, "r", encoding="utf-8") as f:
        for raw_line in f:
            cleaned = _strip_inline_comment(raw_line)
            if not cleaned:
                continue
            if "pyside" in cleaned.lower():
                continue
            if cleaned.startswith("-") or cleaned.endswith("\\"):
                unparseable.append(cleaned)
                continue
            try:
                req = Requirement(cleaned)
            except InvalidRequirement:
                unparseable.append(cleaned)
                continue
            canon = _normalize_pkg_name(req.name)
            if canon in desired:
                unparseable.append(cleaned)
                continue
            desired[canon] = req
    return desired, unparseable


def _extract_module_requirement(module: dict):
    """Extract the single Requirement installed by a flatpak-pip-generator module."""
    from packaging.requirements import InvalidRequirement, Requirement

    if not isinstance(module, dict):
        return None
    build_cmds = module.get("build-commands")
    if not isinstance(build_cmds, list) or len(build_cmds) != 1:
        return None
    cmd_str = build_cmds[0]
    if not isinstance(cmd_str, str):
        return None
    try:
        tokens = shlex.split(cmd_str)
    except ValueError:
        return None
    if not tokens or "install" not in tokens:
        return None
    install_idx = tokens.index("install")
    pip_bin = os.path.basename(tokens[0])
    if not pip_bin.startswith("pip") and "pip" not in tokens[:install_idx]:
        return None

    positional = [tok for tok in tokens[install_idx + 1:] if not tok.startswith("-")]
    if len(positional) != 1:
        return None
    try:
        return Requirement(positional[0])
    except InvalidRequirement:
        return None


def _query_flatpak_runtime_packages(flatpak_manifest_path: str | None) -> dict[str, str] | None:
    """Query Python packages pre-installed inside the target Flatpak runtime."""
    if not flatpak_manifest_path or not os.path.exists(flatpak_manifest_path):
        return None
    try:
        with open(flatpak_manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        runtime = manifest.get("runtime")
        runtime_version = manifest.get("runtime-version")
        if not runtime or not runtime_version:
            return None
        runtime_ref = f"{runtime}//{runtime_version}"
        code = (
            "import importlib.metadata, json; "
            "print(json.dumps({d.metadata['Name']: d.version "
            "for d in importlib.metadata.distributions() if d.metadata.get('Name')}))"
        )
        res = subprocess.run(
            ["flatpak", "run", "--command=python3", runtime_ref, "-c", code],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        raw_pkgs = json.loads(res.stdout)
        return {_normalize_pkg_name(k): str(v) for k, v in raw_pkgs.items()}
    except Exception:
        return None


def _requirements_equivalent(a, b) -> bool:
    """Check semantic equivalence between two parsed Requirement objects."""
    return (
        _normalize_pkg_name(a.name) == _normalize_pkg_name(b.name)
        and {e.lower() for e in a.extras} == {e.lower() for e in b.extras}
        and a.specifier == b.specifier
        and str(a.marker or "") == str(b.marker or "")
        and (a.url or "") == (b.url or "")
    )


def reconcile_python_requirements(
    requirements_path: str,
    python_reqs_json_path: str,
    flatpak_manifest_path: str | None = None,
    runtime_packages: dict[str, str] | None = None,
) -> tuple[str, list[str]]:
    """Verify python3-requirements.json against requirements.txt and prune orphaned modules.

    Returns ("up_to_date", []), ("pruned", pruned_module_names), or
    ("needs_regen", reasons_or_changed_requirements).
    """
    if not os.path.exists(python_reqs_json_path):
        return ("needs_regen", [f"{python_reqs_json_path} is missing"])
    if not os.path.exists(requirements_path):
        return ("needs_regen", [f"{requirements_path} is missing"])

    try:
        with open(python_reqs_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        return ("needs_regen", [f"invalid JSON in python3-requirements.json: {exc}"])

    if not isinstance(data, dict) or not isinstance(data.get("modules"), list):
        return ("needs_regen", ["python3-requirements.json missing 'modules' list"])

    desired_reqs, unparseable = _parse_requirements_txt(requirements_path)
    if unparseable:
        return ("needs_regen", unparseable)

    existing_by_canon = {}
    for mod in data["modules"]:
        req = _extract_module_requirement(mod)
        if req is None:
            mod_name = mod.get("name", "<unknown>") if isinstance(mod, dict) else "<invalid>"
            return ("needs_regen", [f"unrecognized module format: {mod_name}"])
        canon = _normalize_pkg_name(req.name)
        if canon in existing_by_canon:
            return ("needs_regen", [f"duplicate module for package: {canon}"])
        existing_by_canon[canon] = (mod, req)

    normalized_runtime = (
        {_normalize_pkg_name(k): str(v) for k, v in runtime_packages.items()}
        if runtime_packages is not None
        else None
    )

    missing_or_changed = []
    for canon, desired_req in desired_reqs.items():
        if canon in existing_by_canon:
            _, existing_req = existing_by_canon[canon]
            if not _requirements_equivalent(desired_req, existing_req):
                missing_or_changed.append(str(desired_req))
            continue

        if normalized_runtime is None:
            queried = _query_flatpak_runtime_packages(flatpak_manifest_path)
            normalized_runtime = queried if queried is not None else {}

        runtime_ver = normalized_runtime.get(canon)
        satisfied_by_runtime = (
            runtime_ver is not None
            and not desired_req.extras
            and not desired_req.url
            and desired_req.marker is None
            and desired_req.specifier.contains(runtime_ver, prereleases=True)
        )
        if not satisfied_by_runtime:
            missing_or_changed.append(str(desired_req))

    if missing_or_changed:
        return ("needs_regen", missing_or_changed)

    pruned_names = []
    kept_modules = []
    for mod in data["modules"]:
        req = _extract_module_requirement(mod)
        assert req is not None
        canon = _normalize_pkg_name(req.name)
        if canon not in desired_reqs:
            pruned_names.append(str(mod.get("name", canon)))
        else:
            kept_modules.append(mod)

    if pruned_names:
        data["modules"] = kept_modules
        with open(python_reqs_json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
            f.write("\n")
        return ("pruned", pruned_names)

    return ("up_to_date", [])


def run_cmd(cmd, **kwargs):
    print(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd, check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(
        description="Sync local environment and Flatpak Python/Node dependency manifests."
    )
    parser.add_argument(
        "--reconcile-python",
        action="store_true",
        help=(
            "Check whether compile/python3-requirements.json already satisfies requirements.txt "
            "(pruning removed packages in place). Exits 0 if satisfied, 2 if full regeneration is needed."
        ),
    )
    args = parser.parse_args()

    if args.reconcile_python:
        compile_dir = os.path.dirname(os.path.abspath(__file__))
        req_path = os.path.join(compile_dir, "..", "requirements.txt")
        py_reqs_json = os.path.join(compile_dir, "python3-requirements.json")
        manifest_path = os.path.join(compile_dir, "com.openamity.OpenAmity.json")
        try:
            status, details = reconcile_python_requirements(
                req_path, py_reqs_json, flatpak_manifest_path=manifest_path
            )
        except ImportError as exc:
            print(f"Reconcile Python requirements fallback (missing dependency: {exc}); regeneration required.")
            sys.exit(2)

        if status == "up_to_date":
            print("All Python requirements in requirements.txt are already satisfied by python3-requirements.json.")
            sys.exit(0)
        if status == "pruned":
            print(f"Pruned removed Python module(s) from python3-requirements.json: {', '.join(details)}")
            sys.exit(0)
        print(f"Python Flatpak manifest requires full regeneration for: {', '.join(details)}")
        sys.exit(2)

    # 1. Sync the local environment
    print("=== Syncing Local Environment ===")
    
    # We use python3 -m pip to ensure we use the active virtual environment
    # Actually, we should make sure we are inside .venv. Let's find the pip executable.
    pip_exe = "../.venv/bin/pip"
    if not os.path.exists(pip_exe):
        print("Error: Virtual environment not found at ../.venv")
        sys.exit(1)
        
    print("Installing requirements from requirements.txt...")
    run_cmd([pip_exe, "install", "-r", REQUIREMENTS_FILE])
    
    print("Uninstalling extraneous packages...")
    # Get currently installed packages
    freeze_output = subprocess.check_output([pip_exe, "freeze"]).decode('utf-8')
    installed_pkgs = [line.split('==')[0].split('@')[0].strip().lower() for line in freeze_output.splitlines() if line]
    
    # Get desired packages
    with open(REQUIREMENTS_FILE, 'r') as f:
        desired_lines = f.readlines()
    desired_pkgs = [line.split('==')[0].strip().lower() for line in desired_lines if line.strip() and not line.startswith('#')]
    
    # We want to keep core packages
    core_pkgs = {'pip', 'setuptools', 'wheel', 'requirements-parser', 'toml', 'packaging'}
    
    # Since we can't easily parse dependencies of dependencies without pip-tools, 
    # a simple pip freeze sync might be dangerous if it removes dependencies.
    # Actually, let's just use `pip install` to add, and skip `pip uninstall` for now 
    # to avoid breaking transitive dependencies, unless we use pip-sync.
    # Alternatively, the user can manually wipe .venv if they remove packages.
    # We will just ensure all requirements are installed.
    print("Note: To fully purge old dependencies, it's recommended to recreate the .venv or use pip-sync.")
    
    # 2. Prepare flatpak requirements file (filtering PySide6)
    print("\n=== Preparing Flatpak Requirements ===")
    filtered_lines = []
    for line in desired_lines:
        line_clean = line.strip().lower()
        if 'pyside' not in line_clean:
            filtered_lines.append(line)
            
    with open(FLATPAK_REQS_FILE, 'w') as f:
        f.writelines(filtered_lines)
        
    # 3. Generate the python3-requirements.json
    print("\n=== Generating Flatpak Manifest ===")
    if not os.path.exists(GENERATOR_SCRIPT):
        print(f"Downloading {GENERATOR_SCRIPT}...")
        run_cmd(["wget", "-q", GENERATOR_URL])
        run_cmd(["chmod", "+x", GENERATOR_SCRIPT])
        
        # Patch the generator to mount host filesystem and set PYTHONPATH
        import glob
        venv_sites = glob.glob(os.path.abspath("../.venv/lib/python3.*/site-packages"))
        venv_site = venv_sites[0] if venv_sites else os.path.abspath("../.venv/lib/python3.14/site-packages")
        patch_cmd = f"sed -i 's|\"run\",|\"run\", \"--filesystem=host\", \"--env=PYTHONPATH={venv_site}\",|' {GENERATOR_SCRIPT}"
        subprocess.run(patch_cmd, shell=True, check=True)
        
        # Patch socket timeout to avoid hanging indefinitely on stalled connections
        timeout_patch = f"sed -i 's|import urllib.request|import urllib.request\\nimport socket\\nsocket.setdefaulttimeout(15)|' {GENERATOR_SCRIPT}"
        subprocess.run(timeout_patch, shell=True, check=True)
        
    # Ensure requirements-parser and toml are installed locally
    run_cmd([pip_exe, "install", "-q", "requirements-parser", "toml"])
    
    # Run flatpak-pip-generator using the virtual environment python
    python_exe = "../.venv/bin/python3"
    try:
        pip_freeze_out = subprocess.check_output([python_exe, "-m", "pip", "freeze"], text=True)
        all_packages = []
        for line in pip_freeze_out.splitlines():
            line = line.strip()
            if line and "==" in line:
                pkg_name = line.split("==")[0]
                all_packages.append(pkg_name)
        # Add some known ones just in case they aren't explicitly in freeze
        all_packages.extend(["ctranslate2", "onnxruntime", "hf_xet", "hf-xet"])
        prefer_wheels_arg = "--prefer-wheels=" + ",".join(set(all_packages))
    except Exception as e:
        print(f"Warning: Could not get pip freeze: {e}")
        prefer_wheels_arg = "--prefer-wheels=ctranslate2,onnxruntime,cryptography,bcrypt,numpy,grpcio,pillow,av,pydantic-core,chromadb,mempalace,pypika,frozenlist,yarl,multidict,MarkupSafe,cffi,tokenizers,rpds-py,orjson,psutil,httptools,uvloop,websockets,hf_xet"

    generator_cmd = [
        python_exe,
        f"./{GENERATOR_SCRIPT}",
        f"--requirements-file={FLATPAK_REQS_FILE}",
        "--output=python3-requirements",
        "--runtime=org.kde.Sdk//6.11",
        prefer_wheels_arg
    ]
    
    max_retries = 10
    import time
    for attempt in range(max_retries):
        try:
            run_cmd(generator_cmd)
            break
        except subprocess.CalledProcessError as e:
            print(f"Attempt {attempt + 1} failed due to network error. Retrying in 5 seconds...")
            if attempt == max_retries - 1:
                raise
            time.sleep(5)
    
    # Clean up
    if os.path.exists(FLATPAK_REQS_FILE):
        os.remove(FLATPAK_REQS_FILE)
    if os.path.exists(GENERATOR_SCRIPT):
        os.remove(GENERATOR_SCRIPT)

    # 4. Generate the node-sources.json for WhatsApp bridge
    print("\n=== Generating Flatpak Node Dependencies ===")
    node_dir = "../src/tools/whatsapp_node"
    lock_file = os.path.join(node_dir, "package-lock.json")
    if os.path.exists(lock_file):
        print(f"Ensuring complete dependency tree in {lock_file}...")
        try:
            subprocess.run(["npm", "i", "--package-lock-only", "--lockfile-version", "2"], cwd=node_dir, check=True)
        except Exception as e:
            print(f"Warning: npm command failed: {e}")

        print(f"Generating node-sources.json from {lock_file}...")
        try:
            import flatpak_node_generator
        except ImportError:
            print("Installing flatpak-node-generator in venv...")
            run_cmd([pip_exe, "install", "-q", "git+https://github.com/flatpak/flatpak-builder-tools.git#subdirectory=node"])

        node_gen_exe = "../.venv/bin/flatpak-node-generator"
        run_cmd([node_gen_exe, "npm", lock_file, "-o", "node-sources.json"])
        print("node-sources.json generated successfully.")

    print("\n=== Done! ===")
    print("python3-requirements.json and node-sources.json have been generated successfully.")

if __name__ == "__main__":
    main()
