"""
cot_gui/add_sound_gui.py
GUI workflow: add music/audio to existing movie files without modifying originals.
"""

from __future__ import annotations

import os
import sys
import threading

SCRIPTS_DIR = os.environ.get(
    "COT_SCRIPTS_DIR",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
sys.path.insert(0, SCRIPTS_DIR)

import customtkinter as ctk
from tkinter import filedialog, messagebox

from cot_gui.cot_base_gui import CotBaseWindow
from cot_core.crash_utils import install_global_crash_handler
from cot_core.video_audio_core import (
    AUDIO_EXTS,
    VIDEO_EXTS,
    add_audio_to_video,
    default_output_path,
    detect_audio_for_video,
    list_video_files,
)

try:
    import cot_config as cfg
    cfg.load(gui_mode=True)
    HAS_CONFIG = True
except Exception:
    cfg = None
    HAS_CONFIG = False


def _cfg_get(key: str, default: str = "") -> str:
    if not HAS_CONFIG:
        return default
    try:
        return str(cfg.get(key, default) or default)
    except Exception:
        return default


def _cfg_set(**updates) -> None:
    if not HAS_CONFIG:
        return
    try:
        for key, value in updates.items():
            cfg.set(key, value, save_now=False)
        cfg.save()
    except Exception:
        pass


class AddSoundGui(CotBaseWindow):
    def __init__(self):
        super().__init__(
            title="Add Sound to Existing Movie",
            subtitle="Existing MP4/MOV + audio → new MP4",
            width=760,
            height=620,
        )
        self._stop_event = threading.Event()
        self._build_options()
        self._build_action_buttons()
        self._apply_mode_from_env()

    def _build_options(self) -> None:
        self.options_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            self.options_frame,
            text="Workflow",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, columnspan=4, sticky="w", padx=12, pady=(10, 6))

        self._mode_var = ctk.StringVar(value="Single movie")
        ctk.CTkLabel(self.options_frame, text="Mode:", anchor="w").grid(row=1, column=0, sticky="w", padx=12, pady=6)
        ctk.CTkOptionMenu(
            self.options_frame,
            values=["Single movie", "Batch folder"],
            variable=self._mode_var,
            command=lambda _v: self._on_mode_change(),
            width=160,
        ).grid(row=1, column=1, sticky="w", padx=8, pady=6)

        self._target_label = ctk.CTkLabel(self.options_frame, text="Movie file:", anchor="w")
        self._target_label.grid(row=2, column=0, sticky="w", padx=12, pady=6)
        self._target_var = ctk.StringVar(value=_cfg_get("LAST_VIDEO_PATH", ""))
        ctk.CTkEntry(self.options_frame, textvariable=self._target_var, width=420).grid(
            row=2, column=1, columnspan=2, sticky="ew", padx=8, pady=6
        )
        ctk.CTkButton(self.options_frame, text="Browse", width=85, command=self._browse_target).grid(row=2, column=3, padx=8, pady=6)

        ctk.CTkLabel(self.options_frame, text="Audio source:", anchor="w").grid(row=3, column=0, sticky="w", padx=12, pady=6)
        self._audio_mode_var = ctk.StringVar(value="Auto-detect matching/same-folder audio")
        ctk.CTkOptionMenu(
            self.options_frame,
            values=["Auto-detect matching/same-folder audio", "Use selected audio for all"],
            variable=self._audio_mode_var,
            command=lambda _v: self._on_audio_mode_change(),
            width=300,
        ).grid(row=3, column=1, columnspan=2, sticky="w", padx=8, pady=6)

        ctk.CTkLabel(self.options_frame, text="Selected audio:", anchor="w").grid(row=4, column=0, sticky="w", padx=12, pady=6)
        self._audio_var = ctk.StringVar(value=_cfg_get("LAST_AUDIO_PATH", ""))
        self._audio_entry = ctk.CTkEntry(self.options_frame, textvariable=self._audio_var, width=420)
        self._audio_entry.grid(row=4, column=1, columnspan=2, sticky="ew", padx=8, pady=6)
        self._audio_browse = ctk.CTkButton(self.options_frame, text="Browse", width=85, command=self._browse_audio)
        self._audio_browse.grid(row=4, column=3, padx=8, pady=6)

        ctk.CTkLabel(self.options_frame, text="Output folder:", anchor="w").grid(row=5, column=0, sticky="w", padx=12, pady=6)
        self._output_var = ctk.StringVar(value=_cfg_get("OUTPUT_DIR", ""))
        ctk.CTkEntry(self.options_frame, textvariable=self._output_var, width=420).grid(
            row=5, column=1, columnspan=2, sticky="ew", padx=8, pady=6
        )
        ctk.CTkButton(self.options_frame, text="Browse", width=85, command=self._browse_output).grid(row=5, column=3, padx=8, pady=6)

        ctk.CTkLabel(self.options_frame, text="Audio handling:", anchor="w").grid(row=6, column=0, sticky="w", padx=12, pady=6)
        self._mix_mode_var = ctk.StringVar(value="Replace/add selected audio")
        ctk.CTkOptionMenu(
            self.options_frame,
            values=["Replace/add selected audio", "Mix with existing movie audio"],
            variable=self._mix_mode_var,
            width=240,
        ).grid(row=6, column=1, sticky="w", padx=8, pady=6)

        ctk.CTkLabel(self.options_frame, text="Fade out seconds:", anchor="w").grid(row=6, column=2, sticky="e", padx=8, pady=6)
        self._fade_var = ctk.StringVar(value=str(_cfg_get("MAKE_SHOW_AUDIO_FADE_SEC", "2.0")))
        ctk.CTkEntry(self.options_frame, textvariable=self._fade_var, width=80).grid(row=6, column=3, sticky="w", padx=8, pady=6)

        self._hint = ctk.CTkLabel(
            self.options_frame,
            text="Non-destructive: outputs are saved as *_with_audio.mp4. Originals are not modified.",
            text_color="gray",
            font=ctk.CTkFont(size=10),
            anchor="w",
        )
        self._hint.grid(row=7, column=0, columnspan=4, sticky="w", padx=12, pady=(4, 10))
        self._on_audio_mode_change()

    def _build_action_buttons(self) -> None:
        self.run_btn = ctk.CTkButton(
            self.buttons_frame,
            text="Add Sound",
            command=self._run,
            font=ctk.CTkFont(size=13, weight="bold"),
            height=38,
        )
        self.run_btn.grid(row=0, column=0, padx=(0, 6), pady=8, sticky="ew")
        self.stop_btn = ctk.CTkButton(
            self.buttons_frame,
            text="Stop",
            command=self._stop,
            state="disabled",
            fg_color="#F44336",
            hover_color="#D32F2F",
            text_color="black",
            text_color_disabled="black",
        )
        self.stop_btn.grid(row=0, column=1, padx=6, pady=8, sticky="ew")
        self.buttons_frame.grid_columnconfigure(2, weight=1)

    def _apply_mode_from_env(self) -> None:
        requested = (os.environ.get("YT_ADD_SOUND_MODE") or "").strip().lower()
        if requested == "batch":
            self._mode_var.set("Batch folder")
            self._on_mode_change()

    def _on_mode_change(self) -> None:
        if self._mode_var.get() == "Batch folder":
            self._target_label.configure(text="Movies folder:")
            self._hint.configure(
                text="Batch mode scans the selected folder for movie files and saves *_with_audio.mp4 outputs. Originals are not modified."
            )
        else:
            self._target_label.configure(text="Movie file:")
            self._hint.configure(
                text="Non-destructive: output is saved as *_with_audio.mp4. The original movie is not modified."
            )

    def _on_audio_mode_change(self) -> None:
        selected = self._audio_mode_var.get() == "Use selected audio for all"
        state = "normal" if selected else "disabled"
        try:
            self._audio_entry.configure(state=state)
            self._audio_browse.configure(state=state)
        except Exception:
            pass

    def _browse_target(self) -> None:
        if self._mode_var.get() == "Batch folder":
            folder = filedialog.askdirectory(title="Select folder containing movie files")
            if folder:
                self._target_var.set(folder)
                _cfg_set(LAST_VIDEO_FOLDER=folder)
            return
        exts = " ".join(f"*{e}" for e in sorted(VIDEO_EXTS))
        path = filedialog.askopenfilename(title="Select movie file", filetypes=[("Video", exts), ("All files", "*.*")])
        if path:
            self._target_var.set(path)
            _cfg_set(LAST_VIDEO_PATH=path, LAST_VIDEO_FOLDER=os.path.dirname(path))

    def _browse_audio(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(AUDIO_EXTS))
        path = filedialog.askopenfilename(title="Select audio file", filetypes=[("Audio", exts), ("All files", "*.*")])
        if path:
            self._audio_var.set(path)
            _cfg_set(LAST_AUDIO_PATH=path, AUDIO_DIR=os.path.dirname(path))

    def _browse_output(self) -> None:
        folder = filedialog.askdirectory(title="Select output folder")
        if folder:
            self._output_var.set(folder)
            _cfg_set(OUTPUT_DIR=folder)

    def _stop(self) -> None:
        self._stop_event.set()
        self.set_status("Stopping...")
        self.log("Stop requested...", "info")

    def _fade_seconds(self) -> float:
        try:
            return max(0.0, float((self._fade_var.get() or "2.0").strip()))
        except Exception:
            return 2.0

    def _run(self) -> None:
        target = (self._target_var.get() or "").strip()
        if not target:
            messagebox.showerror("Missing target", "Select a movie file or batch folder first.")
            return
        output_dir = (self._output_var.get() or "").strip() or None
        audio_mode = self._audio_mode_var.get()
        selected_audio = (self._audio_var.get() or "").strip()
        if audio_mode == "Use selected audio for all" and not selected_audio:
            messagebox.showerror("Missing audio", "Choose an audio file or switch to auto-detect audio.")
            return
        if output_dir and not os.path.isdir(output_dir):
            try:
                os.makedirs(output_dir, exist_ok=True)
            except Exception as exc:
                messagebox.showerror("Output folder", f"Could not create output folder:\n{output_dir}\n\n{exc}")
                return

        _cfg_set(
            OUTPUT_DIR=output_dir or _cfg_get("OUTPUT_DIR", ""),
            LAST_VIDEO_PATH=target if os.path.isfile(target) else _cfg_get("LAST_VIDEO_PATH", ""),
            LAST_VIDEO_FOLDER=target if os.path.isdir(target) else os.path.dirname(target),
            LAST_AUDIO_PATH=selected_audio or _cfg_get("LAST_AUDIO_PATH", ""),
            MAKE_SHOW_AUDIO_FADE_SEC=self._fade_seconds(),
        )

        self.clear_log()
        self.show_progress()
        self.set_status("Adding sound...")
        self.run_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self._stop_event.clear()
        threading.Thread(target=self._worker, args=(target, selected_audio, output_dir), daemon=True).start()

    def _worker(self, target: str, selected_audio: str, output_dir: str | None) -> None:
        try:
            batch = self._mode_var.get() == "Batch folder"
            if batch:
                videos = list_video_files(target, recursive=False)
                if not videos:
                    self.log(f"No supported movie files found in: {target}", "error")
                    return
            else:
                videos = [target]

            self.log(f"Found {len(videos)} movie file(s).", "info")
            mode = "mix" if self._mix_mode_var.get().startswith("Mix") else "replace"
            use_selected = self._audio_mode_var.get() == "Use selected audio for all"
            fade_sec = self._fade_seconds()
            done = 0
            skipped = 0
            failed = 0

            for idx, video in enumerate(videos, 1):
                if self._stop_event.is_set():
                    break
                self.set_status(f"Processing {idx}/{len(videos)}")
                self.log(f"\n{idx}/{len(videos)}  {os.path.basename(video)}", "header")

                audio = selected_audio if use_selected else detect_audio_for_video(video)
                if not audio:
                    skipped += 1
                    self.log("Skipped: no matching/same-folder audio found.", "info")
                    continue
                self.log(f"Audio: {os.path.basename(audio)}", "info")

                out = default_output_path(video, output_dir)
                result = add_audio_to_video(
                    video_path=video,
                    audio_path=audio,
                    output_path=out,
                    mode=mode,
                    fade_sec=fade_sec,
                    overwrite=False,
                )
                if result.ok:
                    done += 1
                    self.log(f"Saved: {result.output_path}", "success")
                else:
                    failed += 1
                    self.log(f"Failed: {result.error}", "error")

            if self._stop_event.is_set():
                self.log(f"Stopped. Done={done}  Skipped={skipped}  Failed={failed}", "info")
            else:
                self.log(f"Complete. Done={done}  Skipped={skipped}  Failed={failed}", "success")
        except Exception as exc:
            import traceback
            self.log(f"Error: {exc}", "error")
            self.log(traceback.format_exc(), "error")
        finally:
            self.after(0, self._finish)

    def _finish(self) -> None:
        self.hide_progress()
        self.run_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.set_status("Done")


if __name__ == "__main__":
    try:
        install_global_crash_handler(
            tool="add_sound_gui",
            out_dir=SCRIPTS_DIR,
            log_path=os.path.join(SCRIPTS_DIR, "launcher_log.txt"),
        )
    except Exception:
        pass
    app = AddSoundGui()
    app.mainloop()
