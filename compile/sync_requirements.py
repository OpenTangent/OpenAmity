#!/usr/bin/env python3
import os
import subprocess
import sys

REQUIREMENTS_FILE = "../requirements.txt"
FLATPAK_REQS_FILE = "requirements-flatpak.txt"
GENERATOR_URL = "https://raw.githubusercontent.com/flatpak/flatpak-builder-tools/master/pip/flatpak-pip-generator.py"
GENERATOR_SCRIPT = "flatpak-pip-generator.py"

def run_cmd(cmd, **kwargs):
    print(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd, check=True, **kwargs)

def main():
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
        if 'pyside' not in line.lower():
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
        venv_site = os.path.abspath("../.venv/lib/python3.14/site-packages")
        patch_cmd = f"sed -i 's|\"run\",|\"run\", \"--filesystem=host\", \"--env=PYTHONPATH={venv_site}\",|' {GENERATOR_SCRIPT}"
        subprocess.run(patch_cmd, shell=True, check=True)
        
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
        all_packages.extend(["ctranslate2", "google-antigravity", "onnxruntime", "hf_xet", "hf-xet"])
        prefer_wheels_arg = "--prefer-wheels=" + ",".join(set(all_packages))
    except Exception as e:
        print(f"Warning: Could not get pip freeze: {e}")
        prefer_wheels_arg = "--prefer-wheels=ctranslate2,google-antigravity,onnxruntime,cryptography,bcrypt,numpy,grpcio,pillow,av,pydantic-core,chromadb,mempalace,pypika,frozenlist,yarl,multidict,MarkupSafe,cffi,tokenizers,rpds-py,orjson,psutil,httptools,uvloop,websockets,hf_xet"

    generator_cmd = [
        python_exe,
        f"./{GENERATOR_SCRIPT}",
        f"--requirements-file={FLATPAK_REQS_FILE}",
        "--output=python3-requirements",
        "--runtime=org.kde.Sdk//6.8",
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
    print("\n=== Done! ===")
    print("python3-requirements.json has been generated successfully.")

if __name__ == "__main__":
    main()
