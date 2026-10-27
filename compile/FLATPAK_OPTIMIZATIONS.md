# Open Amity Flatpak Size Optimizations & Developer Playbook

This document defines the Flatpak optimization architecture for Open Amity (`com.openamity.OpenAmity`). It details all applied size reductions, explains why redundant components existed, and provides an audit checklist so future developers can maintain and build upon these optimizations when updating dependencies.

---

## 1. Summary of Optimizations (~3.55 GB → ~880 MB Installed)

The unoptimized baseline Flatpak weighed **~3,550 MB (~3.55 GB)** installed. Through the Tier 1 (zero-sacrifice) and Tier 2 (on-demand + runtime decoding) optimizations below, the installed footprint is reduced to **~880 MB** (a **~75.2% reduction**, saving **~2.67 GB**).

| Component / Subsystem | Unoptimized | Optimized | Saved | Mechanism & Reason |
| :--- | :---: | :---: | :---: | :--- |
| **Camoufox Browser & Fonts** | 1,200 MB | 0 MB (in Flatpak)<br>~310 MB (in user cache) | **1,200 MB in Flatpak**<br>*(+ 891 MB in user cache)* | Camoufox bundle contained 933 MB of fonts (`fonts/macos` 569 MB, `fonts/windows` 322 MB) for spoofing non-Linux User-Agents. We pinned `os="linux"` in `BrowserManager`, moved Camoufox to an on-demand download via `CamoufoxFetcher`, and automatically prune `fonts/macos` and `fonts/windows` after extraction. |
| **`io.qt.PySide.BaseApp` Runtime Cleanup** | 522 MB | 0 MB | **522 MB** | BaseApp bundled unneeded `QtWebEngine` (200 MB), `translations` (44 MB), `PyOpenGL` (31 MB), `libexec/webenginedriver` (23 MB), and unused Qt 3D/Charts/Graphs/Quick bindings and `.pyi` stubs. Pruned via `BASEAPP_REMOVE_WEBENGINE=1 /app/cleanup-BaseApp.sh` and explicit cleanup commands. |
| **Puppeteer Chromium** | 350 MB | 0 MB (in Flatpak)<br>~285 MB (in user cache) | **350 MB in Flatpak**<br>*(+ 66 MB in user cache)* | Full Chrome for Testing was bundled statically for WhatsApp. Since WhatsApp is disabled by default, Chrome is now downloaded on demand asynchronously into `~/.cache/puppeteer` only when WhatsApp is enabled, and stripped of `WidevineCdm/` (18 MB) and 55+ unused locales (48 MB). |
| **Playwright Node.js Deduplication** | 168 MB | 0 MB | **168 MB** | The `playwright` Python wheel bundled its own duplicate 118 MB `node` binary at `site-packages/playwright/driver/node`. We use Flathub's `org.freedesktop.Sdk.Extension.node24` (`/usr/lib/sdk/node24/install.sh`) which builds a stripped `node` binary linked against runtime OpenSSL/zlib/ICU, omits `npm` and C++ headers from `/app`, and symlinks `playwright/driver/node` to `/app/bin/node`. |
| **Unused `google-antigravity` & `kubernetes`** | 197 MB | 0 MB | **197 MB** | `google-antigravity` (115 MB) was never imported by `src/` (AgyWorker calls the host `agy` CLI). `kubernetes` (82 MB) was an optional distributed cluster dependency in ChromaDB never used by local `PersistentClient`. Both removed. |
| **`PyAV` (`av` + `av.libs`) Audio Decoding** | 104 MB | 0 MB | **104 MB** | `faster-whisper` pulled in `PyAV` (104 MB) including video encoders (`libx265`, `libx264`, `libvpx`, `libSvtAv1Enc`). We replaced PyAV with `/usr/bin/ffmpeg` (already in the KDE Platform runtime) to decode audio directly into a NumPy float32 array, stubbed `sys.modules["av"]` in `LocalSTT` so `faster_whisper.audio` imports cleanly, and removed `av` and `av.libs`. |
| **ChromaDB ONNX Model Bundle** | 80 MB | 0 MB | **80 MB in Flatpak**<br>*(+ 80 MB duplicate copy)* | Static `onnx.tar.gz` removed from Flatpak. `InitWorker` downloads, verifies (SHA-256), and extracts `all-MiniLM-L6-v2` into persistent XDG cache (`~/.cache/chroma` symlinked to `$XDG_CACHE_HOME`) on first launch with live splash screen progress. |
| **Non-Latin Piper Models & App Assets** | 44 MB | 0 MB | **44 MB** | Pruned `piper/hebrew/*.onnx` (21 MB) and `piper/tashkeel/*.onnx` (4.6 MB) while retaining their Python `__init__.py` modules so `piper.voice` imports cleanly, plus `piper/train` (0.4 MB), `/app/src/assets/screenshots` (3.4 MB), unused `Inter-*.ttf` fonts (1.6 MB), and `whatsapp_node/node_modules` typing/map files (~13 MB). |
| **Total Reduction** | **~3,550 MB** | **~880 MB** | **~2,665 MB (~2.67 GB saved)** | |

---

## 2. Component-by-Component Implementation Details

### 2.1 Camoufox & Fonts (`src/core/browser_manager.py`)
- **Location**: `src/core/browser_manager.py`
- **Behavior**:
  - `BrowserManager` enforces `os="linux"` when launching `AsyncCamoufox`. On Linux hosts, authentic Linux fingerprints avoid anti-bot OS/TCP-stack mismatches.
  - If Camoufox is not yet installed, `BrowserManager` emits a one-time message `[System: Downloading stealth browser engine (one-time setup)...]` to the conversation log, sets `effective_timeout = page_timeout + 300s`, downloads Camoufox via `camoufox.pkgman.camoufox_path()`, and immediately deletes `fonts/macos` and `fonts/windows` from the extracted directory.

### 2.2 PySide6 & `io.qt.PySide.BaseApp` Cleanup (`compile/com.openamity.OpenAmity.json`)
- **Location**: `compile/com.openamity.OpenAmity.json` (`post-install`)
- **Behavior**:
  - `BASEAPP_REMOVE_WEBENGINE=1 /app/cleanup-BaseApp.sh` removes `QtWebEngineCore`, `QtPdf`, `QtWebView`, `qtwebengine_dictionaries`, and associated locales.
  - `rm -rf /app/lib/python*/site-packages/OpenGL*` removes PyOpenGL.
  - We remove unused PySide6 modules: `Qt3D*`, `QtQuick*`, `QtQml*`, `QtPdf*`, `QtSensors*`, `QtPositioning*`.
  - **Preserved**: `QtCore`, `QtGui`, `QtWidgets`, `QtMultimedia`, and `QtSvg` (required for Open Amity GUI and SVG icons).

### 2.3 Puppeteer Chromium for WhatsApp (`src/core/whatsapp_daemon.py`)
- **Location**: `src/core/whatsapp_daemon.py`
- **Behavior**:
  - `WhatsAppDaemon.start()` checks `/app/share/puppeteer`, `$XDG_CACHE_HOME/puppeteer`, and `~/.cache/puppeteer`.
  - If missing, it downloads `chrome-linux64.zip` (pinned version 153.0.8010.36, SHA-256 verified) asynchronously on a background thread into `paths.get_app_cache_dir() / "puppeteer"` so the GUI never freezes.
  - Immediately deletes `WidevineCdm/` and all `locales/*.pak` except `en-US.pak`.
  - Emits `[System: Downloading browser engine for WhatsApp bridge (one-time setup)...]` and `[System: WhatsApp bridge ready.]` to the conversation log.
  - If an agent invokes a `WhatsApp_*` command while the download is active, it receives an informative status notice asking it to wait a moment.

### 2.4 Node.js & Playwright Driver Deduplication
- **Location**: `compile/com.openamity.OpenAmity.json`
- **Behavior**:
  - Uses `org.freedesktop.Sdk.Extension.node24` (`/usr/lib/sdk/node24/install.sh` + `ln -sf /app/node/bin/node /app/bin/node`) which provides a stripped, shared-library-linked `node` binary for both `x86_64` and `aarch64` without bundling `npm` or C++ headers into `/app`.
  - Symlinks Playwright's bundled node binary:
    `ln -sf /app/bin/node /app/lib/python3.*/site-packages/playwright/driver/node`

### 2.5 FFmpeg Audio Decoding & PyAV Stub in LocalSTT (`src/core/local_stt.py`)
- **Location**: `src/core/local_stt.py`
- **Behavior**:
  - `LocalSTT.transcribe()` decodes input audio using `/usr/bin/ffmpeg` (`-f s16le -ac 1 -acodec pcm_s16le -ar 16000 -`) into a 16kHz mono `np.float32` array and passes the array directly to `WhisperModel.transcribe(audio_array)`.
  - Because `faster_whisper.audio` still executes `import av` at the top of the module on import, `_ensure_faster_whisper_av_stub()` registers a lightweight `types.ModuleType("av")` stub in `sys.modules["av"]` when `PyAV` is absent so `from faster_whisper import WhisperModel` succeeds without `av` installed.
  - `site-packages/av*` and `site-packages/av.libs*` (104 MB) are removed from the Flatpak.

### 2.6 Flatpak Persistent Cache Symlinks & ChromaDB ONNX Setup (`run_OpenAmity.sh`, `src/config/paths.py`, `src/main.py`)
- **Locations**: `run_OpenAmity.sh`, `src/config/paths.py`, `src/main.py`
- **Behavior**:
  - Because Flatpak mounts `$HOME` (`/home/$USER`) as an ephemeral `tmpfs` when `--filesystem=home` is omitted, `run_OpenAmity.sh` and `paths.ensure_flatpak_persistence_links()` symlink `~/.cache -> $XDG_CACHE_HOME` and `~/.local/share -> $XDG_DATA_HOME`. This ensures libraries that hardcode `Path.home() / ".cache"` (such as ChromaDB's `ONNXMiniLM_L6_V2`) store downloaded models on persistent disk rather than ephemeral RAM.
  - On first launch, `_ensure_chroma_onnx_model()` in `src/main.py` streams `onnx.tar.gz` in 64 KB chunks with SHA-256 verification and live splash percentage updates, extracts `onnx/`, and removes the temporary archive.

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
