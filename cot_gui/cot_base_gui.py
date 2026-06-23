"""
cot_gui/cot_base_gui.py  v2.4
Shared base for Video Toolkit tool windows.

SCRIPTS_DIR is read from the COT_SCRIPTS_DIR environment variable,
which master_launcher.py sets to the toolkit folder before spawning
any child process. This avoids __file__ resolving incorrectly on Windows.
"""

import os
import sys
import subprocess

# ── Resolve toolkit folder reliably ──────────────────────────
# master_launcher sets COT_SCRIPTS_DIR = path to toolkit folder
SCRIPTS_DIR = os.environ.get(
    "COT_SCRIPTS_DIR",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
sys.path.insert(0, SCRIPTS_DIR)

import customtkinter as ctk
import json

_POPEN_FLAGS = {}
if sys.platform == "win32":
    _POPEN_FLAGS["creationflags"] = subprocess.CREATE_NO_WINDOW

MASTER_CONFIG_PATH = os.path.join(SCRIPTS_DIR, "master_config.json")


def _get_theme():
    try:
        with open(MASTER_CONFIG_PATH) as f:
            cfg = json.load(f)
        return cfg.get("theme", "system"), cfg.get("accent", "blue")
    except Exception:
        return "system", "blue"


_theme, _accent = _get_theme()
ctk.set_appearance_mode(_theme)
ctk.set_default_color_theme(_accent)

LOG_COLORS = {
    "success": "#4CAF50",
    "error":   "#F44336",
    "info":    "#9E9E9E",
    "header":  "#4FC3F7",
    "normal":  "",
 }

def _focus_window(win):
    """
    Reliably bring a window to the front on Windows.
    - topmost True forces it above everything including taskbar
    - lift + focus_force claim keyboard focus
    - Two-stage release: stay topmost for 600ms so Windows finishes
      compositing, then release so it behaves normally afterwards
    """
    def _do():
        try:
            win.attributes("-topmost", True)
            win.lift()
            win.focus_force()
            win.after(600, _release)
        except Exception:
            pass

    def _release():
        try:
            win.attributes("-topmost", False)
            win.lift()
        except Exception:
            pass

    win.after(80, _do)

class CotBaseWindow(ctk.CTk):
    """
    Row layout:
      0 — nav bar  (Back to Launcher | title)
      1 — options_frame  (subclass fills)
      2 — buttons_frame  (subclass fills)
      3 — log textbox    (expands)
      4 — progress bar
      5 — status bar
    """

    def __init__(self, title: str, subtitle: str = "",
                 width: int = 700, height: int = 580):
        super().__init__()
        self.title(title)
        self.geometry(f"{width}x{height}")
        self.minsize(560, 460)
        self._build_shell(title, subtitle)
        _focus_window(self)

    def _build_shell(self, title: str, subtitle: str):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        # ── Row 0: Nav bar ────────────────────────────────────────
        nav = ctk.CTkFrame(self, corner_radius=0, height=46)
        nav.grid(row=0, column=0, sticky="ew")
        nav.grid_propagate(False)
        nav.grid_columnconfigure(1, weight=1)

        ctk.CTkButton(
            nav, text="< Back to Launcher", width=140, height=30,
            fg_color="transparent", border_width=1,
            command=self._back_to_launcher,
        ).grid(row=0, column=0, padx=(10, 6), pady=8, sticky="w")

        ctk.CTkLabel(
            nav,
            text=title + (f"  |  {subtitle}" if subtitle else ""),
            font=ctk.CTkFont(size=13, weight="bold"), anchor="w"
        ).grid(row=0, column=1, sticky="w", padx=4, pady=8)

        # ── Row 1: Options ────────────────────────────────────────
        self.options_frame = ctk.CTkFrame(self)
        self.options_frame.grid(row=1, column=0, sticky="ew",
                                padx=14, pady=(10, 0))
        self.options_frame.grid_columnconfigure(0, weight=1)

        # ── Row 2: Buttons ────────────────────────────────────────
        self.buttons_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.buttons_frame.grid(row=2, column=0, sticky="ew",
                                padx=14, pady=(8, 0))
        self.buttons_frame.grid_columnconfigure((0, 1, 2), weight=1)

        # ── Row 3: Log ────────────────────────────────────────────
        log_wrap = ctk.CTkFrame(self)
        log_wrap.grid(row=3, column=0, sticky="nsew", padx=14, pady=(8, 0))
        log_wrap.grid_columnconfigure(0, weight=1)
        log_wrap.grid_rowconfigure(0, weight=1)

        self._log_box = ctk.CTkTextbox(
            log_wrap,
            font=ctk.CTkFont(family="Courier New", size=11),
            wrap="word", state="disabled",
        )
        self._log_box.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)

        # ── Row 4: Progress bar ───────────────────────────────────
        self._progress = ctk.CTkProgressBar(self, mode="indeterminate")
        self._progress.grid(row=4, column=0, sticky="ew", padx=14, pady=(4, 0))
        self._progress.grid_remove()

        # ── Row 5: Status bar ─────────────────────────────────────
        sbar = ctk.CTkFrame(self, height=26, corner_radius=0)
        sbar.grid(row=5, column=0, sticky="ew", pady=(4, 0))
        sbar.grid_propagate(False)
        sbar.grid_columnconfigure(0, weight=1)
        self._status_label = ctk.CTkLabel(
            sbar, text="Ready",
            font=ctk.CTkFont(size=10), text_color="gray", anchor="w"
        )
        self._status_label.grid(row=0, column=0, sticky="w", padx=10)

    # ── Public API ────────────────────────────────────────────────

    def log(self, msg: str, kind: str = "normal"):
        self.after(0, self._append_log, msg, kind)

    def set_status(self, msg: str):
        self.after(0, self._status_label.configure, {"text": msg})

    def show_progress(self):
        self.after(0, self._progress.grid)
        self.after(0, self._progress.start)

    def hide_progress(self):
        self.after(0, self._progress.stop)
        self.after(0, self._progress.grid_remove)

    def clear_log(self):
        self.after(0, self._clear_log)

    # ── Internal ──────────────────────────────────────────────────

    def _append_log(self, msg: str, kind: str):
        self._log_box.configure(state="normal")
        color = LOG_COLORS.get(kind, "")
        if color:
            tag = f"tag_{kind}"
            self._log_box.tag_config(tag, foreground=color)
            self._log_box.insert("end", msg + "\n", tag)
        else:
            self._log_box.insert("end", msg + "\n")
        self._log_box.configure(state="disabled")
        self._log_box.see("end")

    def _clear_log(self):
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")

    def _back_to_launcher(self):
        launcher = os.path.join(SCRIPTS_DIR, "master_launcher.py")
        env = os.environ.copy()
        env["COT_SCRIPTS_DIR"] = SCRIPTS_DIR

        if os.path.isfile(launcher):
            subprocess.Popen(
                [sys.executable, launcher],
                cwd=SCRIPTS_DIR,
                env=env,
                **_POPEN_FLAGS
            )

        self.destroy()
