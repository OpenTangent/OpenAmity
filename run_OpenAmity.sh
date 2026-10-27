#!/bin/bash
# Open Amity Launcher

# Check if we are running inside Flatpak
if [ -n "$FLATPAK_ID" ]; then
    # In Flatpak, the script is in /app/bin, and source is in /app
    cd /app

    # Ensure persistent XDG directories exist and link ~/.cache and ~/.local/share
    # inside the ephemeral Flatpak $HOME tmpfs so libraries hardcoding ~/.cache
    # (e.g. ChromaDB ONNX, Puppeteer, HuggingFace) persist across restarts.
    export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$HOME/.var/app/$FLATPAK_ID/cache}"
    export XDG_DATA_HOME="${XDG_DATA_HOME:-$HOME/.var/app/$FLATPAK_ID/data}"
    mkdir -p "$XDG_CACHE_HOME" "$XDG_DATA_HOME"
    if [ ! -e "$HOME/.cache" ] && [ ! -L "$HOME/.cache" ]; then
        ln -s "$XDG_CACHE_HOME" "$HOME/.cache"
    fi
    if [ ! -e "$HOME/.local/share" ] && [ ! -L "$HOME/.local/share" ]; then
        mkdir -p "$HOME/.local"
        ln -s "$XDG_DATA_HOME" "$HOME/.local/share"
    fi

    # Pre-populate ChromaDB ONNX model cache if bundled
    if [ -f "/app/share/chroma_onnx/all-MiniLM-L6-v2/onnx.tar.gz" ]; then
        CHROMA_CACHE_DIR="$HOME/.cache/chroma/onnx_models/all-MiniLM-L6-v2"
        if [ ! -f "$CHROMA_CACHE_DIR/onnx.tar.gz" ]; then
            echo "Populating ChromaDB ONNX model cache..."
            mkdir -p "$CHROMA_CACHE_DIR"
            cp "/app/share/chroma_onnx/all-MiniLM-L6-v2/onnx.tar.gz" "$CHROMA_CACHE_DIR/onnx.tar.gz"
        fi
    fi

    # Set Camoufox binary path if bundled
    if [ -f "/app/share/camoufox/camoufox-bin" ]; then
        export CAMOUFOX_BINARY_PATH="/app/share/camoufox/camoufox-bin"
    elif [ -f "/app/share/camoufox/camoufox" ]; then
        export CAMOUFOX_BINARY_PATH="/app/share/camoufox/camoufox"
    elif [ -f "/app/share/camoufox/firefox" ]; then
        export CAMOUFOX_BINARY_PATH="/app/share/camoufox/firefox"
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
