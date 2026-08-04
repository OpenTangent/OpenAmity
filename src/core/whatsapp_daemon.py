import os
import subprocess
import time
import requests
import logging
import threading
import shutil
import stat
import psutil
from config import paths
try:
    from core.settings_manager import SettingsManager
except ImportError:
    from .settings_manager import SettingsManager

class WhatsAppDaemon:
    def __init__(self, port: int = 3000):
        self.port = port
        self.base_url = f"http://localhost:{self.port}"
        self.source_bridge_dir = os.path.join(os.path.dirname(__file__), '..', 'tools', 'whatsapp_node')
        self.bridge_dir = paths.get_whatsapp_bridge_dir()
        self.data_dir = paths.get_whatsapp_data_dir()
        self.lock_file = os.path.join(self.bridge_dir, "daemon.pid")
        self.node_process = None
        self.message_callback = None
        self._stopping = False

    def start(self, force_update: bool = False):
        os.makedirs(self.bridge_dir, exist_ok=True)
        os.makedirs(self.data_dir, exist_ok=True)
        
        # Always overwrite server.js and package.json from the immutable app to the stateful dir
        for filename in ["server.js", "package.json"]:
            src_file = os.path.join(self.source_bridge_dir, filename)
            dst_file = os.path.join(self.bridge_dir, filename)
            if os.path.exists(src_file):
                shutil.copy(src_file, dst_file)
                os.chmod(dst_file, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
        
        # Update dependencies, rate limited to once per 24 hours
        update_timestamp_file = os.path.join(self.data_dir, ".last_update")
        should_update = True
        update_interval_seconds = 24 * 60 * 60 # 24 hours
        
        if not force_update and os.path.exists(update_timestamp_file):
            try:
                with open(update_timestamp_file, "r") as f:
                    last_update = float(f.read().strip())
                if time.time() - last_update < update_interval_seconds:
                    should_update = False
            except Exception as e:
                logging.debug(f"Error reading last update timestamp: {e}")
        
        if should_update:
            settings = SettingsManager()
            target = settings.get("core.whatsapp-web-target", "github:wwebjs/whatsapp-web.js#main")
            logging.info(f"Updating WhatsApp bridge dependencies (target: {target})...")
            env = os.environ.copy()
            env["PUPPETEER_CACHE_DIR"] = os.path.join(self.data_dir, "puppeteer_cache")
            subprocess.run(["npm", "install"], cwd=self.bridge_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
            subprocess.run(["npm", "install", target], cwd=self.bridge_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
            try:
                with open(update_timestamp_file, "w") as f:
                    f.write(str(time.time()))
            except Exception as e:
                logging.debug(f"Error writing last update timestamp: {e}")
        else:
            logging.info("Skipping WhatsApp bridge dependency update (rate limited).")

        if not os.path.exists(os.path.join(self.bridge_dir, "server.js")):
            logging.error("WhatsApp node server not found.")
            return

        self._kill_orphaned_daemon()

        logging.info("Starting WhatsApp Node bridge...")
        env = os.environ.copy()
        env["WHATSAPP_DATA_DIR"] = self.data_dir
        env["PUPPETEER_CACHE_DIR"] = os.path.join(self.data_dir, "puppeteer_cache")
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
                        if len(parts) >= 3:
                            sender_id = parts[1]
                            sender_name = parts[2]
                            if self.message_callback:
                                self.message_callback(sender_id, sender_name)
            pipe.close()

        threading.Thread(target=read_output, args=(self.node_process.stdout, "WhatsApp-JS"), daemon=True).start()
        threading.Thread(target=read_output, args=(self.node_process.stderr, "WhatsApp-JS-ERR"), daemon=True).start()

        self._stopping = False
        def monitor_process(proc):
            proc.wait()
            if not self._stopping and self.node_process == proc:
                logging.error(f"WhatsApp Node process crashed with return code {proc.returncode}. Restarting in 5s...")
                time.sleep(5)
                if not self._stopping:
                    self.restart()
                    
        threading.Thread(target=monitor_process, args=(self.node_process,), daemon=True).start()

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
                        logging.info(f"Killing orphaned WhatsApp daemon (PID: {pid})...")
                        
                        children = []
                        try:
                            children = process.children(recursive=True)
                        except psutil.NoSuchProcess:
                            pass
                            
                        # Try graceful shutdown
                        try:
                            requests.post(f"{self.base_url}/shutdown", timeout=2)
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
        chrom_lock = os.path.join(self.data_dir, '.wwebjs_auth', 'session', 'SingletonLock')
        if os.path.exists(chrom_lock):
            try:
                os.remove(chrom_lock)
            except Exception:
                pass
                
    def restart(self):
        self.stop()
        cache_dir = os.path.join(self.data_dir, '.wwebjs_cache')
        if os.path.exists(cache_dir):
            shutil.rmtree(cache_dir)
        
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
                logging.debug(f"Error shutting down WhatsApp daemon gracefully: {e}")
            
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
