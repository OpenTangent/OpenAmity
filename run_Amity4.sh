#!/bin/bash
# Amity 4 Launcher

# Ensure we are in the project directory
cd "$(dirname "$0")"

# Activate environment variables if needed (none for system python currently)
# export PYTHONPATH=$PYTHONPATH:$(pwd)/src

# Run the application
echo "Starting Amity 4..."
python3 src/main.py "$@"
