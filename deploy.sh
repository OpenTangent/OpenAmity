#!/bin/bash
set -e

# Define color codes for logging
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}=== Open Amity Deployment Script ===${NC}"
echo ""

# 1. Update python3-requirements.json if needed
echo -e "${BLUE}[1/5] Checking Python requirements...${NC}"
if [ requirements.txt -nt compile/python3-requirements.json ]; then
    echo -e "${YELLOW}requirements.txt has changed. Regenerating Flatpak dependencies...${NC}"
    python3 compile/sync_requirements.py
    echo -e "${GREEN}Dependencies regenerated successfully.${NC}"
else
    echo -e "${GREEN}requirements.txt has not changed. Skipping dependency regeneration.${NC}"
fi
echo ""

# 2. Extract current version
echo -e "${BLUE}[2/5] Preparing version metadata...${NC}"
VERSION=$(grep '__version__' src/core/version.py | cut -d '"' -f 2)
if [ -z "$VERSION" ]; then
    VERSION=$(grep '__version__' src/core/version.py | cut -d "'" -f 2)
fi
if [ -z "$VERSION" ]; then
    echo "Error: Could not extract version from src/core/version.py"
    exit 1
fi
echo -e "Deploying version: ${YELLOW}v$VERSION${NC}"

# Sync version metadata
echo "Syncing version metadata..."
python3 compile/sync_version_metadata.py
echo -e "${GREEN}Version metadata synced successfully.${NC}"
echo ""

# 3. Commit and Push
echo -e "${BLUE}[3/5] Committing and pushing to GitHub...${NC}"
FULL_COMMIT_MSG="Release v$VERSION"

echo "Staging files..."
git add .
echo "Committing with message: '$FULL_COMMIT_MSG'"
git commit -m "$FULL_COMMIT_MSG"
echo "Pushing to main branch..."
git push origin main
echo -e "${GREEN}Changes pushed successfully.${NC}"
echo ""

# 4. Bump patch version
echo -e "${BLUE}[4/5] Bumping patch version for next deployment...${NC}"
python3 -c '
import re
import sys

try:
    with open("src/core/version.py", "r") as f:
        content = f.read()
    
    match = re.search(r"__version__\s*=\s*[\"\'](\d+)\.(\d+)\.(\d+)[\"\']", content)
    if match:
        major, minor, patch = match.groups()
        new_version = f"{major}.{minor}.{int(patch)+1}"
        new_content = re.sub(r"__version__\s*=\s*[\"\'].*[\"\']", f"__version__ = \"{new_version}\"", content)
        
        with open("src/core/version.py", "w") as f:
            f.write(new_content)
        
        print(f"Version successfully bumped to: {new_version}")
    else:
        print("Warning: Could not automatically bump version (pattern not found).")
        sys.exit(1)
except Exception as e:
    print(f"Error bumping version: {e}")
    sys.exit(1)
'
echo ""

# 5. Print GitHub Release Instructions
echo -e "${BLUE}[5/5] Manual Steps Required${NC}"
echo -e "${YELLOW}==========================================================${NC}"
echo -e "${YELLOW}ACTION REQUIRED: Publish a GitHub Release${NC}"
echo -e "${YELLOW}==========================================================${NC}"
echo -e "1. Go to the repository on GitHub and click ${GREEN}'Releases'${NC} on the right side."
echo -e "2. Click ${GREEN}'Draft a new release'${NC}."
echo -e "3. Create a new tag for this version: ${GREEN}v$VERSION${NC}"
echo -e "4. Write your release notes (changelog)."
echo -e "5. Click ${GREEN}'Publish release'${NC}."
echo -e "${YELLOW}==========================================================${NC}"
echo ""

echo -e "${GREEN}=== Deployment Script Completed Successfully ===${NC}"
