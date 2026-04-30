---
description: "Use when adding features, fixing bugs, refactoring, or writing tests for the NuRestore project. Covers architecture, coding conventions, GUI patterns, CLI entry point, settings persistence, NuGet API integration, and build/packaging workflows."
applyTo: "**/*.py,**/*.ps1,**/*.spec,pyproject.toml"
---

# NuRestore – Developer Instructions

## Project Overview

NuRestore is a Python GUI application (CustomTkinter + Tkinter) that manages NuGet packages for .NET projects. It communicates with NuGet v3 HTTP feeds and invokes the `dotnet` CLI for install/uninstall/update operations.

---

## Architecture

- **Single-file app:** all logic lives in `nurestore.py`.
- **Threading model:** background work runs on `threading.Thread` or `concurrent.futures.ThreadPoolExecutor`. Results are marshalled back to the GUI thread via `root.after(0, callback)`. Never update GUI widgets directly from a background thread.
- **Endpoint caching:** NuGet v3 service-index lookups are stored in `self._svc_cache` (guarded by `self._svc_cache_lock`) to avoid repeated HTTP calls within a session.
- **Event-driven UI:** tabs (Browse, Installed, Updates) are built once in `_build_tabview()` and refreshed by dedicated methods (`load_projects`, `_run_search`, `_load_installed`, `_load_updates`).

### Key Classes

| Class | Purpose |
|---|---|
| `NuGetSource` | Immutable-ish value object for a NuGet feed (url, credentials, enabled, origin) |
| `SourceManagerDialog` | Modal `ctk.CTkToplevel` for CRUD of NuGet sources |
| `PackageList` | Reusable `ctk.CTkScrollableFrame` for rendering package cards with selection/hover |
| `CopyableErrorDialog` | Error dialog with clipboard copy and log-file shortcut |
| `NuGetManagerApp` | Main app controller; owns state, wires UI callbacks, drives all operations |

---

## Coding Conventions

- **Private scope:** prefix with `_` — functions (`_load_settings`), class methods (`self._build_toolbar`), instance attributes (`self._items`).
- **Type hints:** use them on all new functions and class attributes. Use built-in generics (`list[str]`, `dict[str, str]`, `tuple[str, str]`), not `typing.*`.
- **Section dividers:** use `# ── Section Name ──────────────────────────` to separate logical blocks inside large functions or the module.
- **`snake_case`** for functions/variables, **`CamelCase`** for classes, **`SCREAMING_SNAKE`** for module-level constants.
- **No bare `except:`** — always catch a specific exception or at minimum `Exception as e`.

---

## Settings Persistence

Settings are stored as JSON in `nurestore.settings.json` next to the executable/script (resolved via `_app_storage_dir()`).

- **Load:** `_load_settings() -> dict` — returns `{}` on missing or corrupt file; never raises.
- **Save:** `_save_settings(settings: dict)` — overwrites the file atomically.
- **Fields persisted:** `last_base_dir`, `loaded_config_path`.
- Always call `_persist_settings()` after any user action that changes `self.base_dir` or `self.loaded_config_path`.

---

## CLI Entry Point

```python
def main():
    root = ctk.CTk()
    NuGetManagerApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
```

- `pyproject.toml` maps `nurestore = "nurestore:main"` so `pip install .` registers the command.
- In `NuGetManagerApp.__init__`, `sys.argv[1]` (if provided and a valid directory) overrides the initial `base_dir`; otherwise `os.getcwd()` is used.
- Do **not** fall back to `last_base_dir` from settings for the initial directory — always honour the working directory or explicit CLI argument.

---

## NuGet Feed Integration

- Feeds are resolved via the NuGet v3 service index (`/index.json`). Use `_resolve_endpoints(src)` to get `SearchQueryService` and `PackageBaseAddress` URLs.
- Authentication for private feeds: pass `Authorization: Basic <base64(user:pass)>` headers via `urllib.request.Request`.
- All HTTP calls use `urllib.request` (not `requests`). Handle `urllib.error.HTTPError` and `urllib.error.URLError`.
- Rate-limit / timeout errors should be caught per-source; log with `_log_error()` and continue rather than crashing the whole search.

---

## NuGet Config Parsing

- `_parse_config(path, origin, user_managed)` — parses a NuGet.Config XML file, returns `list[NuGetSource]`.
- `_load_all_sources(base_dir, extra_configs)` — merges Machine → User → project-level configs in priority order.
- `_find_config_files(base_dir)` — walks the workspace for `NuGet.Config` / `nuget.config` files.
- Use `_xml_local_name()` and `_xml_child()` / `_xml_children()` when traversing ElementTree nodes to handle namespaces.

---

## GUI Patterns

- All top-level dialogs extend `ctk.CTkToplevel` and call `self.transient(parent)` + `self.grab_set()` for modal behaviour.
- Scrollable lists use `PackageList`; do not create ad-hoc frames for package cards.
- Status/loading state is tracked via `self._loading_count`; increment before async work, decrement in the `root.after` callback.
- Appearance mode is set globally to `"System"` and colour theme to `"green"` — do not override per-widget unless unavoidable.

---

## Error Handling & Logging

- Use `_log_error(error_msg: str)` to write timestamped entries to `nurestore.log`.
- Show user-facing errors via `CopyableErrorDialog`, not `messagebox.showerror`, for multi-line or technical messages.
- Use `messagebox.showerror` only for brief, simple failures (e.g., "No folder selected").

---

## Testing

- Test file: `tests/test_nurestore.py`, framework: `unittest`.
- Test only pure/utility functions — no GUI component tests.
- Use `tempfile.TemporaryDirectory` / `tempfile.NamedTemporaryFile` for file-based tests.
- New utility functions must have corresponding unit tests covering happy path and edge cases.
- Run tests: `python -m unittest discover -s tests`

---

## Build & Packaging

| Script | Purpose |
|---|---|
| `scripts/install_cli.ps1` | `pip install .` — registers `nurestore` as a global CLI command |
| `scripts/build_portable.ps1` | PyInstaller portable `.exe` via `nurestore_portable.spec` |

- The `.spec` file uses `collect_all("customtkinter")` to bundle themes and assets.
- The exe is windowed (`console=False`); do not change this without updating the spec.
- Keep `pyproject.toml` and the `.spec` file in sync when adding new dependencies.
