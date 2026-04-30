# NuRestore

A lightweight, portable GUI application for managing NuGet packages on .NET projects — built with Python and CustomTkinter.

---

## Features

- **Browse** — Search for packages on any NuGet feed with live results, download counts, and author info
- **Installed** — View all packages currently installed in a project or across an entire solution
- **Updates** — Detect and apply available updates in one click
- **Install / Uninstall / Update** — Full package lifecycle management via the .NET CLI (`dotnet`)
- **Solution mode** — Open a `.sln` file and manage packages across all projects at once; per-project badges show installation scope
- **NuGet source manager** — Add, remove, enable/disable, and reorder NuGet feeds; supports credentials (username + password) for private feeds
- **Multi-config support** — Automatically merges Machine, User, and project-level `NuGet.Config` files; load any additional config with a single click
- **Parallel operations** — Package searches and installs run concurrently using a thread pool scaled to your CPU count
- **Portable single-file executable** — Ship as a single `.exe` with no Python installation required (built with PyInstaller)
- **Light / Dark / System theme** — Adapts to your OS appearance preference automatically
- **Settings persistence** — Remembers your last project directory and loaded config path between sessions
- **Error logging** — Errors are written to `nurestore.log` alongside the executable for easy diagnostics

---

## Requirements

### To run from source

| Requirement | Version |
|---|---|
| Python | 3.11 or later |
| customtkinter | latest |
| .NET SDK | any version with `dotnet` CLI on `PATH` |

Install Python dependencies:

```bash
pip install customtkinter
```

### To build the portable executable

- PyInstaller 6.11 or later (installed automatically by the build script)

---

## Getting Started

### Run from source

```bash
python nurestore.py
# or open a specific directory directly
python nurestore.py D:\MyProject
```

### Install as a CLI command

Run the install script from the project root to register `nurestore` as a global command:

```powershell
.\scripts\install_cli.ps1
```

Install scope behavior:

- Non-elevated PowerShell: installs for the current user and updates User PATH.
- Elevated PowerShell (Run as Administrator): installs for all users and updates Machine PATH.
- Force all-users install explicitly:

```powershell
.\scripts\install_cli.ps1 -AllUsers
```

After installation you can launch the app from any folder:

```powershell
# Opens in the current directory
nurestore

# Opens with a specific directory
nurestore D:\MyProject
```

### Build portable `.exe`

Run the PowerShell build script from the project root:

```powershell
.\scripts\build_portable.ps1
```

The output is written to `dist\NuRestorePortable.exe`. This file is fully self-contained and requires no Python or additional libraries.

Optional: install portable build and add Explorer right-click integration:

```powershell
# Explicit build-only mode
.\scripts\build_portable.ps1 -BuildOnly

# Current user install + context menu
.\scripts\build_portable.ps1 -Install

# All users install + context menu (run as Administrator)
.\scripts\build_portable.ps1 -Install -AllUsers

# Register context menu only (no copy/install)
.\scripts\build_portable.ps1 -RegisterContextMenu
```

This adds an `Open with NuRestore` entry when right-clicking a folder or the background inside a folder in Windows Explorer.

---

## Usage

1. **Select a project or solution** — Use the dropdown to pick a `.csproj`, `.fsproj`, `.vbproj`, or `.sln` file. The app scans your last-used directory automatically.
2. **Browse packages** — Type a package name in the search box and press Enter or click **Search**. Results appear with version, author, and download count.
3. **Install a package** — Select a result, choose the target version from the dropdown, and click **Install**.
4. **View installed packages** — Switch to the **Installed** tab to see what is installed; click a package to uninstall it.
5. **Check for updates** — Switch to the **Updates** tab; packages with newer versions are listed. Select one and click **Update**.
6. **Manage sources** — Click **Sources** to open the NuGet source manager. Add private feeds with credentials, disable public feeds, or load an additional `NuGet.Config`.

---

## NuGet Source Manager

The source manager reads all active `NuGet.Config` files in the following priority order:

1. Machine-level configs (`%ProgramData%\NuGet\Config\`)
2. User-level config (`%APPDATA%\NuGet\NuGet.Config`)
3. Solution / project-level `NuGet.Config` files found under the selected directory

You can also **Load Config…** to merge in any additional config file without modifying your existing configs.

Sources marked as read-only (Machine or project-level) cannot be edited directly; add overrides at the User level.

---

## Settings

Settings are stored in `nurestore.settings.json` alongside the executable (or script). The file is created automatically and contains:

```json
{
  "last_base_dir": "D:\\path\\to\\your\\project",
  "loaded_config_path": "C:\\path\\to\\NuGet.Config"
}
```

---

## Project Structure

```text
nurestore.py                        Main application
nurestore_portable.spec             PyInstaller spec for single-file build
nurestore.settings.json             User settings (auto-generated)
scripts/
  build_portable.ps1                Builds the portable .exe with PyInstaller
  install_cli.ps1                   Installs nurestore as a global CLI command
tests/
  test_nurestore.py                 Unit tests
```

### Running tests

```bash
python -m unittest discover -s tests
```

---

## License

[MIT](LICENSE)
