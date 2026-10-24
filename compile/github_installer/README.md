# GitHub Pages to Flathub Migration Installer

This directory contains the staged **Open Amity Flathub Migration Installer**.

## Purpose
When Open Amity is approved on Flathub, users who originally installed Open Amity from the GitHub Pages repository (`flatpak install https://opentangent.github.io/OpenAmity/...`) need to be seamlessly migrated to the official Flathub release without losing any agent data, memories, or settings.

Because user data is stored under the XDG app ID path:
`~/.var/app/com.openamity.OpenAmity/`
both the GitHub Pages installation (running on branch `//master`) and the Flathub installation (running on branch `//stable`) share the exact same persistent storage.

## How the Migration Installer Works
1. When launched, the installer presents a clean Open Amity branded update dialog.
2. It invokes host `flatpak` via `flatpak-spawn --host` (authorized via `--talk-name=org.freedesktop.Flatpak`).
3. It adds the official Flathub remote if missing (`flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo`).
4. It installs `com.openamity.OpenAmity//stable` directly from Flathub.
5. It launches the new Flathub release.
6. In the background on the host, it removes the obsolete `com.openamity.OpenAmity//master` build.

## When to Deploy
> [!IMPORTANT]
> **DO NOT deploy this installer until the Flathub pull request for `com.openamity.OpenAmity` is merged and published.**
> If deployed beforehand, the installer will attempt to fetch `com.openamity.OpenAmity` from Flathub before it is live, causing an install failure.

## Deployment Steps (Post-Flathub Approval)
Once Flathub approval is complete:
1. Update `.github/workflows/flatpak-repo.yml`:
   Change the manifest path from:
   ```yaml
   manifest-path: compile/com.openamity.OpenAmity.json
   ```
   to:
   ```yaml
   manifest-path: compile/github_installer/com.openamity.OpenAmity.json
   ```
2. Trigger the GitHub Action workflow or push a commit.
3. Update `README.md` to point users to Flathub:
   ```bash
   flatpak install flathub com.openamity.OpenAmity
   ```
4. Existing users who run `flatpak update` will receive the lightweight installer and be smoothly transitioned to Flathub on their next launch.
