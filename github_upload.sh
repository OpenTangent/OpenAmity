#!/bin/bash

# Ensure script stops on first error
set -e

echo "=== Open Amity GitHub Upload Script ==="

# 1. Clean up local state
echo "Cleaning up local stateful files..."
rm -rf src/tools/whatsapp_node/.wwebjs_auth
rm -rf src/tools/whatsapp_node/node_modules

# 2. Extract version
VERSION=$(grep '__version__' src/core/version.py | cut -d '"' -f 2)
if [ -z "$VERSION" ]; then
    echo "Error: Could not extract version from src/core/version.py"
    exit 1
fi
echo "Detected version: $VERSION"

# 3. Prompt for commit message
read -p "Enter a commit message: " COMMIT_MSG
if [ -z "$COMMIT_MSG" ]; then
    echo "Error: Commit message cannot be empty."
    exit 1
fi
FULL_COMMIT_MSG="v$VERSION - $COMMIT_MSG"

# 4. Check git initialization and remote
if [ ! -d ".git" ]; then
    echo "Initializing git repository..."
    git init
    git branch -M main
fi

REMOTE_URL=$(git config --get remote.origin.url || true)
if [ -z "$REMOTE_URL" ]; then
    echo "Linking to remote repository..."
    git remote add origin https://github.com/OpenTangent/OpenAmity.git
fi

# 5. Commit and push
echo "Staging files..."
git add .

echo "Committing with message: $FULL_COMMIT_MSG"
git commit -m "$FULL_COMMIT_MSG"

echo "Pushing to GitHub..."
git push -u origin main

# 6. Create GitHub Release
echo "Creating GitHub Release for v$VERSION..."
if command -v gh &> /dev/null; then
    if [ -f "compile/OpenAmity.flatpak" ]; then
        gh release create "v$VERSION" compile/OpenAmity.flatpak -t "Release v$VERSION" -n "Release version $VERSION"
        echo "Release v$VERSION created successfully with Flatpak attached!"
    else
        echo "Warning: compile/OpenAmity.flatpak not found. Creating release without asset."
        gh release create "v$VERSION" -t "Release v$VERSION" -n "Release version $VERSION"
    fi
else
    echo "Error: gh CLI not found. Please install and authenticate it to create releases."
fi

echo "=== Upload Complete ==="
