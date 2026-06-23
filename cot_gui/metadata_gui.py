"""
cot_gui/metadata_gui.py
YouTube Metadata Generator — GUI wrapper around youtube_meta.py

Modes A (one-by-one) and B (selective) use input() — launched in terminal.
Mode C (batch) is automatic — runs in GUI thread with stdout capture.
Review & Edit Live also uses input() — launched in terminal.
"""

import os
import sys
import threading
import io

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

try:
    import cot_config as cfg
    cfg.load(gui_mode=True)
    HAS_CONFIG = True
except ImportError:
    HAS_CONFIG = False


class _GuiWriter(io.TextIOBase):
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


class MetadataGui(CotBaseWindow):
    def __init__(self):
        super().__init__(
            title="Metadata Generator",
            subtitle="Generate YouTube titles, descriptions & tags",
            width=700, height=580,
        )
        self._build_options()
        self._build_action_buttons()

    def _build_options(self):
        ctk.CTkLabel(
            self.options_frame, text="Metadata Options",
            font=ctk.CTkFont(size=12, weight="bold"), anchor="w"
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=12, pady=(10, 6))

        # Root folder
        ctk.CTkLabel(self.options_frame, text="Pictures folder:", anchor="w"
                     ).grid(row=1, column=0, sticky="w", padx=12, pady=6)
        default_root = cfg.get("PICTURES_DIR", "") if HAS_CONFIG else ""
        self._root_var = ctk.StringVar(value=default_root)
        ctk.CTkEntry(self.options_frame, textvariable=self._root_var, width=300
                     ).grid(row=1, column=1, sticky="w", padx=8, pady=6)
        ctk.CTkButton(self.options_frame, text="Browse", width=80,
                      command=self._browse_root
                      ).grid(row=1, column=2, padx=4, pady=6)

        # Mode
        ctk.CTkLabel(self.options_frame, text="Mode:", anchor="w"
                     ).grid(row=2, column=0, sticky="w", padx=12, pady=6)
        self._mode_var = ctk.StringVar(value="C — Batch (automatic, no prompts)")
        ctk.CTkOptionMenu(
            self.options_frame,
            values=[
                "A — One by one (interactive, seeded)",
                "B — Selective (pick folders, interactive)",
                "C — Batch (automatic, no prompts)",
                "T — Refresh thumbnails (update CSV only)",
            ],
            variable=self._mode_var,
            width=320,
        ).grid(row=2, column=1, columnspan=2, sticky="w", padx=8, pady=(6, 4))

        llm_mode = cfg.get("LLM_MODE", "not configured") if HAS_CONFIG else "not configured"
        ctk.CTkLabel(
            self.options_frame,
            text=f"Modes A and B are interactive — they open in a terminal window.\n"
                 f"LLM mode: {llm_mode}  |  Change in Settings",
            font=ctk.CTkFont(size=10), text_color="gray", anchor="w", justify="left"
        ).grid(row=3, column=0, columnspan=3, sticky="w", padx=12, pady=(0, 10))

    def _browse_root(self):
        folder = filedialog.askdirectory(title="Select pictures root folder")
        if folder:
            self._root_var.set(folder)

    def _build_action_buttons(self):
        self.run_btn = ctk.CTkButton(
            self.buttons_frame, text="Generate Metadata",
            command=self._run,
            font=ctk.CTkFont(size=13, weight="bold"), height=38,
        )
        self.run_btn.grid(row=0, column=0, padx=(0, 6), pady=8, sticky="ew")

        ctk.CTkButton(
            self.buttons_frame, text="Review and Edit Live",
            command=self._run_review,
        ).grid(row=0, column=1, padx=6, pady=8, sticky="ew")

        ctk.CTkButton(
            self.buttons_frame, text="Clear Log",
            fg_color="transparent", border_width=1,
            command=self.clear_log,
        ).grid(row=0, column=2, padx=(6, 0), pady=8, sticky="ew")

    def _run(self):
        root = self._root_var.get().strip()
        mode_letter = self._mode_var.get()[0]

        if not root or not os.path.isdir(root):
            messagebox.showerror("No Folder", "Please select a valid pictures folder.")
            return

        # Modes A and B are interactive — launch in terminal
        if mode_letter in ("A", "B"):
            self._launch_interactive(mode_letter, root)
            return

        if mode_letter == "T":
            self.clear_log()
            self.show_progress()
            self.set_status("Refreshing thumbnails...")
            self.run_btn.configure(state="disabled")
            self.log("Mode T — Refresh thumbnails (CSV only)", "info")
            self.log(f"Root: {root}", "info")
            self.log("-" * 48, "info")
            threading.Thread(target=self._run_refresh_thumbnails,
                             args=(root,), daemon=True).start()
            return

        # Mode C — batch, automatic, no input() calls
        self.clear_log()
        self.show_progress()
        self.set_status("Generating metadata (batch)...")
        self.run_btn.configure(state="disabled")
        self.log("Mode C — Batch (automatic)", "info")
        self.log(f"Root: {root}", "info")
        self.log("-" * 48, "info")
        threading.Thread(target=self._run_batch,
                         args=(root,), daemon=True).start()


    def _run_refresh_thumbnails(self, root):
        old_stdout = sys.stdout
        sys.stdout = _GuiWriter(self.log)
        try:
            from cot_core.metadata_core import run_refresh_thumbnails
            run_refresh_thumbnails(
                root,
                reload_module=True,
                log_cb=lambda s: self.log(s, "info"),
            )
            self.log("Thumbnail refresh complete.", "success")
        except Exception as e:
            import traceback
            self.log(f"Error: {e}", "error")
            self.log(traceback.format_exc(), "error")
        finally:
            sys.stdout = old_stdout
            self.after(0, self._finish)

    def _launch_interactive(self, mode_letter, root):
        """Launch interactive modes in a terminal window."""
        meta_path = os.path.join(SCRIPTS_DIR, "youtube_meta.py")
        if not os.path.isfile(meta_path):
            messagebox.showerror("Not found",
                                 f"youtube_meta.py not found in:\n{SCRIPTS_DIR}")
            return

        runner = os.path.join(SCRIPTS_DIR, "_meta_runner.py")
        fn_map = {"A": "mode_one_by_one", "B": "mode_selective"}
        fn = fn_map[mode_letter]

        try:
            with open(runner, "w", encoding="utf-8") as f:
                f.write("import sys, os\n")
                f.write(f"sys.path.insert(0, r'{SCRIPTS_DIR}')\n")
                f.write(f"os.chdir(r'{SCRIPTS_DIR}')\n")
                f.write("import youtube_meta\n")
                f.write(f"root = r'{root}'\n")
                f.write(f"youtube_meta.{fn}(root)\n")
                f.write("input('\\nDone. Press Enter to close...')\n")
        except Exception as e:
            messagebox.showerror("Error", f"Could not write runner:\n{e}")
            return

        bat = os.path.join(SCRIPTS_DIR, "_meta_launcher.bat")
        py_exe = sys.executable.replace("pythonw.exe", "python.exe")

        ok = launch_interactive_windows(
            title=f"Metadata — Mode {mode_letter}",
            cmd=[py_exe, runner],
            cwd=SCRIPTS_DIR,
            env=os.environ.copy(),
            bat_path=bat,
            log_path=os.path.join(SCRIPTS_DIR, "launcher_log.txt"),
        )

        self.log(f"Launching Mode {mode_letter} in terminal window...", "info")
        if not ok:
            messagebox.showerror("Launch Error", "Failed to open terminal (see launcher_log.txt).")


    def _run_batch(self, root):
        """Mode C — batch, no input() calls."""
        old_stdout = sys.stdout
        sys.stdout = _GuiWriter(self.log)
        try:
            from cot_core.metadata_core import run_batch_metadata
            run_batch_metadata(
                root,
                reload_module=True,
                log_cb=lambda s: self.log(s, "info"),
            )
            self.log("Batch metadata generation complete.", "success")
        except Exception as e:
            import traceback
            self.log(f"Error: {e}", "error")
            self.log(traceback.format_exc(), "error")
        finally:
            sys.stdout = old_stdout
            self.after(0, self._finish)

    def _run_review(self):
        """Review & Edit Live — always interactive, open in terminal."""
        meta_path = os.path.join(SCRIPTS_DIR, "youtube_meta.py")
        if not os.path.isfile(meta_path):
            messagebox.showerror("Not found",
                                 f"youtube_meta.py not found in:\n{SCRIPTS_DIR}")
            return

        runner = os.path.join(SCRIPTS_DIR, "_review_runner.py")
        try:
            with open(runner, "w", encoding="utf-8") as f:
                f.write("import sys, os\n")
                f.write(f"sys.path.insert(0, r'{SCRIPTS_DIR}')\n")
                f.write(f"os.chdir(r'{SCRIPTS_DIR}')\n")
                f.write("import youtube_meta\n")
                f.write("youtube_meta.mode_review_live()\n")
                f.write("input('\\nDone. Press Enter to close...')\n")
        except Exception as e:
            messagebox.showerror("Error", f"Could not write runner:\n{e}")
            return

        bat = os.path.join(SCRIPTS_DIR, "_review_launcher.bat")
        py_exe = sys.executable.replace("pythonw.exe", "python.exe")

        ok = launch_interactive_windows(
            title="Review and Edit Live",
            cmd=[py_exe, runner],
            cwd=SCRIPTS_DIR,
            env=os.environ.copy(),
            bat_path=bat,
            log_path=os.path.join(SCRIPTS_DIR, "launcher_log.txt"),
        )

        self.log("Launching Review and Edit Live in terminal window...", "info")
        if not ok:
            messagebox.showerror("Launch Error", "Failed to open terminal (see launcher_log.txt).")


    def _finish(self):
        self.hide_progress()
        self.run_btn.configure(state="normal")
        self.set_status("Done")


if __name__ == "__main__":
    try:
        install_global_crash_handler(
            tool="metadata_gui",
            out_dir=SCRIPTS_DIR,
            log_path=os.path.join(SCRIPTS_DIR, "launcher_log.txt"),
        )
    except Exception:
        pass
    app = MetadataGui()
    app.mainloop()
