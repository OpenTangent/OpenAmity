import os
import subprocess
import time
import requests
import logging
import threading
import json
import shutil
import stat
import psutil
import socket
from config import paths
try:
    from core.settings_manager import SettingsManager
except ImportError:
    from .settings_manager import SettingsManager


class WhatsAppDaemon:
    _chrome_download_lock = threading.Lock()
    _port_lock = threading.Lock()
    _instances = {}

    def __init__(self, port: int = None, agent_id=None):
        self.agent_id = agent_id
        self.source_bridge_dir = os.path.join(
            os.path.dirname(__file__), '..', 'tools', 'whatsapp_node')
        self.bridge_dir = paths.get_whatsapp_bridge_dir(self.agent_id)
        self.data_dir = paths.get_whatsapp_data_dir(self.agent_id)
        self.lock_file = os.path.join(self.bridge_dir, "daemon.pid")
        self.port_file = os.path.join(self.bridge_dir, "daemon.port")
        self.node_process = None
        self.message_callback = None
        self.status_callback = None
        self._is_downloading_chrome = False
        self._stopping = False

        if port is not None:
            self.port = port
        else:
            self.port = self._resolve_port()
        self.base_url = f"http://localhost:{self.port}"
        WhatsAppDaemon._instances[self.agent_id or "default"] = self

    @classmethod
    def get_instance(cls, agent_id: str = None):
        return cls._instances.get(agent_id or "default")

    @classmethod
    def get_port_for_agent(cls, agent_id: str = None) -> int:
        if not agent_id:
            return 3000
        inst = cls._instances.get(agent_id)
        if inst is not None and getattr(inst, "port", None):
            return inst.port
        try:
            bridge_dir = paths.get_whatsapp_bridge_dir(agent_id)
            port_file = os.path.join(bridge_dir, "daemon.port")
            if os.path.exists(port_file):
                with open(port_file, "r") as f:
                    val = f.read().strip()
                    if val.isdigit():
                        return int(val)
        except Exception:
            pass
        # Avoid returning another agent's allocated port when this agent has not written daemon.port yet
        try:
            allocated = set()
            agents_dir = os.path.join(paths.get_app_data_dir(), "agents")
            if os.path.exists(agents_dir):
                for aid in os.listdir(agents_dir):
                    if aid != agent_id:
                        pf = os.path.join(agents_dir, aid, "whatsapp_bridge", "daemon.port")
                        if os.path.exists(pf):
                            try:
                                with open(pf, "r") as f:
                                    v = f.read().strip()
                                    if v.isdigit():
                                        allocated.add(int(v))
                            except Exception:
                                pass
            for candidate in range(3000, 3100):
                if candidate not in allocated:
                    return candidate
        except Exception:
            pass
        return 3000

    @staticmethod
    def _is_port_in_use(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                return True
        try:
            with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as s6:
                s6.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    s6.bind(("::1", port))
                except OSError:
                    return True
        except Exception:
            pass
        return False

    def _get_allocated_ports_for_other_agents(self) -> set:
        allocated = set()
        try:
            agents_dir = os.path.join(paths.get_app_data_dir(), "agents")
            if os.path.exists(agents_dir):
                for aid in os.listdir(agents_dir):
                    if aid != self.agent_id:
                        port_file = os.path.join(
                            agents_dir, aid, "whatsapp_bridge", "daemon.port")
                        if os.path.exists(port_file):
                            try:
                                with open(port_file, "r") as f:
                                    val = f.read().strip()
                                    if val.isdigit():
                                        allocated.add(int(val))
                            except Exception:
                                pass
        except Exception:
            pass
        return allocated

    def _save_port_file(self, port: int):
        try:
            os.makedirs(self.bridge_dir, exist_ok=True)
            with open(self.port_file, "w") as f:
                f.write(str(port))
        except Exception as e:
            logging.debug(f"Error saving daemon port to file: {e}")

    def _resolve_port(self) -> int:
        if not self.agent_id:
            return 3000

        with WhatsAppDaemon._port_lock:
            other_ports = self._get_allocated_ports_for_other_agents()

            # Check if this agent already has a saved port
            if os.path.exists(self.port_file):
                try:
                    with open(self.port_file, "r") as f:
                        val = f.read().strip()
                        if val.isdigit():
                            p = int(val)
                            if p not in other_ports:
                                # If port is not in use, it is safe to reuse
                                if not self._is_port_in_use(p):
                                    return p
                                # If port is in use, check if it is held by our own previous daemon process
                                if os.path.exists(self.lock_file):
                                    try:
                                        with open(self.lock_file, "r") as lf:
                                            pid = int(lf.read().strip())
                                        if psutil.pid_exists(pid):
                                            proc = psutil.Process(pid)
                                            if "node" in proc.name().lower():
                                                # Our own process is holding the port and will be killed on start
                                                return p
                                    except Exception:
                                        pass
                except Exception as e:
                    logging.debug(f"Error reading existing daemon port: {e}")

            # Otherwise find the next free port starting from 3000
            for candidate in range(3000, 3100):
                if candidate not in other_ports and not self._is_port_in_use(candidate):
                    self._save_port_file(candidate)
                    return candidate

            return 3000

    def update_engine_asset(self, force: bool = False) -> bool:
        """Downloads the latest wppconnect-wa.js asset from GitHub releases without running npm."""
        settings = SettingsManager(agent_id=self.agent_id)
        if not force and not settings.get("core.whatsapp-auto-update-engine", True):
            return False

        update_timestamp_file = os.path.join(self.data_dir, ".last_engine_update")
        update_interval_seconds = 24 * 60 * 60  # 24 hours

        if not force and os.path.exists(update_timestamp_file):
            try:
                with open(update_timestamp_file, "r") as f:
                    last_update = float(f.read().strip())
                if time.time() - last_update < update_interval_seconds:
                    logging.info("Skipping WhatsApp engine update (rate limited).")
                    return False
            except Exception as e:
                logging.debug(f"Error reading last engine update timestamp: {e}")

        logging.info("Checking for updated WPPConnect WA-JS engine...")
        url = "https://github.com/wppconnect-team/wa-js/releases/latest/download/wppconnect-wa.js"
        try:
            res = requests.get(url, timeout=30)
            if res.status_code == 200 and len(res.content) > 50000:
                dst = os.path.join(self.data_dir, "wppconnect-wa.js")
                with open(dst, "wb") as f:
                    f.write(res.content)
                with open(update_timestamp_file, "w") as f:
                    f.write(str(time.time()))
                logging.info("Successfully updated WPPConnect WA-JS engine asset.")
                return True
            else:
                logging.warning(f"Failed to fetch WA-JS release: HTTP {res.status_code}")
        except Exception as e:
            logging.warning(f"Unable to download latest WA-JS release: {e}")

        return False

    def _get_chrome_search_dirs(self) -> list:
        dirs = [
            os.path.join(self.data_dir, "puppeteer_cache"),
            os.path.join(paths.get_app_cache_dir(), "puppeteer"),
            os.path.expanduser("~/.cache/puppeteer"),
            "/app/share/puppeteer",
        ]
        unique_dirs = []
        for d in dirs:
            if d and d not in unique_dirs:
                unique_dirs.append(d)
        return unique_dirs

    def _ensure_chrome_executable_permissions(self, extra_dir: str = None):
        """Ensures chrome, companion binaries (e.g. chrome_crashpad_handler), and shared libraries in puppeteer cache have executable permissions."""
        target_dirs = self._get_chrome_search_dirs()
        if extra_dir and extra_dir not in target_dirs:
            target_dirs.insert(0, extra_dir)
        helper_names = {
            "chrome",
            "chrome_crashpad_handler",
            "crashpad_handler",
            "chrome_sandbox",
            "chrome-wrapper",
            "chromedriver",
            "chrome_management_service",
            "interactive_ui_tests",
            "xdg-mime",
            "xdg-settings",
        }
        exec_bits = (
            stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH |
            stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH
        )
        for base_dir in target_dirs:
            if not os.path.exists(base_dir):
                continue
            try:
                for root, dirs, files in os.walk(base_dir):
                    dirs[:] = [d for d in dirs if not d.startswith(".chrome-staging")]
                    for fname in files:
                        is_helper = (
                            fname in helper_names
                            or "crashpad" in fname.lower()
                            or fname.startswith("chrome")
                            or fname.endswith(".so")
                            or ".so." in fname
                        )
                        if is_helper:
                            fpath = os.path.join(root, fname)
                            try:
                                current_mode = os.stat(fpath).st_mode
                                os.chmod(fpath, current_mode | exec_bits)
                            except Exception as e:
                                logging.debug(f"Could not chmod {fpath}: {e}")
            except Exception as e:
                logging.debug(f"Error scanning {base_dir} for permissions: {e}")

    @property
    def is_downloading_chrome(self) -> bool:
        return getattr(self, "_is_downloading_chrome", False)

    def _find_chrome_executable(self) -> str | None:
        for bdir in self._get_chrome_search_dirs():
            if not os.path.exists(bdir):
                continue
            matches = []
            for root, dirs, files in os.walk(bdir):
                dirs[:] = [d for d in dirs if not d.startswith(".chrome-staging")]
                for f in files:
                    if f in ("chrome", "chromium", "google-chrome"):
                        fpath = os.path.join(root, f)
                        try:
                            if not os.path.isfile(fpath):
                                continue
                            fsize = os.path.getsize(fpath)
                            if fsize <= 0:
                                continue
                            # For real Chrome for Testing installations (>1MB), verify companion files exist
                            # so an interrupted extraction is never mistaken for a complete installation.
                            if fsize > 1024 * 1024 and os.path.basename(root) == "chrome-linux64":
                                if not (
                                    os.path.exists(os.path.join(root, "icudtl.dat"))
                                    and os.path.exists(os.path.join(root, "chrome_crashpad_handler"))
                                ):
                                    continue
                            if not os.access(fpath, os.X_OK):
                                self._ensure_chrome_executable_permissions(extra_dir=root)
                            if os.access(fpath, os.X_OK):
                                matches.append(fpath)
                        except Exception:
                            continue
            if matches:
                matches.sort(reverse=True)
                return matches[0]
        return None

    def _find_chrome(self) -> bool:
        return self._find_chrome_executable() is not None

    def _download_and_extract_chrome(self) -> bool:
        """Downloads Chrome for Testing on demand into XDG cache / ~/.cache/puppeteer, verified by SHA-256."""
        import zipfile
        import hashlib
        try:
            import fcntl
        except ImportError:
            fcntl = None

        url = "https://storage.googleapis.com/chrome-for-testing-public/153.0.8010.36/linux64/chrome-linux64.zip"
        expected_sha = "167a098c4fdec156b58a9f678c90a84f9072d789f9c6e7b35496a6987b8b7ef8"

        cache_dir = os.path.join(paths.get_app_cache_dir(), "puppeteer")
        os.makedirs(cache_dir, exist_ok=True)
        lock_file_path = os.path.join(cache_dir, ".chrome_download.lock")

        with WhatsAppDaemon._chrome_download_lock:
            lock_fd = None
            try:
                if fcntl is not None:
                    lock_fd = open(lock_file_path, "w")
                    fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)

                # Re-check inside the lock: if another agent thread already finished downloading
                # and extracting Chrome while we waited, reuse it immediately!
                if self._find_chrome():
                    logging.info("Chrome for Testing already available (downloaded by peer agent).")
                    return True

                zip_path = os.path.join(
                    cache_dir,
                    f"chrome-linux64.{os.getpid()}.{threading.get_ident()}.zip.tmp"
                )
                staging_dir = os.path.join(
                    cache_dir,
                    f".chrome-staging-{os.getpid()}-{threading.get_ident()}"
                )

                logging.info("Downloading Chrome for WhatsApp bridge (one-time setup, ~150 MB)...")
                if self.status_callback:
                    try:
                        self.status_callback("[System: Downloading browser engine for WhatsApp bridge (one-time setup)...]")
                    except Exception:
                        pass

                try:
                    with requests.get(url, stream=True, timeout=60) as r:
                        r.raise_for_status()
                        sha = hashlib.sha256()
                        with open(zip_path, "wb") as f:
                            for chunk in r.iter_content(chunk_size=65536):
                                f.write(chunk)
                                sha.update(chunk)
                    if sha.hexdigest() != expected_sha:
                        logging.error(f"Chrome download checksum mismatch: {sha.hexdigest()} != {expected_sha}")
                        if os.path.exists(zip_path):
                            os.remove(zip_path)
                        return False

                    if os.path.exists(staging_dir):
                        shutil.rmtree(staging_dir, ignore_errors=True)
                    os.makedirs(staging_dir, exist_ok=True)

                    logging.info("Extracting Chrome for Testing into staging directory...")
                    with zipfile.ZipFile(zip_path, "r") as zf:
                        zf.extractall(staging_dir)

                    if os.path.exists(zip_path):
                        os.remove(zip_path)

                    staged_chrome_dir = os.path.join(staging_dir, "chrome-linux64")
                    if not os.path.exists(staged_chrome_dir):
                        staged_chrome_dir = staging_dir

                    # Prune WidevineCdm and non-en-US locales to save 66 MB in user home
                    widevine_dir = os.path.join(staged_chrome_dir, "WidevineCdm")
                    if os.path.exists(widevine_dir):
                        shutil.rmtree(widevine_dir, ignore_errors=True)

                    locales_dir = os.path.join(staged_chrome_dir, "locales")
                    if os.path.exists(locales_dir):
                        for lfile in os.listdir(locales_dir):
                            if lfile != "en-US.pak":
                                try:
                                    os.remove(os.path.join(locales_dir, lfile))
                                except Exception:
                                    pass

                    # Fix executable permissions on all binaries and shared libraries in staging BEFORE moving into place
                    exec_bits = (
                        stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH |
                        stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH
                    )
                    for root, _, files in os.walk(staged_chrome_dir):
                        for fname in files:
                            fpath = os.path.join(root, fname)
                            try:
                                current_mode = os.stat(fpath).st_mode
                                os.chmod(fpath, current_mode | exec_bits)
                            except Exception:
                                pass

                    final_chrome_dir = os.path.join(cache_dir, "chrome-linux64")
                    if staged_chrome_dir != staging_dir:
                        if os.path.exists(final_chrome_dir):
                            shutil.rmtree(final_chrome_dir, ignore_errors=True)
                        os.replace(staged_chrome_dir, final_chrome_dir)
                        shutil.rmtree(staging_dir, ignore_errors=True)
                    else:
                        # Fallback if archive extracted directly without top-level chrome-linux64 folder
                        for item in os.listdir(staging_dir):
                            src_item = os.path.join(staging_dir, item)
                            dst_item = os.path.join(cache_dir, item)
                            if os.path.exists(dst_item):
                                if os.path.isdir(dst_item):
                                    shutil.rmtree(dst_item, ignore_errors=True)
                                else:
                                    os.remove(dst_item)
                            os.replace(src_item, dst_item)
                        shutil.rmtree(staging_dir, ignore_errors=True)

                    self._ensure_chrome_executable_permissions()
                    logging.info("Chrome for Testing extracted and verified successfully.")
                    return True
                except Exception as e:
                    logging.error(f"Failed to download Chrome for WhatsApp: {e}")
                    if os.path.exists(zip_path):
                        try:
                            os.remove(zip_path)
                        except Exception:
                            pass
                    if os.path.exists(staging_dir):
                        shutil.rmtree(staging_dir, ignore_errors=True)
                    return False
            finally:
                if lock_fd is not None:
                    try:
                        if fcntl is not None:
                            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
                        lock_fd.close()
                    except Exception:
                        pass

    def start(self, force_update: bool = False):
        if not self._find_chrome():
            self._is_downloading_chrome = True

            def download_and_run():
                try:
                    success = self._download_and_extract_chrome()
                    if success:
                        if self.status_callback:
                            try:
                                self.status_callback("[System: WhatsApp bridge ready.]")
                            except Exception:
                                pass
                        self._do_start(force_update=force_update)
                    else:
                        if self.status_callback:
                            try:
                                self.status_callback("[System: Failed to download browser engine for WhatsApp.]")
                            except Exception:
                                pass
                finally:
                    self._is_downloading_chrome = False

            threading.Thread(
                target=download_and_run,
                daemon=True,
                name=f"WhatsAppChromeDL-{self.agent_id}"
            ).start()
            return

        self._do_start(force_update=force_update)

    def _do_start(self, force_update: bool = False):
        os.makedirs(self.bridge_dir, exist_ok=True)
        os.makedirs(self.data_dir, exist_ok=True)

        # Always overwrite server.js and package.json from the immutable app to the stateful dir
        for filename in ["server.js", "package.json"]:
            src_file = os.path.join(self.source_bridge_dir, filename)
            dst_file = os.path.join(self.bridge_dir, filename)
            if os.path.exists(src_file):
                shutil.copy(src_file, dst_file)
                os.chmod(dst_file, stat.S_IRUSR | stat.S_IWUSR |
                         stat.S_IRGRP | stat.S_IROTH)

        if force_update:
            self.update_engine_asset(force=True)

        self._ensure_chrome_executable_permissions()

        if not os.path.exists(os.path.join(self.bridge_dir, "server.js")):
            logging.error("WhatsApp node server not found.")
            return

        self._kill_orphaned_daemon()
        self._save_port_file(self.port)

        logging.info(f"Starting WhatsApp Node bridge on port {self.port}...")
        env = os.environ.copy()
        env["PORT"] = str(self.port)
        env["WHATSAPP_PORT"] = str(self.port)
        env["WHATSAPP_DATA_DIR"] = self.data_dir

        chrome_exe = self._find_chrome_executable()
        if chrome_exe:
            env["CHROME_EXECUTABLE_PATH"] = chrome_exe
            env["PUPPETEER_EXECUTABLE_PATH"] = chrome_exe
            env["PUPPETEER_CACHE_DIR"] = os.path.dirname(os.path.dirname(chrome_exe))
        else:
            env["PUPPETEER_CACHE_DIR"] = os.path.join(paths.get_app_cache_dir(), "puppeteer")

        # Configure NODE_PATH so node can resolve modules from source, bridge, and Flatpak app dirs
        source_modules = os.path.join(self.source_bridge_dir, "node_modules")
        bridge_modules = os.path.join(self.bridge_dir, "node_modules")
        flatpak_source_modules = "/app/src/tools/whatsapp_node/node_modules"
        flatpak_lib_modules = "/app/lib/node_modules"
        existing_node_path = env.get("NODE_PATH", "")
        paths_to_add = [p for p in [source_modules, bridge_modules, flatpak_source_modules, flatpak_lib_modules, existing_node_path] if p and os.path.exists(p)]
        if not paths_to_add:
            paths_to_add = [source_modules, bridge_modules]
        env["NODE_PATH"] = ":".join(paths_to_add)

        self.node_process = subprocess.Popen(
            ["node", "server.js"],
            cwd=self.bridge_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env
        )

        with open(self.lock_file, "w") as f:
            f.write(str(self.node_process.pid))

        def read_output(pipe, label):
            for line in iter(pipe.readline, ''):
                if line:
                    line_str = line.strip()
                    logging.debug(f"[{label}:{self.port}] {line_str}")
                    self._parse_message_line(line_str)
            pipe.close()

        threading.Thread(target=read_output, args=(
            self.node_process.stdout, "WhatsApp-JS"), daemon=True).start()
        threading.Thread(target=read_output, args=(
            self.node_process.stderr, "WhatsApp-JS-ERR"), daemon=True).start()

        self._stopping = False

        def monitor_process(proc):
            proc.wait()
            if not self._stopping and self.node_process == proc:
                logging.error(
                    f"WhatsApp Node process (port {self.port}) crashed with return code {proc.returncode}. Restarting in 5s...")
                time.sleep(5)
                if not self._stopping:
                    self.restart()

        threading.Thread(target=monitor_process, args=(
            self.node_process,), daemon=True).start()

        # Wait for server to be ready
        for _ in range(30):
            try:
                res = requests.get(f"{self.base_url}/status", timeout=2)
                if res.status_code == 200:
                    break
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
                pass
            time.sleep(1)

    def _parse_message_line(self, line_str: str):
        """Parses [MSG_RECEIVED_JSON] or legacy [MSG_RECEIVED] output and invokes callback."""
        if not self.message_callback:
            return

        if line_str.startswith("[MSG_RECEIVED_JSON]"):
            json_part = line_str[len("[MSG_RECEIVED_JSON]"):].strip()
            try:
                data = json.loads(json_part)
                sender_id = data.get("chatId", "")
                sender_name = data.get("senderName", "")
                is_group = bool(data.get("isGroup", False))
                group_name = data.get("groupName", "")
                mentioned = bool(data.get("mentioned", False))
                self.message_callback(
                    sender_id,
                    sender_name,
                    is_group=is_group,
                    group_name=group_name,
                    mentioned=mentioned,
                )
            except Exception as e:
                logging.debug(f"WhatsAppDaemon: Error parsing JSON message event: {e}")
        elif line_str.startswith("[MSG_RECEIVED]"):
            parts = line_str.split(" ", 2)
            if len(parts) >= 2:
                sender_id = parts[1]
                sender_name = parts[2] if len(parts) >= 3 else ""
                is_group = sender_id.endswith("@g.us")
                self.message_callback(
                    sender_id,
                    sender_name,
                    is_group=is_group,
                    group_name="",
                    mentioned=False,
                )

    def _kill_orphaned_daemon(self):
        # Attempt to kill old daemon by PID
        if os.path.exists(self.lock_file):
            try:
                with open(self.lock_file, "r") as f:
                    pid = int(f.read().strip())
                if psutil.pid_exists(pid):
                    process = psutil.Process(pid)
                    if "node" in process.name().lower():
                        logging.info(
                            f"Killing orphaned WhatsApp daemon (PID: {pid})...")

                        children = []
                        try:
                            children = process.children(recursive=True)
                        except psutil.NoSuchProcess:
                            pass

                        # Try graceful shutdown
                        try:
                            requests.post(
                                f"{self.base_url}/shutdown", timeout=2)
                            process.wait(timeout=3)
                        except Exception:
                            pass

                        if process.is_running():
                            try:
                                process.terminate()
                                process.wait(timeout=3)
                            except (psutil.NoSuchProcess, psutil.TimeoutExpired):
                                pass

                        if process.is_running():
                            try:
                                process.kill()
                            except psutil.NoSuchProcess:
                                pass

                        for child in children:
                            try:
                                if child.is_running():
                                    child.kill()
                            except psutil.NoSuchProcess:
                                pass
            except Exception as e:
                logging.warning(f"Failed to kill orphaned daemon via PID: {e}")
            finally:
                try:
                    os.remove(self.lock_file)
                except Exception:
                    pass

        # Also clean up any reparented/orphaned Chromium processes holding this agent's .wpp_session
        session_dir_marker = os.path.join(self.data_dir, ".wpp_session")
        try:
            for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                try:
                    cmdline = proc.info.get("cmdline") or []
                    if any(session_dir_marker in arg for arg in cmdline):
                        proc.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception:
            pass

        # Force release the Chromium user data dir locks.
        # Note: On Linux, SingletonLock/SingletonCookie/SingletonSocket are dangling symlinks,
        # so os.path.exists() returns False! Use os.path.lexists() to detect and unlink them.
        for session_subdir in [
            os.path.join(self.data_dir, ".wpp_session"),
            os.path.join(self.data_dir, ".wwebjs_auth", "session"),
        ]:
            for lock_name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
                lock_path = os.path.join(session_subdir, lock_name)
                if os.path.lexists(lock_path):
                    try:
                        os.unlink(lock_path)
                    except Exception:
                        pass

    def restart(self):
        self.stop()
        for cache_path in [
            os.path.join(self.data_dir, '.wwebjs_cache')
        ]:
            if os.path.exists(cache_path):
                shutil.rmtree(cache_path, ignore_errors=True)

        self.start(force_update=True)

    def stop(self):
        self._stopping = True
        if self.node_process:
            logging.info("Sending shutdown signal to WhatsApp node daemon...")
            try:
                res = requests.post(f"{self.base_url}/shutdown", timeout=2)
                if res.status_code == 200:
                    self.node_process.wait(timeout=5)
            except Exception as e:
                logging.debug(
                    f"Error shutting down WhatsApp daemon gracefully: {e}")

            try:
                if self.node_process.poll() is None:
                    children = []
                    try:
                        parent = psutil.Process(self.node_process.pid)
                        children = parent.children(recursive=True)
                    except Exception:
                        pass

                    self.node_process.terminate()

                    try:
                        self.node_process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        self.node_process.kill()

                    for child in children:
                        try:
                            if child.is_running():
                                child.kill()
                        except Exception:
                            pass
            except Exception:
                pass

            self.node_process = None

            if os.path.exists(self.lock_file):
                try:
                    os.remove(self.lock_file)
                except Exception:
                    pass
