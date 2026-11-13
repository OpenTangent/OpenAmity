#!/usr/bin/env python3
"""
Open Amity Flathub Migration Installer
Seamlessly migrates GitHub Pages Flatpak installations of Open Amity to the official Flathub release.
"""
import os
import subprocess
import sys
from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

FLATHUB_REPO_URL = "https://dl.flathub.org/repo/flathub.flatpakrepo"
APP_ID = "com.openamity.OpenAmity"
STABLE_REF = f"{APP_ID}//stable"
MASTER_REF = f"{APP_ID}//master"
LEGACY_REMOTE = "openamity"


def build_host_cmd(cmd_list):
    """Wrap command with flatpak-spawn --host when executing inside a Flatpak sandbox."""
    if os.environ.get("FLATPAK_ID"):
        return ["flatpak-spawn", "--host"] + list(cmd_list)
    return list(cmd_list)


def run_host_cmd(cmd_list, dry_run=False):
    """Execute a synchronous command on the host OS."""
    full_cmd = build_host_cmd(cmd_list)
    cmd_str = " ".join(full_cmd)
    if dry_run:
        print(f"[DRY-RUN] Host Command: {cmd_str}")
        return True, f"[DRY-RUN] Executed: {cmd_str}"

    try:
        proc = subprocess.run(
            full_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        return True, proc.stdout
    except (subprocess.CalledProcessError, OSError) as e:
        err_msg = getattr(e, "stderr", None) or getattr(e, "stdout", None) or str(e)
        return False, err_msg


def spawn_host_cmd(cmd_list, dry_run=False):
    """Launch a detached background command on the host OS without blocking the UI."""
    full_cmd = build_host_cmd(cmd_list)
    cmd_str = " ".join(full_cmd)
    if dry_run:
        print(f"[DRY-RUN] Host Spawn: {cmd_str}")
        return True, f"[DRY-RUN] Spawned: {cmd_str}"

    try:
        subprocess.Popen(
            full_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True, ""
    except OSError as e:
        return False, str(e)


def detect_scopes_for_ref(ref, dry_run=False):
    """Return Flatpak scopes ('--user', '--system') where the given ref is installed."""
    if dry_run:
        return ["--user"] if ref == MASTER_REF else []

    scopes = []
    for scope in ("--user", "--system"):
        ok, _ = run_host_cmd(["flatpak", "info", scope, ref], dry_run=False)
        if ok:
            scopes.append(scope)
    return scopes


def detect_scopes_for_remote(remote_name, dry_run=False):
    """Return Flatpak scopes ('--user', '--system') where the named remote exists."""
    if dry_run:
        return ["--user"] if remote_name == LEGACY_REMOTE else []

    scopes = []
    for scope in ("--user", "--system"):
        ok, out = run_host_cmd(
            ["flatpak", "remotes", scope, "--columns=name"], dry_run=False
        )
        if ok and remote_name in {line.strip() for line in out.splitlines()}:
            scopes.append(scope)
    return scopes


def build_cleanup_command(master_scopes, remote_scopes):
    """Build a delayed shell command that removes legacy //master refs and the deprecated remote."""
    steps = []
    for scope in master_scopes:
        if scope not in ("--user", "--system"):
            continue
        steps.append(
            f"flatpak uninstall {scope} -y --noninteractive --force-remove {MASTER_REF} >/dev/null 2>&1 || true"
        )
    for scope in remote_scopes:
        if scope not in ("--user", "--system"):
            continue
        steps.append(
            f"flatpak remote-delete {scope} --force {LEGACY_REMOTE} >/dev/null 2>&1 || true"
        )

    if not steps:
        return ""
    return "sleep 2 && " + " && ".join(steps)


class MigrationWorker(QObject):
    progress = Signal(int, str)
    finished = Signal(bool, str)

    def __init__(self, dry_run=False):
        super().__init__()
        self.dry_run = dry_run

    def run(self):
        try:
            self.progress.emit(10, "Verifying Flathub repository on host system...")
            stable_scopes = detect_scopes_for_ref(STABLE_REF, self.dry_run)

            if not stable_scopes:
                ok, out = run_host_cmd(
                    [
                        "flatpak",
                        "remote-add",
                        "--user",
                        "--if-not-exists",
                        "flathub",
                        FLATHUB_REPO_URL,
                    ],
                    self.dry_run,
                )
                if not ok and not self.dry_run:
                    self.finished.emit(
                        False, f"Failed to register Flathub repository:\n{out}"
                    )
                    return

                self.progress.emit(35, "Installing official Open Amity from Flathub...")
                ok, out = run_host_cmd(
                    [
                        "flatpak",
                        "install",
                        "--user",
                        "-y",
                        "--noninteractive",
                        "--or-update",
                        "flathub",
                        STABLE_REF,
                    ],
                    self.dry_run,
                )
                if not ok and not self.dry_run:
                    self.finished.emit(
                        False, f"Failed to install Open Amity from Flathub:\n{out}"
                    )
                    return
            else:
                self.progress.emit(35, "Official Flathub installation detected...")

            self.progress.emit(75, "Launching official Open Amity...")
            ok, out = spawn_host_cmd(["flatpak", "run", STABLE_REF], self.dry_run)
            if not ok and not self.dry_run:
                self.finished.emit(
                    False, f"Failed to launch official Open Amity:\n{out}"
                )
                return

            self.progress.emit(90, "Cleaning up legacy GitHub installation...")
            master_scopes = detect_scopes_for_ref(MASTER_REF, self.dry_run)
            remote_scopes = detect_scopes_for_remote(LEGACY_REMOTE, self.dry_run)
            cleanup_script = build_cleanup_command(master_scopes, remote_scopes)
            if cleanup_script:
                spawn_host_cmd(["sh", "-c", cleanup_script], self.dry_run)

            self.progress.emit(100, "Migration complete!")
            self.finished.emit(
                True, "Successfully migrated to official Flathub release."
            )
        except Exception as e:
            self.finished.emit(False, f"Unexpected error during migration: {e}")


class InstallerWindow(QWidget):
    def __init__(self, dry_run=False):
        super().__init__()
        self.dry_run = dry_run
        self.setWindowTitle("Open Amity Update & Migration")
        self.setFixedSize(520, 320)
        self.setStyleSheet("background-color: #1a1a1a; color: #f0f0f0;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title_label = QLabel("Upgrading to Official Flathub Release")
        title_font = QFont("Ubuntu", 15)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setStyleSheet("color: #ffffff;")
        layout.addWidget(title_label)

        desc_text = (
            "Open Amity is now officially distributed on Flathub!\n\n"
            "This automated updater will install the verified Flathub release, "
            "remove the legacy GitHub Pages installation and remote, and switch your "
            "system to receive future updates directly from Flathub.\n"
            "All your existing agents, memories, and settings will remain completely intact."
        )
        desc_label = QLabel(desc_text)
        desc_label.setFont(QFont("Ubuntu", 10))
        desc_label.setStyleSheet("color: #cccccc; line-height: 1.4;")
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)

        layout.addStretch()

        self.status_label = QLabel("Ready to migrate...")
        self.status_label.setFont(QFont("Ubuntu", 9))
        self.status_label.setStyleSheet("color: #999999;")
        layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #333333;
                border-radius: 5px;
                text-align: center;
                background-color: #222222;
                color: #ffffff;
                height: 18px;
            }
            QProgressBar::chunk {
                background-color: #a12924;
                border-radius: 4px;
            }
        """)
        layout.addWidget(self.progress_bar)

        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.start_btn = QPushButton("Upgrade Now")
        self.start_btn.setStyleSheet("""
            QPushButton {
                background-color: #a12924;
                color: white;
                font-weight: bold;
                padding: 8px 18px;
                border-radius: 6px;
                border: none;
            }
            QPushButton:hover {
                background-color: #ba322c;
            }
            QPushButton:disabled {
                background-color: #555555;
                color: #888888;
            }
        """)
        self.start_btn.clicked.connect(self.start_migration)
        button_layout.addWidget(self.start_btn)

        layout.addLayout(button_layout)

    def start_migration(self):
        self.start_btn.setEnabled(False)
        self.thread = QThread()
        self.worker = MigrationWorker(dry_run=self.dry_run)
        self.worker.moveToThread(self.thread)

        self.worker.progress.connect(self.on_progress)
        self.worker.finished.connect(self.on_finished)
        self.thread.started.connect(self.worker.run)

        self.thread.start()

    def on_progress(self, val, msg):
        self.progress_bar.setValue(val)
        self.status_label.setText(msg)

    def on_finished(self, success, msg):
        self.thread.quit()
        self.thread.wait()
        if success:
            self.status_label.setText(msg)
            self.start_btn.setText("Completed")
            QApplication.instance().quit()
        else:
            self.status_label.setText(msg)
            self.status_label.setStyleSheet("color: #e57373;")
            self.start_btn.setEnabled(True)
            self.start_btn.setText("Retry")


def main():
    dry_run = "--dry-run" in sys.argv
    app = QApplication(sys.argv)
    window = InstallerWindow(dry_run=dry_run)
    window.show()

    if dry_run and "--auto" in sys.argv:
        window.start_migration()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
