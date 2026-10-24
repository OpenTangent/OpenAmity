#!/usr/bin/env python3
"""
Open Amity Flathub Migration Installer
Seamlessly migrates GitHub Pages Flatpak installations of Open Amity to the official Flathub release.
"""
import sys
import os
import subprocess
import shutil
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLabel, QProgressBar, QPushButton, QHBoxLayout, QFrame
)
from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtGui import QFont, QIcon, QColor


def run_host_cmd(cmd_list, dry_run=False):
    """Executes a command on the host OS via flatpak-spawn if sandboxed, or directly if unconfined."""
    if os.environ.get("FLATPAK_ID"):
        full_cmd = ["flatpak-spawn", "--host"] + cmd_list
    else:
        full_cmd = cmd_list

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
            check=True
        )
        return True, proc.stdout
    except subprocess.CalledProcessError as e:
        err_msg = e.stderr or e.stdout or str(e)
        return False, err_msg


class MigrationWorker(QObject):
    progress = Signal(int, str)
    finished = Signal(bool, str)

    def __init__(self, dry_run=False):
        super().__init__()
        self.dry_run = dry_run

    def run(self):
        try:
            self.progress.emit(10, "Verifying Flathub repository on host system...")
            # Step 1: Ensure Flathub remote exists
            ok, out = run_host_cmd([
                "flatpak", "remote-add", "--if-not-exists", "flathub",
                "https://dl.flathub.org/repo/flathub.flatpakrepo"
            ], self.dry_run)
            if not ok and not self.dry_run:
                self.finished.emit(False, f"Failed to register Flathub repository:\n{out}")
                return

            self.progress.emit(35, "Installing official Open Amity from Flathub...")
            # Step 2: Install official Open Amity from Flathub (branch //stable)
            ok, out = run_host_cmd([
                "flatpak", "install", "-y", "--noninteractive", "flathub", "com.openamity.OpenAmity//stable"
            ], self.dry_run)
            if not ok and not self.dry_run:
                self.finished.emit(False, f"Failed to install Open Amity from Flathub:\n{out}")
                return

            self.progress.emit(75, "Launching official Open Amity...")
            # Step 3: Spawn the newly installed stable app on the host
            run_host_cmd(["flatpak", "run", "com.openamity.OpenAmity//stable"], self.dry_run)

            self.progress.emit(90, "Cleaning up legacy GitHub installation...")
            # Step 4: Schedule uninstallation of the legacy master branch on host in background
            cleanup_script = "sleep 2 && flatpak uninstall -y --noninteractive com.openamity.OpenAmity//master >/dev/null 2>&1 &"
            if not self.dry_run:
                subprocess.Popen(
                    ["flatpak-spawn", "--host", "bash", "-c", cleanup_script]
                    if os.environ.get("FLATPAK_ID")
                    else ["bash", "-c", cleanup_script]
                )
            else:
                print(f"[DRY-RUN] Cleanup scheduled: {cleanup_script}")

            self.progress.emit(100, "Migration complete!")
            self.finished.emit(True, "Successfully migrated to official Flathub release.")
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

        # Title
        title_label = QLabel("Upgrading to Official Flathub Release")
        title_font = QFont("Ubuntu", 15)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setStyleSheet("color: #ffffff;")
        layout.addWidget(title_label)

        # Description
        desc_text = (
            "Open Amity is now officially distributed on Flathub!\n\n"
            "This automated updater will install the verified Flathub release "
            "and switch your system to receive future updates directly from Flathub.\n"
            "All your existing agents, memories, and settings will remain completely intact."
        )
        desc_label = QLabel(desc_text)
        desc_label.setFont(QFont("Ubuntu", 10))
        desc_label.setStyleSheet("color: #cccccc; line-height: 1.4;")
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)

        layout.addStretch()

        # Status & Progress
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

        # Action Buttons
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
            # Automatically close installer after successful launch
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
