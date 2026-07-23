import os
import re
from datetime import datetime

def main():
    # Paths
    compile_dir = os.path.dirname(os.path.abspath(__file__))
    version_file = os.path.join(compile_dir, "..", "src", "core", "version.py")
    metainfo_file = os.path.join(compile_dir, "com.openamity.OpenAmity.metainfo.xml")

    # 1. Read version from version.py
    if not os.path.exists(version_file):
        print(f"Error: {version_file} not found.")
        return

    with open(version_file, "r") as f:
        version_content = f.read()

    match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', version_content)
    if not match:
        print("Error: Could not find __version__ in version.py")
        return
        
    current_version = match.group(1)
    today = datetime.today().strftime('%Y-%m-%d')
    print(f"Sync Metadata: Detected Open Amity Version {current_version}")

    # 2. Read metainfo.xml
    if not os.path.exists(metainfo_file):
        print(f"Error: {metainfo_file} not found.")
        return

    with open(metainfo_file, "r") as f:
        metainfo_content = f.read()

    # 3. Check if version already exists
    release_tag = f'<release version="{current_version}"'
    if release_tag in metainfo_content:
        print(f"Sync Metadata: Version {current_version} already exists in metainfo.xml. Skipping.")
        return

    # 4. Insert new release
    new_release = f'    <release version="{current_version}" date="{today}"/>\n'
    
    # We find the <releases> tag and insert right after it
    if '<releases>' in metainfo_content:
        metainfo_content = metainfo_content.replace(
            '<releases>',
            f'<releases>\n{new_release}',
            1
        )
        
        with open(metainfo_file, "w") as f:
            f.write(metainfo_content)
        print(f"Sync Metadata: Successfully added version {current_version} to metainfo.xml.")
    else:
        print("Error: <releases> tag not found in metainfo.xml")

if __name__ == "__main__":
    main()
