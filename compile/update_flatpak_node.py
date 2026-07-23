#!/usr/bin/env python3
import urllib.request
import json
import sys

MANIFEST_FILE = "compile/com.openamity.OpenAmity.json"

def main():
    print("Fetching latest Node.js LTS version...")
    # Get index
    with urllib.request.urlopen('https://nodejs.org/dist/index.json') as response:
        index = json.loads(response.read().decode())
        
    # Find latest LTS
    lts_release = next((release for release in index if release['lts']), None)
    if not lts_release:
        print("Could not find LTS release.")
        sys.exit(1)
        
    version = lts_release['version']
    print(f"Latest LTS version is {version}")
    
    # Fetch SHA256
    tarball_name = f"node-{version}-linux-x64.tar.xz"
    print(f"Fetching SHA256 for {tarball_name}...")
    shasums_url = f"https://nodejs.org/dist/{version}/SHASUMS256.txt"
    try:
        with urllib.request.urlopen(shasums_url) as response:
            shasums = response.read().decode().splitlines()
    except Exception as e:
        print(f"Error fetching SHASUMS256.txt: {e}")
        sys.exit(1)
        
    sha256 = next((line.split()[0] for line in shasums if tarball_name in line), None)
    if not sha256:
        print(f"Could not find SHA256 for {tarball_name}")
        sys.exit(1)
        
    print(f"SHA256 is {sha256}")
    
    print(f"Updating {MANIFEST_FILE}...")
    with open(MANIFEST_FILE, 'r') as f:
        manifest = json.load(f)
        
    # Remove sdk-extensions if present
    if "sdk-extensions" in manifest:
        del manifest["sdk-extensions"]

    node_module = {
        "name": "nodejs",
        "buildsystem": "simple",
        "build-commands": [
            "cp -a * /app/"
        ],
        "sources": [
            {
                "type": "archive",
                "url": f"https://nodejs.org/dist/{version}/{tarball_name}",
                "sha256": sha256
            }
        ]
    }

    # Check if nodejs module already exists
    modules = manifest.get("modules", [])
    nodejs_idx = next((i for i, m in enumerate(modules) if isinstance(m, dict) and m.get("name") == "nodejs"), -1)

    if nodejs_idx != -1:
        modules[nodejs_idx] = node_module
    else:
        # Insert after python3-requirements.json
        modules.insert(1, node_module)
        
    # Also clean up the open_amity build-commands that copied from the SDK extension
    amity_idx = next((i for i, m in enumerate(modules) if isinstance(m, dict) and m.get("name") == "open_amity"), -1)
    if amity_idx != -1:
        build_commands = modules[amity_idx].get("build-commands", [])
        # Filter out the SDK copy commands
        filtered_commands = [cmd for cmd in build_commands if "/usr/lib/sdk/node" not in cmd]
        modules[amity_idx]["build-commands"] = filtered_commands

    manifest["modules"] = modules
        
    with open(MANIFEST_FILE, 'w') as f:
        json.dump(manifest, f, indent=4)
        
    print("Update successful!")

if __name__ == "__main__":
    main()
