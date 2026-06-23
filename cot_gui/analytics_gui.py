"""
cot_gui/analytics_gui.py
YouTube Analytics — GUI wrapper around cot_analytics.py

Redirects all print() output from cot_analytics into the GUI log panel
so results are visible without a terminal window.
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


class AnalyticsGui(CotBaseWindow):
    def __init__(self):
        super().__init__(
            title="YouTube Analytics",
            subtitle="Pull video performance stats and leaderboard",
            width=720, height=620,
        )
        self._build_options()
        self._build_action_buttons()

    def _build_options(self):
        ctk.CTkLabel(
            self.options_frame, text="Analytics Options",
            font=ctk.CTkFont(size=12, weight="bold"), anchor="w"
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(10, 4))

        ctk.CTkLabel(
            self.options_frame,
            text="Note: YouTube Analytics data has a 2-3 day delay after upload.",
            font=ctk.CTkFont(size=11), text_color="gray", anchor="w"
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 10))

    def _build_action_buttons(self):
        self.pull_btn = ctk.CTkButton(
            self.buttons_frame, text="Pull Analytics (all videos)",
            command=self._run_pull,
            font=ctk.CTkFont(size=13, weight="bold"), height=38,
        )
        self.pull_btn.grid(row=0, column=0, padx=(0, 6), pady=8, sticky="ew")

        ctk.CTkButton(
            self.buttons_frame, text="Show Leaderboard",
            command=self._run_leaderboard,
        ).grid(row=0, column=1, padx=6, pady=8, sticky="ew")

        ctk.CTkButton(
            self.buttons_frame, text="Clear Log",
            fg_color="transparent", border_width=1,
            command=self.clear_log,
        ).grid(row=0, column=2, padx=(6, 0), pady=8, sticky="ew")

    # ── Pull analytics ───────────────────────────────────────────

    def _run_pull(self):
        self.clear_log()
        self.show_progress()
        self.set_status("Pulling analytics...")
        self.pull_btn.configure(state="disabled")
        self.log("Authenticating and pulling analytics for all channel videos...", "info")
        self.log("This may take a few minutes.", "info")
        self.log("-" * 48, "info")
        threading.Thread(target=self._pull_thread, daemon=True).start()

    def _pull_thread(self):
        old_stdout = sys.stdout
        sys.stdout = _GuiWriter(self.log)
        try:
            from cot_core.analytics_core import run_analytics
            run_analytics(reload_module=True, log_cb=lambda s: self.log(s, "info"))
            self.log("-" * 48, "info")
            self.log("Analytics pull complete.", "success")
        except Exception as e:
            import traceback
            self.log(f"Error: {e}", "error")
            self.log(traceback.format_exc(), "error")
        finally:
            sys.stdout = old_stdout
            self.after(0, self._finish)

    # ── Leaderboard ──────────────────────────────────────────────

    def _run_leaderboard(self):
        self.clear_log()
        self.show_progress()
        self.set_status("Loading leaderboard...")
        self.pull_btn.configure(state="disabled")
        threading.Thread(target=self._leaderboard_thread, daemon=True).start()

    def _leaderboard_thread(self):
        old_stdout = sys.stdout
        sys.stdout = _GuiWriter(self.log)
        try:
            from cot_core.analytics_core import show_leaderboard
            show_leaderboard(reload_module=True, log_cb=lambda s: self.log(s, "info"))
        except Exception as e:
            import traceback
            self.log(f"Error: {e}", "error")
            self.log(traceback.format_exc(), "error")
        finally:
            sys.stdout = old_stdout
            self.after(0, self._finish)

    def _finish(self):
        self.hide_progress()
        self.pull_btn.configure(state="normal")
        self.set_status("Done")


if __name__ == "__main__":
    try:
        install_global_crash_handler(
            tool="analytics_gui",
            out_dir=SCRIPTS_DIR,
            log_path=os.path.join(SCRIPTS_DIR, "launcher_log.txt"),
        )
    except Exception:
        pass
    app = AnalyticsGui()
    app.mainloop()
