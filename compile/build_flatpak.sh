#!/bin/bash
set -e

# Change directory to the script's location
cd "$(dirname "$0")"

# Ensure BaseApp and SDK are installed
echo "Ensuring Flatpak dependencies are installed..."
flatpak remote-add --user --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
flatpak install --user -y flathub org.kde.Sdk//6.8 org.kde.Platform//6.8 io.qt.PySide.BaseApp//6.8

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
