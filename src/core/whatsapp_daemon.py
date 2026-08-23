import os
import subprocess
import time
import requests
import logging
import threading
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
        self._stopping = False

        if port is not None:
            self.port = port
        else:
            self.port = self._resolve_port()
        self.base_url = f"http://localhost:{self.port}"

    @classmethod
    def get_port_for_agent(cls, agent_id: str = None) -> int:
        if not agent_id:
            return 3000
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

    def _resolve_port(self) -> int:
        if not self.agent_id:
            return 3000

        # Check if this agent already has a saved port
        if os.path.exists(self.port_file):
            try:
                with open(self.port_file, "r") as f:
                    val = f.read().strip()
                    if val.isdigit():
                        p = int(val)
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
        other_ports = self._get_allocated_ports_for_other_agents()
        for candidate in range(3000, 3100):
            if candidate not in other_ports and not self._is_port_in_use(candidate):
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

    def _ensure_chrome_executable_permissions(self):
        """Ensures chrome and companion binaries (e.g. chrome_crashpad_handler) in puppeteer cache have executable permissions."""
        target_dirs = [
            os.path.join(self.data_dir, "puppeteer_cache"),
            os.path.expanduser("~/.cache/puppeteer"),
            "/app/share/puppeteer"
        ]
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
            "xdg-settings"
        }
        for base_dir in target_dirs:
            if not os.path.exists(base_dir):
                continue
            try:
                for root, _, files in os.walk(base_dir):
                    for fname in files:
                        if fname in helper_names or "crashpad" in fname.lower() or fname.startswith("chrome"):
                            fpath = os.path.join(root, fname)
                            try:
                                current_mode = os.stat(fpath).st_mode
                                os.chmod(fpath, current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH | stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
                            except Exception as e:
                                logging.debug(f"Could not chmod {fpath}: {e}")
            except Exception as e:
                logging.debug(f"Error scanning {base_dir} for permissions: {e}")

    def start(self, force_update: bool = False):
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

        try:
            with open(self.port_file, "w") as f:
                f.write(str(self.port))
        except Exception as e:
            logging.debug(f"Error saving daemon port to file: {e}")

        logging.info(f"Starting WhatsApp Node bridge on port {self.port}...")
        env = os.environ.copy()
        env["PORT"] = str(self.port)
        env["WHATSAPP_PORT"] = str(self.port)
        env["WHATSAPP_DATA_DIR"] = self.data_dir
        env["PUPPETEER_CACHE_DIR"] = os.path.join(
            self.data_dir, "puppeteer_cache")

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
                    logging.debug(f"[{label}] {line_str}")
                    if line_str.startswith("[MSG_RECEIVED]"):
                        parts = line_str.split(" ", 2)
                        if len(parts) >= 2:
                            sender_id = parts[1]
                            sender_name = parts[2] if len(parts) >= 3 else ""
                            if self.message_callback:
                                self.message_callback(sender_id, sender_name)
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
                    f"WhatsApp Node process crashed with return code {proc.returncode}. Restarting in 5s...")
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

        # Force release the Chromium user data dir lock
        for lock_path in [
            os.path.join(self.data_dir, '.wpp_session', 'SingletonLock'),
            os.path.join(self.data_dir, '.wwebjs_auth', 'session', 'SingletonLock')
        ]:
            if os.path.exists(lock_path):
                try:
                    os.remove(lock_path)
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
