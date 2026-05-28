import sys
import threading
import time
from pathlib import Path
from tkinter import filedialog, messagebox
import fnmatch

import customtkinter as ctk

from config import CREDS_FILE, TOKEN_FILE, ConfigManager
from drive import DriveSync
import ludusavi
from watcher import SaveWatcher

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ── Colour palette ──────────────────────────────────────────────────────────
BG_DARK    = "#07070b"
BG_CARD    = "#12121c"
BG_SIDEBAR = "#0c0c12"
ACCENT     = "#7257ff"
ACCENT2    = "#00e5a3"
DANGER     = "#ff3355"
TEXT_DIM   = "#5d5d75"
TEXT_MAIN  = "#f2f2fa"


class GameCard(ctk.CTkFrame):
    """Sidebar game entry card containing title, size badge, and launcher tag."""

    def __init__(self, master, game_name, last_sync, launcher_type, on_click, is_selected=False):
        super().__init__(
            master,
            fg_color="#1a1a26" if is_selected else "#12121c",
            border_width=1,
            border_color="#7257ff" if is_selected else "#1b1b2a",
            corner_radius=12,
            height=68,
        )
        self.pack_propagate(False)
        self.game_name = game_name
        self.on_click = on_click
        
        # Click handler
        def click_handler(event):
            self.on_click(self.game_name)

        self.bind("<Button-1>", click_handler)
        
        # Game Name
        self.lbl_name = ctk.CTkLabel(
            self, text=game_name,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color="#e8e8f0",
            anchor="w"
        )
        self.lbl_name.pack(side="top", anchor="w", padx=12, pady=(8, 2))
        self.lbl_name.bind("<Button-1>", click_handler)

        # Bottom row container
        self.bot_row = ctk.CTkFrame(self, fg_color="transparent")
        self.bot_row.pack(side="bottom", fill="x", padx=12, pady=(0, 8))
        self.bot_row.bind("<Button-1>", click_handler)

        # Sync text
        lbl_sync_text = f"⏱  {last_sync.split('  ')[0]}" if last_sync != "Never" else "⏱  Never"
        self.lbl_sync = ctk.CTkLabel(
            self.bot_row, text=lbl_sync_text,
            font=ctk.CTkFont(family="Segoe UI", size=10),
            text_color="#6b6b80",
            anchor="w"
        )
        self.lbl_sync.pack(side="left")
        self.lbl_sync.bind("<Button-1>", click_handler)

        # Launcher tag badge
        badge_text = launcher_type if launcher_type else "DRM-Free"
        self.lbl_badge = ctk.CTkLabel(
            self.bot_row, text=badge_text,
            font=ctk.CTkFont(family="Segoe UI", size=9, weight="bold"),
            text_color="#7257ff" if is_selected else "#5d5d75",
            fg_color="#221a48" if is_selected else "#1a1a24",
            corner_radius=6,
            width=54,
            height=18
        )
        self.lbl_badge.pack(side="right")
        self.lbl_badge.bind("<Button-1>", click_handler)

    def set_selected(self, is_selected):
        bg = "#1a1a26" if is_selected else "#12121c"
        border = "#7257ff" if is_selected else "#1b1b2a"
        self.configure(fg_color=bg, border_color=border)
        
        if is_selected:
            self.lbl_badge.configure(text_color="#7257ff", fg_color="#221a48")
        else:
            self.lbl_badge.configure(text_color="#5d5d75", fg_color="#1a1a24")


class AddGameDialog(ctk.CTkToplevel):
    """Modal dialog for adding a new game — supports multiple save paths."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Add Game — SaveVault")
        self.geometry("540x520")
        self.resizable(False, False)
        self.grab_set()
        self.configure(fg_color=BG_CARD)
        self.result = None          # (name, [paths])
        self._detected_paths: list[str] = []
        self._path_vars: list[ctk.BooleanVar] = []

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(5, weight=1)

        # Title
        ctk.CTkLabel(
            self, text="Add New Game",
            font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
            text_color=TEXT_MAIN,
        ).grid(row=0, column=0, padx=24, pady=(24, 4), sticky="w")

        # Game name + auto-detect
        ctk.CTkLabel(self, text="Game Name", font=ctk.CTkFont(size=12),
                     text_color=TEXT_DIM).grid(row=1, column=0, padx=24, sticky="w")

        name_row = ctk.CTkFrame(self, fg_color="transparent")
        name_row.grid(row=2, column=0, padx=24, pady=(4, 6), sticky="ew")
        name_row.grid_columnconfigure(0, weight=1)

        self.name_entry = ctk.CTkEntry(
            name_row, placeholder_text="e.g.  Lies of P",
            height=40, corner_radius=8, font=ctk.CTkFont(size=13),
        )
        self.name_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        self.detect_btn = ctk.CTkButton(
            name_row, text="⚡ Auto-Detect", width=130, height=40,
            corner_radius=8, fg_color=ACCENT, hover_color="#5a52e0",
            command=self._auto_detect,
        )
        self.detect_btn.grid(row=0, column=1)

        self.detect_status = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=11), text_color=TEXT_DIM,
            wraplength=490, justify="left",
        )
        self.detect_status.grid(row=3, column=0, padx=24, sticky="w")

        # Path checklist (shown after auto-detect or manual add)
        ctk.CTkLabel(
            self, text="Save Locations  (all will be backed up)",
            font=ctk.CTkFont(size=12), text_color=TEXT_DIM,
        ).grid(row=4, column=0, padx=24, pady=(10, 4), sticky="w")

        self.paths_frame = ctk.CTkScrollableFrame(
            self, fg_color="#13131a", corner_radius=8, height=140,
        )
        self.paths_frame.grid(row=5, column=0, padx=24, pady=(0, 6), sticky="nsew")
        self.paths_frame.grid_columnconfigure(0, weight=1)

        self._empty_lbl = ctk.CTkLabel(
            self.paths_frame,
            text="Click ⚡ Auto-Detect, or add paths manually below.",
            font=ctk.CTkFont(size=11), text_color=TEXT_DIM,
        )
        self._empty_lbl.pack(pady=16)

        # Manual path add row
        add_row = ctk.CTkFrame(self, fg_color="transparent")
        add_row.grid(row=6, column=0, padx=24, pady=(0, 14), sticky="ew")
        add_row.grid_columnconfigure(0, weight=1)

        self.path_entry = ctk.CTkEntry(
            add_row, placeholder_text=r"Or browse manually …",
            height=36, corner_radius=8, font=ctk.CTkFont(size=11),
        )
        self.path_entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        ctk.CTkButton(
            add_row, text="File", width=52, height=36, corner_radius=8,
            fg_color="#2a2a38", hover_color="#3a3a50",
            command=self._browse_file,
        ).grid(row=0, column=1, padx=(0, 4))

        ctk.CTkButton(
            add_row, text="Folder", width=64, height=36, corner_radius=8,
            fg_color="#2a2a38", hover_color="#3a3a50",
            command=self._browse_folder,
        ).grid(row=0, column=2, padx=(0, 4))

        ctk.CTkButton(
            add_row, text="+ Add", width=52, height=36, corner_radius=8,
            fg_color="#2a2a38", hover_color="#3a3a50", text_color=ACCENT2,
            command=self._manual_add,
        ).grid(row=0, column=3)

        # Confirm / cancel
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.grid(row=7, column=0, padx=24, pady=(0, 24), sticky="e")

        ctk.CTkButton(
            btn_row, text="Cancel", width=100, height=38, corner_radius=8,
            fg_color="transparent", border_width=1, border_color="#3a3a50",
            text_color=TEXT_DIM, command=self.destroy,
        ).pack(side="left", padx=(0, 10))

        ctk.CTkButton(
            btn_row, text="Add Game", width=120, height=38, corner_radius=8,
            fg_color=ACCENT, hover_color="#5a52e0",
            command=self._confirm,
        ).pack(side="left")

    # ── Path checklist helpers ──────────────────────────────────────────────────

    def _rebuild_path_list(self):
        """Rebuild the checklist from _detected_paths."""
        for w in self.paths_frame.winfo_children():
            w.destroy()
        self._path_vars.clear()

        if not self._detected_paths:
            self._empty_lbl = ctk.CTkLabel(
                self.paths_frame,
                text="Click ⚡ Auto-Detect, or add paths manually below.",
                font=ctk.CTkFont(size=11), text_color=TEXT_DIM,
            )
            self._empty_lbl.pack(pady=16)
            return

        for path in self._detected_paths:
            var = ctk.BooleanVar(value=True)
            self._path_vars.append(var)
            row = ctk.CTkFrame(self.paths_frame, fg_color="transparent")
            row.pack(fill="x", pady=2)
            # CTkCheckBox doesn't support wraplength — use checkbox + separate label
            ctk.CTkCheckBox(
                row, variable=var, text="",
                checkbox_width=16, checkbox_height=16,
                width=20,
                fg_color=ACCENT, hover_color="#5a52e0",
            ).pack(side="left", padx=(0, 6))
            
            disp_text = path
            lbl_color = TEXT_MAIN
            if path.startswith("registry://"):
                disp_text = f"🔑  [Registry]  {path[11:]}"
                lbl_color = ACCENT2
                
            ctk.CTkLabel(
                row, text=disp_text,
                font=ctk.CTkFont(size=10), text_color=lbl_color,
                wraplength=430, justify="left", anchor="w",
            ).pack(side="left", fill="x", expand=True)

    # ── Auto-detect ──────────────────────────────────────────────────────────

    def _auto_detect(self):
        name = self.name_entry.get().strip()
        if not name:
            self.detect_status.configure(text="Enter a game name first.", text_color=DANGER)
            return

        self.detect_btn.configure(text="Searching…", state="disabled")
        self.detect_status.configure(text="", text_color=TEXT_DIM)

        def _work():
            try:
                manifest = ludusavi.load_manifest()
                if manifest is None:
                    self.after(0, lambda: self._detect_done(
                        [], "⚠  Could not download database. Check your connection."
                    ))
                    return

                paths = ludusavi.find_save_paths(name, manifest)
                if paths:
                    msg = f"✓  Found {len(paths)} save location(s) — all pre-selected."
                    self.after(0, lambda p=paths, m=msg: self._detect_done(p, m))
                else:
                    suggestions = ludusavi.search_games(name, manifest, limit=3)
                    if suggestions:
                        tips = ", ".join(f'"{s}"' for s in suggestions)
                        msg = f"Not found. Did you mean: {tips}?"
                    else:
                        msg = "Not found in database. Add paths manually."
                    self.after(0, lambda m=msg: self._detect_done([], m))
            except Exception as exc:
                self.after(0, lambda e=exc: self._detect_done([], f"Error: {e}"))

        threading.Thread(target=_work, daemon=True).start()

    def _detect_done(self, paths: list[str], status: str):
        self.detect_btn.configure(text="⚡ Auto-Detect", state="normal")
        color = ACCENT2 if paths else DANGER
        self.detect_status.configure(text=status, text_color=color)
        if paths:
            self._detected_paths = paths
            self._rebuild_path_list()

    # ── Manual path helpers ───────────────────────────────────────────────────

    def _browse_file(self):
        p = filedialog.askopenfilename(title="Select Save File")
        if p:
            self.path_entry.delete(0, "end")
            self.path_entry.insert(0, p)

    def _browse_folder(self):
        p = filedialog.askdirectory(title="Select Save Folder")
        if p:
            self.path_entry.delete(0, "end")
            self.path_entry.insert(0, p)

    def _manual_add(self):
        p = self.path_entry.get().strip()
        if not p:
            return
        if not Path(p).exists():
            messagebox.showwarning("Invalid Path", "That path doesn't exist on disk.", parent=self)
            return
        if p not in self._detected_paths:
            self._detected_paths.append(p)
            self._rebuild_path_list()
        self.path_entry.delete(0, "end")

    # ── Confirm ───────────────────────────────────────────────────────────────

    def _confirm(self):
        name = self.name_entry.get().strip()
        selected = [
            p for p, var in zip(self._detected_paths, self._path_vars)
            if var.get()
        ]
        if not name:
            messagebox.showwarning("Missing Info", "Please enter a game name.", parent=self)
            return
        if not selected:
            messagebox.showwarning("No Paths", "Select or add at least one save location.", parent=self)
            return
        self.result = (name, selected)
        self.destroy()


class EditExclusionsDialog(ctk.CTkToplevel):
    """Dialog to edit list of exclude patterns (comma-separated)."""

    def __init__(self, parent, game_name, current_exclusions):
        super().__init__(parent)
        self.title("Edit Exclusions — SaveVault")
        self.geometry("450x240")
        self.resizable(False, False)
        self.grab_set()
        self.configure(fg_color=BG_CARD)
        self.result = None

        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self, text=f"Edit Exclusions for {game_name}",
            font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold"),
            text_color=TEXT_MAIN,
        ).grid(row=0, column=0, padx=24, pady=(24, 4), sticky="w")

        ctk.CTkLabel(
            self, text="Files matching these glob patterns will be ignored (comma-separated):",
            font=ctk.CTkFont(size=12), text_color=TEXT_DIM,
            wraplength=400, justify="left",
        ).grid(row=1, column=0, padx=24, pady=(0, 10), sticky="w")

        self.entry = ctk.CTkEntry(
            self, height=36, corner_radius=8, font=ctk.CTkFont(size=13),
        )
        self.entry.grid(row=2, column=0, padx=24, pady=(0, 20), sticky="ew")
        self.entry.insert(0, ", ".join(current_exclusions))

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.grid(row=3, column=0, padx=24, pady=(0, 24), sticky="e")

        ctk.CTkButton(
            btn_row, text="Cancel", width=90, height=36, corner_radius=8,
            fg_color="transparent", border_width=1, border_color="#3a3a50",
            text_color=TEXT_DIM, command=self.destroy,
        ).pack(side="left", padx=(0, 10))

        ctk.CTkButton(
            btn_row, text="Save Patterns", width=120, height=36, corner_radius=8,
            fg_color=ACCENT, hover_color="#5a52e0",
            command=self._confirm,
        ).pack(side="left")

    def _confirm(self):
        val = self.entry.get().strip()
        patterns = [p.strip() for p in val.split(",") if p.strip()]
        self.result = patterns
        self.destroy()


class PreviewDialog(ctk.CTkToplevel):
    """Dialog showing backup preview: files, exclusions, registry keys, and estimated sizes."""

    def __init__(self, parent, game_name, save_paths, exclude_patterns):
        super().__init__(parent)
        self.title("Backup Preview — SaveVault")
        self.geometry("600x500")
        self.grab_set()
        self.configure(fg_color=BG_CARD)
        self.confirm_backup = False

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(
            self, text=f"Backup Preview: {game_name}",
            font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
            text_color=TEXT_MAIN,
        ).grid(row=0, column=0, padx=24, pady=(24, 4), sticky="w")

        # Compute preview
        included_files = []
        excluded_files = []
        registry_keys = []
        total_size = 0

        def should_exclude(file_path: Path) -> bool:
            name = file_path.name.lower()
            rel_path_str = str(file_path).lower()
            for pat in exclude_patterns:
                pat_l = pat.lower()
                if fnmatch.fnmatch(name, pat_l) or fnmatch.fnmatch(rel_path_str, f"*{pat_l}*"):
                    return True
            return False

        for path_str in save_paths:
            if path_str.startswith("registry://"):
                registry_keys.append(path_str[11:])
            else:
                p = Path(path_str)
                if not p.exists():
                    continue
                if p.is_file():
                    if should_exclude(p):
                        excluded_files.append((str(p), p.stat().st_size))
                    else:
                        included_files.append((str(p), p.stat().st_size))
                        total_size += p.stat().st_size
                else:
                    try:
                        for file in p.rglob("*"):
                            if file.is_file():
                                if should_exclude(file):
                                    excluded_files.append((str(file), file.stat().st_size))
                                else:
                                    included_files.append((str(file), file.stat().st_size))
                                    total_size += file.stat().st_size
                    except Exception:
                        pass

        # Summary text
        summary = f"Estimated backup size: {total_size / 1024 / 1024:.2f} MB across {len(included_files)} file(s)"
        if registry_keys:
            summary += f" + {len(registry_keys)} registry hive(s)"
        
        ctk.CTkLabel(
            self, text=summary, font=ctk.CTkFont(size=12, weight="bold"),
            text_color=ACCENT2, wraplength=550, justify="left",
        ).grid(row=1, column=0, padx=24, pady=(0, 10), sticky="w")

        # Scrollable area listing details
        scroll = ctk.CTkScrollableFrame(self, fg_color="#13131a", corner_radius=8)
        scroll.grid(row=2, column=0, padx=24, pady=(0, 14), sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)

        # 1. Registry Keys
        if registry_keys:
            lbl = ctk.CTkLabel(scroll, text="🔑 REGISTRY KEYS", font=ctk.CTkFont(size=11, weight="bold"), text_color=ACCENT2)
            lbl.pack(anchor="w", pady=(8, 4))
            for key in registry_keys:
                ctk.CTkLabel(scroll, text=f"  {key}", font=ctk.CTkFont(size=10), text_color=TEXT_MAIN, anchor="w", justify="left").pack(anchor="w")

        # 2. Included Files
        if included_files:
            lbl = ctk.CTkLabel(scroll, text="📁 INCLUDED FILES", font=ctk.CTkFont(size=11, weight="bold"), text_color=TEXT_MAIN)
            lbl.pack(anchor="w", pady=(12, 4))
            for path, sz in included_files:
                ctk.CTkLabel(scroll, text=f"  {Path(path).name}  ({sz / 1024:.1f} KB) — {path}", font=ctk.CTkFont(size=10), text_color="#a0a0b0", anchor="w", justify="left").pack(anchor="w")

        # 3. Excluded Files
        if excluded_files:
            lbl = ctk.CTkLabel(scroll, text="🚫 EXCLUDED FILES", font=ctk.CTkFont(size=11, weight="bold"), text_color=DANGER)
            lbl.pack(anchor="w", pady=(12, 4))
            for path, sz in excluded_files:
                ctk.CTkLabel(scroll, text=f"  {Path(path).name}  ({sz / 1024:.1f} KB) — {path}", font=ctk.CTkFont(size=10), text_color=TEXT_DIM, anchor="w", justify="left").pack(anchor="w")

        if not registry_keys and not included_files:
            ctk.CTkLabel(scroll, text="No files or registry keys detected for backup.", font=ctk.CTkFont(size=12), text_color=DANGER).pack(pady=40)

        # Action buttons
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.grid(row=3, column=0, padx=24, pady=(0, 24), sticky="e")

        ctk.CTkButton(
            btn_row, text="Close", width=100, height=38, corner_radius=8,
            fg_color="transparent", border_width=1, border_color="#3a3a50",
            text_color=TEXT_DIM, command=self.destroy,
        ).pack(side="left", padx=(0, 10))

        ctk.CTkButton(
            btn_row, text="Backup Now", width=130, height=38, corner_radius=8,
            fg_color=ACCENT, hover_color="#5a52e0",
            command=self._confirm,
        ).pack(side="left")

    def _confirm(self):
        self.confirm_backup = True
        self.destroy()


class DetectGamesDialog(ctk.CTkToplevel):
    """Dialog showing auto-detected installed games on the system."""

    def __init__(self, parent, existing_names):
        super().__init__(parent)
        self.title("Auto-Detect Games — SaveVault")
        self.geometry("540x520")
        self.resizable(False, False)
        self.grab_set()
        self.configure(fg_color=BG_CARD)
        self.result = [] # list of (name, paths)
        
        self.existing_names = set(existing_names)
        self._detected = [] # list of (name, paths, source)
        self._checkbox_vars = {} # name -> BooleanVar

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Title
        ctk.CTkLabel(
            self, text="Auto-Detect Installed Games",
            font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
            text_color=TEXT_MAIN,
        ).grid(row=0, column=0, padx=24, pady=(24, 4), sticky="w")

        # Loading / Scrollable frame
        self.content_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.content_frame.grid(row=1, column=0, padx=24, pady=10, sticky="nsew")
        self.content_frame.grid_columnconfigure(0, weight=1)
        self.content_frame.grid_rowconfigure(0, weight=1)

        self.loading_lbl = ctk.CTkLabel(
            self.content_frame, text="⚡ Scanning system for installed games...\nThis checks Steam, GOG, Epic Games, and Xbox Game Pass.",
            font=ctk.CTkFont(size=13), text_color=TEXT_DIM, justify="center"
        )
        self.loading_lbl.pack(pady=80)

        # Bottom buttons
        self.btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.btn_frame.grid(row=2, column=0, padx=24, pady=(10, 24), sticky="ew")
        
        self.select_all_btn = ctk.CTkButton(
            self.btn_frame, text="Select All", width=90, height=36, corner_radius=8,
            fg_color="#2a2a38", hover_color="#3a3a50", text_color=TEXT_MAIN,
            command=self._select_all, state="disabled"
        )
        self.select_all_btn.pack(side="left", padx=(0, 6))

        self.deselect_all_btn = ctk.CTkButton(
            self.btn_frame, text="Deselect All", width=100, height=36, corner_radius=8,
            fg_color="#2a2a38", hover_color="#3a3a50", text_color=TEXT_MAIN,
            command=self._deselect_all, state="disabled"
        )
        self.deselect_all_btn.pack(side="left")

        self.add_btn = ctk.CTkButton(
            self.btn_frame, text="Add Selected", width=140, height=38, corner_radius=8,
            fg_color=ACCENT, hover_color="#5a52e0", state="disabled",
            command=self._confirm
        )
        self.add_btn.pack(side="right")

        self.cancel_btn = ctk.CTkButton(
            self.btn_frame, text="Cancel", width=90, height=38, corner_radius=8,
            fg_color="transparent", border_width=1, border_color="#3a3a50",
            text_color=TEXT_DIM, command=self.destroy
        )
        self.cancel_btn.pack(side="right", padx=(0, 10))

        # Start scanning in background
        threading.Thread(target=self._scan_background, daemon=True).start()

    def _scan_background(self):
        try:
            # Load manifest
            manifest = ludusavi.load_manifest()
            raw_detected = ludusavi.detect_installed_games(manifest)
            # Filter out games already in config
            self._detected = [g for g in raw_detected if g[0] not in self.existing_names]
            self.after(0, self._scan_complete)
        except Exception as e:
            self.after(0, lambda: self._scan_failed(str(e)))

    def _scan_failed(self, err_msg):
        self.loading_lbl.configure(text=f"⚠  Scan failed: {err_msg}", text_color=DANGER)

    def _scan_complete(self):
        self.loading_lbl.destroy()
        
        if not self._detected:
            lbl = ctk.CTkLabel(
                self.content_frame, text="No new installed games detected on your system.\n\nVerify that your launcher libraries are configured correctly,\nor add your games manually using '＋ Add Game'.",
                font=ctk.CTkFont(size=13), text_color=TEXT_DIM, justify="center"
            )
            lbl.pack(pady=60)
            return

        # Enable select/deselect and add buttons
        self.select_all_btn.configure(state="normal")
        self.deselect_all_btn.configure(state="normal")
        self.add_btn.configure(state="normal")

        # Create scrollable checklist
        scroll = ctk.CTkScrollableFrame(self.content_frame, fg_color="#13131a", corner_radius=8)
        scroll.pack(fill="both", expand=True)
        scroll.grid_columnconfigure(0, weight=1)

        # Header description
        ctk.CTkLabel(
            scroll, text=f"Found {len(self._detected)} installed game(s) with save data:",
            font=ctk.CTkFont(size=12, weight="bold"), text_color=TEXT_DIM
        ).pack(anchor="w", padx=10, pady=(10, 6))

        for name, paths, src in self._detected:
            var = ctk.BooleanVar(value=True)
            self._checkbox_vars[name] = var

            row = ctk.CTkFrame(scroll, fg_color="transparent")
            row.pack(fill="x", pady=4, padx=10)

            ctk.CTkCheckBox(
                row, variable=var, text="",
                checkbox_width=16, checkbox_height=16,
                width=20,
                fg_color=ACCENT, hover_color="#5a52e0",
                command=self._update_add_btn_text
            ).pack(side="left", padx=(0, 6))

            lbl_text = f"{name}  ({src})  —  {len(paths)} locations tracked"
            ctk.CTkLabel(
                row, text=lbl_text,
                font=ctk.CTkFont(size=12), text_color=TEXT_MAIN,
                anchor="w", justify="left"
            ).pack(side="left", fill="x", expand=True)

        self._update_add_btn_text()

    def _update_add_btn_text(self):
        count = sum(1 for var in self._checkbox_vars.values() if var.get())
        self.add_btn.configure(text=f"Add Selected ({count})")

    def _select_all(self):
        for var in self._checkbox_vars.values():
            var.set(True)
        self._update_add_btn_text()

    def _deselect_all(self):
        for var in self._checkbox_vars.values():
            var.set(False)
        self._update_add_btn_text()

    def _confirm(self):
        selected_names = {name for name, var in self._checkbox_vars.items() if var.get()}
        self.result = [
            (name, paths, src) for name, paths, src in self._detected
            if name in selected_names
        ]
        self.destroy()


class SaveVaultApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("SaveVault")
        self.geometry("1000x660")
        self.minsize(820, 560)
        self.configure(fg_color=BG_DARK)
        
        # Set app icon if logo.ico is present
        def resource_path(relative_path):
            """ Get absolute path to resource, works for dev and for PyInstaller """
            try:
                base_path = sys._MEIPASS
            except Exception:
                base_path = os.path.abspath(".")
            return os.path.join(base_path, relative_path)

        icon_path = resource_path("logo.ico")
        if os.path.exists(icon_path):
            self.iconbitmap(icon_path)

        self.config_mgr = ConfigManager()
        self.drive      = DriveSync()
        self.watcher    = SaveWatcher(callback=self._on_save_changed)

        self.selected_game: str | None = None
        self._game_btns: dict[str, GameCard] = {}

        self._build_ui()
        self._load_game_list()
        self._start_background()

    # ── UI construction ──────────────────────────────────────────────────────

    def _build_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # ── Sidebar ──────────────────────────────────────────────────────────
        sidebar = ctk.CTkFrame(self, width=260, corner_radius=0, fg_color=BG_SIDEBAR)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)
        sidebar.grid_rowconfigure(3, weight=1)

        # Logo
        ctk.CTkLabel(
            sidebar,
            text="💾  SaveVault",
            font=ctk.CTkFont(family="Segoe UI", size=22, weight="bold"),
            text_color=TEXT_MAIN,
        ).grid(row=0, column=0, padx=20, pady=(28, 4), sticky="w")

        # Drive status pill
        self.status_lbl = ctk.CTkLabel(
            sidebar,
            text="● Not Connected",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_DIM,
        )
        self.status_lbl.grid(row=1, column=0, padx=22, pady=(0, 16), sticky="w")

        # Divider
        ctk.CTkFrame(sidebar, height=1, fg_color="#2a2a38").grid(
            row=2, column=0, sticky="ew", padx=16, pady=(0, 8)
        )

        # Games scroll list
        self.games_list = ctk.CTkScrollableFrame(
            sidebar,
            label_text="YOUR GAMES",
            label_font=ctk.CTkFont(size=10, weight="bold"),
            label_text_color=TEXT_DIM,
            fg_color="transparent",
            scrollbar_button_color="#2a2a38",
        )
        self.games_list.grid(row=3, column=0, padx=8, pady=4, sticky="nsew")

        # Bottom buttons
        ctk.CTkButton(
            sidebar,
            text="＋  Add Game (Manual)",
            height=38,
            corner_radius=8,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#2a2a38",
            hover_color="#3a3a50",
            command=self._open_add_dialog,
        ).grid(row=4, column=0, padx=12, pady=(8, 4), sticky="ew")

        ctk.CTkButton(
            sidebar,
            text="⚡  Auto-Detect Installed",
            height=38,
            corner_radius=8,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=ACCENT,
            hover_color="#5a52e0",
            command=self._open_detect_dialog,
        ).grid(row=5, column=0, padx=12, pady=(0, 4), sticky="ew")

        self.backup_all_btn = ctk.CTkButton(
            sidebar,
            text="☁  Backup All Games",
            height=38,
            corner_radius=8,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#1a4a3a",
            hover_color="#0f3028",
            text_color=ACCENT2,
            command=self._backup_all,
        )
        self.backup_all_btn.grid(row=6, column=0, padx=12, pady=(0, 4), sticky="ew")

        self.connect_btn = ctk.CTkButton(
            sidebar,
            text="Connect Google Drive",
            height=38,
            corner_radius=8,
            font=ctk.CTkFont(size=12),
            fg_color="transparent",
            border_width=1,
            border_color="#3a3a50",
            text_color=TEXT_DIM,
            command=self._connect_drive,
        )
        self.connect_btn.grid(row=7, column=0, padx=12, pady=(0, 4), sticky="ew")

        self.refresh_db_btn = ctk.CTkButton(
            sidebar,
            text="↻  Refresh Game Database",
            height=32,
            corner_radius=8,
            font=ctk.CTkFont(size=11),
            fg_color="transparent",
            border_width=1,
            border_color="#2a2a38",
            text_color=TEXT_DIM,
            hover_color="#1e1e2a",
            command=self._refresh_manifest,
        )
        self.refresh_db_btn.grid(row=8, column=0, padx=12, pady=(0, 16), sticky="ew")

        # ── Main panel ───────────────────────────────────────────────────────
        self.main = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.main.grid(row=0, column=1, sticky="nsew", padx=28, pady=28)
        self.main.grid_columnconfigure(0, weight=1)
        self.main.grid_rowconfigure(99, weight=1)

        self._show_welcome()

    def _show_welcome(self):
        self._clear_main()
        holder = ctk.CTkFrame(self.main, fg_color="transparent")
        holder.place(relx=0.5, rely=0.45, anchor="center")

        ctk.CTkLabel(
            holder,
            text="💾",
            font=ctk.CTkFont(size=52),
        ).pack()
        ctk.CTkLabel(
            holder,
            text="SaveVault",
            font=ctk.CTkFont(family="Segoe UI", size=28, weight="bold"),
            text_color=TEXT_MAIN,
        ).pack(pady=(8, 4))
        ctk.CTkLabel(
            holder,
            text="Add a game on the left to start backing up your saves.",
            font=ctk.CTkFont(size=13),
            text_color=TEXT_DIM,
        ).pack()

    def _show_game_panel(self, name: str):
        self._clear_main()
        game = self.config_mgr.get_game(name)

        # Configure columns for two-column dashboard
        self.main.grid_columnconfigure(0, weight=5)
        self.main.grid_columnconfigure(1, weight=4)
        self.main.grid_rowconfigure(0, weight=1)

        # ── LEFT COLUMN ──────────────────────────────────────────────────────
        left_frame = ctk.CTkFrame(self.main, fg_color="transparent")
        left_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        left_frame.grid_columnconfigure(0, weight=1)
        left_frame.grid_rowconfigure(5, weight=1) # Logbox expands

        # Game Title + Locations
        ctk.CTkLabel(
            left_frame, text=name,
            font=ctk.CTkFont(family="Segoe UI", size=28, weight="bold"),
            text_color=TEXT_MAIN,
        ).grid(row=0, column=0, sticky="w")

        path_strs = game.get("save_paths", [])
        n_paths = len(path_strs)
        paths_summary = path_strs[0] if n_paths == 1 else f"{n_paths} locations tracked"
        ctk.CTkLabel(
            left_frame, text=f"📁  {paths_summary}",
            font=ctk.CTkFont(size=12), text_color=TEXT_DIM,
            wraplength=450, justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        # Size + Last Sync
        last = game.get("last_sync", "Never")
        sync_color = ACCENT2 if last != "Never" else TEXT_DIM
        
        sizes = game.get("last_backup_sizes", {"unzipped": 0, "zipped": 0})
        unzipped_mb = sizes.get("unzipped", 0) / 1024 / 1024
        zipped_mb = sizes.get("zipped", 0) / 1024 / 1024
        size_str = f"  •  Unzipped: {unzipped_mb:.2f} MB (Zipped: {zipped_mb:.2f} MB)" if unzipped_mb > 0 else ""

        ctk.CTkLabel(
            left_frame, text=f"Last synced:  {last}{size_str}",
            font=ctk.CTkFont(size=12), text_color=sync_color,
        ).grid(row=2, column=0, sticky="w", pady=(4, 8))

        # Exclusions + Auto-backup setting row
        settings_frame = ctk.CTkFrame(left_frame, fg_color=BG_CARD, corner_radius=10, height=80)
        settings_frame.grid(row=3, column=0, sticky="ew", pady=(4, 16))
        settings_frame.grid_columnconfigure(0, weight=1)

        # Switch for Auto-backup
        auto_var = ctk.BooleanVar(value=game.get("auto_backup", True))
        auto_sw = ctk.CTkSwitch(
            settings_frame, text="Auto-backup on changes", variable=auto_var,
            font=ctk.CTkFont(size=12), fg_color="#3a3a50", progress_color=ACCENT,
            command=lambda: self.config_mgr.set_game_auto_backup(name, auto_var.get())
        )
        auto_sw.grid(row=0, column=0, padx=16, pady=(12, 4), sticky="w")

        # Exclusions display
        ex_patterns = game.get("exclude_patterns", [])
        ex_text = ", ".join(ex_patterns) if ex_patterns else "None"
        ex_lbl = ctk.CTkLabel(
            settings_frame, text=f"Exclusions:  {ex_text}",
            font=ctk.CTkFont(size=11), text_color=TEXT_DIM,
            wraplength=340, justify="left"
        )
        ex_lbl.grid(row=1, column=0, padx=16, pady=(0, 12), sticky="w")

        edit_ex_btn = ctk.CTkButton(
            settings_frame, text="Edit", width=50, height=22, corner_radius=6,
            fg_color="#2a2a38", hover_color="#3a3a50", font=ctk.CTkFont(size=11),
            command=lambda: self._edit_exclusions(name)
        )
        edit_ex_btn.grid(row=1, column=1, padx=16, pady=(0, 12), sticky="e")

        # Sync Log box
        ctk.CTkLabel(
            left_frame, text="SYNC LOG",
            font=ctk.CTkFont(size=10, weight="bold"), text_color=TEXT_DIM,
        ).grid(row=4, column=0, sticky="w", pady=(0, 4))

        self.log_box = ctk.CTkTextbox(
            left_frame,
            font=ctk.CTkFont(family="Consolas", size=11),
            fg_color=BG_CARD, text_color="#9090aa", corner_radius=10,
        )
        self.log_box.grid(row=5, column=0, sticky="nsew")

        for entry in game.get("logs", [])[-60:]:
            self.log_box.insert("end", entry + "\n")
        self.log_box.see("end")

        # Action Buttons frame (at bottom of left frame)
        btn_row = ctk.CTkFrame(left_frame, fg_color="transparent")
        btn_row.grid(row=6, column=0, sticky="ew", pady=(14, 0))

        ctk.CTkButton(
            btn_row, text="☁  Backup", height=42, width=110, corner_radius=8,
            font=ctk.CTkFont(size=13, weight="bold"), fg_color=ACCENT, hover_color="#5a52e0",
            command=lambda: self._backup(name),
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            btn_row, text="👁  Preview", height=42, width=100, corner_radius=8,
            font=ctk.CTkFont(size=13), fg_color="#2a2a38", hover_color="#3a3a50",
            command=lambda: self._preview_backup(name),
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            btn_row, text="🗑  Remove", height=42, width=100, corner_radius=8,
            font=ctk.CTkFont(size=13), fg_color="#3a1a1a", hover_color="#2a0f0f", text_color=DANGER,
            command=lambda: self._remove_game(name),
        ).pack(side="left")

        # ── RIGHT COLUMN: BACKUP HISTORY ──────────────────────────────────────
        right_frame = ctk.CTkFrame(self.main, fg_color="transparent")
        right_frame.grid(row=0, column=1, sticky="nsew", padx=(12, 0))
        right_frame.grid_columnconfigure(0, weight=1)
        right_frame.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            right_frame, text="BACKUP HISTORY",
            font=ctk.CTkFont(size=11, weight="bold"), text_color=TEXT_DIM,
        ).grid(row=0, column=0, sticky="w", pady=(0, 6))

        self.history_frame = ctk.CTkScrollableFrame(
            right_frame, fg_color=BG_CARD, corner_radius=10,
            scrollbar_button_color="#2a2a38"
        )
        self.history_frame.grid(row=1, column=0, sticky="nsew")
        self.history_frame.grid_columnconfigure(0, weight=1)

        # Render loading placeholder, fetch from Drive in background
        self._show_history_loading()
        threading.Thread(target=lambda: self._load_history_async(name), daemon=True).start()

    # ── Game list ─────────────────────────────────────────────────────────────

    def _load_game_list(self):
        for w in self.games_list.winfo_children():
            w.destroy()
        self._game_btns.clear()

        games = self.config_mgr.get_all_games()
        if not games:
            ctk.CTkLabel(
                self.games_list,
                text="No games yet",
                font=ctk.CTkFont(size=12),
                text_color=TEXT_DIM,
            ).pack(pady=20)
            return

        for gname, gdata in games.items():
            card = GameCard(
                self.games_list,
                game_name=gname,
                last_sync=gdata.get("last_sync", "Never"),
                launcher_type=gdata.get("source", "DRM-Free"),
                on_click=self._select_game,
                is_selected=(gname == self.selected_game)
            )
            card.pack(fill="x", pady=4, padx=4)
            self._game_btns[gname] = card

    def _select_game(self, name: str):
        self.selected_game = name
        self._show_game_panel(name)
        for n, card in self._game_btns.items():
            card.set_selected(n == name)

    # ── Actions ───────────────────────────────────────────────────────────────

    def _open_add_dialog(self):
        dlg = AddGameDialog(self)
        self.wait_window(dlg)
        if dlg.result:
            name, paths = dlg.result
            self.config_mgr.add_game(name, paths)
            for p in paths:
                self.watcher.add_path(p, name)
            self._load_game_list()
            self._select_game(name)

    def _open_detect_dialog(self):
        dlg = DetectGamesDialog(self, self.config_mgr.get_all_games().keys())
        self.wait_window(dlg)
        if dlg.result:
            added_names = []
            for name, paths, src in dlg.result:
                self.config_mgr.add_game(name, paths, src)
                for p in paths:
                    self.watcher.add_path(p, name)
                added_names.append(name)
            self._load_game_list()
            if added_names:
                self._select_game(added_names[0])
                messagebox.showinfo("Success", f"Successfully auto-detected and added {len(added_names)} game(s) to SaveVault!", parent=self)

    def _backup_all(self):
        if not self.drive.is_authenticated():
            messagebox.showwarning("Not Connected", "Please connect Google Drive first.")
            return

        games = self.config_mgr.get_all_games()
        if not games:
            messagebox.showinfo("No Games", "You have not added any games to SaveVault yet.")
            return

        if not messagebox.askyesno(
            "Backup All Games",
            f"Are you sure you want to back up all {len(games)} tracked game(s) to Google Drive in the background?",
            parent=self
        ):
            return

        self.backup_all_btn.configure(text="☁  Backing up...", state="disabled")
        self._log("Starting batch backup of all games...")

        def _work():
            success_count = 0
            config_data = self.config_mgr.load_config()
            max_versions = config_data.get("max_versions", 5)

            for gname, gdata in games.items():
                self._log(f"[{gname}]  Starting batch backup...")
                paths = gdata.get("save_paths", [])
                exclude_patterns = gdata.get("exclude_patterns", [])

                res = self.drive.upload_save(gname, paths, exclude_patterns, max_versions)
                ok, msg, unzipped_sz, zipped_sz = res
                if ok:
                    ts = time.strftime("%Y-%m-%d  %H:%M:%S")
                    self.config_mgr.update_last_sync(gname, ts)
                    self.config_mgr.update_last_backup_sizes(gname, unzipped_sz, zipped_sz)
                    self._log(f"[{gname}]  ✓  {msg} — {ts}")
                    success_count += 1
                else:
                    self._log(f"[{gname}]  ✗  Failed: {msg}")

            def _done():
                self.backup_all_btn.configure(text="☁  Backup All Games", state="normal")
                self._log(f"Batch backup complete! Successfully backed up {success_count}/{len(games)} game(s).")
                if self.selected_game:
                    self._show_game_panel(self.selected_game)
                messagebox.showinfo("Backup Complete", f"Successfully backed up {success_count}/{len(games)} game(s) to Google Drive!", parent=self)

            self.after(0, _done)

        threading.Thread(target=_work, daemon=True).start()

    def _connect_drive(self):
        def _work():
            try:
                self.drive.authenticate()
                self.after(0, self._on_drive_connected)
            except FileNotFoundError as e:
                self.after(0, lambda: messagebox.showerror("Setup Required", str(e)))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Drive Error", str(e)))

        threading.Thread(target=_work, daemon=True).start()

    def _on_drive_connected(self):
        self.status_lbl.configure(text="● Connected to Drive", text_color=ACCENT2)
        self.connect_btn.configure(
            text="✓  Drive Connected",
            fg_color="#1a4a3a",
            text_color=ACCENT2,
            border_color="#1a4a3a",
        )
        self._log("Google Drive connected successfully.")

    def _backup(self, name: str):
        if not self.drive.is_authenticated():
            messagebox.showwarning("Not Connected", "Please connect Google Drive first.")
            return

        def _work():
            self._log(f"[{name}]  Backing up...")
            game = self.config_mgr.get_game(name)
            
            # Load exclusions
            exclude_patterns = game.get("exclude_patterns", [])
            # Load global max_versions
            config_data = self.config_mgr.load_config()
            max_versions = config_data.get("max_versions", 5)

            res = self.drive.upload_save(name, game.get("save_paths", []), exclude_patterns, max_versions)
            ok, msg, unzipped_sz, zipped_sz = res
            if ok:
                ts = time.strftime("%Y-%m-%d  %H:%M:%S")
                self.config_mgr.update_last_sync(name, ts)
                self.config_mgr.update_last_backup_sizes(name, unzipped_sz, zipped_sz)
                self._log(f"[{name}]  ✓  {msg} — {ts}")
                self.after(0, lambda: self._show_game_panel(name))
            else:
                self._log(f"[{name}]  ✗  Failed: {msg}")

        threading.Thread(target=_work, daemon=True).start()

    def _restore(self, name: str, version_folder_id: str | None = None, timestamp: str | None = None):
        if not self.drive.is_authenticated():
            messagebox.showwarning("Not Connected", "Please connect Google Drive first.")
            return

        target_desc = f"version from {timestamp}" if timestamp else "latest version"
        if not messagebox.askyesno(
            "Restore Save",
            f"This will overwrite your current local saves for:\n\n  {name}\n\nRestore {target_desc}?",
            parent=self
        ):
            return

        def _work():
            self._log(f"[{name}]  Restoring from Drive ({target_desc})...")
            game = self.config_mgr.get_game(name)
            ok, msg = self.drive.download_save(name, game.get("save_paths", []), version_folder_id)
            if ok:
                self._log(f"[{name}]  ✓  {msg}")
            else:
                self._log(f"[{name}]  ✗  Failed: {msg}")

        threading.Thread(target=_work, daemon=True).start()

    # ── History & Exclusions Helpers ──────────────────────────────────────────

    def _show_history_loading(self):
        for w in self.history_frame.winfo_children():
            w.destroy()
        ctk.CTkLabel(
            self.history_frame, text="Loading backup history...",
            font=ctk.CTkFont(size=12), text_color=TEXT_DIM
        ).pack(pady=40)

    def _load_history_async(self, name: str):
        versions = self.drive.list_backup_versions(name)
        self.after(0, lambda: self._render_history(name, versions))

    def _render_history(self, name: str, versions: list[dict]):
        for w in self.history_frame.winfo_children():
            w.destroy()

        if not versions:
            ctk.CTkLabel(
                self.history_frame, text="No backup history found on Drive.\nClick ☁ Backup to create one.",
                font=ctk.CTkFont(size=12), text_color=TEXT_DIM, justify="center"
            ).pack(pady=40)
            return

        for v in versions:
            v_frame = ctk.CTkFrame(self.history_frame, fg_color="#13131a", corner_radius=8)
            v_frame.pack(fill="x", pady=4, padx=2)
            v_frame.grid_columnconfigure(0, weight=1)

            # Details
            ts = v.get("timestamp", "Unknown Date")
            is_legacy = v.get("is_legacy", False)
            folder_id = v.get("folder_id")

            # Size text
            sz_str = ""
            unzipped_sz = v.get("unzipped_size", 0)
            zipped_sz = v.get("zipped_size", 0)
            if unzipped_sz > 0:
                sz_str = f"\n{unzipped_sz / 1024 / 1024:.2f} MB (zipped: {zipped_sz / 1024 / 1024:.2f} MB)"
            elif is_legacy:
                sz_str = "\nLegacy Format"

            ctk.CTkLabel(
                v_frame, text=f"⏱  {ts}{sz_str}",
                font=ctk.CTkFont(size=11), text_color=TEXT_MAIN,
                justify="left", anchor="w"
            ).grid(row=0, column=0, padx=12, pady=10, sticky="w")

            # Action buttons
            actions = ctk.CTkFrame(v_frame, fg_color="transparent")
            actions.grid(row=0, column=1, padx=12, pady=10, sticky="e")

            ctk.CTkButton(
                actions, text="Restore", width=60, height=26, corner_radius=6,
                fg_color="#1a4a3a", hover_color="#0f3028", text_color=ACCENT2,
                font=ctk.CTkFont(size=11),
                command=lambda fid=folder_id, t=ts: self._restore(name, fid, t)
            ).pack(side="left", padx=(0, 6))

            if not is_legacy:
                ctk.CTkButton(
                    actions, text="Delete", width=54, height=26, corner_radius=6,
                    fg_color="#3a1a1a", hover_color="#2a0f0f", text_color=DANGER,
                    font=ctk.CTkFont(size=11),
                    command=lambda fid=folder_id, t=ts: self._delete_version(name, fid, t)
                ).pack(side="left")

    def _delete_version(self, game_name: str, version_folder_id: str, timestamp: str):
        if not messagebox.askyesno(
            "Delete Backup",
            f"Are you sure you want to permanently delete the backup version from:\n\n  {timestamp}?\n\nThis cannot be undone.",
            parent=self
        ):
            return

        def _work():
            self._log(f"[{game_name}]  Deleting version from {timestamp}...")
            ok, msg = self.drive.delete_backup_version(game_name, version_folder_id)
            if ok:
                self._log(f"[{game_name}]  ✓  {msg}")
                self.after(0, lambda: self._show_game_panel(game_name))
            else:
                self._log(f"[{game_name}]  ✗  Failed: {msg}")

        threading.Thread(target=_work, daemon=True).start()

    def _edit_exclusions(self, name: str):
        game = self.config_mgr.get_game(name)
        dlg = EditExclusionsDialog(self, name, game.get("exclude_patterns", []))
        self.wait_window(dlg)
        if dlg.result is not None:
            self.config_mgr.set_game_exclude_patterns(name, dlg.result)
            self._log(f"[{name}]  Exclusions updated: {', '.join(dlg.result) if dlg.result else 'None'}")
            self._show_game_panel(name)

    def _preview_backup(self, name: str):
        game = self.config_mgr.get_game(name)
        dlg = PreviewDialog(
            self, name, game.get("save_paths", []), game.get("exclude_patterns", [])
        )
        self.wait_window(dlg)
        if dlg.confirm_backup:
            self._backup(name)

    def _remove_game(self, name: str):
        if messagebox.askyesno(
            "Remove Game",
            f"Remove  {name}  from SaveVault?\n\n(Drive backups are NOT deleted)",
        ):
            self.config_mgr.remove_game(name)
            self.selected_game = None
            self._load_game_list()
            self._show_welcome()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _clear_main(self):
        for w in self.main.winfo_children():
            w.destroy()

    def _log(self, message: str):
        ts    = time.strftime("%H:%M:%S")
        entry = f"[{ts}]  {message}"

        if self.selected_game:
            self.config_mgr.add_log(self.selected_game, entry)

        if hasattr(self, "log_box"):
            self.after(
                0,
                lambda e=entry: (
                    self.log_box.insert("end", e + "\n"),
                    self.log_box.see("end"),
                ),
            )

    def _on_save_changed(self, game_name: str, path: str):
        if self.config_mgr.get_auto_sync() and self.drive.is_authenticated():
            game = self.config_mgr.get_game(game_name)
            if not game.get("auto_backup", True):
                self._log(f"[{game_name}]  Change detected, but auto-backup is disabled for this game.")
                return
            self._log(f"[{game_name}]  Change detected — auto-syncing…")
            self._backup(game_name)

    def _start_background(self):
        games = self.config_mgr.get_all_games()
        for gname, gdata in games.items():
            for p in gdata.get("save_paths", []):
                self.watcher.add_path(p, gname)

        threading.Thread(target=self.watcher.start, daemon=True).start()

        # Auto-reconnect if token exists
        if TOKEN_FILE.exists():
            threading.Thread(target=self._silent_reconnect, daemon=True).start()

    def _silent_reconnect(self):
        try:
            self.drive.authenticate()
            self.after(0, self._on_drive_connected)
        except Exception:
            pass

    def _refresh_manifest(self):
        """Force-download the latest Ludusavi game database in the background."""
        self.refresh_db_btn.configure(text="↻  Updating…", state="disabled")

        def _work():
            ok = ludusavi.download_manifest(force=True)
            if ok:
                age = ludusavi.get_cache_age_days()
                msg = f"↻  Database updated ({ludusavi.CACHE_FILE.stat().st_size // 1024} KB)"
            else:
                msg = "↻  Update failed — using cached data"
            self.after(0, lambda m=msg: self._refresh_done(m, ok))

        threading.Thread(target=_work, daemon=True).start()

    def _refresh_done(self, label: str, success: bool):
        color = ACCENT2 if success else DANGER
        self.refresh_db_btn.configure(
            text=label,
            state="normal",
            text_color=color,
        )
        # Reset button text after 4 seconds
        self.after(4000, lambda: self.refresh_db_btn.configure(
            text="↻  Refresh Game Database",
            text_color=TEXT_DIM,
        ))



if __name__ == "__main__":
    app = SaveVaultApp()
    app.mainloop()
