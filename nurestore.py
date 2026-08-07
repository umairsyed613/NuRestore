import tkinter as tk
from tkinter import messagebox, filedialog
import customtkinter as ctk
import urllib.request
import urllib.parse
import base64
import json
import threading
import concurrent.futures
import subprocess
import os
import re
import sys
import webbrowser
import xml.etree.ElementTree as ET
from datetime import datetime


ctk.set_appearance_mode("System")
ctk.set_default_color_theme("green")


def _version_gt(a: str, b: str) -> bool:
    def parts(v: str):
        return [int(x) if x.isdigit() else 0
                for x in re.split(r"[.\-]", v.split("+")[0])]
    try:
        return parts(a) > parts(b)
    except Exception:
        return a > b


def _xml_local_name(tag) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _xml_child(parent: ET.Element, name: str) -> ET.Element | None:
    for child in list(parent):
        if _xml_local_name(child.tag) == name:
            return child
    return None


def _xml_children(parent: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in list(parent) if _xml_local_name(c.tag) == name]


def _decode_nuget_escaped_tag(tag: str) -> str:
    return re.sub(r"_x([0-9A-Fa-f]{4})_",
                  lambda m: chr(int(m.group(1), 16)), tag)


def _app_storage_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _log_error(error_msg: str):
    """Log errors to file for debugging."""
    try:
        log_path = os.path.join(_app_storage_dir(), "nurestore.log")
        with open(log_path, "a", encoding="utf-8") as f:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{timestamp}] {error_msg}\n")
    except Exception:
        pass


def _settings_path() -> str:
    return os.path.join(_app_storage_dir(), "nurestore.settings.json")


def _load_settings() -> dict:
    path = _settings_path()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_settings(settings: dict):
    path = _settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)


def _project_scope_badge(installed_count: int, total_count: int,
                         solution_mode: bool) -> str:
    if not solution_mode or total_count <= 0:
        return ""
    return f"· {installed_count} of {total_count} projects"


def _optimal_workers(item_count: int, *, base: int = 4, cap: int = 24) -> int:
    """Choose a bounded worker count based on pending work and CPU count."""
    cpu = os.cpu_count() or base
    suggested = max(base, cpu * 2)
    return max(1, min(cap, suggested, item_count if item_count > 0 else 1))


def _operation_outcome(total_count: int, error_count: int) -> str:
    """Classify operation outcome as success, partial, or failure."""
    if total_count <= 0:
        return "failure"
    if error_count <= 0:
        return "success"
    if error_count >= total_count:
        return "failure"
    return "partial"


_PROJECT_FILE_EXTENSIONS = (".csproj", ".fsproj", ".vbproj")
_SOLUTION_FILE_EXTENSIONS = (".sln", ".slnx")


# ── NuGet source ──────────────────────────────────────────────────────────────

class NuGetSource:
    def __init__(self, key: str, url: str, enabled: bool = True,
                 username: str = "", password: str = "",
                 origin: str = "User", user_managed: bool = True):
        self.key          = key
        self.url          = url
        self.enabled      = enabled
        self.username     = username
        self.password     = password
        self.origin       = origin
        self.user_managed = user_managed

    def copy(self) -> "NuGetSource":
        return NuGetSource(self.key, self.url, self.enabled,
                           self.username, self.password,
                           self.origin, self.user_managed)


# ── NuGet config helpers ──────────────────────────────────────────────────────

def _user_config_path() -> str:
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(appdata, "NuGet", "NuGet.Config")


def _machine_config_paths() -> list[str]:
    paths: list[str] = []
    for env in ("ProgramData", "ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(env)
        if not base:
            continue
        cfg_dir = os.path.join(base, "NuGet", "Config")
        if not os.path.isdir(cfg_dir):
            continue
        try:
            for f in os.listdir(cfg_dir):
                if f.lower().endswith(".config"):
                    paths.append(os.path.join(cfg_dir, f))
        except OSError:
            pass
    return paths


def _find_config_files(base_dir: str) -> list[tuple[str, str]]:
    configs: list[tuple[str, str]] = []

    for mp in _machine_config_paths():
        if os.path.isfile(mp):
            configs.append((mp, "Machine"))

    user = _user_config_path()
    if os.path.isfile(user):
        configs.append((user, "User"))

    skip = {".git", "bin", "obj", "node_modules", ".vs", "packages"}
    for dirpath, dirnames, filenames in os.walk(base_dir):
        dirnames[:] = [d for d in dirnames
                       if d not in skip and not d.startswith(".")]
        for f in filenames:
            if f.lower() == "nuget.config":
                full  = os.path.join(dirpath, f)
                rel   = os.path.relpath(full, base_dir)
                depth = len(rel.split(os.sep))
                label = ("Solution" if depth == 1
                         else f"Project ({os.path.basename(dirpath)})")
                configs.append((full, label))
    return configs


def _parse_config(path: str, origin: str,
                  user_managed: bool) -> tuple[list[NuGetSource], bool]:
    sources: list[NuGetSource] = []
    disabled: set[str]         = set()
    clear_all                  = False

    try:
        root = ET.parse(path).getroot()

        ps = _xml_child(root, "packageSources")
        if ps is not None:
            if _xml_child(ps, "clear") is not None:
                clear_all = True
            for add in _xml_children(ps, "add"):
                key = add.get("key", "").strip()
                url = add.get("value", "").strip()
                if key and url:
                    sources.append(NuGetSource(key, url, True, "", "",
                                               origin, user_managed))

        ds = _xml_child(root, "disabledPackageSources")
        if ds is not None:
            for add in _xml_children(ds, "add"):
                k = add.get("key", "").strip()
                if k and add.get("value", "true").strip().lower() == "true":
                    disabled.add(k)

        creds = _xml_child(root, "packageSourceCredentials")
        if creds is not None:
            for src_el in list(creds):
                tag_key = _decode_nuget_escaped_tag(_xml_local_name(src_el.tag))
                for src in sources:
                    if src.key.lower() == tag_key.lower():
                        for add in _xml_children(src_el, "add"):
                            ck = add.get("key", "").lower()
                            cv = add.get("value", "")
                            if ck == "username":
                                src.username = cv
                            elif "password" in ck:
                                src.password = cv
                        break
    except Exception:
        pass

    for src in sources:
        if src.key in disabled:
            src.enabled = False

    return sources, clear_all


def _load_all_sources(base_dir: str,
                      extra_config_paths: list[str] | None = None
                      ) -> list[NuGetSource]:
    merged: dict[str, NuGetSource] = {}

    config_files = _find_config_files(base_dir)
    known_paths = {
        os.path.normcase(os.path.abspath(path))
        for path, _origin in config_files
    }
    for path in extra_config_paths or []:
        full = os.path.abspath(path)
        norm = os.path.normcase(full)
        if os.path.isfile(full) and norm not in known_paths:
            config_files.append((full, f"Loaded Config ({os.path.basename(full)})"))
            known_paths.add(norm)

    for path, origin in config_files:
        user_managed = (origin == "User")
        if origin == "Machine" or origin.startswith("Loaded Config"):
            user_managed = False
        sources, clear_all = _parse_config(path, origin, user_managed)
        if clear_all:
            merged.clear()
        for src in sources:
            if src.key in merged:
                existing = merged[src.key]
                existing.url     = src.url
                existing.enabled = src.enabled
                existing.origin  = src.origin
                if src.username:
                    existing.username = src.username
                    existing.password = src.password
            else:
                merged[src.key] = src

    result = list(merged.values())
    if not result:
        result = [NuGetSource("nuget.org",
                              "https://api.nuget.org/v3/index.json",
                              True, "", "", "Default", True)]
    return result


def _resolve_solution_project_path(solution_dir: str, raw_path: str) -> str | None:
    raw_path = raw_path.strip().strip("\"").strip("'")
    if not raw_path or not raw_path.lower().endswith(_PROJECT_FILE_EXTENSIONS):
        return None

    full = os.path.normpath(os.path.join(solution_dir, raw_path))
    return full if os.path.isfile(full) else None


def _parse_sln(sln_path: str) -> list[str]:
    sln_dir = os.path.dirname(sln_path)
    found: list[str] = []

    try:
        with open(sln_path, encoding="utf-8-sig", errors="replace") as f:
            content = f.read()
        for m in re.finditer(
            r'"([^\"]+\.(?:csproj|fsproj|vbproj))"',
            content,
            re.IGNORECASE,
        ):
            full = _resolve_solution_project_path(sln_dir, m.group(1))
            if full and full not in found:
                found.append(full)
    except Exception:
        pass

    return found


def _parse_slnx(slnx_path: str) -> list[str]:
    slnx_dir = os.path.dirname(slnx_path)
    found: list[str] = []
    seen: set[str] = set()

    def visit(node: ET.Element):
        for raw in node.attrib.values():
            full = _resolve_solution_project_path(slnx_dir, raw)
            if full and full not in seen:
                seen.add(full)
                found.append(full)
        for child in list(node):
            visit(child)

    try:
        root = ET.parse(slnx_path).getroot()
        visit(root)
    except Exception:
        pass

    return found


def _write_user_config(user_sources: list[NuGetSource],
                       all_sources: list[NuGetSource],
                       path: str):
    config = ET.Element("configuration")

    pkg_el = ET.SubElement(config, "packageSources")
    for src in user_sources:
        add = ET.SubElement(pkg_el, "add")
        add.set("key", src.key)
        add.set("value", src.url)

    disabled = [s for s in all_sources if not s.enabled]
    if disabled:
        dis_el = ET.SubElement(config, "disabledPackageSources")
        for src in disabled:
            d = ET.SubElement(dis_el, "add")
            d.set("key", src.key)
            d.set("value", "True")

    cred_sources = [s for s in user_sources if s.username]
    if cred_sources:
        cred_el = ET.SubElement(config, "packageSourceCredentials")
        for src in cred_sources:
            tag = re.sub(r"[^\w]", "_", src.key)
            se  = ET.SubElement(cred_el, tag)
            u   = ET.SubElement(se, "add")
            u.set("key", "Username")
            u.set("value", src.username)
            p   = ET.SubElement(se, "add")
            p.set("key", "ClearTextPassword")
            p.set("value", src.password)

    try:
        ET.indent(config, space="  ")
    except AttributeError:
        pass

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b'<?xml version="1.0" encoding="utf-8"?>\n')
        ET.ElementTree(config).write(f, encoding="utf-8", xml_declaration=False)


# ── Source Manager Dialog ─────────────────────────────────────────────────────

class SourceManagerDialog(ctk.CTkToplevel):
    def __init__(self, parent, sources: list[NuGetSource], user_config: str,
                 base_dir: str, loaded_config_path: str = ""):
        super().__init__(parent)
        self.title("NuRestore Package Sources")
        self.geometry("780x560")
        self.minsize(720, 520)
        self.transient(parent)
        self.after(50, lambda: self._safe_grab())

        self._sources     = [s.copy() for s in sources]
        self._user_config = user_config
        self._base_dir = base_dir
        self._loaded_config_path = loaded_config_path
        self._sel: int | None = None
        self._row_widgets: list[dict] = []
        self.result: list[NuGetSource] | None = None

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.after(100, self.focus_set)

    def _safe_grab(self):
        try:
            self.grab_set()
        except Exception:
            pass

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 6))
        ctk.CTkLabel(header, text="Package Sources",
                     font=ctk.CTkFont(size=15, weight="bold")
                     ).pack(side="left")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=18, pady=0)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)

        self._list = ctk.CTkScrollableFrame(body, corner_radius=8)
        self._list.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self._list.grid_columnconfigure(0, weight=1)

        btn_col = ctk.CTkFrame(body, fg_color="transparent", width=130)
        btn_col.grid(row=0, column=1, sticky="ns")
        btn_col.grid_propagate(False)

        ctk.CTkButton(btn_col, text="Add", command=self._add,
                      width=120).pack(pady=(0, 6))
        ctk.CTkButton(btn_col, text="Load Config…",
                      command=self._load_config,
                      width=120, fg_color="transparent",
                      border_width=1,
                      text_color=("gray10", "gray90")
                      ).pack(pady=(0, 6))
        self._btn_clear_config = ctk.CTkButton(
            btn_col, text="Clear Config", command=self._clear_loaded_config,
            width=120, fg_color="transparent", border_width=1,
            text_color=("gray10", "gray90"))
        self._btn_clear_config.pack(pady=(0, 12))

        self._btn_remove = ctk.CTkButton(
            btn_col, text="Remove", command=self._remove,
            width=120, state="disabled",
            fg_color="transparent", border_width=1,
            text_color=("gray10", "gray90"))
        self._btn_remove.pack(pady=(0, 12))

        self._btn_toggle = ctk.CTkButton(
            btn_col, text="Enable", command=self._toggle,
            width=120, state="disabled",
            fg_color="transparent", border_width=1,
            text_color=("gray10", "gray90"))
        self._btn_toggle.pack()

        # ── Edit form ─────────────────────────────────────────────────────────
        form_card = ctk.CTkFrame(self, corner_radius=8)
        form_card.grid(row=2, column=0, sticky="ew", padx=18, pady=(12, 0))
        form_card.grid_columnconfigure(1, weight=1)
        form_card.grid_columnconfigure(3, weight=1)

        def _label(text, r, c, **kw):
            ctk.CTkLabel(form_card, text=text, anchor="e", width=84
                         ).grid(row=r, column=c, padx=(12, 6), pady=6, sticky="e", **kw)

        _label("Name:", 0, 0)
        self._v_name = tk.StringVar()
        self._e_name = ctk.CTkEntry(form_card, textvariable=self._v_name)
        self._e_name.grid(row=0, column=1, columnspan=3, sticky="ew",
                          padx=(0, 12), pady=6)

        _label("Source URL:", 1, 0)
        self._v_url = tk.StringVar()
        self._e_url = ctk.CTkEntry(form_card, textvariable=self._v_url)
        self._e_url.grid(row=1, column=1, columnspan=3, sticky="ew",
                         padx=(0, 12), pady=6)

        _label("Username:", 2, 0)
        self._v_user = tk.StringVar()
        self._e_user = ctk.CTkEntry(form_card, textvariable=self._v_user)
        self._e_user.grid(row=2, column=1, sticky="ew", padx=(0, 8), pady=6)

        _label("Password:", 2, 2)
        self._v_pass = tk.StringVar()
        self._e_pass = ctk.CTkEntry(form_card, textvariable=self._v_pass, show="•")
        self._e_pass.grid(row=2, column=3, sticky="ew", padx=(0, 12), pady=6)

        self._lbl_origin = ctk.CTkLabel(form_card, text="",
                                        text_color=("gray40", "gray60"),
                                        font=ctk.CTkFont(size=11))
        self._lbl_origin.grid(row=3, column=0, columnspan=2,
                              sticky="w", padx=12, pady=(0, 10))

        self._lbl_loaded_config = ctk.CTkLabel(
            form_card, text="",
            text_color=("gray40", "gray60"),
            font=ctk.CTkFont(size=11), anchor="w")
        self._lbl_loaded_config.grid(row=4, column=0, columnspan=3,
                                     sticky="w", padx=12, pady=(0, 10))

        self._btn_apply = ctk.CTkButton(form_card, text="Apply",
                                        command=self._apply,
                                        state="disabled", width=90)
        self._btn_apply.grid(row=4, column=3, sticky="e",
                             padx=(0, 12), pady=(0, 10))

        # ── Footer ────────────────────────────────────────────────────────────
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=3, column=0, sticky="ew", padx=18, pady=(14, 16))
        ctk.CTkButton(foot, text="OK", command=self._ok,
                      width=90).pack(side="right")
        ctk.CTkButton(foot, text="Cancel", command=self._cancel,
                      width=90, fg_color="transparent", border_width=1,
                      text_color=("gray10", "gray90")
                      ).pack(side="right", padx=(0, 8))

        self._set_form_state(False)
        self._update_loaded_config_label()
        self._reload_sources()  # Load sources from config files
        self._refresh_list()    # Display the loaded sources

    # ── List rendering ────────────────────────────────────────────────────────

    def _refresh_list(self):
        for child in self._list.winfo_children():
            child.destroy()
        self._row_widgets.clear()

        for i, src in enumerate(self._sources):
            row = ctk.CTkFrame(
                self._list, corner_radius=6,
                fg_color=("gray92", "gray22"))
            row.grid(row=i, column=0, sticky="ew", padx=4, pady=3)
            row.grid_columnconfigure(2, weight=1)

            chk_var = tk.BooleanVar(value=src.enabled)
            chk = ctk.CTkCheckBox(
                row, text="", variable=chk_var, width=24,
                command=lambda idx=i, v=chk_var: self._toggle_row(idx, v))
            chk.grid(row=0, column=0, rowspan=2, padx=(12, 8), pady=8)

            name_lbl = ctk.CTkLabel(
                row, text=src.key, anchor="w",
                font=ctk.CTkFont(size=13, weight="bold"))
            name_lbl.grid(row=0, column=1, columnspan=2, sticky="ew",
                          pady=(8, 0))

            url_lbl = ctk.CTkLabel(
                row, text=src.url, anchor="w",
                font=ctk.CTkFont(size=11),
                text_color=("gray35", "gray65"))
            url_lbl.grid(row=1, column=1, columnspan=2, sticky="ew",
                         pady=(2, 8))

            origin_lbl = ctk.CTkLabel(
                row, text=src.origin, width=80, height=22,
                font=ctk.CTkFont(size=10),
                text_color=("gray45", "gray55"),
                fg_color=("gray85", "gray30"),
                corner_radius=10)
            origin_lbl.grid(row=0, column=3, rowspan=2, padx=(8, 12), pady=8)

            for w in (row, name_lbl, url_lbl, origin_lbl):
                w.bind("<Button-1>",
                       lambda _e, idx=i: self._select_row(idx))

            self._row_widgets.append({
                "row":    row,
                "name":   name_lbl,
                "url":    url_lbl,
                "origin": origin_lbl,
                "chk":    chk_var,
            })

        if self._sel is not None and 0 <= self._sel < len(self._sources):
            self._highlight_row(self._sel)

    def _highlight_row(self, idx: int):
        for i, rw in enumerate(self._row_widgets):
            if i == idx:
                rw["row"].configure(fg_color=("#cfe2ff", "#1f3a5f"))
            else:
                rw["row"].configure(fg_color=("gray92", "gray22"))

    def _select_row(self, idx: int):
        if not (0 <= idx < len(self._sources)):
            return
        self._sel = idx
        src = self._sources[idx]

        self._v_name.set(src.key)
        self._v_url.set(src.url)
        self._v_user.set(src.username)
        self._v_pass.set(src.password)

        editable = src.user_managed
        self._set_form_state(editable)
        self._btn_remove.configure(state="normal" if editable else "disabled")
        self._btn_toggle.configure(
            state="normal",
            text="Disable" if src.enabled else "Enable")
        self._lbl_origin.configure(
            text="" if editable else f"\U0001F512  Read-only ({src.origin})")
        self._highlight_row(idx)

    def _toggle_row(self, idx: int, var: tk.BooleanVar):
        if 0 <= idx < len(self._sources):
            self._sources[idx].enabled = bool(var.get())
            if idx == self._sel:
                self._btn_toggle.configure(
                    text="Disable" if self._sources[idx].enabled else "Enable")

    def _set_form_state(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        for e in (self._e_name, self._e_url, self._e_user, self._e_pass):
            e.configure(state=state)
        self._btn_apply.configure(state=state)

    def _clear_form(self):
        for v in (self._v_name, self._v_url, self._v_user, self._v_pass):
            v.set("")
        self._set_form_state(False)
        self._btn_remove.configure(state="disabled")
        self._btn_toggle.configure(state="disabled", text="Enable")
        self._lbl_origin.configure(text="")

    def _update_loaded_config_label(self):
        if self._loaded_config_path:
            self._lbl_loaded_config.configure(
                text=f"Loaded config: {self._loaded_config_path}")
            self._btn_clear_config.configure(state="normal")
        else:
            self._lbl_loaded_config.configure(text="")
            self._btn_clear_config.configure(state="disabled")

    def _reload_sources(self):
        extra_configs = ([self._loaded_config_path]
                         if self._loaded_config_path else None)
        self._sources = _load_all_sources(self._base_dir, extra_configs)

        # Make sure feeds from the explicitly selected config are always visible
        # in the dialog, even if workspace config layering overrides/clears them.
        if self._loaded_config_path and os.path.isfile(self._loaded_config_path):
            loaded_origin = f"Loaded Config ({os.path.basename(self._loaded_config_path)})"
            loaded_sources, _ = _parse_config(self._loaded_config_path,
                                              loaded_origin, False)
            merged: dict[str, NuGetSource] = {s.key: s for s in self._sources}
            for src in loaded_sources:
                merged[src.key] = src
            self._sources = list(merged.values())

        if not self._sources:
            self._sources = [NuGetSource(
                "nuget.org", "https://api.nuget.org/v3/index.json",
                True, "", "", "Default", True)]

        self._sel = None
        self._refresh_list()
        self._clear_form()
        self._update_loaded_config_label()

    # ── Actions ───────────────────────────────────────────────────────────────

    def _load_config(self):
        initial_dir = (os.path.dirname(self._loaded_config_path)
                       if self._loaded_config_path else self._base_dir)
        chosen = filedialog.askopenfilename(
            title="Choose NuGet config file",
            initialdir=initial_dir,
            filetypes=[
                ("NuGet Config", "NuGet.Config"),
                ("Config Files", "*.config"),
                ("All Files", "*.*"),
            ],
            parent=self,
        )
        if not chosen:
            return

        self._loaded_config_path = os.path.normpath(chosen)
        self._reload_sources()

    def _clear_loaded_config(self):
        self._loaded_config_path = ""
        self._reload_sources()

    def _add(self):
        new = NuGetSource("New Source",
                          "https://api.nuget.org/v3/index.json",
                          True, "", "", "User", True)
        self._sources.append(new)
        self._sel = len(self._sources) - 1
        self._refresh_list()
        self._select_row(self._sel)
        self._e_name.focus_set()
        self._e_name.select_range(0, "end")

    def _remove(self):
        if self._sel is None or not self._sources[self._sel].user_managed:
            return
        if not messagebox.askyesno(
                "Remove Source",
                f"Remove '{self._sources[self._sel].key}'?",
                parent=self):
            return
        del self._sources[self._sel]
        self._sel = None
        self._refresh_list()
        self._clear_form()

    def _toggle(self):
        if self._sel is None:
            return
        src = self._sources[self._sel]
        src.enabled = not src.enabled
        self._refresh_list()
        self._select_row(self._sel)

    def _apply(self):
        if self._sel is None:
            return
        name = self._v_name.get().strip()
        url  = self._v_url.get().strip()
        if not name or not url:
            messagebox.showerror("Validation",
                                 "Name and Source URL are required.",
                                 parent=self)
            return
        if not (url.startswith("http://") or url.startswith("https://")):
            messagebox.showerror(
                "Validation",
                "Source URL must start with http:// or https://",
                parent=self)
            return
        src          = self._sources[self._sel]
        src.key      = name
        src.url      = url
        src.username = self._v_user.get().strip()
        src.password = self._v_pass.get().strip()
        self._refresh_list()
        self._select_row(self._sel)

    def _ok(self):
        for src in self._sources:
            if not src.key.strip() or not src.url.strip():
                messagebox.showerror(
                    "Validation",
                    f"Source '{src.key or '(unnamed)'}' has incomplete settings.",
                    parent=self)
                return

        user_sources = [s for s in self._sources if s.user_managed]
        if not user_sources:
            # Avoid wiping user config when the dialog currently only shows
            # read-only (machine/loaded) feeds.
            existing_user_sources, _ = _parse_config(self._user_config, "User", True)
            if existing_user_sources:
                user_sources = existing_user_sources
            elif self._loaded_config_path and os.path.isfile(self._loaded_config_path):
                loaded_sources, _ = _parse_config(self._loaded_config_path, "User", True)
                user_sources = [
                    NuGetSource(s.key, s.url, s.enabled, s.username, s.password,
                                "User", True)
                    for s in loaded_sources
                ]
            if not user_sources:
                user_sources = [NuGetSource(
                    "nuget.org", "https://api.nuget.org/v3/index.json",
                    True, "", "", "User", True)]

        try:
            _write_user_config(
                user_sources,
                self._sources,
                self._user_config)
        except Exception as e:
            messagebox.showerror("Save Error",
                                 f"Could not write NuGet.Config:\n{e}",
                                 parent=self)
            return
        self.result = self._sources
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


# ── Scrollable package-card list ──────────────────────────────────────────────

class PackageList(ctk.CTkScrollableFrame):
    def __init__(self, parent, on_select, *, enable_marks: bool = False,
                 on_mark_changed=None, **kw):
        super().__init__(parent, corner_radius=8, **kw)
        self._on_select = on_select
        self._enable_marks = enable_marks
        self._on_mark_changed = on_mark_changed
        self._items: dict[str, dict] = {}
        self._selected: str | None = None
        self._row_idx = 0
        self.grid_columnconfigure(0, weight=1)

    def clear(self):
        for w in self.winfo_children():
            w.destroy()
        self._items.clear()
        self._selected = None
        self._row_idx = 0

    def message(self, text: str):
        self.clear()
        ctk.CTkLabel(self, text=text,
                     text_color=("gray45", "gray60"),
                     font=ctk.CTkFont(size=13)).grid(
            row=0, column=0, pady=40)

    def add_item(self, pkg_id: str, *, author="", version="", downloads="",
                 badge="", badge_color=("#107C10", "#3ec43e"),
                 version_inline=False):
        inline_meta = version_inline and not author
        has_author    = bool(author)
        has_meta_pill = (not version_inline) and bool(version)
        has_meta      = (not inline_meta) and (has_meta_pill or bool(downloads) or bool(badge))
        compact       = not has_author and not has_meta

        row = ctk.CTkFrame(self, corner_radius=4 if compact else 6,
                           fg_color="transparent")
        row_pady = 0 if compact else 1
        row.grid(row=self._row_idx, column=0, sticky="ew", padx=4, pady=row_pady)
        # When marks enabled, checkbox is at column 0; content starts at column 1
        col_offset = 1 if self._enable_marks else 0
        row.grid_columnconfigure(col_offset + 1, weight=1)
        if inline_meta:
            row.grid_columnconfigure(col_offset + 2, weight=0)
            row.grid_columnconfigure(col_offset + 3, weight=0)
        self._row_idx += 1

        indicator_rowspan = 1 + (1 if has_author else 0) + (1 if has_meta else 0)
        indicator = ctk.CTkFrame(row, width=3, height=1,
                     fg_color="transparent",
                     corner_radius=2)
        indicator_pady = 1 if compact else 2
        indicator.grid(row=0, column=col_offset, rowspan=indicator_rowspan,
                   sticky="ns", padx=(2, 6), pady=indicator_pady)

        if compact:
            name_size = 12
            name_pady = (0, 0)
        else:
            name_size = 14
            name_pady = (6, 0)

        name_lbl = ctk.CTkLabel(
            row, text=pkg_id, anchor="w",
            font=ctk.CTkFont(size=name_size, weight="bold"))
        name_lbl.grid(row=0, column=col_offset + 1, sticky="ew",
                      padx=(0, 4), pady=name_pady)

        widgets = [row, name_lbl, indicator]

        if version_inline and version:
            version_inline_lbl = ctk.CTkLabel(
                row, text=version, anchor="e",
                font=ctk.CTkFont(size=11),
                text_color=("gray45", "gray60"))
            version_pad = (0, 6) if inline_meta else (0, 10)
            version_inline_lbl.grid(row=0, column=col_offset + 2, sticky="e",
                                    padx=version_pad, pady=name_pady)
            widgets.append(version_inline_lbl)

        if inline_meta and (downloads or badge):
            meta_bits = []
            if downloads:
                meta_bits.append(f"↓ {downloads}")
            if badge:
                meta_bits.append(badge)
            meta_lbl = ctk.CTkLabel(
                row, text="   ".join(meta_bits), anchor="e",
                font=ctk.CTkFont(size=11),
                text_color=badge_color if badge and not downloads else ("gray45", "gray60"))
            meta_lbl.grid(row=0, column=col_offset + 3, sticky="e",
                          padx=(0, 10), pady=name_pady)
            widgets.append(meta_lbl)

        # Checkbox positioned at column 0 (left edge) with smaller size
        mark_var = None
        if self._enable_marks:
            mark_var = tk.BooleanVar(value=False)
            mark_chk = ctk.CTkCheckBox(
                row, text="", variable=mark_var, width=18, height=18,
                command=lambda pid=pkg_id, v=mark_var: self._on_mark(pid, v))
            mark_chk.grid(row=0, column=0, sticky="w", padx=(4, 6), pady=name_pady)
            widgets.append(mark_chk)

        next_row = 1
        if has_author:
            author_lbl = ctk.CTkLabel(
                row, text=f"by {author}", anchor="w",
                font=ctk.CTkFont(size=11),
                text_color=("gray35", "gray65"))
            author_lbl.grid(row=next_row, column=col_offset + 1, columnspan=2, sticky="ew",
                            padx=(0, 10),
                            pady=(0, 0 if has_meta else 4))
            widgets.append(author_lbl)
            next_row += 1

        if has_meta:
            meta_row = ctk.CTkFrame(row, fg_color="transparent")
            meta_row.grid(row=next_row, column=col_offset + 1, columnspan=2, sticky="ew",
                          padx=(0, 10), pady=(2, 6))
            widgets.append(meta_row)

            if has_meta_pill:
                pill = ctk.CTkLabel(
                    meta_row, text=version, corner_radius=10,
                    fg_color=("#0078D4", "#1f6feb"),
                    text_color="white",
                    font=ctk.CTkFont(size=10, weight="bold"),
                    padx=10, pady=2)
                pill.pack(side="left")
                widgets.append(pill)

            if downloads:
                dl_lbl = ctk.CTkLabel(
                    meta_row, text=f"  ↓ {downloads}",
                    font=ctk.CTkFont(size=11),
                    text_color=("gray45", "gray60"))
                dl_lbl.pack(side="left")
                widgets.append(dl_lbl)

            if badge:
                badge_lbl = ctk.CTkLabel(
                    meta_row, text=f"  {badge}",
                    font=ctk.CTkFont(size=11, weight="bold"),
                    text_color=badge_color)
                badge_lbl.pack(side="left")
                widgets.append(badge_lbl)

        def set_state(state: str):
            if state == "selected":
                row.configure(fg_color=("#cfe2ff", "#1f3a5f"))
                indicator.configure(fg_color=("#0078D4", "#1f6feb"))
            elif state == "hover":
                row.configure(fg_color=("gray90", "gray25"))
                indicator.configure(fg_color="transparent")
            else:
                row.configure(fg_color="transparent")
                indicator.configure(fg_color="transparent")

        def on_enter(_):
            if self._selected != pkg_id:
                set_state("hover")

        def on_leave(_):
            if self._selected != pkg_id:
                set_state("normal")

        def on_click(_):
            if self._selected and self._selected in self._items:
                self._items[self._selected]["set_state"]("normal")
            self._selected = pkg_id
            set_state("selected")
            self._on_select(pkg_id)

        for w in widgets:
            w.bind("<Enter>",    on_enter)
            w.bind("<Leave>",    on_leave)
            w.bind("<Button-1>", on_click)

        self._items[pkg_id] = {"set_state": set_state, "row": row,
                               "compact": compact,
                               "has_author": has_author,
                               "has_meta": has_meta,
                               "mark_var": mark_var}

    def _on_mark(self, pkg_id: str, var: tk.BooleanVar):
        if self._on_mark_changed:
            self._on_mark_changed(pkg_id, bool(var.get()))

    def get_marked_ids(self) -> set[str]:
        return {
            pkg_id for pkg_id, meta in self._items.items()
            if meta.get("mark_var") is not None and bool(meta["mark_var"].get())
        }

    def clear_marks(self):
        for meta in self._items.values():
            mv = meta.get("mark_var")
            if mv is not None:
                mv.set(False)
        if self._on_mark_changed:
            self._on_mark_changed("", False)


# ── Copyable Error Dialog ─────────────────────────────────────────────────────

class CopyableErrorDialog(ctk.CTkToplevel):
    """Error dialog with copyable text and a link to the log file."""
    def __init__(self, parent, title: str, message: str, log_path: str = ""):
        super().__init__(parent)
        self.title(title)
        self.geometry("600x350")
        self.minsize(400, 250)
        self.transient(parent)
        self.after(50, lambda: self._safe_grab())

        self._log_path = log_path
        self._build_ui(message)

    def _safe_grab(self):
        try:
            self.grab_set()
        except Exception:
            pass

    def _build_ui(self, message: str):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
        ctk.CTkLabel(header, text="Error Details:",
                     font=ctk.CTkFont(size=13, weight="bold")
                     ).pack(side="left")

        # Text area with scroll
        text_frame = ctk.CTkFrame(self, fg_color="transparent")
        text_frame.grid(row=1, column=0, sticky="nsew", padx=16, pady=8)
        text_frame.grid_rowconfigure(0, weight=1)
        text_frame.grid_columnconfigure(0, weight=1)

        self._text_widget = ctk.CTkTextbox(
            text_frame, corner_radius=6, wrap="word")
        self._text_widget.grid(row=0, column=0, sticky="nsew")
        self._text_widget.insert("1.0", message)
        self._text_widget.configure(state="normal")

        # Footer with buttons
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=2, column=0, sticky="ew", padx=16, pady=(8, 16))
        footer.grid_columnconfigure(0, weight=1)

        info_lbl = ctk.CTkLabel(
            footer, text="Full error log saved to nurestore.log",
            text_color=("gray35", "gray65"),
            font=ctk.CTkFont(size=10))
        info_lbl.grid(row=0, column=0, sticky="w")

        btn_frame = ctk.CTkFrame(footer, fg_color="transparent")
        btn_frame.grid(row=0, column=1, sticky="e")

        ctk.CTkButton(btn_frame, text="Copy to Clipboard",
                      command=self._copy_text,
                      width=130).pack(side="left", padx=(0, 6))

        if self._log_path and os.path.isfile(self._log_path):
            ctk.CTkButton(btn_frame, text="Open Log File",
                          command=self._open_log,
                          width=110,
                          fg_color="transparent",
                          border_width=1,
                          text_color=("gray10", "gray90")).pack(side="left", padx=(0, 6))

        ctk.CTkButton(btn_frame, text="OK", command=self.destroy,
                      width=60).pack(side="left")

    def _copy_text(self):
        text_content = self._text_widget.get("1.0", "end-1c")
        self.clipboard_clear()
        self.clipboard_append(text_content)
        self.update()
        # Visual feedback
        old_text = "Copy to Clipboard"
        for w in self.winfo_children():
            if isinstance(w, ctk.CTkFrame):
                for child in w.winfo_children():
                    if isinstance(child, ctk.CTkFrame):
                        for btn in child.winfo_children():
                            if isinstance(btn, ctk.CTkButton):
                                try:
                                    if "Copy" in btn.cget("text"):
                                        btn.configure(text="Copied!")
                                        self.after(1500, lambda: btn.configure(text=old_text))
                                except:
                                    pass

    def _open_log(self):
        if os.path.isfile(self._log_path):
            if os.name == "nt":
                os.startfile(self._log_path)
            else:
                os.system(f"open {self._log_path}")


# ── Main application ──────────────────────────────────────────────────────────

class NuGetManagerApp:
    def __init__(self, root: ctk.CTk):
        self.root = root
        self.root.title("NuRestore")
        self.root.geometry("1200x740")
        self.root.minsize(960, 600)

        settings = _load_settings()
        saved_config = settings.get("loaded_config_path", "")

        # If a path is passed as a CLI argument, use it;
        # otherwise default to the current working directory.
        cli_arg = sys.argv[1] if len(sys.argv) > 1 else ""
        if cli_arg and os.path.isdir(cli_arg):
            self.base_dir = os.path.abspath(cli_arg)
        else:
            self.base_dir = os.getcwd()
        self.loaded_config_path = (saved_config if os.path.isfile(saved_config)
                                   else "")
        self.entries: list[dict]            = []
        self.sources: list[NuGetSource]     = []
        self._svc_cache: dict               = {}
        self.browse_results: list           = []
        self.installed_pkgs: list           = []
        self.updates_data: list             = []
        self._loading_count                 = 0
        self._svc_cache_lock                = threading.Lock()
        self.include_prerelease             = tk.BooleanVar(value=False)
        self.base_dir_var                   = tk.StringVar(value=self.base_dir)

        self._build_ui()
        self.load_projects()

    def _persist_settings(self):
        try:
            _save_settings({
                "last_base_dir": self.base_dir,
                "loaded_config_path": self.loaded_config_path,
            })
        except PermissionError as exc:
            messagebox.showerror(
                "Settings Save Failed",
                f"Could not save settings — permission denied:\n{exc.filename}\n\n"
                "If NuRestore is installed in a protected location (e.g. inside "
                "Python's site-packages), run it as an administrator or reinstall "
                "to a user-writable location.",
                parent=self.root,
            )
        except OSError as exc:
            messagebox.showerror(
                "Settings Save Failed",
                f"Could not save settings:\n{exc}",
                parent=self.root,
            )

    # ── UI layout ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(1, weight=1)

        self._build_toolbar()
        self._build_tabview()
        self._build_statusbar()

    def _build_toolbar(self):
        bar = ctk.CTkFrame(self.root, corner_radius=0, height=110)
        bar.grid(row=0, column=0, sticky="ew")
        bar.grid_columnconfigure(0, weight=1)

        # Row 1: project selector
        row1 = ctk.CTkFrame(bar, fg_color="transparent")
        row1.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
        row1.grid_columnconfigure(3, weight=1)

        ctk.CTkLabel(row1, text="Project / Solution:",
                     font=ctk.CTkFont(weight="bold")
                     ).grid(row=0, column=0, padx=(0, 8))

        self.project_combo = ctk.CTkOptionMenu(
            row1, values=["No projects found"],
            command=self._on_project_changed,
            dynamic_resizing=False, width=420, anchor="w")
        self.project_combo.grid(row=0, column=1, sticky="w")

        ctk.CTkButton(row1, text="Choose Folder…",
                      command=self._choose_base_dir,
                      width=130, fg_color="transparent",
                      border_width=1,
                      text_color=("gray10", "gray90")
                      ).grid(row=0, column=2, padx=(8, 8))

        ctk.CTkLabel(row1, textvariable=self.base_dir_var,
                     anchor="w",
                     font=ctk.CTkFont(size=11),
                     text_color=("gray35", "gray65")
                     ).grid(row=0, column=3, sticky="w")

        # Row 2: source / search
        row2 = ctk.CTkFrame(bar, fg_color="transparent")
        row2.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 12))
        row2.grid_columnconfigure(3, weight=1)

        ctk.CTkLabel(row2, text="Package source:",
                     font=ctk.CTkFont(weight="bold")
                     ).grid(row=0, column=0, padx=(0, 8))

        self.source_combo = ctk.CTkOptionMenu(
            row2, values=["All Sources"],
            dynamic_resizing=False, width=240, anchor="w")
        self.source_combo.grid(row=0, column=1, padx=(0, 6))

        ctk.CTkButton(row2, text="Sources…",
                      command=self._open_source_manager,
                      width=90, fg_color="transparent",
                      border_width=1,
                      text_color=("gray10", "gray90")
                      ).grid(row=0, column=2, sticky="w", padx=(0, 16))

        ctk.CTkCheckBox(row2, text="pre-release",
                        variable=self.include_prerelease
                        ).grid(row=0, column=5, padx=(0, 5))

        self.search_var = tk.StringVar()
        self._search_entry = ctk.CTkEntry(
            row2, textvariable=self.search_var,
            width=480, placeholder_text="Search packages…")
        self._search_entry.grid(row=0, column=3, sticky="ew", padx=(0, 8))
        self._search_entry.bind("<Return>", lambda _: self.start_search())
        self.root.bind("<Control-l>",
                       lambda _: (self._search_entry.focus_set(),
                                  self._search_entry.select_range(0, "end")))

        ctk.CTkButton(row2, text="Search",
                      command=self.start_search, width=84
                      ).grid(row=0, column=4, padx=(0, 8))

    def _build_tabview(self):
        self.tabview = ctk.CTkTabview(self.root, corner_radius=8,
                                      command=self._on_tab_changed)
        self.tabview.grid(row=1, column=0, sticky="nsew", padx=14, pady=10)

        self._tab_browse_name    = "Browse"
        self._tab_installed_name = "Installed"
        self._tab_updates_name   = "Updates"

        for n in (self._tab_browse_name,
                  self._tab_installed_name,
                  self._tab_updates_name):
            self.tabview.add(n)
            self.tabview.tab(n).grid_columnconfigure(0, weight=1)
            self.tabview.tab(n).grid_rowconfigure(0, weight=1)

        self._build_browse_tab()
        self._build_installed_tab()
        self._build_updates_tab()

    def _build_statusbar(self):
        self.status_var = tk.StringVar(value="Ready")
        bar = ctk.CTkFrame(self.root, corner_radius=0, height=28)
        bar.grid(row=2, column=0, sticky="ew")
        bar.grid_propagate(False)
        bar.grid_columnconfigure(0, weight=1)
        bar.grid_columnconfigure(1, weight=0)

        ctk.CTkLabel(bar, textvariable=self.status_var,
                     text_color="white", anchor="w",
                     font=ctk.CTkFont(size=11)
                     ).grid(row=0, column=0, sticky="w", padx=12)

        self.status_progress = ctk.CTkProgressBar(
            bar, mode="indeterminate", width=120, height=10,
            progress_color="white")
        self.status_progress.grid(row=0, column=1, sticky="e", padx=(0, 10), pady=8)
        self.status_progress.grid_remove()

    def _set_loading(self, active: bool):
        if active:
            self._loading_count += 1
            if self._loading_count == 1:
                self.status_progress.grid()
                self.status_progress.start()
            return

        if self._loading_count > 0:
            self._loading_count -= 1
        if self._loading_count == 0:
            self.status_progress.stop()
            self.status_progress.grid_remove()

    def _set_status_error(self, message: str):
        self.status_var.set(message)
        self._set_loading(False)

    def _make_split(self, parent) -> tuple[ctk.CTkFrame, ctk.CTkFrame]:
        wrap = ctk.CTkFrame(parent, fg_color="transparent")
        wrap.grid(row=0, column=0, sticky="nsew")
        splitter_width = 8
        min_left = 750
        min_right = 260
        wrap.grid_columnconfigure(0, weight=0, minsize=min_left)
        wrap.grid_columnconfigure(1, weight=0, minsize=splitter_width)
        wrap.grid_columnconfigure(2, weight=1, minsize=min_right)
        wrap.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(wrap, fg_color="transparent")
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(0, weight=1)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 4))

        splitter = ctk.CTkFrame(
            wrap, width=splitter_width, corner_radius=0,
            fg_color="transparent")
        splitter.grid(row=0, column=1, sticky="ns")
        splitter.configure(cursor="sb_h_double_arrow")

        handle = ctk.CTkFrame(
            splitter, width=6, height=54, corner_radius=4,
            fg_color=("gray72", "gray32"))
        handle.place(relx=0.5, rely=0.5, anchor="center")

        handle_icon = ctk.CTkLabel(
            handle, text="::", width=0,
            text_color=("gray28", "gray85"),
            font=ctk.CTkFont(size=11, weight="bold"))
        handle_icon.place(relx=0.5, rely=0.5, anchor="center")

        for w in (handle, handle_icon):
            w.configure(cursor="sb_h_double_arrow")

        right = ctk.CTkFrame(wrap, corner_radius=8)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(0, weight=1)
        right.grid(row=0, column=2, sticky="nsew", padx=(4, 0))

        drag = {"active": False, "start_x": 0, "start_left": 0}
        state = {"left_px": 0, "applied_left": -1}
        hint = {"frame": None, "label": None}

        def _clamp_left(total_width: int, left_px: int) -> int:
            max_left = max(min_left, total_width - min_right - splitter_width)
            return max(min_left, min(max_left, left_px))

        def _apply_left(left_px: int):
            total = max(1, wrap.winfo_width())
            clamped = _clamp_left(total, left_px)
            if clamped == state["applied_left"]:
                return
            state["left_px"] = clamped
            state["applied_left"] = clamped
            wrap.grid_columnconfigure(0, minsize=clamped, weight=0)
            wrap.grid_columnconfigure(2, minsize=min_right, weight=1)

        def _ensure_hint():
            if hint["frame"] is not None and hint["label"] is not None:
                return
            hint["frame"] = ctk.CTkFrame(
                wrap, corner_radius=8,
                fg_color=("gray88", "gray18"),
                border_width=1, border_color=("gray70", "gray30"))
            hint["label"] = ctk.CTkLabel(
                hint["frame"], text="", font=ctk.CTkFont(size=11, weight="bold"),
                text_color=("gray10", "gray90"))
            hint["label"].pack(padx=8, pady=4)

        def _update_hint(x_root: int):
            _ensure_hint()
            total = max(1, wrap.winfo_width())
            left_px = max(min_left, state["left_px"] if state["left_px"] > 0 else left.winfo_width())
            usable = max(1, total - splitter_width)
            right_px = max(min_right, usable - left_px)
            left_pct = int(round((left_px / usable) * 100))
            right_pct = max(0, 100 - left_pct)
            hint["label"].configure(
                text=f"List {left_px}px ({left_pct}%)   Details {right_px}px ({right_pct}%)")

            wrap.update_idletasks()
            frame = hint["frame"]
            frame_w = frame.winfo_reqwidth()
            x_local = x_root - wrap.winfo_rootx()
            x = max(6, min(max(6, total - frame_w - 6), x_local - (frame_w // 2)))
            frame.place(x=x, y=8)

        def _hide_hint():
            if hint["frame"] is not None:
                hint["frame"].place_forget()

        def on_press(event):
            drag["active"] = True
            handle.configure(fg_color=("#0078D4", "#1f6feb"))
            drag["start_x"] = event.x_root
            drag["start_left"] = left.winfo_width()
            _update_hint(event.x_root)

        def on_drag(event):
            if not drag["active"]:
                return
            delta = event.x_root - drag["start_x"]
            new_left = drag["start_left"] + delta
            _apply_left(new_left)
            _update_hint(event.x_root)

        def on_release(_event):
            drag["active"] = False
            handle.configure(fg_color=("gray72", "gray32"))
            _hide_hint()

        def on_wrap_resize(event):
            if drag["active"]:
                return
            if state["left_px"] <= 0:
                _apply_left(int(event.width * 0.70))
            else:
                _apply_left(state["left_px"])

        for w in (splitter, handle, handle_icon):
            w.bind("<ButtonPress-1>", on_press)
            w.bind("<B1-Motion>", on_drag)
            w.bind("<ButtonRelease-1>", on_release)
        wrap.bind("<Configure>", on_wrap_resize)

        def set_initial_split():
            try:
                total = wrap.winfo_width()
                if total > 0:
                    _apply_left(int(total * 0.70))
            except Exception:
                pass

        wrap.after(50, set_initial_split)

        return left, right

    def _build_browse_tab(self):
        left, right = self._make_split(self.tabview.tab(self._tab_browse_name))
        self.browse_list = PackageList(left, self._on_browse_select)
        self.browse_list.grid(row=0, column=0, sticky="nsew")
        self.browse_panel = self._make_details_panel(right, mode="browse")

    def _build_installed_tab(self):
        left, right = self._make_split(self.tabview.tab(self._tab_installed_name))
        self.installed_list = PackageList(left, self._on_installed_select)
        self.installed_list.grid(row=0, column=0, sticky="nsew")
        self.installed_panel = self._make_details_panel(right, mode="installed")

    def _build_updates_tab(self):
        left, right = self._make_split(self.tabview.tab(self._tab_updates_name))
        self.updates_list = PackageList(
            left, self._on_update_select,
            enable_marks=True,
            on_mark_changed=lambda _pid, _checked: self._refresh_updates_bulk_button())
        self.updates_list.grid(row=0, column=0, sticky="nsew")
        self.updates_panel = self._make_details_panel(right, mode="updates")
        self._refresh_updates_bulk_button()

    def _make_details_panel(self, parent: ctk.CTkFrame, mode: str) -> dict:
        for child in parent.winfo_children():
            child.destroy()

        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.grid(row=0, column=0, sticky="nsew", padx=18, pady=16)
        frame.grid_columnconfigure(0, weight=1)

        title = ctk.CTkLabel(frame, text="Select a package",
                             font=ctk.CTkFont(size=18, weight="bold"),
                             anchor="w", justify="left", wraplength=320)
        title.grid(row=0, column=0, sticky="ew")

        author = ctk.CTkLabel(frame, text="", anchor="w",
                              text_color=("gray35", "gray65"),
                              font=ctk.CTkFont(size=12))
        author.grid(row=1, column=0, sticky="ew", pady=(2, 6))

        ver_info = ctk.CTkLabel(frame, text="", anchor="w",
                                text_color=("#0F6CBD", "#4ea1f5"),
                                font=ctk.CTkFont(size=12, weight="bold"))
        ver_info.grid(row=2, column=0, sticky="ew", pady=(0, 6))

        act = ctk.CTkFrame(frame, fg_color="transparent")
        act.grid(row=3, column=0, sticky="ew", pady=(2, 12))
        act.grid_columnconfigure(0, weight=0)
        act.grid_columnconfigure(1, weight=1)
        act.grid_columnconfigure(2, weight=0)

        ctk.CTkLabel(act, text="Version:").grid(row=0, column=0, sticky="w", padx=(0, 6))
        ver_combo = ctk.CTkOptionMenu(act, values=["—"],
                                      dynamic_resizing=False, width=160)
        ver_combo.grid(row=0, column=1, sticky="ew", padx=(0, 8))

        btn_text, btn_kwargs = {
            "browse":    ("Install", {}),
            "installed": ("Uninstall", {"fg_color": ("#C42B1C", "#a8231b"),
                                        "hover_color": ("#a8231b", "#7e1813")}),
            "updates":   ("Update", {}),
        }[mode]
        btn = ctk.CTkButton(act, text=btn_text, state="disabled",
                            width=110, **btn_kwargs)
        btn.grid(row=0, column=2, sticky="e")

        bulk_btn = None
        selected_btn = None
        if mode == "updates":
            act.grid_rowconfigure(1, weight=0)
            btn_row = ctk.CTkFrame(act, fg_color="transparent")
            btn_row.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(8, 0))
            btn_row.grid_columnconfigure(0, weight=1)
            btn_row.grid_columnconfigure(1, weight=1)

            bulk_btn = ctk.CTkButton(
                btn_row, text="Update All", state="disabled",
                command=self._run_update_all)
            bulk_btn.grid(row=0, column=0, sticky="ew", padx=(0, 4))

            selected_btn = ctk.CTkButton(
                btn_row, text="Update Selected", state="disabled",
                command=self._run_update_selected)
            selected_btn.grid(row=0, column=1, sticky="ew", padx=(4, 0))

        ctk.CTkFrame(frame, height=1, fg_color=("gray80", "gray30")
                     ).grid(row=4, column=0, sticky="ew", pady=8)

        ctk.CTkLabel(frame, text="Description",
                     font=ctk.CTkFont(weight="bold"), anchor="w"
                     ).grid(row=5, column=0, sticky="ew")

        desc = ctk.CTkTextbox(frame, height=130, wrap="word",
                              font=ctk.CTkFont(size=12),
                              activate_scrollbars=True)
        desc.grid(row=6, column=0, sticky="ew", pady=(4, 10))
        desc.configure(state="disabled")

        ctk.CTkFrame(frame, height=1, fg_color=("gray80", "gray30")
                     ).grid(row=7, column=0, sticky="ew", pady=4)

        proj_lbl = None
        proj_box = None
        info_row = 8
        if mode == "installed":
            proj_lbl = ctk.CTkLabel(
                frame, text="Installed in projects", anchor="w",
                font=ctk.CTkFont(weight="bold"))
            proj_lbl.grid(row=8, column=0, sticky="ew", pady=(6, 2))

            proj_box = ctk.CTkTextbox(
                frame, height=84, wrap="word",
                font=ctk.CTkFont(size=11),
                activate_scrollbars=True)
            proj_box.grid(row=9, column=0, sticky="ew", pady=(0, 8))
            proj_box.configure(state="disabled")
            info_row = 10

        dl_lbl = ctk.CTkLabel(frame, text="", anchor="w",
                              text_color=("gray45", "gray60"),
                              font=ctk.CTkFont(size=11))
        dl_lbl.grid(row=info_row, column=0, sticky="ew", pady=(6, 2))

        url_lbl = ctk.CTkLabel(frame, text="", anchor="w", cursor="hand2",
                               text_color=("#0078D4", "#4ea1f5"),
                               font=ctk.CTkFont(size=11))
        url_lbl.grid(row=info_row + 1, column=0, sticky="ew")

        return {
            "title":    title,    "author":   author,
            "ver_info": ver_info, "version":  ver_combo,
            "btn":      btn,      "desc":     desc,
            "dl":       dl_lbl,   "url":      url_lbl,
            "proj_lbl": proj_lbl, "proj_box": proj_box,
            "bulk_btn": bulk_btn,
            "selected_btn": selected_btn,
        }

    def _refresh_updates_bulk_button(self):
        bulk_btn = self.updates_panel.get("bulk_btn") if hasattr(self, "updates_panel") else None
        selected_btn = self.updates_panel.get("selected_btn") if hasattr(self, "updates_panel") else None
        if bulk_btn is None and selected_btn is None:
            return
        has_updates = bool(self.updates_data)
        has_projects = bool(self._get_projects())
        has_marked = bool(self.updates_list.get_marked_ids()) if hasattr(self, "updates_list") else False
        if bulk_btn is not None:
            bulk_btn.configure(state="normal" if has_updates and has_projects else "disabled")
        if selected_btn is not None:
            selected_btn.configure(
                state="normal" if has_updates and has_projects and has_marked else "disabled")

    # ── Source management ────────────────────────────────────────────────────

    def _load_sources(self):
        extra_configs = ([self.loaded_config_path]
                         if self.loaded_config_path else None)
        self.sources = _load_all_sources(self.base_dir, extra_configs)
        
        # Make sure feeds from the explicitly loaded config are always visible,
        # even if workspace config layering overrides/clears them.
        if self.loaded_config_path and os.path.isfile(self.loaded_config_path):
            loaded_origin = f"Loaded Config ({os.path.basename(self.loaded_config_path)})"
            loaded_sources, _ = _parse_config(self.loaded_config_path, loaded_origin, False)
            merged: dict[str, NuGetSource] = {s.key: s for s in self.sources}
            for src in loaded_sources:
                merged[src.key] = src
            self.sources = list(merged.values())
        
        self._refresh_source_combo()
        self._preload_endpoints()

    def _refresh_source_combo(self):
        names = ["All Sources"] + [
            f"{s.key}{'' if s.enabled else '  (disabled)'}"
            for s in self.sources
        ]
        self.source_combo.configure(values=names)
        self.source_combo.set("All Sources")

    def _preload_endpoints(self):
        enabled = [s for s in self.sources if s.enabled]
        if not enabled:
            return

        def run():
            workers = _optimal_workers(len(enabled), base=2, cap=8)
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
                list(ex.map(self._resolve_endpoints, enabled))
        threading.Thread(target=run, daemon=True).start()

    def _resolve_endpoints(self, src: NuGetSource) -> tuple[str | None, str | None]:
        with self._svc_cache_lock:
            cached = self._svc_cache.get(src.url)
        if cached is not None:
            return cached.get("search"), cached.get("flat")

        resolved = {"search": None, "flat": None}
        try:
            req = urllib.request.Request(
                src.url, headers={"User-Agent": "NuRestore/1.0"})
            if src.username:
                b64 = base64.b64encode(
                    f"{src.username}:{src.password}".encode()).decode()
                req.add_header("Authorization", f"Basic {b64}")
            with urllib.request.urlopen(req, timeout=10) as r:
                idx = json.loads(r.read().decode())
            search = flat = None
            for res in idx.get("resources", []):
                t = res.get("@type", "")
                if not search and "SearchQueryService" in t:
                    search = res["@id"].rstrip("/")
                elif not flat and "PackageBaseAddress" in t:
                    flat = res["@id"].rstrip("/")
            resolved = {"search": search, "flat": flat}
        except Exception:
            resolved = {"search": None, "flat": None}

        with self._svc_cache_lock:
            self._svc_cache[src.url] = resolved
            c = self._svc_cache[src.url]
        return c.get("search"), c.get("flat")

    def _search_sources(self) -> list[NuGetSource]:
        choice = self.source_combo.get()
        if choice == "All Sources" or not choice:
            return [s for s in self.sources if s.enabled]
        # Strip the optional " (disabled)" suffix
        key = re.sub(r"\s*\(disabled\)\s*$", "", choice)
        match = next((s for s in self.sources if s.key == key), None)
        if match and match.enabled:
            return [match]
        # Fall back to enabled sources if the chosen one is disabled / missing
        return [s for s in self.sources if s.enabled]

    def _open_source_manager(self):
        try:
            dlg = SourceManagerDialog(
                self.root, self.sources, _user_config_path(),
                self.base_dir, self.loaded_config_path)
            self.root.wait_window(dlg)
        except Exception as e:
            error_msg = f"Could not open source manager: {e}"
            _log_error(error_msg)
            messagebox.showerror("Sources", error_msg)
            return

        if dlg.result is None:
            return

        # Sync the loaded config path from dialog
        self.loaded_config_path = dlg._loaded_config_path
        self._persist_settings()

        # Always reload sources from config files to ensure everything is in sync
        # This picks up both user-managed sources (just saved) and loaded config sources
        try:
            self._load_sources()
            self._svc_cache.clear()
            self.status_var.set("Package sources updated.")
        except Exception as e:
            error_msg = f"Failed to reload sources: {e}"
            _log_error(error_msg)
            messagebox.showerror("Error", error_msg)

    # ── Project / Solution discovery ─────────────────────────────────────────

    def _choose_base_dir(self):
        chosen = filedialog.askdirectory(
            title="Choose folder containing projects/solutions",
            initialdir=self.base_dir,
            mustexist=True,
            parent=self.root)
        if not chosen:
            return
        self.base_dir = os.path.normpath(chosen)
        self.base_dir_var.set(self.base_dir)
        self._persist_settings()
        self.status_var.set(f"Scanning {self.base_dir}…")
        self.installed_list.clear()
        self.updates_list.clear()
        self.installed_pkgs = []
        self.updates_data   = []
        self.load_projects()

    def _parse_sln(self, sln_path: str) -> list[str]:
        if sln_path.lower().endswith(".slnx"):
            return _parse_slnx(sln_path)
        return _parse_sln(sln_path)

    def load_projects(self):
        """Trigger async project loading in background thread."""
        self.status_var.set("⟳ Loading projects…")
        self._set_loading(True)
        threading.Thread(target=self._load_projects_async, daemon=True).start()

    def _load_projects_async(self):
        """Background task: scan filesystem and parse projects."""
        try:
            base = self.base_dir
            skip = {".git", "bin", "obj", "node_modules", ".vs", "packages", ".idea"}
            sln_files:    list[str] = []
            csproj_files: list[str] = []

            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = [d for d in dirnames
                               if d not in skip and not d.startswith(".")]
                for f in filenames:
                    full = os.path.join(dirpath, f)
                    lower = f.lower()
                    if lower.endswith(_SOLUTION_FILE_EXTENSIONS):
                        sln_files.append(full)
                    elif lower.endswith(_PROJECT_FILE_EXTENSIONS):
                        csproj_files.append(full)

            entries  = []
            display: list[str] = []

            for sln in sorted(sln_files):
                projs = self._parse_sln(sln)
                if projs:
                    name = os.path.basename(sln)
                    entries.append({"type": "sln", "path": sln,
                                         "projects": projs, "display": name})
                    display.append(f"\U0001F4CB {name}  ({len(projs)} projects)")

            basenames = [os.path.basename(p) for p in sorted(csproj_files)]
            use_rel   = len(set(basenames)) != len(basenames)
            for p in sorted(csproj_files):
                rel  = os.path.relpath(p, base).replace("\\", "/")
                name = rel if use_rel else os.path.basename(p)
                entries.append({"type": "csproj", "path": p,
                                     "projects": [p], "display": name})
                display.append(name)

            # Update UI in main thread
            self.root.after(0, self._load_projects_done, entries, display)
        except Exception as e:
            self.root.after(0, self._set_status_error, f"Error loading projects: {e}")

    def _load_projects_done(self, entries: list, display: list):
        """UI callback after async project loading."""
        self.entries = entries
        if not self.entries:
            self.project_combo.configure(values=["No projects found"])
            self.project_combo.set("No projects found")
            self.status_var.set("No .sln, .slnx, or .csproj files found.")
        else:
            self.project_combo.configure(values=display)
            self.project_combo.set(display[0])
            self.refresh_installed()

        self._load_sources()
        self._set_loading(False)

    def _project_index(self) -> int:
        choice = self.project_combo.get()
        for i, entry in enumerate(self.entries):
            disp = (f"\U0001F4CB {entry['display']}  ({len(entry['projects'])} projects)"
                    if entry["type"] == "sln" else entry["display"])
            if disp == choice:
                return i
        return -1

    def _get_projects(self) -> list[str]:
        idx = self._project_index()
        return self.entries[idx]["projects"] if 0 <= idx < len(self.entries) else []

    def _is_solution(self) -> bool:
        idx = self._project_index()
        return 0 <= idx < len(self.entries) and self.entries[idx]["type"] == "sln"

    # ── Events ───────────────────────────────────────────────────────────────

    def _on_project_changed(self, _value=None):
        self.refresh_installed()

    def _on_tab_changed(self):
        current = self.tabview.get()
        if current == self._tab_installed_name:
            self.refresh_installed()
        elif current == self._tab_updates_name:
            self.refresh_updates()

    # ── Browse ───────────────────────────────────────────────────────────────

    def start_search(self):
        query = self.search_var.get().strip()
        if not query:
            return
        search_sources = self._search_sources()
        if not search_sources:
            self.status_var.set("No enabled sources configured.")
            return

        src_label = (search_sources[0].key if len(search_sources) == 1
                     else "all sources")
        self.status_var.set(f'Searching {src_label} for "{query}"…')
        self._set_loading(True)
        pre = "true" if self.include_prerelease.get() else "false"

        def fetch():
            results_by_id: dict = {}
            for src in search_sources:
                try:
                    search_url, _ = self._resolve_endpoints(src)
                    if not search_url:
                        continue
                    url = (f"{search_url}?q={urllib.parse.quote(query)}"
                           f"&take=25&prerelease={pre}")
                    req = urllib.request.Request(
                        url, headers={"User-Agent": "NuRestore/1.0"})
                    if src.username:
                        b64 = base64.b64encode(
                            f"{src.username}:{src.password}".encode()).decode()
                        req.add_header("Authorization", f"Basic {b64}")
                    with urllib.request.urlopen(req, timeout=10) as r:
                        data = json.loads(r.read().decode())["data"]
                    for pkg in data:
                        pid = pkg.get("id", "")
                        if pid not in results_by_id or _version_gt(
                                pkg.get("version", ""),
                                results_by_id[pid].get("version", "")):
                            results_by_id[pid] = pkg
                except Exception:
                    pass

            merged = sorted(results_by_id.values(),
                            key=lambda p: p.get("totalDownloads", 0) or 0,
                            reverse=True)[:25]
            self.root.after(0, self._show_search_results, merged)

        threading.Thread(target=fetch, daemon=True).start()

    def _show_search_results(self, data):
        self._set_loading(False)
        self.browse_results = data
        self.browse_list.clear()
        if not data:
            self.browse_list.message("No packages found.")
            self.status_var.set("No results.")
            return
        for pkg in data:
            dl = pkg.get("totalDownloads", 0)
            self.browse_list.add_item(
                pkg["id"],
                author    = ", ".join(pkg.get("authors", [])),
                version   = pkg.get("version", ""),
                downloads = f"{dl:,}" if isinstance(dl, int) else "",
            )
        self.status_var.set(f"{len(data)} packages found.")

    def _on_browse_select(self, pkg_id: str):
        pkg = next((p for p in self.browse_results if p["id"] == pkg_id), None)
        if pkg:
            p = dict(pkg)
            p["versions"] = list(reversed(p.get("versions", [])))
            self._populate_panel(self.browse_panel, p, mode="browse")

    # ── Installed ────────────────────────────────────────────────────────────

    def refresh_installed(self, refresh_updates_after: bool = False):
        projects = self._get_projects()
        if not projects:
            return
        self.status_var.set("⟳ Loading packages…")
        self._set_loading(True)
        threading.Thread(
            target=self._refresh_installed_async,
            args=(projects, self._is_solution(), len(projects),
                  refresh_updates_after),
            daemon=True
        ).start()

    def _refresh_installed_async(self, projects: list, solution_mode: bool,
                                 project_count: int,
                                 refresh_updates_after: bool):
        """Background task: parse project files for installed packages."""
        try:
            pkg_map: dict = {}

            def parse_project(proj: str) -> list[tuple[str, str, str]]:
                refs: list[tuple[str, str, str]] = []
                try:
                    root = ET.parse(proj).getroot()
                    for ref in root.findall(".//PackageReference"):
                        pid = ref.get("Include")
                        ver = (ref.get("Version") or ref.findtext("Version") or "Auto")
                        if pid:
                            refs.append((pid, ver, proj))
                except Exception:
                    pass
                return refs

            workers = _optimal_workers(len(projects), base=4, cap=16)
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
                parsed = ex.map(parse_project, projects)

            for refs in parsed:
                for pid, ver, proj in refs:
                    if pid not in pkg_map:
                        pkg_map[pid] = {"versions": set(), "in_projects": []}
                    pkg_map[pid]["versions"].add(ver)
                    pkg_map[pid]["in_projects"].append(proj)

            installed_pkgs = []
            for pid, info in sorted(pkg_map.items(), key=lambda x: x[0].lower()):
                vers    = info["versions"]
                ver_str = (next(iter(vers)) if len(vers) == 1
                           else f"{len(vers)} versions")
                installed_pkgs.append({
                    "id":          pid,
                    "version":     sorted(vers)[0],
                    "in_projects": info["in_projects"],
                })

            # Update UI in main thread
            self.root.after(0, self._refresh_installed_done,
                            installed_pkgs, solution_mode,
                            project_count, refresh_updates_after)
        except Exception as e:
            self.root.after(0, self._set_status_error, f"Error loading packages: {e}")

    def _refresh_installed_done(self, installed_pkgs: list, solution_mode: bool,
                                project_count: int,
                                refresh_updates_after: bool):
        """UI callback after async package loading."""
        self._set_loading(False)
        self.installed_list.clear()
        self.installed_pkgs = installed_pkgs

        for pkg in installed_pkgs:
            versions = {pkg["version"]}  # simplified version display
            badge = _project_scope_badge(
                len(pkg["in_projects"]), project_count, solution_mode)
            self.installed_list.add_item(
                pkg["id"], version=pkg["version"], badge=badge,
                badge_color=("gray45", "gray60"),
                version_inline=True)

        n = len(self.installed_pkgs)
        idx = self._project_index()
        label = (self.entries[idx]["display"]
                 if 0 <= idx < len(self.entries) else "")
        self.status_var.set(
            f"{n} package{'s' if n != 1 else ''} in {label}")
        if refresh_updates_after:
            self.refresh_updates()

    def _on_installed_select(self, pkg_id: str):
        pkg = next((p for p in self.installed_pkgs if p["id"] == pkg_id), None)
        if not pkg:
            return
        in_projects = pkg["in_projects"]
        placeholder = {
            "id": pkg_id,
            "description": "Fetching package details…",
            "authors": [], "totalDownloads": None,
            "versions": [{"version": pkg["version"]}],
            "projectUrl": "", "_in_projects": in_projects,
        }
        self._populate_panel(self.installed_panel, placeholder, mode="installed")
        self._fetch_pkg_metadata(pkg_id, pkg["version"], in_projects,
                                 self.installed_panel)

    def _fetch_pkg_metadata(self, pkg_id: str, installed_ver: str,
                            in_projects: list[str], panel: dict):
        sources = self._search_sources()[:3]

        def fetch():
            for src in sources:
                try:
                    search_url, _ = self._resolve_endpoints(src)
                    if not search_url:
                        continue
                    url = (f"{search_url}?q={urllib.parse.quote(pkg_id)}"
                           f"&take=5&prerelease=true")
                    req = urllib.request.Request(
                        url, headers={"User-Agent": "NuRestore/1.0"})
                    if src.username:
                        b64 = base64.b64encode(
                            f"{src.username}:{src.password}".encode()).decode()
                        req.add_header("Authorization", f"Basic {b64}")
                    with urllib.request.urlopen(req, timeout=10) as r:
                        results = json.loads(r.read().decode())["data"]
                    match = next(
                        (p for p in results if p["id"].lower() == pkg_id.lower()),
                        None)
                    if match:
                        all_vers = match.get("versions", [])
                        if not self.include_prerelease.get():
                            all_vers = [v for v in all_vers
                                        if "-" not in v["version"]]
                        m = dict(match)
                        m["versions"]     = list(reversed(all_vers))
                        m["_in_projects"] = in_projects
                        self.root.after(0, self._apply_pkg_metadata,
                                        panel, m, installed_ver)
                        return
                except Exception:
                    pass

        threading.Thread(target=fetch, daemon=True).start()

    def _apply_pkg_metadata(self, panel: dict, pkg_data: dict, installed_ver: str):
        self._populate_panel(panel, pkg_data, mode="installed")
        vals = panel["version"].cget("values") or []
        if installed_ver in vals:
            panel["version"].set(installed_ver)

    # ── Updates ──────────────────────────────────────────────────────────────

    def refresh_updates(self):
        if not self.installed_pkgs:
            self.refresh_installed()
        self.updates_list.clear()
        self.updates_data = []
        self._refresh_updates_bulk_button()
        if not self.installed_pkgs:
            self.updates_list.message("No packages installed.")
            return

        self.status_var.set("Checking for updates…")
        self._set_loading(True)
        pre = self.include_prerelease.get()
        all_sources = [s for s in self.sources if s.enabled]
        project_count = len(self._get_projects())
        solution_mode = self._is_solution()
        if not all_sources:
            self._set_loading(False)
            self.updates_list.message("No enabled package sources configured.")
            self.status_var.set("No enabled package sources configured.")
            return

        def check_one(pkg, resolved_sources: list[tuple[NuGetSource, str | None, str | None]]):
            all_versions: set[str] = set()
            for src, search_url, flat_url in resolved_sources:
                fetched_any = False
                try:
                    if not flat_url:
                        raise RuntimeError("No flat endpoint")
                    flat_id = urllib.parse.quote(pkg['id'].lower(), safe="")
                    url = f"{flat_url}/{flat_id}/index.json"
                    req = urllib.request.Request(
                        url, headers={"User-Agent": "NuRestore/1.0"})
                    if src.username:
                        b64 = base64.b64encode(
                            f"{src.username}:{src.password}".encode()).decode()
                        req.add_header("Authorization", f"Basic {b64}")
                    with urllib.request.urlopen(req, timeout=8) as r:
                        versions = json.loads(r.read().decode()).get("versions", [])
                    if not pre:
                        versions = [v for v in versions if "-" not in v]
                    all_versions.update(versions)
                    fetched_any = True
                except Exception:
                    # Some private feeds do not expose/allow flat container; fallback to search.
                    try:
                        if not search_url:
                            continue
                        q = urllib.parse.quote(pkg["id"])
                        url = f"{search_url}?q={q}&take=20&prerelease=true"
                        req = urllib.request.Request(
                            url, headers={"User-Agent": "NuRestore/1.0"})
                        if src.username:
                            b64 = base64.b64encode(
                                f"{src.username}:{src.password}".encode()).decode()
                            req.add_header("Authorization", f"Basic {b64}")
                        with urllib.request.urlopen(req, timeout=8) as r:
                            data = json.loads(r.read().decode()).get("data", [])

                        match = next(
                            (p for p in data
                             if p.get("id", "").lower() == pkg["id"].lower()),
                            None)
                        if not match:
                            continue
                        versions = [v.get("version", "")
                                    for v in match.get("versions", [])]
                        versions = [v for v in versions if v]
                        if not pre:
                            versions = [v for v in versions if "-" not in v]
                        all_versions.update(versions)
                        fetched_any = True
                    except Exception:
                        pass

                if fetched_any:
                    continue

            if not all_versions:
                return None
            sorted_v = sorted(all_versions,
                              key=lambda v: tuple(
                                  int(x) for x in v.split("-")[0].split(".")
                                  if x.isdigit()))
            latest = sorted_v[-1]
            if _version_gt(latest, pkg["version"]):
                return {"id": pkg["id"], "installed": pkg["version"],
                        "latest": latest, "versions": sorted_v,
                        "in_projects": pkg["in_projects"]}
            return None

        def run():
            endpoint_workers = _optimal_workers(len(all_sources), base=2, cap=8)
            with concurrent.futures.ThreadPoolExecutor(max_workers=endpoint_workers) as ex:
                resolved_sources = list(
                    ex.map(lambda src: (src, *self._resolve_endpoints(src)), all_sources)
                )

            # Keep only sources that expose at least one usable endpoint.
            resolved_sources = [
                item for item in resolved_sources if item[1] or item[2]
            ]
            if not resolved_sources:
                self.root.after(
                    0,
                    lambda: (
                        self._set_loading(False),
                        self.updates_list.message("No reachable NuGet endpoints for enabled sources."),
                        self.status_var.set("No reachable NuGet endpoints for enabled sources."),
                        self._refresh_updates_bulk_button(),
                    ),
                )
                return

            update_workers = _optimal_workers(len(self.installed_pkgs), base=6, cap=24)
            with concurrent.futures.ThreadPoolExecutor(max_workers=update_workers) as ex:
                results = [
                    r for r in ex.map(
                        lambda pkg: check_one(pkg, resolved_sources), self.installed_pkgs
                    ) if r
                ]
            self.root.after(0, self._show_updates, results)

        threading.Thread(target=run, daemon=True).start()

    def _show_updates(self, results: list):
        self._set_loading(False)
        self.updates_data = results
        n = len(results)
        project_count = len(self._get_projects())
        solution_mode = self._is_solution()
        self.updates_list.clear()
        if not results:
            self.updates_list.message("All packages are up to date.")
            self.status_var.set("All packages are up to date.")
            self._refresh_updates_bulk_button()
            return
        for u in results:
            scope_badge = _project_scope_badge(
                len(u["in_projects"]), project_count, solution_mode)
            badge = f"→ {u['latest']} {scope_badge}".strip()
            self.updates_list.add_item(
                u["id"], version=u["installed"],
                badge=badge,
                badge_color=("#0F6CBD", "#4ea1f5"),
                version_inline=True)
        self.status_var.set(f"{n} update{'s' if n != 1 else ''} available.")
        self._refresh_updates_bulk_button()

    def _run_update_all(self):
        if not self.updates_data:
            messagebox.showinfo("Updates", "No updates available.")
            return

        tasks: list[tuple[str, str, str]] = []
        for upd in self.updates_data:
            pkg_id = upd.get("id", "")
            latest = upd.get("latest", "")
            for project in upd.get("in_projects", []):
                if pkg_id and latest and project:
                    tasks.append((project, pkg_id, latest))

        # Keep deterministic order while de-duplicating work.
        tasks = list(dict.fromkeys(tasks))
        package_count = len({pkg for _proj, pkg, _ver in tasks})
        project_count = len({proj for proj, _pkg, _ver in tasks})
        command_count = len(tasks)

        if not tasks:
            messagebox.showinfo("Updates", "No applicable update tasks found.")
            return

        if not messagebox.askyesno(
                "Update All Packages",
                (f"Update {package_count} package(s) across {project_count} project(s)?\n"
                 f"This will run {command_count} dotnet command(s)."),
                parent=self.root):
            return

        self.status_var.set(
            f"Updating {package_count} package(s) across {project_count} project(s)…")
        self._set_loading(True)
        for p in (self.browse_panel, self.installed_panel, self.updates_panel):
            p["btn"].configure(state="disabled")
            if p.get("bulk_btn") is not None:
                p["bulk_btn"].configure(state="disabled")

        # Determine which config file to use for dotnet commands
        config_file = None
        if self.loaded_config_path and os.path.isfile(self.loaded_config_path):
            config_file = self.loaded_config_path
        else:
            user_config = _user_config_path()
            if os.path.isfile(user_config):
                config_file = user_config

        by_project: dict[str, list[tuple[str, str]]] = {}
        for project, pkg_id, version in tasks:
            by_project.setdefault(project, []).append((pkg_id, version))

        def run_one_project(item: tuple[str, list[tuple[str, str]]]) -> str | None:
            project, pkg_updates = item
            try:
                kw = {}
                if os.name == "nt":
                    si = subprocess.STARTUPINFO()
                    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    kw["startupinfo"] = si
                kw["cwd"] = os.path.dirname(project)

                for pkg_id, version in pkg_updates:
                    add_cmd = [
                        "dotnet", "add", project, "package", pkg_id,
                        "--version", version, "--no-restore"
                    ]
                    add_res = subprocess.run(add_cmd, capture_output=True, text=True, **kw)
                    if add_res.returncode != 0:
                        return (f"{os.path.basename(project)} / {pkg_id}: "
                                f"{(add_res.stderr or add_res.stdout).strip()}")

                restore_cmd = ["dotnet", "restore", project]
                if config_file:
                    restore_cmd += ["--configfile", config_file]
                restore_res = subprocess.run(restore_cmd, capture_output=True, text=True, **kw)
                if restore_res.returncode != 0:
                    return (f"{os.path.basename(project)}: "
                            f"{(restore_res.stderr or restore_res.stdout).strip()}")
            except Exception as e:
                return f"{os.path.basename(project)}: {e}"
            return None

        def run():
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(by_project) or 1)) as ex:
                errors = [e for e in ex.map(run_one_project, by_project.items()) if e]
            total_projects = len(by_project)
            successful_projects = total_projects - len(errors)
            outcome = _operation_outcome(total_projects, len(errors))
            if outcome == "failure":
                self.root.after(0, self._show_error, "\n".join(errors), "Update Error")
            elif outcome == "partial":
                self.root.after(
                    0, self._on_bulk_partial,
                    package_count, total_projects, successful_projects, errors)
            else:
                self.root.after(0, self._on_bulk_success,
                                package_count, total_projects)

        threading.Thread(target=run, daemon=True).start()

    def _run_update_selected(self):
        marked = self.updates_list.get_marked_ids()
        if not marked:
            messagebox.showinfo("Updates", "No packages selected.")
            return

        selected_updates = [u for u in self.updates_data if u.get("id") in marked]
        if not selected_updates:
            messagebox.showinfo("Updates", "Selected packages are not in the current update list.")
            return

        tasks: list[tuple[str, str, str]] = []
        for upd in selected_updates:
            pkg_id = upd.get("id", "")
            latest = upd.get("latest", "")
            for project in upd.get("in_projects", []):
                if pkg_id and latest and project:
                    tasks.append((project, pkg_id, latest))

        tasks = list(dict.fromkeys(tasks))
        package_count = len({pkg for _proj, pkg, _ver in tasks})
        project_count = len({proj for proj, _pkg, _ver in tasks})
        command_count = len(tasks)

        if not tasks:
            messagebox.showinfo("Updates", "No applicable update tasks for selected packages.")
            return

        if not messagebox.askyesno(
                "Update Selected Packages",
                (f"Update {package_count} selected package(s) across {project_count} project(s)?\n"
                 f"This will run {command_count} dotnet command(s)."),
                parent=self.root):
            return

        self.status_var.set(
            f"Updating {package_count} selected package(s) across {project_count} project(s)…")
        self._set_loading(True)
        for p in (self.browse_panel, self.installed_panel, self.updates_panel):
            p["btn"].configure(state="disabled")
            if p.get("bulk_btn") is not None:
                p["bulk_btn"].configure(state="disabled")
            if p.get("selected_btn") is not None:
                p["selected_btn"].configure(state="disabled")

        # Determine which config file to use for dotnet commands
        config_file = None
        if self.loaded_config_path and os.path.isfile(self.loaded_config_path):
            config_file = self.loaded_config_path
        else:
            user_config = _user_config_path()
            if os.path.isfile(user_config):
                config_file = user_config

        by_project: dict[str, list[tuple[str, str]]] = {}
        for project, pkg_id, version in tasks:
            by_project.setdefault(project, []).append((pkg_id, version))

        def run_one_project(item: tuple[str, list[tuple[str, str]]]) -> str | None:
            project, pkg_updates = item
            try:
                kw = {}
                if os.name == "nt":
                    si = subprocess.STARTUPINFO()
                    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    kw["startupinfo"] = si
                kw["cwd"] = os.path.dirname(project)

                for pkg_id, version in pkg_updates:
                    add_cmd = [
                        "dotnet", "add", project, "package", pkg_id,
                        "--version", version, "--no-restore"
                    ]
                    add_res = subprocess.run(add_cmd, capture_output=True, text=True, **kw)
                    if add_res.returncode != 0:
                        return (f"{os.path.basename(project)} / {pkg_id}: "
                                f"{(add_res.stderr or add_res.stdout).strip()}")

                restore_cmd = ["dotnet", "restore", project]
                if config_file:
                    restore_cmd += ["--configfile", config_file]
                restore_res = subprocess.run(restore_cmd, capture_output=True, text=True, **kw)
                if restore_res.returncode != 0:
                    return (f"{os.path.basename(project)}: "
                            f"{(restore_res.stderr or restore_res.stdout).strip()}")
            except Exception as e:
                return f"{os.path.basename(project)}: {e}"
            return None

        def run():
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(by_project) or 1)) as ex:
                errors = [e for e in ex.map(run_one_project, by_project.items()) if e]
            total_projects = len(by_project)
            successful_projects = total_projects - len(errors)
            outcome = _operation_outcome(total_projects, len(errors))
            if outcome == "failure":
                self.root.after(0, self._show_error, "\n".join(errors), "Update Error")
            elif outcome == "partial":
                self.root.after(
                    0, self._on_bulk_partial,
                    package_count, total_projects, successful_projects, errors)
            else:
                self.root.after(0, self._on_bulk_success,
                                package_count, total_projects)

        threading.Thread(target=run, daemon=True).start()

    def _on_bulk_success(self, package_count: int, project_count: int):
        self.refresh_installed(refresh_updates_after=True)
        self._set_loading(False)
        self.updates_list.clear_marks()
        self.status_var.set(
            f"Updated {package_count} package(s) across {project_count} project(s).")
        for p in (self.browse_panel, self.installed_panel, self.updates_panel):
            p["btn"].configure(state="normal")
            if p.get("bulk_btn") is not None:
                p["bulk_btn"].configure(state="normal")
            if p.get("selected_btn") is not None:
                p["selected_btn"].configure(state="normal")
        self._refresh_updates_bulk_button()

    def _on_bulk_partial(self, package_count: int, total_projects: int,
                         successful_projects: int, errors: list[str]):
        failed_projects = max(0, total_projects - successful_projects)
        self.refresh_installed(refresh_updates_after=True)
        self._set_loading(False)
        self.updates_list.clear_marks()

        self.status_var.set(
            f"Updated {package_count} package(s) in "
            f"{successful_projects} of {total_projects} project(s).")

        details = (
            f"Updated {package_count} package(s) in "
            f"{successful_projects} of {total_projects} project(s).\n"
            f"{failed_projects} project(s) failed.\n\n"
            "Failed projects:\n"
            f"{chr(10).join(errors)}"
        )
        _log_error(f"Partial update: {successful_projects}/{total_projects}\n"
                   + "\n".join(errors))
        log_path = os.path.join(_app_storage_dir(), "nurestore.log")
        CopyableErrorDialog(self.root, "Partial Update", details, log_path)

        for p in (self.browse_panel, self.installed_panel, self.updates_panel):
            p["btn"].configure(state="normal")
            if p.get("bulk_btn") is not None:
                p["bulk_btn"].configure(state="normal")
            if p.get("selected_btn") is not None:
                p["selected_btn"].configure(state="normal")
        self._refresh_updates_bulk_button()

    def _on_update_select(self, pkg_id: str):
        upd = next((u for u in self.updates_data if u["id"] == pkg_id), None)
        if not upd:
            return
        self._populate_panel(self.updates_panel, {
            "id": pkg_id,
            "description": "A newer version is available for this package.",
            "authors": [], "totalDownloads": None,
            "versions": [{"version": v} for v in reversed(upd["versions"])],
            "projectUrl": "",
            "_installed":   upd["installed"],
            "_latest":      upd["latest"],
            "_in_projects": upd["in_projects"],
        }, mode="updates")

    # ── Details panel population ─────────────────────────────────────────────

    def _populate_panel(self, panel: dict, pkg_data: dict, mode: str):
        pkg_id      = pkg_data.get("id", "Unknown")
        in_projects = pkg_data.get("_in_projects", self._get_projects())

        panel["title"].configure(text=pkg_id)
        authors = pkg_data.get("authors", [])
        panel["author"].configure(
            text=f"by {', '.join(authors)}" if authors else "")

        inst   = pkg_data.get("_installed", "")
        latest = pkg_data.get("_latest",    "")
        panel["ver_info"].configure(
            text=f"{inst}  →  {latest}" if (inst and latest) else "")

        panel["desc"].configure(state="normal")
        panel["desc"].delete("1.0", "end")
        panel["desc"].insert(
            "end", pkg_data.get("description", "No description available."))
        panel["desc"].configure(state="disabled")

        versions = [v["version"] for v in pkg_data.get("versions", [])]
        if versions:
            panel["version"].configure(values=versions)
            panel["version"].set(versions[0])
        else:
            panel["version"].configure(values=["—"])
            panel["version"].set("—")

        dl = pkg_data.get("totalDownloads")
        panel["dl"].configure(
            text=f"↓ {dl:,} total downloads" if isinstance(dl, int) else "")

        url = pkg_data.get("projectUrl", "")
        if url:
            panel["url"].configure(text="\U0001F517 Project Site")
            panel["url"].bind("<Button-1>",
                              lambda _, u=url: webbrowser.open(u))
        else:
            panel["url"].configure(text="")
            panel["url"].unbind("<Button-1>")

        proj_lbl = panel.get("proj_lbl")
        proj_box = panel.get("proj_box")
        if proj_lbl is not None and proj_box is not None:
            total_projects = len(self._get_projects())
            installed_count = len(in_projects)
            proj_lbl.configure(
                text=f"Installed in {installed_count} of {total_projects} project(s)")

            display_paths = []
            for proj in dict.fromkeys(in_projects):
                try:
                    rel = os.path.relpath(proj, self.base_dir).replace("\\", "/")
                    display_paths.append(rel)
                except Exception:
                    display_paths.append(proj)

            proj_text = "\n".join(display_paths) if display_paths else "—"
            proj_box.configure(state="normal")
            proj_box.delete("1.0", "end")
            proj_box.insert("end", proj_text)
            proj_box.configure(state="disabled")

        panel["btn"].configure(state="normal" if in_projects else "disabled")
        if mode == "browse":
            panel["btn"].configure(
                command=lambda: self._run_dotnet(
                    "add", pkg_id, self._get_projects(), panel["version"].get()))
        elif mode == "installed":
            panel["btn"].configure(
                command=lambda: self._run_dotnet("remove", pkg_id, in_projects))
        else:
            panel["btn"].configure(
                command=lambda: self._run_dotnet(
                    "add", pkg_id, in_projects, panel["version"].get()))

    # ── dotnet CLI ───────────────────────────────────────────────────────────

    def _run_dotnet(self, action: str, pkg_id: str,
                    projects: list[str], version: str | None = None):
        if not projects:
            messagebox.showerror("Error", "No project selected.")
            return
        verb = "Installing" if action == "add" else "Uninstalling"
        n    = len(projects)
        self.status_var.set(
            f"{verb} {pkg_id} in {n} project{'s' if n > 1 else ''}…")
        self._set_loading(True)
        for p in (self.browse_panel, self.installed_panel, self.updates_panel):
            p["btn"].configure(state="disabled")

        # Determine which config file to use for dotnet commands
        # Use the loaded custom config if available, otherwise use the default user config
        config_file = None
        if self.loaded_config_path and os.path.isfile(self.loaded_config_path):
            config_file = self.loaded_config_path
        else:
            user_config = _user_config_path()
            if os.path.isfile(user_config):
                config_file = user_config

        def run_one(project: str) -> str | None:
            try:
                kw = {}
                if os.name == "nt":
                    si = subprocess.STARTUPINFO()
                    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    kw["startupinfo"] = si
                kw["cwd"] = os.path.dirname(project)

                if action == "add":
                    add_cmd = ["dotnet", "add", project, "package", pkg_id]
                    if version and version != "—":
                        add_cmd += ["--version", version]
                    add_cmd += ["--no-restore"]

                    add_res = subprocess.run(add_cmd, capture_output=True, text=True, **kw)
                    if add_res.returncode != 0:
                        return (f"{os.path.basename(project)}: "
                                f"{(add_res.stderr or add_res.stdout).strip()}")

                    restore_cmd = ["dotnet", "restore", project]
                    if config_file:
                        restore_cmd += ["--configfile", config_file]
                    restore_res = subprocess.run(restore_cmd, capture_output=True, text=True, **kw)
                    if restore_res.returncode != 0:
                        return (f"{os.path.basename(project)}: "
                                f"{(restore_res.stderr or restore_res.stdout).strip()}")
                else:
                    remove_cmd = ["dotnet", action, project, "package", pkg_id]
                    remove_res = subprocess.run(remove_cmd, capture_output=True, text=True, **kw)
                    if remove_res.returncode != 0:
                        return (f"{os.path.basename(project)}: "
                                f"{(remove_res.stderr or remove_res.stdout).strip()}")
            except Exception as e:
                return f"{os.path.basename(project)}: {e}"
            return None

        def run():
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
                results = list(ex.map(run_one, projects))
            errors = [e for e in results if e]
            outcome = _operation_outcome(len(results), len(errors))
            if outcome == "failure":
                self.root.after(0, self._show_error, "\n".join(errors), "Update Error")
            elif outcome == "partial":
                self.root.after(
                    0, self._on_partial_success,
                    verb, pkg_id, len(results) - len(errors), len(results), errors)
            else:
                self.root.after(0, self._on_success, verb, pkg_id, n)

        threading.Thread(target=run, daemon=True).start()

    def _on_success(self, verb: str, pkg_id: str, n: int):
        past   = "Installed" if verb == "Installing" else "Uninstalled"
        suffix = f" across {n} projects" if n > 1 else ""
        self.refresh_installed()
        self._set_loading(False)

        if any(u["id"] == pkg_id for u in self.updates_data):
            self.updates_data = [u for u in self.updates_data if u["id"] != pkg_id]
            self.updates_list.clear()
            if not self.updates_data:
                self.updates_list.message("All packages are up to date.")
            else:
                for u in self.updates_data:
                    scope_badge = _project_scope_badge(
                        len(u["in_projects"]), len(self._get_projects()),
                        self._is_solution())
                    badge = f"→ {u['latest']} {scope_badge}".strip()
                    self.updates_list.add_item(
                        u["id"], version=u["installed"],
                        badge=badge,
                        badge_color=("#0F6CBD", "#4ea1f5"),
                        version_inline=True)

        self.status_var.set(f"{past} {pkg_id}{suffix}.")
        for p in (self.browse_panel, self.installed_panel, self.updates_panel):
            p["btn"].configure(state="normal")
            if p.get("bulk_btn") is not None:
                p["bulk_btn"].configure(state="normal")
            if p.get("selected_btn") is not None:
                p["selected_btn"].configure(state="normal")
        self._refresh_updates_bulk_button()

    def _on_partial_success(self, verb: str, pkg_id: str,
                            success_count: int, total_count: int,
                            errors: list[str]):
        past = "Installed" if verb == "Installing" else "Uninstalled"
        self.refresh_installed()
        self._set_loading(False)

        self.status_var.set(
            f"{past} {pkg_id} in {success_count} of {total_count} project(s).")
        details = (
            f"{past} {pkg_id} in {success_count} of {total_count} project(s).\n"
            f"{len(errors)} project(s) failed.\n\n"
            "Failed projects:\n"
            f"{chr(10).join(errors)}"
        )
        _log_error(f"Partial operation for {pkg_id}: {success_count}/{total_count}\n"
                   + "\n".join(errors))
        log_path = os.path.join(_app_storage_dir(), "nurestore.log")
        CopyableErrorDialog(self.root, "Partial Success", details, log_path)

        for p in (self.browse_panel, self.installed_panel, self.updates_panel):
            p["btn"].configure(state="normal")
            if p.get("bulk_btn") is not None:
                p["bulk_btn"].configure(state="normal")
            if p.get("selected_btn") is not None:
                p["selected_btn"].configure(state="normal")
        self._refresh_updates_bulk_button()

    def _show_error(self, msg: str, title: str = "Update Error"):
        self.status_var.set("Ready")
        self._set_loading(False)
        # Log the error to file
        _log_error(f"{title}: {msg}")
        # Show custom copyable error dialog
        log_path = os.path.join(_app_storage_dir(), "nurestore.log")
        CopyableErrorDialog(self.root, title, msg, log_path)
        for p in (self.browse_panel, self.installed_panel, self.updates_panel):
            p["btn"].configure(state="normal")
            if p.get("bulk_btn") is not None:
                p["bulk_btn"].configure(state="normal")
            if p.get("selected_btn") is not None:
                p["selected_btn"].configure(state="normal")
        self._refresh_updates_bulk_button()


def main():
    root = ctk.CTk()
    NuGetManagerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

