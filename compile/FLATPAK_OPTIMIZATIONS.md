# Open Amity Flatpak Size Optimizations & Developer Playbook

This document defines the Flatpak optimization architecture for Open Amity (`com.openamity.OpenAmity`). It details all applied size reductions, explains why redundant components existed, and provides an audit checklist so future developers can maintain and build upon these optimizations when updating dependencies.

---

## 1. Summary of Optimizations (~3.55 GB → ~880 MB Installed)

The unoptimized baseline Flatpak weighed **~3,550 MB (~3.55 GB)** installed. Through the Tier 1 (zero-sacrifice) and Tier 2 (on-demand + runtime decoding) optimizations below, the installed footprint is reduced to **~880 MB** (a **~75.2% reduction**, saving **~2.67 GB**).

| Component / Subsystem | Unoptimized | Optimized | Saved | Mechanism & Reason |
| :--- | :---: | :---: | :---: | :--- |
| **Camoufox Browser & Fonts** | 1,200 MB | 0 MB (in Flatpak)<br>~310 MB (in user cache) | **1,200 MB in Flatpak**<br>*(+ 891 MB in user cache)* | Camoufox bundle contained 933 MB of fonts (`fonts/macos` 569 MB, `fonts/windows` 322 MB) for spoofing non-Linux User-Agents. We pinned `os="linux"` in `BrowserManager`, moved Camoufox to an on-demand download via `CamoufoxFetcher`, and automatically prune `fonts/macos` and `fonts/windows` after extraction. |
| **`io.qt.PySide.BaseApp` Runtime Cleanup** | 522 MB | 0 MB | **522 MB** | BaseApp bundled unneeded `QtWebEngine` (200 MB), `libLLVM` (92 MB), `libclang` (58 MB), `translations` (44 MB), `PyOpenGL` (31 MB), `libexec/webenginedriver` (23 MB), and unused Qt 3D/Charts/Graphs/Quick bindings and `.pyi` stubs. Pruned via `BASEAPP_REMOVE_WEBENGINE=1 /app/cleanup-BaseApp.sh` and explicit cleanup commands. |
| **Puppeteer Chromium** | 350 MB | 0 MB (in Flatpak)<br>~285 MB (in user cache) | **350 MB in Flatpak**<br>*(+ 66 MB in user cache)* | Full Chrome for Testing was bundled statically for WhatsApp. Since WhatsApp is disabled by default, Chrome is now downloaded on demand asynchronously into `~/.cache/puppeteer` only when WhatsApp is enabled, and stripped of `WidevineCdm/` (18 MB) and 55+ unused locales (48 MB). |
| **Playwright Node.js Deduplication** | 168 MB | 0 MB | **168 MB** | The `playwright` Python wheel bundled its own duplicate 118 MB `node` binary at `site-packages/playwright/driver/node`. We symlinked this driver to `/app/bin/node`, stripped `/app/bin/node` (saving 23 MB), and removed build-time `npm` (18 MB) and C++ headers (11 MB). |
| **Unused `google-antigravity` & `kubernetes`** | 197 MB | 0 MB | **197 MB** | `google-antigravity` (115 MB) was never imported by `src/` (AgyWorker calls the host `agy` CLI). `kubernetes` (82 MB) was an optional distributed cluster dependency in ChromaDB never used by local `PersistentClient`. Both removed. |
| **`PyAV` (`av` + `av.libs`) Audio Decoding** | 104 MB | 0 MB | **104 MB** | `faster-whisper` pulled in `PyAV` (104 MB) including video encoders (`libx265`, `libx264`, `libvpx`, `libSvtAv1Enc`). We replaced PyAV with `/usr/bin/ffmpeg` (already in the KDE Platform runtime) to decode audio directly into a NumPy float32 array, removing `av` and `av.libs`. |
| **ChromaDB ONNX Model Bundle** | 80 MB | 0 MB | **80 MB in Flatpak**<br>*(+ 80 MB duplicate copy)* | Static `onnx.tar.gz` removed from Flatpak. ChromaDB downloads and caches `all-MiniLM-L6-v2` into `~/.cache/chroma` on first launch with a minimal splash screen notification. |
| **Non-Latin Piper Models & App Assets** | 44 MB | 0 MB | **44 MB** | Pruned `piper/hebrew` (21 MB), `piper/tashkeel` (4.7 MB), `piper/train` (0.4 MB), `/app/src/assets/screenshots` (3.4 MB), unused `Inter-*.ttf` fonts (1.6 MB), and `whatsapp_node/node_modules` typing/map files (~13 MB). |
| **Total Reduction** | **~3,550 MB** | **~880 MB** | **~2,665 MB (~2.67 GB saved)** | |

---

## 2. Component-by-Component Implementation Details

### 2.1 Camoufox & Fonts (`src/core/browser_manager.py`)
- **Location**: `src/core/browser_manager.py`
- **Behavior**:
  - `BrowserManager` enforces `os="linux"` when launching `AsyncCamoufox`. On Linux hosts, authentic Linux fingerprints avoid anti-bot OS/TCP-stack mismatches.
  - If Camoufox is not yet installed, `BrowserManager` emits a one-time message `[System: Downloading stealth browser engine (one-time setup)...]` to the conversation log, sets `effective_timeout = 300s`, downloads Camoufox via `camoufox.pkgman.camoufox_path()`, and immediately deletes `fonts/macos` and `fonts/windows` from the extracted directory.
  - A subtle note `[System Note: One-time browser engine download completed...]` is prepended to the tool output returned to the agent.

### 2.2 PySide6 & `io.qt.PySide.BaseApp` Cleanup (`compile/com.openamity.OpenAmity.json`)
- **Location**: `compile/com.openamity.OpenAmity.json` (`cleanup-commands`)
- **Behavior**:
  - `BASEAPP_REMOVE_WEBENGINE=1 /app/cleanup-BaseApp.sh` removes `QtWebEngineCore`, `QtPdf`, `QtWebView`, `qtwebengine_dictionaries`, and associated locales.
  - `rm -rf /app/lib/libLLVM* /app/lib/libclang*` removes LLVM/Clang build artifacts.
  - `rm -rf /app/lib/python*/site-packages/OpenGL*` removes PyOpenGL.
  - We remove unused PySide6 modules: `Qt3D*`, `QtCharts*`, `QtGraphs*`, `QtQuick*`, `QtQml*`, `QtDesigner*`, `QtHelp*`, `QtSensors*`, `QtSerial*`, `QtBluetooth*`, `QtNfc*`, `QtPositioning*`, `QtLocation*`, `QtRemoteObjects*`, `QtScxml*`, `QtStateMachine*`, `QtTest*`, `QtUiTools*`, `QtWeb*`, `QtPdf*`, `QtOpenGL*`, `qmlls`, `qmlformat`, `assistant`, `lupdate`, `lrelease`, `*.pyi`.
  - **Preserved**: `QtCore`, `QtGui`, `QtWidgets`, `QtMultimedia`, and `QtSvg` (required for Open Amity GUI and SVG icons).

### 2.3 Puppeteer Chromium for WhatsApp (`src/core/whatsapp_daemon.py`)
- **Location**: `src/core/whatsapp_daemon.py`
- **Behavior**:
  - `WhatsAppDaemon.start()` checks `/app/share/puppeteer` and `~/.cache/puppeteer`.
  - If missing, it downloads `chrome-linux64.zip` (pinned version 153.0.8010.36, SHA-256 verified) asynchronously on a background thread so the GUI never freezes.
  - Immediately deletes `WidevineCdm/` and all `locales/*.pak` except `en-US.pak`.
  - Emits `[System: Downloading browser engine for WhatsApp bridge (one-time setup)...]` and `[System: WhatsApp bridge ready.]` to the conversation log.
  - If an agent invokes a `WhatsApp_*` command while the download is active, it receives an informative status notice asking it to wait a moment.

### 2.4 Node.js & Playwright Driver Deduplication
- **Location**: `compile/com.openamity.OpenAmity.json`
- **Behavior**:
  - Strips debug symbols: `strip --strip-unneeded /app/bin/node`.
  - Symlinks Playwright's bundled node binary:
    `ln -sf /app/bin/node /app/lib/python*/site-packages/playwright/driver/node`
  - Removes build-time `npm` and headers after `npm ci`:
    `rm -rf /app/lib/node_modules/npm /app/bin/npm /app/bin/npx /app/include/node`

### 2.5 FFmpeg Audio Decoding in LocalSTT (`src/core/local_stt.py`)
- **Location**: `src/core/local_stt.py`
- **Behavior**:
  - `LocalSTT.transcribe()` decodes input audio using `/usr/bin/ffmpeg` (`-f s16le -ac 1 -acodec pcm_s16le -ar 16000 -`) into a 16kHz mono `np.float32` array and passes the array directly to `WhisperModel.transcribe(audio_array)`.
  - Because `faster-whisper` only uses PyAV when a file path string is passed, passing a NumPy array bypasses `import av` entirely.
  - `site-packages/av*` and `site-packages/av.libs*` (104 MB) are removed from the Flatpak.

---

## 3. Developer Audit Playbook: How to Audit Future Builds

When updating `requirements.txt`, `package.json`, or the Flatpak runtime version, run this audit to ensure dependencies haven't reintroduced bloat.

### 3.1 Build & Inspect Installed Size
```bash
# 1. Build and install locally
./compile/build_flatpak.sh

# 2. Check total installed size (target: <= 900 MB)
flatpak info com.openamity.OpenAmity

# 3. Inspect top directories in /app
FLATPAK_FILES="$HOME/.local/share/flatpak/app/com.openamity.OpenAmity/current/active/files"
du -sh "$FLATPAK_FILES"/* | sort -hr
```

### 3.2 Audit Top 20 Python Site-Packages
```bash
du -sh "$FLATPAK_FILES"/lib/python*/site-packages/* | sort -hr | head -n 25
```
**Red Flags to Watch For**:
- `kubernetes`: Should be **0 MB**. If present, ensure `rm -rf .../site-packages/kubernetes*` is in `cleanup-commands`.
- `google/antigravity`: Should be **0 MB**. Filtered out in `sync_requirements.py`.
- `av` / `av.libs`: Should be **0 MB**.
- `OpenGL`: Should be **0 MB**.
- `playwright/driver/node`: Verify it is a **symlink** to `/app/bin/node`, not an actual 118 MB binary.

### 3.3 Audit `/app/share` and `/app/bin`
```bash
du -sh "$FLATPAK_FILES"/share/* | sort -hr
du -sh "$FLATPAK_FILES"/bin/* | sort -hr
```
**Red Flags to Watch For**:
- `/app/share/camoufox`: Should be absent from the static image (fetched on demand).
- `/app/share/puppeteer`: Should be absent from the static image (fetched on demand).
- `/app/bin/node`: Should be ~75–80 MB (stripped), not >100 MB.
