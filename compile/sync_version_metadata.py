#!/usr/bin/env python3
import os
import sys
import re
import json
import shutil
import argparse
from datetime import datetime


def get_current_version(version_file: str) -> str:
    if not os.path.exists(version_file):
        raise FileNotFoundError(f"{version_file} not found.")

    with open(version_file, "r") as f:
        version_content = f.read()

    match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', version_content)
    if not match:
        raise ValueError("Could not find __version__ in version.py")
    return match.group(1)


def sync_metainfo(metainfo_file: str, current_version: str):
    if not os.path.exists(metainfo_file):
        print(f"Error: {metainfo_file} not found.")
        return

    with open(metainfo_file, "r") as f:
        metainfo_content = f.read()

    today = datetime.today().strftime('%Y-%m-%d')
    modified = False

    # 1. Update screenshots to point to release tag
    new_metainfo = re.sub(
        r'https://raw\.githubusercontent\.com/OpenTangent/OpenAmity/(?:main|v[0-9\.]+)/src/assets/screenshots/',
        f'https://raw.githubusercontent.com/OpenTangent/OpenAmity/v{current_version}/src/assets/screenshots/',
        metainfo_content
    )
    if new_metainfo != metainfo_content:
        metainfo_content = new_metainfo
        modified = True
        print(f"Sync Metadata: Pinned screenshot URLs to v{current_version}")

    # 2. Check if version release tag already exists
    release_tag = f'<release version="{current_version}"'
    if release_tag not in metainfo_content:
        new_release = (
            f'    <release version="{current_version}" date="{today}">\n'
            f'      <description>\n'
            f'        <p>Open Amity v{current_version} release updates and stability improvements.</p>\n'
            f'      </description>\n'
            f'    </release>\n'
        )
        if '<releases>' in metainfo_content:
            metainfo_content = metainfo_content.replace(
                '<releases>',
                f'<releases>\n{new_release}',
                1
            )
            modified = True
            print(f"Sync Metadata: Successfully added version {current_version} to metainfo.xml.")
        else:
            print("Error: <releases> tag not found in metainfo.xml")
    else:
        print(f"Sync Metadata: Version {current_version} release entry already exists in metainfo.xml.")

    if modified:
        with open(metainfo_file, "w") as f:
            f.write(metainfo_content)


def generate_flathub_manifest(compile_dir: str, current_version: str, commit_sha: str):
    manifest_source = os.path.join(compile_dir, "com.openamity.OpenAmity.json")
    out_dir = os.path.join(compile_dir, "flathub")
    os.makedirs(out_dir, exist_ok=True)

    with open(manifest_source, "r") as f:
        manifest = json.load(f)

    # Find the open_amity module and point its source to git tag + commit
    found_module = False
    for mod in manifest.get("modules", []):
        if isinstance(mod, dict) and mod.get("name") == "open_amity":
            mod["sources"] = [
                {
                    "type": "git",
                    "url": "https://github.com/OpenTangent/OpenAmity.git",
                    "tag": f"v{current_version}",
                    "commit": commit_sha
                },
                "node-sources.json"
            ]
            found_module = True
            break

    if not found_module:
        raise RuntimeError("Could not find 'open_amity' module in manifest.")

    out_manifest = os.path.join(out_dir, "com.openamity.OpenAmity.json")
    with open(out_manifest, "w") as f:
        json.dump(manifest, f, indent=4)
        f.write("\n")

    # Copy companion build files required by Flathub
    companion_files = ["python3-requirements.json", "node-sources.json"]
    for cf in companion_files:
        src_path = os.path.join(compile_dir, cf)
        if os.path.exists(src_path):
            dst_path = os.path.join(out_dir, cf)
            shutil.copy2(src_path, dst_path)
            print(f"Copied {cf} -> {out_dir}/")
        else:
            print(f"Warning: {cf} not found in {compile_dir}")

    print(f"\n[OK] Flathub release bundle generated in {out_dir}:")
    print(f"     - com.openamity.OpenAmity.json (Git tag: v{current_version}, commit: {commit_sha})")
    for cf in companion_files:
        print(f"     - {cf}")


def main():
    parser = argparse.ArgumentParser(description="Sync Open Amity release metadata and generate Flathub manifests.")
    parser.add_argument(
        "--generate-flathub-manifest",
        metavar="COMMIT_SHA",
        help="Generate Flathub git-based manifest and copy dependencies into compile/flathub/"
    )
    args = parser.parse_args()

    compile_dir = os.path.dirname(os.path.abspath(__file__))
    version_file = os.path.join(compile_dir, "..", "src", "core", "version.py")
    metainfo_file = os.path.join(compile_dir, "com.openamity.OpenAmity.metainfo.xml")

    current_version = get_current_version(version_file)
    print(f"Sync Metadata: Detected Open Amity Version {current_version}")

    sync_metainfo(metainfo_file, current_version)

    if args.generate_flathub_manifest:
        commit_sha = args.generate_flathub_manifest
        # Resolve 'HEAD' if passed
        if commit_sha.upper() == "HEAD":
            import subprocess
            commit_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        generate_flathub_manifest(compile_dir, current_version, commit_sha)


if __name__ == "__main__":
    main()
