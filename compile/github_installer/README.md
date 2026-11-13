# GitHub Pages to Flathub Migration Installer

This directory contains the **Open Amity Flathub Migration Installer** deployed to the legacy GitHub Pages Flatpak repository (`https://opentangent.github.io/OpenAmity`).

## Purpose
Users who originally installed Open Amity from the GitHub Pages repository (`//master`) need a seamless upgrade path to the official Flathub release (`//stable`) without losing any agent data, memories, or settings.

Because user data is stored under the XDG app ID path:
`~/.var/app/com.openamity.OpenAmity/`
both the GitHub Pages installation (`//master`) and the Flathub installation (`//stable`) share the exact same persistent storage.

## How the Migration Installer Works
1. When launched (via `flatpak run` or the desktop launcher `run_OpenAmity.sh`), the installer presents a branded Open Amity upgrade dialog.
2. It invokes host `flatpak` via `flatpak-spawn --host` (authorized via `--talk-name=org.freedesktop.Flatpak`).
3. It ensures the `flathub` remote is configured for the `--user` Flatpak installation (`flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo`) and installs `com.openamity.OpenAmity//stable` without requiring root/Polkit prompts.
4. It launches `com.openamity.OpenAmity//stable` non-blockingly on the host.
5. After the migrator exits, a detached cleanup command uninstalls legacy `com.openamity.OpenAmity//master` and removes the deprecated `openamity` remote across detected `--user`/`--system` scopes.

## Deployment
This migrator is built and published to GitHub Pages via [`.github/workflows/flatpak-repo.yml`](../../.github/workflows/flatpak-repo.yml) using `andyholmes/flatter@main`:
```yaml
files: |
  compile/github_installer/com.openamity.OpenAmity.json
```
The workflow uses `workflow_dispatch` so regular `./deploy.sh` app releases never rebuild or re-publish the standalone migrator. If you ever modify files in `compile/github_installer/`, trigger the workflow manually via GitHub Actions or `gh workflow run flatpak-repo.yml`.
