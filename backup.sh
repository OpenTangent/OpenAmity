#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

DATE=$(date +%Y-%m-%d)
BACKUP_ROOT="$HOME/Documents/Backups/$DATE"

echo "Starting Open Amity backup process for date: $DATE"

# Check if backup directory already exists and remove it
if [ -d "$BACKUP_ROOT" ]; then
    echo "Found existing backup for today. Removing it to create a fresh copy..."
    rm -rf "$BACKUP_ROOT"
fi

echo "Creating backup directories at $BACKUP_ROOT..."
mkdir -p "$BACKUP_ROOT/Open Amity"
mkdir -p "$BACKUP_ROOT/data"

echo "Backing up ~/Dev/OpenAmity/ source code (ignoring .gitignore items)..."
rsync -a --filter=':- .gitignore' --exclude='.git/' "$HOME/Dev/OpenAmity/" "$BACKUP_ROOT/OpenAmity/"

echo "Backing up ~/.var/app/com.openamity.OpenAmity/data/ application data..."
rsync -a "$HOME/.var/app/com.openamity.OpenAmity/data/" "$BACKUP_ROOT/data/"

echo "Backup completed successfully!"
