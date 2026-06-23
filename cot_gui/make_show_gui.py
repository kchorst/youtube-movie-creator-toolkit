"""
cot_gui/make_show_gui.py
Make Show — GUI wrapper around make_show.py

Modes A, C, D are interactive (need terminal input) — launched in a
real console window via the launcher's CLI mechanism.
Mode B (Batch silent) needs no input and runs fully in the GUI thread.
"""

import os
import sys
import json
import subprocess
import threading
import io
import subprocess
import time

SCRIPTS_DIR = os.environ.get(
    "COT_SCRIPTS_DIR",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
sys.path.insert(0, SCRIPTS_DIR)

import customtkinter as ctk
from tkinter import filedialog, messagebox
from cot_gui.cot_base_gui import CotBaseWindow, SCRIPTS_DIR
from cot_core.launch_utils import launch_interactive_windows
from cot_core.logging_utils import log_exception
from cot_core.crash_utils import install_global_crash_handler
from cot_core.video_audio_core import AUDIO_EXTS, detect_audio_for_folder

try:
    import cot_config as cfg
    cfg.load(gui_mode=True)
    HAS_CONFIG = True
except ImportError:
    HAS_CONFIG = False

# Windows process flags
CREATE_NEW_CONSOLE = 0x00000010
CREATE_NO_WINDOW   = 0x08000000


class MakeShowGui(CotBaseWindow):
    def __init__(self):
        super().__init__(
            title="Video Creator",
            subtitle="Create videos from media folders or add sound workflows",
            width=700, height=640,
        )
        self._launch_guard = False
        self._pause_event = threading.Event()
        self._stop_event = threading.Event()
        self._build_options()
        self._build_action_buttons()

        try:
            self._apply_project_from_env()
        except Exception:
            pass
        try:
            self._auto_select_audio_for_current_project()
        except Exception:
            pass

    def _apply_project_from_env(self) -> None:
        requested_mode = (os.environ.get("COT_MAKE_SHOW_MODE") or "").strip().upper()
        mode_map = {
            "E": "E — Create movie from media folder",
            "F": "F — Batch create movies from folders",
            "B": "B — Batch silent (no audio, automatic)",
            "C": "C — Batch audio (one audio track, interactive)",
            "D": "D — Add sound to existing movies (terminal)",
        }
        if requested_mode in mode_map:
            try:
                self._mode_var.set(mode_map[requested_mode])
                self._on_mode_select(self._mode_var.get())
            except Exception:
                pass

        p = (os.environ.get("COT_PROJECT_PATH") or "").strip()
        if not p or not os.path.isdir(p):
            return
        p = os.path.normpath(p)
        root = os.path.dirname(p)
        if root and os.path.isdir(root):
            try:
                self._root_var.set(root)
            except Exception:
                pass
        try:
            if not requested_mode:
                self._mode_var.set("E — Create movie from media folder")
                self._on_mode_select(self._mode_var.get())
        except Exception:
            pass
        try:
            self._project_var.set(p)
        except Exception:
            pass

    def _get_output_dims(self):
        v = (getattr(self, "_resolution_var", None).get() if hasattr(self, "_resolution_var") else "")
        s = (v or "").strip().lower()
        if s.startswith("720"):
            return 1280, 720
        if s.startswith("480"):
            return 854, 480
        return 1920, 1080

    def _get_duck_db(self) -> float:
        v = (getattr(self, "_e_duck_var", None).get() if hasattr(self, "_e_duck_var") else "")
        v = (v or "").strip()
        m = {
            "Mute (-60 dB)": -60.0,
            "Low (-24 dB)": -24.0,
            "Medium (-18 dB)": -18.0,
            "High (-12 dB)": -12.0,
            "Very high (-6 dB)": -6.0,
        }
        return float(m.get(v, -18.0))

    def _persist_config(self, **updates) -> None:
        """Persist lightweight user choices without interrupting the GUI."""
        if not HAS_CONFIG:
            return
        try:
            for key, value in updates.items():
                cfg.set(key, value, save_now=False)
            cfg.save()
        except Exception:
            pass

    def _build_options(self):
        ctk.CTkLabel(
            self.options_frame, text="Video Settings",
            font=ctk.CTkFont(size=12, weight="bold"), anchor="w"
        ).grid(row=0, column=0, columnspan=4, sticky="w", padx=12, pady=(10, 6))

        # BPM
        ctk.CTkLabel(self.options_frame, text="BPM:", anchor="w"
                     ).grid(row=1, column=0, sticky="w", padx=12, pady=6)

        self._bpm_var = ctk.StringVar(value="120")
        ctk.CTkOptionMenu(
            self.options_frame,
            values=["60", "90", "120", "150", "180", "Custom"],
            variable=self._bpm_var,
            command=self._on_bpm_select,
            width=120,
        ).grid(row=1, column=1, sticky="w", padx=8, pady=6)

        self._custom_bpm = ctk.CTkEntry(self.options_frame, width=80,
                                         placeholder_text="e.g. 128")
        self._custom_bpm.grid(row=1, column=2, padx=8, pady=6)
        self._custom_bpm.grid_remove()

        ctk.CTkLabel(self.options_frame,
                     text="Multiples of 30 give perfect frame-exact sync.",
                     font=ctk.CTkFont(size=10), text_color="gray"
                     ).grid(row=1, column=3, sticky="w", padx=4, pady=6)

        ctk.CTkLabel(self.options_frame, text="Output resolution:", anchor="w").grid(
            row=2, column=0, sticky="w", padx=12, pady=6
        )
        self._resolution_var = ctk.StringVar(value="1080p (1920x1080)")
        ctk.CTkOptionMenu(
            self.options_frame,
            values=["1080p (1920x1080)", "720p (1280x720)", "480p (854x480)"],
            variable=self._resolution_var,
            width=180,
        ).grid(row=2, column=1, sticky="w", padx=8, pady=6)

        # Pictures folder
        self._root_label = ctk.CTkLabel(self.options_frame, text="Pictures folder:", anchor="w")
        self._root_label.grid(row=3, column=0, sticky="w", padx=12, pady=6)
        default_root = cfg.get("PICTURES_DIR", "") if HAS_CONFIG else ""
        self._root_var = ctk.StringVar(value=default_root)
        ctk.CTkEntry(self.options_frame, textvariable=self._root_var, width=280
                     ).grid(row=3, column=1, columnspan=2, sticky="w", padx=8, pady=6)
        ctk.CTkButton(self.options_frame, text="Browse", width=80,
                      command=self._browse_root
                      ).grid(row=3, column=3, padx=4, pady=6)

        self._project_label = ctk.CTkLabel(self.options_frame, text="Project:", anchor="w")
        self._project_label.grid(row=4, column=0, sticky="w", padx=12, pady=6)
        self._project_var = ctk.StringVar(value="")
        self._project_menu = ctk.CTkOptionMenu(
            self.options_frame,
            values=[""],
            variable=self._project_var,
            width=260,
        )
        self._project_menu.grid(row=4, column=1, sticky="w", padx=8, pady=6)
        self._project_refresh_btn = ctk.CTkButton(
            self.options_frame,
            text="Refresh",
            width=80,
            command=self._refresh_projects,
        )
        self._project_refresh_btn.grid(row=4, column=2, padx=4, pady=6, sticky="w")
        self._project_browse_btn = ctk.CTkButton(
            self.options_frame,
            text="Browse",
            width=80,
            command=self._browse_project,
        )
        self._project_browse_btn.grid(row=4, column=3, padx=4, pady=6)

        # Mode
        ctk.CTkLabel(self.options_frame, text="Mode:", anchor="w"
                     ).grid(row=5, column=0, sticky="w", padx=12, pady=6)
        self._mode_var = ctk.StringVar(value="E — Create movie from media folder")
        ctk.CTkOptionMenu(
            self.options_frame,
            values=[
                "E — Create movie from media folder",
                "F — Batch create movies from folders",
                "D — Add sound to existing movies (terminal)",
                "B — Batch silent (no audio, automatic)",
                "C — Batch audio (one audio track, interactive)",
                "A — Normal (folder by folder, interactive)",
            ],
            variable=self._mode_var,
            command=self._on_mode_select,
            width=360,
        ).grid(row=5, column=1, columnspan=3, sticky="w", padx=8, pady=(6, 4))

        self._mode_hint = ctk.CTkLabel(
            self.options_frame,
            text="Choose the simple folder-to-movie path first; advanced modes remain available below.",
            font=ctk.CTkFont(size=10), text_color="gray", anchor="w"
        )
        self._mode_hint.grid(row=6, column=0, columnspan=4, sticky="w", padx=12, pady=(0, 10))

        self._e_include_flipbooks_var = ctk.BooleanVar(value=True)
        self._e_include_flipbooks_cb = ctk.CTkCheckBox(
            self.options_frame,
            text="Include flipbooks (optional)",
            variable=self._e_include_flipbooks_var,
        )
        self._e_include_flipbooks_cb.grid(row=7, column=0, columnspan=4, sticky="w", padx=12, pady=(0, 6))

        self._e_use_music_var = ctk.BooleanVar(value=True)
        self._e_use_music_cb = ctk.CTkCheckBox(
            self.options_frame,
            text="Add music (optional)",
            variable=self._e_use_music_var,
            command=self._on_e_use_music_toggle,
        )
        self._e_use_music_cb.grid(row=8, column=0, sticky="w", padx=12, pady=6)

        self._e_auto_music_var = ctk.BooleanVar(value=True)
        self._e_auto_music_cb = ctk.CTkCheckBox(
            self.options_frame,
            text="Auto-detect audio in folder",
            variable=self._e_auto_music_var,
            command=self._on_e_use_music_toggle,
        )
        self._e_auto_music_cb.grid(row=8, column=1, sticky="w", padx=8, pady=6)
        self._e_audio_var = ctk.StringVar(value="")
        self._e_audio_entry = ctk.CTkEntry(
            self.options_frame,
            textvariable=self._e_audio_var,
            width=280,
            placeholder_text="Select an mp3 or wav",
        )
        self._e_audio_entry.grid(row=9, column=1, columnspan=2, sticky="w", padx=8, pady=6)
        self._e_audio_browse = ctk.CTkButton(self.options_frame, text="Browse", width=80, command=self._browse_audio)
        self._e_audio_browse.grid(row=9, column=3, padx=4, pady=6)

        self._e_duck_label = ctk.CTkLabel(self.options_frame, text="Flipbook audio under music:", anchor="w")
        self._e_duck_label.grid(row=10, column=0, sticky="w", padx=12, pady=(0, 6))
        self._e_duck_var = ctk.StringVar(value="Medium (-18 dB)")
        self._e_duck_menu = ctk.CTkOptionMenu(
            self.options_frame,
            values=["Mute (-60 dB)", "Low (-24 dB)", "Medium (-18 dB)", "High (-12 dB)", "Very high (-6 dB)"],
            variable=self._e_duck_var,
            width=180,
        )
        self._e_duck_menu.grid(row=10, column=1, sticky="w", padx=8, pady=(0, 6))

        self._project_label.grid_remove()
        self._project_menu.grid_remove()
        self._project_refresh_btn.grid_remove()
        self._project_browse_btn.grid_remove()

        self._e_include_flipbooks_cb.grid_remove()
        self._e_use_music_cb.grid_remove()
        self._e_auto_music_cb.grid_remove()
        self._e_audio_entry.grid_remove()
        self._e_audio_browse.grid_remove()
        self._e_duck_label.grid_remove()
        self._e_duck_menu.grid_remove()

        self._on_e_use_music_toggle()

        self._on_mode_select(self._mode_var.get())


    def _on_mode_select(self, value):
        mode_letter = (value or "").strip()[:1].upper()
        if mode_letter == "E":
            self._root_label.configure(text="Pictures folder:")
            self._mode_hint.configure(
                text="Create ONE MP4 from the selected media folder. Audio can be auto-detected from the folder or selected manually."
            )
            self._project_label.grid()
            self._project_menu.grid()
            self._project_refresh_btn.grid()
            self._project_browse_btn.grid()
            self._refresh_projects()
            self._e_include_flipbooks_cb.grid()
            self._e_use_music_cb.grid()
            self._e_auto_music_cb.grid()
            self._e_audio_entry.grid()
            self._e_audio_browse.grid()
            self._e_duck_label.grid()
            self._e_duck_menu.grid()
            self._on_e_use_music_toggle()
        elif mode_letter == "F":
            self._root_label.configure(text="Projects root folder:")
            self._mode_hint.configure(
                text="Create one MP4 for EACH immediate subfolder. Each folder can use its own detected audio, or one selected audio file for all."
            )
            self._project_label.grid_remove()
            self._project_menu.grid_remove()
            self._project_refresh_btn.grid_remove()
            self._project_browse_btn.grid_remove()
            self._e_include_flipbooks_cb.grid()
            self._e_use_music_cb.grid()
            self._e_auto_music_cb.grid()
            self._e_audio_entry.grid()
            self._e_audio_browse.grid()
            self._e_duck_label.grid()
            self._e_duck_menu.grid()
            self._on_e_use_music_toggle()
        else:
            self._root_label.configure(text="Pictures folder:")
            self._mode_hint.configure(text="Advanced/legacy modes may open a terminal window for input.")
            self._project_label.grid_remove()
            self._project_menu.grid_remove()
            self._project_refresh_btn.grid_remove()
            self._project_browse_btn.grid_remove()
            self._e_include_flipbooks_cb.grid_remove()
            self._e_use_music_cb.grid_remove()
            self._e_auto_music_cb.grid_remove()
            self._e_audio_entry.grid_remove()
            self._e_audio_browse.grid_remove()
            self._e_duck_label.grid_remove()
            self._e_duck_menu.grid_remove()


    def _on_e_use_music_toggle(self):
        on = bool(self._e_use_music_var.get())
        auto = bool(getattr(self, "_e_auto_music_var", None).get()) if hasattr(self, "_e_auto_music_var") else False
        manual_state = "normal" if (on and not auto) else "disabled"
        auto_state = "normal" if on else "disabled"
        try:
            self._e_auto_music_cb.configure(state=auto_state)
        except Exception:
            pass
        self._e_audio_entry.configure(state=manual_state)
        self._e_audio_browse.configure(state=manual_state)
        try:
            self._e_duck_menu.configure(state="normal" if on else "disabled")
        except Exception:
            pass

    def _on_bpm_select(self, value):
        if value == "Custom":
            self._custom_bpm.grid()
        else:
            self._custom_bpm.grid_remove()

    def _browse_root(self):
        folder = filedialog.askdirectory(title="Select pictures root folder")
        if folder:
            self._root_var.set(folder)
            self._persist_config(PICTURES_DIR=folder, LAST_PROJECT_ROOT=folder)
            mode_letter = (self._mode_var.get() or "").strip()[:1].upper()
            if mode_letter == "E":
                self._refresh_projects()

    def _browse_project(self):
        root = self._root_var.get().strip()
        try:
            folder = filedialog.askdirectory(
                title="Select project folder",
                initialdir=root if root and os.path.isdir(root) else None,
            )
        except TypeError:
            folder = filedialog.askdirectory(title="Select project folder")
        if folder:
            self._project_var.set(folder)
            self._persist_config(LAST_PROJECT_FOLDER=folder, LAST_PROJECT_ROOT=os.path.dirname(folder))
            self._auto_select_audio_for_current_project()

    def _refresh_projects(self):
        root = self._root_var.get().strip()
        values = [""]
        if root and os.path.isdir(root):
            try:
                values += sorted(
                    [
                        d
                        for d in os.listdir(root)
                        if os.path.isdir(os.path.join(root, d)) and not d.startswith(".")
                    ],
                    key=lambda s: s.lower(),
                )
            except Exception:
                pass

        cur = (self._project_var.get() or "").strip()
        if cur and os.path.isabs(cur) and os.path.isdir(cur) and cur not in values:
            values.append(cur)

        if len(values) == 1:
            values = [""]

        try:
            self._project_menu.configure(values=values)
        except Exception:
            pass

        if self._project_var.get() not in values:
            self._project_var.set(values[0])

    def _resolve_project_path(self):
        root = self._root_var.get().strip()
        proj = self._project_var.get().strip()

        if proj and os.path.isabs(proj) and os.path.isdir(proj):
            return proj

        if root and proj:
            p = os.path.join(root, proj)
            if os.path.isdir(p):
                return p

        return None

    def _auto_select_audio_for_current_project(self):
        if not hasattr(self, "_e_auto_music_var") or not bool(self._e_auto_music_var.get()):
            return None
        project = self._resolve_project_path() or self._root_var.get().strip()
        if not project or not os.path.isdir(project):
            return None
        audio = detect_audio_for_folder(project)
        if audio:
            try:
                self._e_audio_var.set(audio)
            except Exception:
                pass
        return audio

    def _browse_audio(self):
        path = filedialog.askopenfilename(
            title="Select music file",
            filetypes=[("Audio", "*.mp3 *.wav *.m4a *.aac *.flac *.ogg"), ("All files", "*.*")],
        )
        if path:
            self._e_audio_var.set(path)
            self._persist_config(AUDIO_DIR=os.path.dirname(path))

    def _build_action_buttons(self):
        self.run_btn = ctk.CTkButton(
            self.buttons_frame, text="Create / Process Video",
            command=self._run,
            font=ctk.CTkFont(size=13, weight="bold"), height=38,
        )
        self.run_btn.grid(row=0, column=0, padx=(0, 6), pady=8, sticky="ew")

        self.pause_btn = ctk.CTkButton(
            self.buttons_frame,
            text="Pause",
            command=self._toggle_pause,
            state="disabled",
        )
        self.pause_btn.grid(row=0, column=1, padx=6, pady=8, sticky="ew")

        self.stop_btn = ctk.CTkButton(
            self.buttons_frame,
            text="Stop",
            command=self._stop_run,
            fg_color="#F44336",
            hover_color="#D32F2F",
            text_color="black",
            text_color_disabled="black",
            state="disabled",
        )
        self.stop_btn.grid(row=0, column=2, padx=(6, 0), pady=8, sticky="ew")

    def _toggle_pause(self):
        if self._pause_event.is_set():
            self._pause_event.clear()
            self.pause_btn.configure(text="Pause")
            self.set_status("Running...")
            self.log("Resumed.", "info")
        else:
            self._pause_event.set()
            self.pause_btn.configure(text="Resume")
            self.set_status("Paused")
            self.log("Paused.", "info")

    def _stop_run(self):
        self._stop_event.set()
        self.set_status("Stopping...")
        self.log("Stop requested...", "info")

    def _get_bpm(self):
        val = self._bpm_var.get()
        if val == "Custom":
            try:
                return int(self._custom_bpm.get())
            except ValueError:
                return None
        return int(val)

    def _run(self):
        bpm = self._get_bpm()
        if bpm is None:
            messagebox.showerror("Invalid BPM", "Enter a valid custom BPM number.")
            return

        out_w, out_h = self._get_output_dims()

        mode_letter = self._mode_var.get()[0]
        root = self._root_var.get().strip()
        self._persist_config(PICTURES_DIR=root, LAST_PROJECT_ROOT=root, WORKFLOW_MODE={"A":"single_folder","B":"batch_folders","C":"batch_folders","D":"single_or_batch","E":"single_folder","F":"batch_folders"}.get(mode_letter, "single_or_batch"))

        if mode_letter not in ("D", "E") and (not root or not os.path.isdir(root)):
            messagebox.showerror("No Folder", "Please select a valid pictures folder.")
            return

        # Mode E — project mixed show (one folder)
        if mode_letter == "E":
            project_root = self._resolve_project_path()
            if not project_root:
                messagebox.showerror("No Project", "Please select a valid project folder.")
                return

            try:
                if root and os.path.isdir(root):
                    if os.path.abspath(project_root).lower() == os.path.abspath(root).lower():
                        proceed = messagebox.askokcancel(
                            "Large Folder Warning",
                            "Mode E is set to render your entire Pictures folder:\n\n"
                            f"{project_root}\n\n"
                            "This may process thousands of images. Continue?",
                        )
                        if not proceed:
                            return
            except Exception:
                pass

            include_flipbooks = bool(self._e_include_flipbooks_var.get())

            audio_path = None
            if bool(self._e_use_music_var.get()):
                ap = (self._e_audio_var.get() or "").strip()
                if bool(self._e_auto_music_var.get()):
                    ap = detect_audio_for_folder(project_root) or ap
                    if ap:
                        self._e_audio_var.set(ap)
                if ap and os.path.isfile(ap):
                    audio_path = ap
                    self._persist_config(AUDIO_DIR=os.path.dirname(ap))
                else:
                    proceed = messagebox.askokcancel(
                        "No Audio Found",
                        "Music is enabled, but no audio file was found in the selected media folder.\n\nContinue and create a silent movie?",
                    )
                    if not proceed:
                        return

            duck_db = self._get_duck_db()

            self.clear_log()
            self.show_progress()
            self.set_status("Running mixed show...")
            self.run_btn.configure(state="disabled")
            self.pause_btn.configure(state="normal", text="Pause")
            self.stop_btn.configure(state="normal")
            self._stop_event.clear()
            self._pause_event.clear()
            self._persist_config(LAST_PROJECT_FOLDER=project_root, LAST_PROJECT_ROOT=os.path.dirname(project_root))
            self.log("Create movie from media folder", "info")
            self.log(f"BPM: {bpm}  |  Project: {project_root}", "info")
            self.log(f"Output: {out_w}x{out_h}", "info")
            self.log("-" * 48, "info")
            threading.Thread(
                target=self._run_project_mixed,
                args=(bpm, project_root, include_flipbooks, audio_path, duck_db, out_w, out_h),
                daemon=True,
            ).start()
            return

        if mode_letter == "F":
            include_flipbooks = bool(self._e_include_flipbooks_var.get())

            audio_path = None
            auto_audio = bool(self._e_auto_music_var.get())
            if bool(self._e_use_music_var.get()):
                ap = (self._e_audio_var.get() or "").strip()
                if not auto_audio and ap and os.path.isfile(ap):
                    audio_path = ap
                    self._persist_config(AUDIO_DIR=os.path.dirname(ap))
                elif not auto_audio:
                    messagebox.showerror("No Music", "Choose an audio file, enable auto-detect, or turn off Add music.")
                    return

            duck_db = self._get_duck_db()

            try:
                subs = [
                    e
                    for e in os.scandir(root)
                    if e.is_dir() and (not e.name.startswith("."))
                ]
            except Exception:
                subs = []

            if not subs:
                messagebox.showerror("No Projects", "No subfolders found under the selected root folder.")
                return

            proceed = messagebox.askokcancel(
                "Mixed Batch",
                "Mode F will render each immediate subfolder under:\n\n"
                f"{root}\n\n"
                f"Found {len(subs)} subfolder(s).\n\n"
                "Folders with an existing *_mixed.mp4 output will be skipped.\n\n"
                "Continue?",
            )
            if not proceed:
                return

            self.clear_log()
            self.show_progress()
            self.set_status("Running mixed batch...")
            self.run_btn.configure(state="disabled")
            self.pause_btn.configure(state="normal", text="Pause")
            self.stop_btn.configure(state="normal")
            self._stop_event.clear()
            self._pause_event.clear()
            self.log("Batch create movies from folders", "info")
            self.log(f"BPM: {bpm}  |  Root: {root}", "info")
            self.log(f"Output: {out_w}x{out_h}", "info")
            self.log("-" * 48, "info")
            threading.Thread(
                target=self._run_mixed_batch,
                args=(bpm, root, include_flipbooks, audio_path, duck_db, out_w, out_h, auto_audio),
                daemon=True,
            ).start()
            return

        # Modes A, C, D are interactive — launch in terminal
        if mode_letter in ("A", "C", "D"):
            self._launch_interactive(bpm, mode_letter, root, out_w, out_h)
            return

        # Mode B — batch silent, no input() calls, runs in GUI thread
        self.clear_log()
        self.show_progress()
        self.set_status("Running batch silent render...")
        self.run_btn.configure(state="disabled")
        self.pause_btn.configure(state="normal", text="Pause")
        self.stop_btn.configure(state="normal")
        self._stop_event.clear()
        self._pause_event.clear()
        self.log(f"Mode B — Batch Silent", "info")
        self.log(f"BPM: {bpm}  |  Root: {root}", "info")
        self.log(f"Output: {out_w}x{out_h}", "info")
        self.log("-" * 48, "info")
        threading.Thread(target=self._run_batch_silent,
                         args=(bpm, root, out_w, out_h), daemon=True).start()


    def _run_project_mixed(self, bpm, project_root, include_flipbooks, audio_path, duck_db, out_w, out_h):
        old_stdout = sys.stdout

        import io

        class Writer(io.TextIOBase):
            def __init__(self, log_fn):
                self._log_fn = log_fn
                self._buf = ""

            def write(self, s):
                self._buf += s
                while "\n" in self._buf:
                    line, self._buf = self._buf.split("\n", 1)
                    if line.strip():
                        self._log_fn(line, "normal")
                return len(s)

            def flush(self):
                if self._buf.strip():
                    self._log_fn(self._buf, "normal")
                    self._buf = ""

        sys.stdout = Writer(self.log)
        try:
            import make_show
            import make_show_mixed

            try:
                make_show.WIDTH = int(out_w)
                make_show.HEIGHT = int(out_h)
            except Exception:
                pass

            fps = int(getattr(make_show, "FPS", 30))
            frames_per_image = round((60.0 / float(bpm)) * fps)
            sec_per_image = 60.0 / float(bpm)
            audio_fade_sec = 4.0 * sec_per_image

            final_hold_frames = round(2.0 * fps)
            final_fade_frames = round(2.0 * fps)

            make_show_mixed.run_show(
                project_root=str(project_root),
                bpm=int(bpm),
                frames_per_image=int(frames_per_image),
                final_hold_frames=int(final_hold_frames),
                final_fade_frames=int(final_fade_frames),
                audio_fade_sec=float(audio_fade_sec),
                flipbook_window_min=10,
                prefer_window_min=2,
                flipbook_sec=6.0,
                output_fps=int(fps),
                duck_db=float(duck_db),
                audio_path=str(audio_path) if audio_path else None,
                dry_run=False,
                include_flipbooks=bool(include_flipbooks),
                stop_event=self._stop_event,
                pause_event=self._pause_event,
            )

            if self._stop_event.is_set():
                self.log("Mixed project render stopped.", "info")
            else:
                self.log("Mixed project render complete.", "success")
        except make_show_mixed.StopRequested:
            self.log("Mixed project render stopped.", "info")
        except Exception as e:
            import traceback

            self.log(f"Error: {e}", "error")
            self.log(traceback.format_exc(), "error")
        finally:
            sys.stdout = old_stdout
            self.after(0, self._finish)

    def _run_mixed_batch(self, bpm, root, include_flipbooks, audio_path, duck_db, out_w, out_h, auto_audio=False):
        old_stdout = sys.stdout

        import io

        class Writer(io.TextIOBase):
            def __init__(self, log_fn):
                self._log_fn = log_fn
                self._buf = ""

            def write(self, s):
                self._buf += s
                while "\n" in self._buf:
                    line, self._buf = self._buf.split("\n", 1)
                    if line.strip():
                        self._log_fn(line, "normal")
                return len(s)

            def flush(self):
                if self._buf.strip():
                    self._log_fn(self._buf, "normal")
                    self._buf = ""

        sys.stdout = Writer(self.log)

        def _has_media(project_root: str) -> bool:
            try:
                cot_dir = os.path.join(project_root, ".cot")
                if os.path.isdir(cot_dir):
                    fb = os.path.join(cot_dir, "flipbook")
                    if os.path.isdir(fb):
                        for _cur, _dirnames, filenames in os.walk(fb):
                            for fn in filenames:
                                if fn.lower().endswith(".mp4"):
                                    return True
                if os.path.isfile(os.path.join(cot_dir, "flipbook.json")):
                    return True
            except Exception:
                pass

            exts = {".jpg", ".jpeg"}
            try:
                for cur, dirnames, filenames in os.walk(project_root):
                    dirnames[:] = [
                        d
                        for d in dirnames
                        if (not d.startswith(".")) and d.lower() not in ("exclude", ".cot")
                    ]
                    for fn in filenames:
                        if os.path.splitext(fn)[1].lower() in exts:
                            return True
            except Exception:
                return False
            return False

        try:
            import make_show
            import make_show_mixed

            try:
                make_show.WIDTH = int(out_w)
                make_show.HEIGHT = int(out_h)
            except Exception:
                pass

            fps = int(getattr(make_show, "FPS", 30))
            frames_per_image = round((60.0 / float(bpm)) * fps)
            sec_per_image = 60.0 / float(bpm)
            audio_fade_sec = 4.0 * sec_per_image

            final_hold_frames = round(2.0 * fps)
            final_fade_frames = round(2.0 * fps)

            try:
                out_dir = str(getattr(make_show, "OUTPUT_DIR", "") or "")
            except Exception:
                out_dir = ""

            try:
                entries = [
                    e.path
                    for e in os.scandir(root)
                    if e.is_dir() and (not e.name.startswith("."))
                ]
            except Exception as e:
                self.log(f"Error scanning root: {e}", "error")
                return

            if out_dir:
                out_dir_norm = os.path.normpath(out_dir).lower()
                entries = [
                    p
                    for p in entries
                    if os.path.normpath(p).lower() != out_dir_norm
                ]

            entries.sort(key=lambda p: os.path.basename(os.path.normpath(p)).lower())
            total = len(entries)
            if total <= 0:
                self.log("No project subfolders found.", "error")
                return

            done = 0
            skipped_done = 0
            skipped_empty = 0
            failed = 0

            for i, project_root in enumerate(entries, 1):
                if self._stop_event.is_set():
                    break

                while self._pause_event.is_set():
                    if self._stop_event.is_set():
                        break
                    time.sleep(0.15)

                if self._stop_event.is_set():
                    break

                folder_name = os.path.basename(os.path.normpath(project_root))
                self.set_status(f"Mixed batch: {i}/{total}  {folder_name}")

                expected_out = ""
                try:
                    expected_out = os.path.join(out_dir, folder_name + "_mixed.mp4") if out_dir else ""
                except Exception:
                    expected_out = ""

                if expected_out and os.path.isfile(expected_out):
                    self.log(f"Skipping [DONE]: {folder_name}", "info")
                    skipped_done += 1
                    continue

                if not _has_media(project_root):
                    self.log(f"Skipping (no images/flipbooks): {folder_name}", "info")
                    skipped_empty += 1
                    continue

                self.log(" ", "info")
                self.log(f"--- {i}/{total}: {folder_name} ---", "header")

                try:
                    make_show_mixed.run_show(
                        project_root=str(project_root),
                        bpm=int(bpm),
                        frames_per_image=int(frames_per_image),
                        final_hold_frames=int(final_hold_frames),
                        final_fade_frames=int(final_fade_frames),
                        audio_fade_sec=float(audio_fade_sec),
                        flipbook_window_min=10,
                        prefer_window_min=2,
                        flipbook_sec=6.0,
                        output_fps=int(fps),
                        duck_db=float(duck_db),
                        audio_path=str(detect_audio_for_folder(project_root) if (auto_audio and not audio_path) else audio_path) if (audio_path or auto_audio) else None,
                        dry_run=False,
                        include_flipbooks=bool(include_flipbooks),
                        stop_event=self._stop_event,
                        pause_event=self._pause_event,
                    )
                    done += 1
                except make_show_mixed.StopRequested:
                    self.log("Mixed batch stopped.", "info")
                    break
                except Exception as e:
                    import traceback

                    failed += 1
                    self.log(f"Error in {folder_name}: {e}", "error")
                    self.log(traceback.format_exc(), "error")
                    continue

            if self._stop_event.is_set():
                self.log(
                    f"Mixed batch stopped. Done={done}  SkippedDone={skipped_done}  SkippedEmpty={skipped_empty}  Failed={failed}",
                    "info",
                )
            else:
                self.log(
                    f"Mixed batch complete. Done={done}  SkippedDone={skipped_done}  SkippedEmpty={skipped_empty}  Failed={failed}",
                    "success",
                )
        except Exception as e:
            import traceback

            self.log(f"Error: {e}", "error")
            self.log(traceback.format_exc(), "error")
        finally:
            sys.stdout = old_stdout
            self.after(0, self._finish)

    def _launch_interactive(self, bpm, mode_letter, root, out_w, out_h):
        """Launch interactive modes (A/C/D) in a real terminal window."""
        if self._launch_guard:
            return
        self._launch_guard = True
        self.run_btn.configure(state="disabled")
        self.after(1500, lambda: (setattr(self, "_launch_guard", False), self.run_btn.configure(state="normal")))

        make_show_path = os.path.join(SCRIPTS_DIR, "make_show.py")
        if not os.path.isfile(make_show_path):
            messagebox.showerror("Not found",
                                 f"make_show.py not found in:\n{SCRIPTS_DIR}")
            return

        # Write a runner that pre-sets BPM and mode
        runner = os.path.join(SCRIPTS_DIR, "_make_show_runner.py")
        mode_name = {"A": "mode_normal", "C": "mode_batch_audio",
                     "D": "mode_add_audio_existing"}.get(mode_letter, "main")

        try:
            with open(runner, "w", encoding="utf-8") as f:
                scripts_dir_literal = json.dumps(SCRIPTS_DIR)
                f.write("import sys, os\n")
                f.write(f"sys.path.insert(0, {scripts_dir_literal})\n")
                f.write(f"os.chdir({scripts_dir_literal})\n")
                f.write("import make_show\n")
                f.write("make_show.refresh_runtime_config()\n")
                f.write(f"make_show.WIDTH = {int(out_w)}\n")
                f.write(f"make_show.HEIGHT = {int(out_h)}\n")
                f.write("make_show.startup_cleanup()\n")
                f.write("make_show.rotate_log()\n")
                if mode_letter == "D":
                    f.write(f"make_show.mode_add_audio_existing(2.0)\n")
                else:
                    bpm_val = bpm
                    f.write(f"bpm = {bpm_val}\n")
                    f.write(f"fps = make_show.FPS\n")
                    f.write(f"import math\n")
                    f.write(f"frames_per_image = round((60.0/bpm)*fps)\n")
                    f.write(f"frames_hold = round(2.0*fps)\n")
                    f.write(f"frames_fade = round(2.0*fps)\n")
                    f.write(f"audio_fade_sec = 2.0\n")
                    f.write(f"root = {json.dumps(root)}\n")
                    f.write(f"subfolders = make_show.get_subfolders(root)\n")
                    f.write("if not subfolders:\n")
                    f.write("    # If the user selected a single folder with images (no subfolders), render it as-is.\n")
                    f.write("    try:\n")
                    f.write("        if make_show.count_images(root) > 0:\n")
                    f.write("            subfolders = [root]\n")
                    f.write("    except Exception:\n")
                    f.write("        pass\n")
                    if mode_letter == "A":
                        f.write("make_show.mode_normal(subfolders, frames_per_image, frames_hold, frames_fade, audio_fade_sec)\n")
                    elif mode_letter == "C":
                        f.write("make_show.mode_batch_audio(subfolders, frames_per_image, frames_hold, frames_fade, audio_fade_sec)\n")
                f.write("input('\\nDone. Press Enter to close...')\n")
        except Exception as e:
            messagebox.showerror("Error", f"Could not write runner:\n{e}")
            return

        bat = os.path.join(SCRIPTS_DIR, "_make_show_launcher.bat")
        py_exe = sys.executable.replace("pythonw.exe", "python.exe")

        ok = launch_interactive_windows(
            title=f"Make Show — Mode {mode_letter}",
            cmd=[py_exe, runner],
            cwd=SCRIPTS_DIR,
            env=os.environ.copy(),
            bat_path=bat,
            log_path=os.path.join(SCRIPTS_DIR, "launcher_log.txt"),
        )

        self.log(f"Launching Mode {mode_letter} in terminal window...", "info")
        self.log("Interact with the terminal to proceed.", "info")

        if not ok:
            messagebox.showerror("Launch Error", "Failed to open terminal (see launcher_log.txt).")


    def _run_batch_silent(self, bpm, root, out_w, out_h):
        """Mode B — fully automatic, no input() calls."""
        old_stdout = sys.stdout

        import io
        class Writer(io.TextIOBase):
            def __init__(self, log_fn):
                self._log_fn = log_fn
                self._buf = ""
            def write(self, s):
                self._buf += s
                while "\n" in self._buf:
                    line, self._buf = self._buf.split("\n", 1)
                    if line.strip():
                        self._log_fn(line, "normal")
                return len(s)
            def flush(self):
                if self._buf.strip():
                    self._log_fn(self._buf, "normal")
                    self._buf = ""

        sys.stdout = Writer(self.log)
        try:
            from cot_core.make_show_core import run_batch_silent
            run_batch_silent(
                root=root,
                bpm=bpm,
                skip_done=True,
                width=int(out_w),
                height=int(out_h),
                stop_event=self._stop_event,
                pause_event=self._pause_event,
                log_cb=lambda s: self.log(s, "info"),
            )
            if self._stop_event.is_set():
                self.log("Batch silent render stopped.", "info")
            else:
                self.log("Batch silent render complete.", "success")
        except Exception as e:
            import traceback
            self.log(f"Error: {e}", "error")
            self.log(traceback.format_exc(), "error")
        finally:
            sys.stdout = old_stdout
            self.after(0, self._finish)

    def _finish(self):
        self.hide_progress()
        self.run_btn.configure(state="normal")
        self.pause_btn.configure(state="disabled", text="Pause")
        self.stop_btn.configure(state="disabled")
        self.set_status("Done")


if __name__ == "__main__":
    try:
        install_global_crash_handler(
            tool="make_show_gui",
            out_dir=SCRIPTS_DIR,
            log_path=os.path.join(SCRIPTS_DIR, "launcher_log.txt"),
        )
    except Exception:
        pass
    app = MakeShowGui()
    app.mainloop()
