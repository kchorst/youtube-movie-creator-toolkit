"""
cot_gui/upload_gui.py
YouTube Uploader — GUI wrapper around youtube_upload.py
Shows quota status, pending videos, dry run option.
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
from tkinter import messagebox
from cot_gui.cot_base_gui import CotBaseWindow
from cot_core.crash_utils import install_global_crash_handler

try:
    import cot_config as cfg
    cfg.load(gui_mode=True)
    HAS_CONFIG = True
except ImportError:
    HAS_CONFIG = False



class _GuiWriter(io.TextIOBase):
    """Redirect print() output to a GUI log callback, line by line."""
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

class UploadGui(CotBaseWindow):
    def __init__(self):
        super().__init__(
            title="Upload to YouTube",
            subtitle="Upload videos with metadata and quota tracking",
            width=680, height=600,
        )
        self._build_options()
        self._build_action_buttons()
        self.after(300, self._load_status)

    def _build_options(self):
        ctk.CTkLabel(
            self.options_frame, text="Upload Options",
            font=ctk.CTkFont(size=12, weight="bold"), anchor="w"
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(10, 6))

        self._dryrun_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            self.options_frame,
            text="Dry run — preview without actually uploading",
            variable=self._dryrun_var
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=16, pady=4)

        ctk.CTkLabel(
            self.options_frame,
            text="YouTube free quota: ~6 videos/day  |  1,650 units per upload",
            font=ctk.CTkFont(size=10), text_color="gray", anchor="w"
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=12, pady=(4, 10))

    def _build_action_buttons(self):
        self.upload_btn = ctk.CTkButton(
            self.buttons_frame, text="Upload Pending Videos",
            command=self._run_upload,
            font=ctk.CTkFont(size=13, weight="bold"), height=38,
        )
        self.upload_btn.grid(row=0, column=0, padx=(0, 6), pady=8, sticky="ew")

        ctk.CTkButton(
            self.buttons_frame, text="Refresh Status",
            command=self._load_status,
        ).grid(row=0, column=1, padx=6, pady=8, sticky="ew")

        ctk.CTkButton(
            self.buttons_frame, text="Clear Log",
            fg_color="transparent", border_width=1,
            command=self.clear_log,
        ).grid(row=0, column=2, padx=(6, 0), pady=8, sticky="ew")

    def _load_status(self):
        self.clear_log()
        self.set_status("Loading status…")
        threading.Thread(target=self._status_thread, daemon=True).start()

    def _status_thread(self):
        old_stdout = sys.stdout
        sys.stdout = _GuiWriter(self.log)
        try:
            self.log("── Upload Status ────────────────────────────────", "info")
            from cot_core.upload_core import show_status, show_quota_status
            show_status(reload_module=True, log_cb=lambda s: self.log(s, "info"))
            self.log("", "info")
            self.log("── Quota ────────────────────────────────────────", "info")
            show_quota_status(reload_module=True, log_cb=lambda s: self.log(s, "info"))
        except Exception as e:
            self.log(f"✗ Could not load status: {e}", "error")
        finally:
            self.after(0, lambda: self.set_status("Ready"))

    def _run_upload(self):
        dry = self._dryrun_var.get()
        label = "DRY RUN" if dry else "UPLOAD"
        if not dry:
            if not messagebox.askyesno("Confirm Upload",
                                        "This will upload videos to YouTube.\n\nProceed?"):
                return

        self.clear_log()
        self.show_progress()
        self.set_status(f"{label} in progress…")
        self.upload_btn.configure(state="disabled")
        self.log(f"Starting {label}…", "info")
        self.log("─" * 48, "info")
        threading.Thread(target=self._upload_thread,
                         args=(dry,), daemon=True).start()

    def _upload_thread(self, dry_run):
        old_stdout = sys.stdout
        sys.stdout = _GuiWriter(self.log)
        try:
            from cot_core.upload_core import run_uploads
            run_uploads(dry_run=dry_run, reload_module=True, log_cb=lambda s: self.log(s, "info"))
            self.log("✓ Complete.", "success")
        except Exception as e:
            self.log(f"✗ Error: {e}", "error")
        finally:

            sys.stdout = old_stdout
            self.after(0, self._finish)

    def _finish(self):
        self.hide_progress()
        self.upload_btn.configure(state="normal")
        self.set_status("Done")


if __name__ == "__main__":
    try:
        install_global_crash_handler(
            tool="upload_gui",
            out_dir=SCRIPTS_DIR,
            log_path=os.path.join(SCRIPTS_DIR, "launcher_log.txt"),
        )
    except Exception:
        pass
    app = UploadGui()
    app.mainloop()
