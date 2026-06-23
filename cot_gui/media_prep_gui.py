import os
import sys
import json
import subprocess
import threading
import re
from typing import Optional
from datetime import datetime

import customtkinter as ctk
from tkinter import filedialog, messagebox

from cot_gui.cot_base_gui import CotBaseWindow, SCRIPTS_DIR
from cot_core.launch_utils import launch_interactive_windows, launch_streamed_hidden
from cot_core.logging_utils import log_exception
from cot_core.process_utils import terminate_process
from cot_core.last_run_utils import LastRunArtifact
from cot_core.crash_utils import install_global_crash_handler
from cot_core.run_utils import run_with_artifact


try:
    import cot_config as cfg
    cfg.load(gui_mode=True)
    HAS_CONFIG = True
except Exception:
    HAS_CONFIG = False

    try:
        log_exception(
            context="cot_gui.media_prep_gui:cot_config.load",
            exc=sys.exc_info()[1] or Exception("Unknown error"),
            log_path=os.path.join(SCRIPTS_DIR, "launcher_log.txt"),
        )
    except Exception:
        pass


CREATE_NEW_CONSOLE = 0x00000010
CREATE_NO_WINDOW = 0x08000000


class MediaPrepGui(CotBaseWindow):
    def __init__(self):
        super().__init__(
            title="Media Prep",
            subtitle="Curate backlog + generate flipbook previews",
            width=720,
            height=560,
        )
        try:
            self.grid_rowconfigure(1, weight=1)
            self.grid_rowconfigure(3, weight=0)
            self._log_box.configure(height=120)
        except Exception:
            pass
        self._last_analyze_preset: Optional[str] = None
        self._enable_apply_var = ctk.BooleanVar(value=False)
        self._stream_to_gui_var = ctk.BooleanVar(value=False)
        self._proc: Optional[subprocess.Popen] = None
        self._build_options()
        self._build_action_buttons()

    def _build_options(self):
        # Use a scrollable container so the UI fits on smaller screens.
        self._opts_scroll = ctk.CTkScrollableFrame(self.options_frame)
        self._opts_scroll.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)
        self.options_frame.grid_rowconfigure(0, weight=1)
        self.options_frame.grid_columnconfigure(0, weight=1)
        parent = self._opts_scroll

        ctk.CTkLabel(
            parent,
            text="Media Prep Options",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, columnspan=4, sticky="w", padx=12, pady=(10, 4))

        # Root folder
        ctk.CTkLabel(parent, text="Pictures folder:", anchor="w").grid(
            row=1, column=0, sticky="w", padx=12, pady=6
        )
        default_root = cfg.get("PICTURES_DIR", "") if HAS_CONFIG else ""
        self._root_var = ctk.StringVar(value=default_root)
        ctk.CTkEntry(parent, textvariable=self._root_var, width=360).grid(
            row=1, column=1, columnspan=2, sticky="w", padx=8, pady=6
        )
        ctk.CTkButton(parent, text="Browse", width=80, command=self._browse_root).grid(
            row=1, column=3, padx=4, pady=6
        )

        # Project folder
        ctk.CTkLabel(parent, text="Project:", anchor="w").grid(
            row=2, column=0, sticky="w", padx=12, pady=6
        )
        self._project_var = ctk.StringVar(value="")
        self._project_menu = ctk.CTkOptionMenu(
            parent,
            values=[""],
            variable=self._project_var,
            width=320,
        )
        self._project_menu.grid(row=2, column=1, sticky="w", padx=8, pady=6)
        ctk.CTkButton(parent, text="Refresh", width=80, command=self._refresh_projects).grid(
            row=2, column=2, padx=4, pady=6, sticky="w"
        )
        ctk.CTkButton(parent, text="Browse", width=80, command=self._browse_project).grid(
            row=2, column=3, padx=4, pady=6
        )

        # Stages
        ctk.CTkLabel(
            parent,
            text="Stages",
            font=ctk.CTkFont(size=11, weight="bold"),
            anchor="w",
        ).grid(row=3, column=0, sticky="w", padx=12, pady=(10, 4))

        self._do_curate = ctk.BooleanVar(value=True)
        self._do_flipbook = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            parent,
            text="Scan photos for images to exclude (moves rejects to Exclude)",
            variable=self._do_curate,
        ).grid(row=4, column=0, columnspan=4, sticky="w", padx=16, pady=3)
        ctk.CTkCheckBox(
            parent,
            text="Convert videos to flipbook previews (FPS + Length below; writes to .cot only)",
            variable=self._do_flipbook,
        ).grid(row=5, column=0, columnspan=4, sticky="w", padx=16, pady=3)

        # Curation options
        r = 6
        self._superbatch_var = ctk.BooleanVar(value=False)
        self._superbatch_cb = ctk.CTkCheckBox(
            parent, text="Whole-project mode (treat all folders as one batch)", variable=self._superbatch_var
        )
        self._superbatch_cb.grid(row=r, column=0, columnspan=4, sticky="w", padx=16, pady=(8, 3))
        r += 1

        self._reuse_thresholds_var = ctk.BooleanVar(value=False)
        self._reuse_thresholds_cb = ctk.CTkCheckBox(
            parent,
            text="Reuse saved thresholds (ignore presets)",
            variable=self._reuse_thresholds_var,
            command=self._sync_enabled_state,
        )
        self._reuse_thresholds_cb.grid(row=r, column=0, columnspan=4, sticky="w", padx=16, pady=(2, 3))
        r += 1

        self._stream_to_gui_cb = ctk.CTkCheckBox(
            parent,
            text="Stream output to GUI (show progress — no terminal)",
            variable=self._stream_to_gui_var,
        )
        self._stream_to_gui_cb.grid(row=r, column=0, columnspan=4, sticky="w", padx=16, pady=(2, 3))
        r += 1

        self._skip_dupes_var = ctk.BooleanVar(value=False)
        self._skip_dupes_cb = ctk.CTkCheckBox(
            parent,
            text="Skip duplicate detection (faster)",
            variable=self._skip_dupes_var,
        )
        self._skip_dupes_cb.grid(row=r, column=0, columnspan=4, sticky="w", padx=16, pady=(2, 3))
        r += 1

        self._skip_faces_var = ctk.BooleanVar(value=False)
        self._skip_faces_cb = ctk.CTkCheckBox(
            parent,
            text="Skip face detection (faster)",
            variable=self._skip_faces_var,
        )
        self._skip_faces_cb.grid(row=r, column=0, columnspan=4, sticky="w", padx=16, pady=(2, 3))
        r += 1

        self._no_eye_verify_var = ctk.BooleanVar(value=False)
        self._no_eye_verify_cb = ctk.CTkCheckBox(
            parent,
            text="Disable eye verification (faster; more false positives)",
            variable=self._no_eye_verify_var,
        )
        self._no_eye_verify_cb.grid(row=r, column=0, columnspan=4, sticky="w", padx=16, pady=(2, 3))
        r += 1

        ctk.CTkLabel(parent, text="Faster scan (analyze at):", anchor="w").grid(
            row=r, column=0, sticky="w", padx=16, pady=(2, 3)
        )
        self._analysis_preset_var = ctk.StringVar(value="Balanced (960px)")
        self._analysis_preset_menu = ctk.CTkOptionMenu(
            parent,
            values=["Full (original)", "Accurate (1280px)", "Balanced (960px)", "Fast (640px)"],
            variable=self._analysis_preset_var,
            width=180,
        )
        self._analysis_preset_menu.grid(row=r, column=1, sticky="w", padx=8, pady=(2, 3))
        r += 1

        ctk.CTkLabel(
            parent,
            text="Curation presets",
            font=ctk.CTkFont(size=11, weight="bold"),
            anchor="w",
        ).grid(row=r, column=0, sticky="w", padx=12, pady=(10, 4))

        ctk.CTkButton(
            parent,
            text="Edit thresholds...",
            width=140,
            command=self._open_threshold_editor,
        ).grid(row=r, column=3, sticky="e", padx=12, pady=(10, 4))

        r += 1

        self._keep_mode_values = ["Keep More", "Balanced", "Keep Fewer"]

        self._curation_frames = ctk.CTkFrame(parent)
        self._curation_frames.grid(row=r, column=0, columnspan=4, sticky="ew", padx=12, pady=(4, 6))
        self._curation_frames.grid_columnconfigure(0, weight=1)
        self._curation_frames.grid_columnconfigure(1, weight=1)

        r += 1

        self._analyze_frame = ctk.CTkFrame(self._curation_frames)
        self._analyze_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=0)
        self._apply_frame = ctk.CTkFrame(self._curation_frames)
        self._apply_frame.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=0)

        ctk.CTkLabel(
            self._analyze_frame,
            text="Step 1: Analyze (dry-run — no moves)",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#2b6cb0",
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=10, pady=(8, 4))
        ctk.CTkLabel(self._analyze_frame, text="Preset:", anchor="w").grid(
            row=1, column=0, sticky="w", padx=10, pady=(0, 4)
        )
        self._analyze_keep_var = ctk.StringVar(value="Keep Fewer")
        self._analyze_keep_menu = ctk.CTkOptionMenu(
            self._analyze_frame,
            values=self._keep_mode_values,
            variable=self._analyze_keep_var,
            width=180,
            command=lambda _v=None: self._update_keep_mode_text(),
        )
        self._analyze_keep_menu.grid(row=2, column=0, sticky="w", padx=10, pady=(0, 6))
        self._analyze_desc = ctk.StringVar(value="")
        self._analyze_desc_label = ctk.CTkLabel(
            self._analyze_frame,
            textvariable=self._analyze_desc,
            font=ctk.CTkFont(size=10),
            text_color="gray",
            anchor="w",
            justify="left",
        )
        self._analyze_desc_label.grid(row=3, column=0, sticky="w", padx=10, pady=(0, 10))

        ctk.CTkLabel(
            self._apply_frame,
            text="Step 2: Apply (moves rejects to Exclude)",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#b45309",
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=10, pady=(8, 4))
        ctk.CTkLabel(self._apply_frame, text="Preset:", anchor="w").grid(
            row=1, column=0, sticky="w", padx=10, pady=(0, 4)
        )
        self._apply_keep_var = ctk.StringVar(value="Balanced")
        self._apply_keep_menu = ctk.CTkOptionMenu(
            self._apply_frame,
            values=self._keep_mode_values,
            variable=self._apply_keep_var,
            width=180,
            command=lambda _v=None: self._update_keep_mode_text(),
        )
        self._apply_keep_menu.grid(row=2, column=0, sticky="w", padx=10, pady=(0, 6))
        self._apply_desc = ctk.StringVar(value="")
        self._apply_desc_label = ctk.CTkLabel(
            self._apply_frame,
            textvariable=self._apply_desc,
            font=ctk.CTkFont(size=10),
            text_color="gray",
            anchor="w",
            justify="left",
        )
        self._apply_desc_label.grid(row=3, column=0, sticky="w", padx=10, pady=(0, 10))

        # Flipbook options
        ctk.CTkLabel(
            parent,
            text="Flipbook",
            font=ctk.CTkFont(size=11, weight="bold"),
            anchor="w",
        ).grid(row=r, column=0, sticky="w", padx=12, pady=(10, 4))

        r += 1

        ctk.CTkLabel(parent, text="FPS:", anchor="w").grid(row=r, column=0, sticky="w", padx=12, pady=6)
        self._fps_var = ctk.StringVar(value="30")
        self._fps_entry = ctk.CTkEntry(parent, textvariable=self._fps_var, width=80)
        self._fps_entry.grid(row=r, column=1, sticky="w", padx=8, pady=6)

        ctk.CTkLabel(parent, text="Length (sec):", anchor="w").grid(row=r, column=2, sticky="w", padx=12, pady=6)
        self._sec_var = ctk.StringVar(value="6")
        self._sec_entry = ctk.CTkEntry(parent, textvariable=self._sec_var, width=80)
        self._sec_entry.grid(row=r, column=3, sticky="w", padx=8, pady=6)

        r += 1

        self._overwrite_var = ctk.BooleanVar(value=False)
        self._overwrite_cb = ctk.CTkCheckBox(
            parent, text="Overwrite existing flipbooks", variable=self._overwrite_var
        )
        self._overwrite_cb.grid(row=r, column=0, columnspan=4, sticky="w", padx=16, pady=(2, 10))

        parent.grid_columnconfigure(1, weight=1)

        self._refresh_projects()
        self.after(200, self._sync_enabled_state)

        self._update_keep_mode_text()

        self._do_curate.trace_add("write", lambda *_: self._sync_enabled_state())
        self._do_flipbook.trace_add("write", lambda *_: self._sync_enabled_state())

    def _sync_enabled_state(self):
        curate_on = bool(self._do_curate.get())
        flip_on = bool(self._do_flipbook.get())

        self._superbatch_cb.configure(state="normal" if curate_on else "disabled")

        reuse_on = curate_on and bool(self._reuse_thresholds_var.get())
        self._reuse_thresholds_cb.configure(state="normal" if curate_on else "disabled")

        self._stream_to_gui_cb.configure(state="normal")
        self._skip_dupes_cb.configure(state="normal" if curate_on else "disabled")
        self._skip_faces_cb.configure(state="normal" if curate_on else "disabled")
        self._no_eye_verify_cb.configure(state="normal" if curate_on else "disabled")
        self._analysis_preset_menu.configure(state="normal" if curate_on else "disabled")

        self._analyze_keep_menu.configure(state="disabled" if reuse_on else ("normal" if curate_on else "disabled"))
        self._apply_keep_menu.configure(state="disabled" if reuse_on else ("normal" if curate_on else "disabled"))

        self._fps_entry.configure(state="normal" if flip_on else "disabled")
        self._sec_entry.configure(state="normal" if flip_on else "disabled")
        self._overwrite_cb.configure(state="normal" if flip_on else "disabled")

        apply_enabled = bool(self._enable_apply_var.get())
        # Only allow Apply when the user explicitly enables it.
        self.apply_btn.configure(state="normal" if apply_enabled else "disabled")

    def _update_keep_mode_text(self):
        def _desc(label: str) -> str:
            if label == "Keep More":
                return (
                    "Keep More (lenient): "
                    "blur>=25, dark<=25, bright>=235, dupDist<=4, faces=major@0.08"
                )
            if label == "Keep Fewer":
                return (
                    "Keep Fewer (strict): "
                    "blur>=110, dark<=55, bright>=205, dupDist<=8, faces=any@0.02"
                )
            return "Balanced: uses your project defaults in .cot/curation.json (or global defaults)."

        a = self._analyze_keep_var.get().strip()
        p = self._apply_keep_var.get().strip()
        self._analyze_desc.set(_desc(a))
        self._apply_desc.set(_desc(p))

    def _browse_root(self):
        folder = filedialog.askdirectory(title="Select pictures root folder")
        if folder:
            self._root_var.set(folder)
            self._refresh_projects()

    def _open_threshold_editor(self):
        project_path = self._resolve_project_path()
        if not project_path:
            messagebox.showerror("No Project", "Please select a valid project folder.")
            return

        cot_dir = os.path.join(project_path, ".cot")
        state_path = os.path.join(cot_dir, "curation.json")

        defaults = {
            "face_exclude_mode": "any",
            "face_verify_eyes": True,
            "face_major_ratio": 0.05,
            "blur_laplacian_var_min": 100.0,
            "dark_luma_mean_max": 40.0,
            "bright_luma_mean_min": 215.0,
            "dup_phash_distance_max": 6,
        }

        state = {}
        th = dict(defaults)
        if os.path.isfile(state_path):
            try:
                with open(state_path, "r", encoding="utf-8") as f:
                    state = json.load(f) or {}
            except Exception:
                state = {}

            th_state = state.get("thresholds") if isinstance(state, dict) else None
            if isinstance(th_state, dict):
                for k, v in defaults.items():
                    if k in th_state:
                        th[k] = th_state.get(k)

        win = ctk.CTkToplevel(self)
        win.title("Curation Thresholds")
        win.geometry("560x520")
        win.resizable(False, False)
        try:
            win.attributes("-topmost", True)
            win.after(300, lambda: win.attributes("-topmost", False))
        except Exception:
            pass

        wrap = ctk.CTkFrame(win)
        wrap.pack(fill="both", expand=True, padx=12, pady=12)
        wrap.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            wrap,
            text="Curation Thresholds (saved per-project in .cot/curation.json)",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=(8, 10))

        ctk.CTkLabel(wrap, text=f"Project: {os.path.basename(project_path)}", anchor="w", text_color="gray").grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 12)
        )

        face_mode_var = ctk.StringVar(value=str(th.get("face_exclude_mode") or "any"))
        face_verify_var = ctk.BooleanVar(value=bool(th.get("face_verify_eyes")))
        face_ratio_var = ctk.StringVar(value=str(th.get("face_major_ratio")))
        blur_var = ctk.StringVar(value=str(th.get("blur_laplacian_var_min")))
        dark_var = ctk.StringVar(value=str(th.get("dark_luma_mean_max")))
        bright_var = ctk.StringVar(value=str(th.get("bright_luma_mean_min")))
        dup_var = ctk.StringVar(value=str(th.get("dup_phash_distance_max")))

        r = 2

        ctk.CTkLabel(wrap, text="Face exclude mode:", anchor="w").grid(row=r, column=0, sticky="w", padx=8, pady=6)
        ctk.CTkOptionMenu(wrap, values=["any", "major"], variable=face_mode_var, width=140).grid(
            row=r, column=1, sticky="w", padx=8, pady=6
        )
        r += 1

        ctk.CTkLabel(wrap, text="Require eyes (human verification):", anchor="w").grid(
            row=r, column=0, sticky="w", padx=8, pady=6
        )
        ctk.CTkCheckBox(wrap, text="", variable=face_verify_var).grid(row=r, column=1, sticky="w", padx=8, pady=6)
        r += 1

        ctk.CTkLabel(wrap, text="Major face ratio threshold:", anchor="w").grid(row=r, column=0, sticky="w", padx=8, pady=6)
        ctk.CTkEntry(wrap, textvariable=face_ratio_var, width=140).grid(row=r, column=1, sticky="w", padx=8, pady=6)
        r += 1

        ctk.CTkLabel(wrap, text="Blur threshold (Laplacian var min):", anchor="w").grid(
            row=r, column=0, sticky="w", padx=8, pady=6
        )
        ctk.CTkEntry(wrap, textvariable=blur_var, width=140).grid(row=r, column=1, sticky="w", padx=8, pady=6)
        r += 1

        ctk.CTkLabel(wrap, text="Dark threshold (luma mean max):", anchor="w").grid(
            row=r, column=0, sticky="w", padx=8, pady=6
        )
        ctk.CTkEntry(wrap, textvariable=dark_var, width=140).grid(row=r, column=1, sticky="w", padx=8, pady=6)
        r += 1

        ctk.CTkLabel(wrap, text="Bright threshold (luma mean min):", anchor="w").grid(
            row=r, column=0, sticky="w", padx=8, pady=6
        )
        ctk.CTkEntry(wrap, textvariable=bright_var, width=140).grid(row=r, column=1, sticky="w", padx=8, pady=6)
        r += 1

        ctk.CTkLabel(wrap, text="Duplicate pHash distance max:", anchor="w").grid(row=r, column=0, sticky="w", padx=8, pady=6)
        ctk.CTkEntry(wrap, textvariable=dup_var, width=140).grid(row=r, column=1, sticky="w", padx=8, pady=6)
        r += 1

        hint = (
            "Notes:\n"
            "- Balanced preset uses these saved per-project thresholds.\n"
            "- Keep More / Keep Fewer temporarily override these values during batch runs."
        )
        ctk.CTkLabel(wrap, text=hint, anchor="w", justify="left", text_color="gray").grid(
            row=r, column=0, columnspan=2, sticky="ew", padx=8, pady=(10, 6)
        )
        r += 1

        btns = ctk.CTkFrame(wrap, fg_color="transparent")
        btns.grid(row=r, column=0, columnspan=2, sticky="ew", padx=8, pady=(10, 8))
        btns.grid_columnconfigure((0, 1, 2), weight=1)

        def _parse_float(var: ctk.StringVar, name: str) -> float:
            s = (var.get() or "").strip()
            try:
                return float(s)
            except Exception:
                raise ValueError(f"{name} must be a number")

        def _parse_int(var: ctk.StringVar, name: str) -> int:
            s = (var.get() or "").strip()
            try:
                return int(float(s))
            except Exception:
                raise ValueError(f"{name} must be an integer")

        def _save():
            try:
                out = {
                    "face_exclude_mode": (face_mode_var.get() or "any").strip().lower(),
                    "face_verify_eyes": bool(face_verify_var.get()),
                    "face_major_ratio": _parse_float(face_ratio_var, "Major face ratio"),
                    "blur_laplacian_var_min": _parse_float(blur_var, "Blur threshold"),
                    "dark_luma_mean_max": _parse_float(dark_var, "Dark threshold"),
                    "bright_luma_mean_min": _parse_float(bright_var, "Bright threshold"),
                    "dup_phash_distance_max": _parse_int(dup_var, "Duplicate distance"),
                }

                if out["face_exclude_mode"] not in ("any", "major"):
                    raise ValueError("Face exclude mode must be 'any' or 'major'")
                if not (0.001 <= out["face_major_ratio"] <= 0.5):
                    raise ValueError("Major face ratio should be between 0.001 and 0.5")
                if out["blur_laplacian_var_min"] <= 0:
                    raise ValueError("Blur threshold must be > 0")
                if not (0 <= out["dark_luma_mean_max"] <= 255):
                    raise ValueError("Dark threshold must be 0..255")
                if not (0 <= out["bright_luma_mean_min"] <= 255):
                    raise ValueError("Bright threshold must be 0..255")
                if not (0 <= out["dup_phash_distance_max"] <= 32):
                    raise ValueError("Duplicate distance must be 0..32")

                os.makedirs(cot_dir, exist_ok=True)
                next_state = dict(state) if isinstance(state, dict) else {}
                next_state["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                next_state["thresholds"] = out
                with open(state_path, "w", encoding="utf-8") as f:
                    json.dump(next_state, f, indent=2)

                messagebox.showinfo("Saved", "Thresholds saved to .cot/curation.json")
            except Exception as e:
                messagebox.showerror("Invalid Settings", str(e))

        def _open_file():
            try:
                os.makedirs(cot_dir, exist_ok=True)
                if not os.path.isfile(state_path):
                    stub = dict(state) if isinstance(state, dict) else {}
                    stub["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    stub.setdefault("thresholds", defaults)
                    with open(state_path, "w", encoding="utf-8") as f:
                        json.dump(stub, f, indent=2)
                os.startfile(state_path)
            except Exception as e:
                messagebox.showerror("Open Error", f"Could not open file:\n{e}")

        ctk.CTkButton(btns, text="Save", command=_save).grid(row=0, column=0, padx=(0, 6), sticky="ew")
        ctk.CTkButton(btns, text="Open JSON", command=_open_file, fg_color="transparent", border_width=1).grid(
            row=0, column=1, padx=6, sticky="ew"
        )
        ctk.CTkButton(btns, text="Close", command=win.destroy, fg_color="transparent", border_width=1).grid(
            row=0, column=2, padx=(6, 0), sticky="ew"
        )

    def _browse_project(self):
        folder = filedialog.askdirectory(title="Select project folder")
        if folder:
            self._project_var.set(folder)

    def _refresh_projects(self):
        root = self._root_var.get().strip()
        values = [""]
        if root and os.path.isdir(root):
            try:
                values += sorted(
                    [d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)) and not d.startswith(".")],
                    key=lambda s: s.lower(),
                )
            except Exception:
                pass

        if len(values) == 1:
            values = [""]

        try:
            self._project_menu.configure(values=values)
        except Exception:
            pass

        if self._project_var.get() not in values:
            self._project_var.set(values[0])

    def _resolve_project_path(self) -> Optional[str]:
        root = self._root_var.get().strip()
        proj = self._project_var.get().strip()

        if proj and os.path.isabs(proj) and os.path.isdir(proj):
            return proj

        if root and proj:
            p = os.path.join(root, proj)
            if os.path.isdir(p):
                return p

        return None

    def _build_action_buttons(self):
        self.analyze_btn = ctk.CTkButton(
            self.buttons_frame,
            text="Analyze",
            command=self._run_analyze,
            font=ctk.CTkFont(size=13, weight="bold"),
            height=38,
            fg_color="#2b6cb0",
        )
        self.analyze_btn.grid(row=0, column=0, padx=(0, 6), pady=8, sticky="ew")

        self.enable_apply_cb = ctk.CTkCheckBox(
            self.buttons_frame,
            text="Enable Apply",
            variable=self._enable_apply_var,
            command=self._sync_enabled_state,
        )
        self.enable_apply_cb.grid(row=0, column=1, padx=6, pady=8, sticky="w")

        self.apply_btn = ctk.CTkButton(
            self.buttons_frame,
            text="Apply",
            command=self._run_apply,
            height=38,
            fg_color="#b45309",
        )
        self.apply_btn.grid(row=0, column=2, padx=6, pady=8, sticky="ew")

        self.stop_btn = ctk.CTkButton(
            self.buttons_frame,
            text="Stop",
            command=self._stop_stream,
            height=38,
            fg_color="#991b1b",
        )
        self.stop_btn.grid(row=0, column=3, padx=6, pady=8, sticky="ew")
        self.stop_btn.configure(state="disabled")

        ctk.CTkButton(
            self.buttons_frame,
            text="Clear Log",
            fg_color="transparent",
            border_width=1,
            command=self.clear_log,
        ).grid(row=0, column=4, padx=(6, 0), pady=8, sticky="ew")

    def _set_running_state(self, running: bool) -> None:
        try:
            self.analyze_btn.configure(state="disabled" if running else "normal")
        except Exception:
            pass
        try:
            self.apply_btn.configure(state="disabled" if running else "normal")
        except Exception:
            pass
        try:
            self.stop_btn.configure(state="normal" if running else "disabled")
        except Exception:
            pass

    def _stop_stream(self) -> None:
        if self._proc is None:
            return
        self.log("Stop requested. Terminating Media Prep...", "info")
        try:
            terminate_process(self._proc, log_path=os.path.join(SCRIPTS_DIR, "launcher_log.txt"))
        except Exception:
            pass

    def _run_analyze(self):
        self._last_analyze_preset = self._analyze_keep_var.get().strip()
        self._run(mode="analyze")

    def _run_apply(self):
        if not bool(self._enable_apply_var.get()):
            return
        km = self._apply_keep_var.get().strip()
        proceed = messagebox.askyesno(
            "Apply Curation",
            "Apply will MOVE files into the project's Exclude folder.\n\n"
            f"Apply preset: {km}\n\n"
            "Recommendation: run Analyze first to review the summary before applying.\n\n"
            "Proceed?",
        )
        if not proceed:
            return
        self._run(mode="apply")
        self._enable_apply_var.set(False)
        self._sync_enabled_state()

    def _run(self, *, mode: str):
        project_path = self._resolve_project_path()
        if not project_path:
            messagebox.showerror("No Project", "Please select a valid project folder.")
            return

        project_path = os.path.normpath(project_path)

        do_curate = bool(self._do_curate.get())
        do_flip = bool(self._do_flipbook.get())
        if (not do_curate) and (not do_flip):
            messagebox.showerror("No Stages", "Please enable at least one stage (Curation and/or Flipbooks).")
            return

        is_analyze = mode == "analyze"

        fps = None
        sec = None
        if do_flip:
            try:
                fps = float(self._fps_var.get().strip())
            except Exception:
                messagebox.showerror("Flipbook FPS", "Flipbook FPS must be a number.")
                return
            try:
                sec = float(self._sec_var.get().strip())
            except Exception:
                messagebox.showerror("Flipbook Length", "Flipbook length must be a number.")
                return

        cmd = [
            sys.executable.replace("pythonw.exe", "python.exe"),
            "-u",
            os.path.join(SCRIPTS_DIR, "cot_media_prep.py"),
            "--project",
            project_path,
        ]

        if is_analyze:
            cmd.append("--dry-run")

        if not do_curate:
            cmd.append("--skip-curate")

        if not do_flip:
            cmd.append("--skip-flipbook")

        if do_curate and bool(self._superbatch_var.get()):
            cmd.append("--superbatch")

        if do_curate:
            cmd.append("--curate-batch")
            if bool(self._reuse_thresholds_var.get()):
                cmd += ["--curate-keep-mode", "balanced"]
            else:
                if is_analyze:
                    km = self._analyze_keep_var.get().strip()
                else:
                    km = self._apply_keep_var.get().strip()
                km_map = {"Keep More": "keep_more", "Balanced": "balanced", "Keep Fewer": "keep_less"}
                cmd += ["--curate-keep-mode", km_map.get(km, "balanced")]
            if not is_analyze:
                cmd.append("--curate-apply")

            if bool(self._skip_dupes_var.get()):
                cmd.append("--curate-skip-dupes")

            if bool(self._skip_faces_var.get()):
                cmd.append("--curate-skip-faces")
            if bool(self._no_eye_verify_var.get()):
                cmd.append("--curate-no-eye-verify")
            ap = (self._analysis_preset_var.get() or "").strip().lower()
            ap_map = {
                "full (original)": None,
                "accurate (1280px)": 1280,
                "balanced (960px)": 960,
                "fast (640px)": 640,
            }
            v = ap_map.get(ap)
            if v:
                cmd += ["--curate-analysis-max-size", str(int(v))]

        if do_flip:
            if fps is not None:
                cmd += ["--flipbook-fps", str(fps)]
            if sec is not None:
                cmd += ["--flipbook-sec", str(sec)]
            if bool(self._overwrite_var.get()):
                cmd.append("--flipbook-overwrite")

        label = "ANALYZE" if is_analyze else "APPLY"

        self.clear_log()
        if bool(self._stream_to_gui_var.get()):
            self.set_status(f"{label}: running in GUI")
            self.log("Launching Media Prep (streaming output to GUI)...", "info")
        else:
            self.set_status(f"{label}: opened in terminal")
            self.log("Launching Media Prep in a terminal window...", "info")
        self.log(f"Project: {project_path}", "info")
        self.log(" ".join(f'"{c}"' if " " in c else c for c in cmd), "info")

        if bool(self._stream_to_gui_var.get()):
            self._run_streamed(cmd, label=label, project_path=project_path, is_analyze=is_analyze)
            return

        try:
            if sys.platform == "win32":
                title = "Media Prep — " + ("Analyze" if is_analyze else "Apply")
                post_lines = None
                if not is_analyze:
                    # Prompt user in the same terminal window after Media Prep finishes.
                    # If they choose Yes, open Make Show GUI preloaded to this project.
                    safe_proj = project_path.replace("\"", "\"\"")
                    safe_scripts = SCRIPTS_DIR.replace("\"", "\"\"")
                    post_lines = [
                        "echo.",
                        "echo Next step? (Y/N)",
                        "set /p COT_NEXT=Open Make Show for this project now? ",
                        "if /I \"%COT_NEXT%\"==\"Y\" (",
                        f"  set \"COT_PROJECT_PATH={safe_proj}\"",
                        f"  start \"\" \"{sys.executable}\" \"{os.path.join(safe_scripts, 'cot_gui', 'make_show_gui.py')}\"",
                        ")",
                    ]
                ok = launch_interactive_windows(
                    title=title,
                    cmd=cmd,
                    cwd=SCRIPTS_DIR,
                    env=os.environ.copy(),
                    bat_path=os.path.join(SCRIPTS_DIR, "_media_prep_launcher.bat"),
                    log_path=os.path.join(SCRIPTS_DIR, "launcher_log.txt"),
                    post_bat_lines=post_lines,
                )
                if not ok:
                    messagebox.showerror("Launch Error", "Failed to open terminal (see launcher_log.txt).")
                    return
            else:
                subprocess.Popen(
                    cmd,
                    cwd=SCRIPTS_DIR,
                    env=os.environ.copy(),
                )
        except Exception as e:
            messagebox.showerror("Launch Error", f"Failed to open terminal:\n{e}")
            return

    def _run_streamed(self, cmd, *, label: str, project_path: str, is_analyze: bool):
        if self._proc is not None:
            messagebox.showerror("Already Running", "A Media Prep process is already running.")
            return

        self.show_progress()
        self._set_running_state(True)
        try:
            self._progress.configure(mode="indeterminate")
        except Exception:
            pass

        self.log("-" * 56, "info")

        def _worker():
            def _do_run(_artifact: LastRunArtifact):
                env = os.environ.copy()
                env["PYTHONUNBUFFERED"] = "1"

                creationflags = 0
                if sys.platform == "win32":
                    creationflags = CREATE_NO_WINDOW

                self._proc = launch_streamed_hidden(
                    cmd=cmd,
                    cwd=SCRIPTS_DIR,
                    env=env,
                    on_line=lambda s: self.log(s, "info"),
                    creationflags=creationflags,
                    log_path=os.path.join(SCRIPTS_DIR, "launcher_log.txt"),
                )

                rx = re.compile(r"\[(\d+)\s*/\s*(\d+)\]")
                rc = 1
                try:
                    assert self._proc.stdout is not None
                    for line in self._proc.stdout:
                        s = (line or "").rstrip("\n")
                        if not s:
                            continue
                        self.log(s, "info")

                        m = rx.search(s)
                        if m:
                            try:
                                i = int(m.group(1))
                                n = int(m.group(2))
                                if n > 0:
                                    frac = max(0.0, min(1.0, i / float(n)))
                                    pct = int(round(100.0 * frac))
                                    self.set_status(f"{label}: {i}/{n} ({pct}%)")
                                    try:
                                        self._progress.configure(mode="determinate")
                                        self._progress.set(frac)
                                    except Exception:
                                        pass
                            except Exception:
                                pass

                    rc = int(self._proc.wait())
                finally:
                    self._proc = None

                try:
                    _artifact.set_output("return_code", rc)
                except Exception:
                    pass
                try:
                    _artifact.finish(ok=(rc == 0), return_code=rc)
                except Exception:
                    pass

                return rc

            try:
                rc = int(
                    run_with_artifact(
                        artifact_path=os.path.join(SCRIPTS_DIR, "last_run_media_prep.json"),
                        tool="media_prep_streamed",
                        inputs={
                            "label": label,
                            "cmd": list(cmd),
                        },
                        log_path=os.path.join(SCRIPTS_DIR, "launcher_log.txt"),
                        preflight=True,
                        fn=_do_run,
                    )
                )
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Launch Error", f"Failed to start:\n{e}"))
                self.after(0, self.hide_progress)
                self._proc = None
                return

            try:
                import gc
                gc.collect()
            except Exception:
                pass

            def _finish():
                try:
                    self._progress.configure(mode="indeterminate")
                except Exception:
                    pass

                self.hide_progress()
                self._set_running_state(False)
                if rc == 0:
                    self.set_status(f"{label}: done")
                    self.log(" ", "info")
                    self.log(f"{label}: completed successfully.", "info")
                    if not is_analyze:
                        try:
                            self._prompt_next_tool(project_path=project_path)
                        except Exception:
                            pass
                else:
                    self.set_status(f"{label}: failed ({rc})")
                    self.log(" ", "info")
                    self.log(f"{label}: failed with exit code {rc}", "error")

            self.after(0, _finish)

        threading.Thread(target=_worker, daemon=True).start()

    def _prompt_next_tool(self, *, project_path: str) -> None:
        project_path = os.path.normpath(project_path)
        if not project_path or not os.path.isdir(project_path):
            return

        proceed = messagebox.askyesno(
            "Next step",
            "Media Prep completed.\n\n"
            "Open Make Show for this project now?\n\n"
            f"{project_path}",
        )
        if not proceed:
            return

        target = os.path.join(SCRIPTS_DIR, "cot_gui", "make_show_gui.py")
        if not os.path.isfile(target):
            target = os.path.join(SCRIPTS_DIR, "make_show_gui.py")
        if not os.path.isfile(target):
            messagebox.showerror("Not found", f"make_show_gui.py not found in:\n{SCRIPTS_DIR}")
            return

        env = os.environ.copy()
        env["COT_SCRIPTS_DIR"] = SCRIPTS_DIR
        env["PYTHONPATH"] = SCRIPTS_DIR
        env["COT_PROJECT_PATH"] = project_path

        try:
            subprocess.Popen(
                [sys.executable, target],
                cwd=SCRIPTS_DIR,
                env=env,
                creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                **({"start_new_session": True} if sys.platform != "win32" else {}),
            )
        except Exception as e:
            messagebox.showerror("Launch Error", f"Failed to start Make Show:\n\n{e}")


if __name__ == "__main__":
    try:
        install_global_crash_handler(
            tool="media_prep_gui",
            out_dir=SCRIPTS_DIR,
            log_path=os.path.join(SCRIPTS_DIR, "launcher_log.txt"),
        )
    except Exception:
        pass
    app = MediaPrepGui()
    app.mainloop()
