#!/bin/bash
# Open Amity Launcher

# Check if we are running inside Flatpak
if [ -n "$FLATPAK_ID" ]; then
    # In Flatpak, the script is in /app/bin, and source is in /app
    cd /app
    
    # Pre-populate ChromaDB ONNX model cache if bundled
    if [ -f "/app/share/chroma_onnx/all-MiniLM-L6-v2/onnx.tar.gz" ]; then
        CHROMA_CACHE_DIR="$HOME/.cache/chroma/onnx_models/all-MiniLM-L6-v2"
        if [ ! -f "$CHROMA_CACHE_DIR/onnx.tar.gz" ]; then
            echo "Populating ChromaDB ONNX model cache..."
            mkdir -p "$CHROMA_CACHE_DIR"
            cp "/app/share/chroma_onnx/all-MiniLM-L6-v2/onnx.tar.gz" "$CHROMA_CACHE_DIR/onnx.tar.gz"
        fi
    fi
else
    # Ensure we are in the project directory for local execution
    cd "$(dirname "$0")"
    
    # Activate the virtual environment if it exists
    if [ -f ".venv/bin/activate" ]; then
        source .venv/bin/activate
    fi
fi

# Run the application
echo "Starting Open Amity..."
python3 src/main.py "$@"
