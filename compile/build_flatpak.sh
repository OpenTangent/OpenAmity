#!/bin/bash
set -e

# NOTE: For future Flathub submission:
# Flathub builds do not run custom scripts like this one. They only consume
# the manifest (.json or .yaml) and source code directly from git.
# This script remains useful for local testing and building.

# Change directory to the script's location
cd "$(dirname "$0")"

# Ensure BaseApp and SDK are installed
echo "Ensuring Flatpak dependencies are installed..."
flatpak remote-add --user --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
flatpak info org.kde.Sdk//6.11 >/dev/null 2>&1 || flatpak install --user -y flathub org.kde.Sdk//6.11
flatpak info org.kde.Platform//6.11 >/dev/null 2>&1 || flatpak install --user -y flathub org.kde.Platform//6.11
INSTALLED_BASEAPP_VER=$(flatpak info io.qt.PySide.BaseApp//6.11 2>/dev/null | awk '/Version:/ {print $2}')
if [ -z "$INSTALLED_BASEAPP_VER" ] || [ "$(printf '%s\n6.11.2\n' "$INSTALLED_BASEAPP_VER" | sort -V | head -n1)" != "6.11.2" ]; then
    BASEAPP_VER=$(flatpak remote-info --user flathub io.qt.PySide.BaseApp//6.11 2>/dev/null | awk '/Version:/ {print $2}')
    if [ -z "$BASEAPP_VER" ] || [ "$(printf '%s\n6.11.2\n' "$BASEAPP_VER" | sort -V | head -n1)" != "6.11.2" ]; then
        echo "Flathub BaseApp is at $BASEAPP_VER (< 6.11.2). Installing Qt 6.11.2 compatible BaseApp build..."
        flatpak install --user --reinstall -y https://dl.flathub.org/build-repo/320285/io.qt.PySide.BaseApp.flatpakref
    else
        flatpak install --user --reinstall -y flathub io.qt.PySide.BaseApp//6.11
    fi
fi

# Check if pre-baked requirements file exists
if [ ! -f "python3-requirements.json" ]; then
    echo "Error: python3-requirements.json not found."
    echo "Please run ./sync_requirements.py first to generate the dependencies."
    exit 1
fi

echo "Syncing Flatpak metadata version..."
python3 sync_version_metadata.py

echo "Building Flatpak Open Amity..."
flatpak-builder --disable-rofiles-fuse --force-clean build-dir com.openamity.OpenAmity.json

echo "Installing Flatpak Open Amity..."
flatpak-builder --disable-rofiles-fuse --user --install --force-clean build-dir com.openamity.OpenAmity.json

echo "Cleaning up temporary build directories..."
rm -rf build-dir .flatpak-builder

echo "Build and installation complete!"
echo "You can run the app with: flatpak run com.openamity.OpenAmity"
