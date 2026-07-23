#!/bin/bash
set -e

# Change directory to the script's location
cd "$(dirname "$0")"

echo "Exporting Open Amity to a standalone Flatpak bundle..."

# Flatpak installs user apps to ~/.local/share/flatpak/repo
# We will use this repo to generate the standalone .flatpak file
flatpak build-bundle ~/.local/share/flatpak/repo OpenAmity.flatpak com.openamity.OpenAmity

echo "Success! The bundle has been created at: $(pwd)/OpenAmity.flatpak"
echo "You can share this file. Users can install it simply by double-clicking it or running:"
echo "flatpak install --user OpenAmity.flatpak"
