"""
master_launcher.py  v4.1
YouTube Video Toolkit Launcher

Changes from v3.4:
- Console window fully suppressed for GUI tools (CREATE_NO_WINDOW)
- CLI tools (make_show, cot_pipeline) open a real console window (CREATE_NEW_CONSOLE)
- Clean error messages — no long path lists
- Settings now includes cot_config.json fields: PICTURES_DIR, OUTPUT_DIR,
  LLM_MODE (numbered radio), MODEL_NAME (dropdown from live Local LLM)
- LLM model dropdown fetches available models from Local LLM API
"""

import os
import sys
import json
import subprocess
import time
import traceback
import socket
import threading
from cot_core.logging_utils import log_exception
from cot_core.launch_utils import launch_interactive_windows
from cot_core.last_run_utils import LastRunArtifact
from cot_core.crash_utils import install_global_crash_handler
from cot_core.run_utils import run_with_artifact
from cot_core.orphan_utils import cleanup_orphans
from cot_core.path_settings import (
    default_audio_dir,
    default_output_dir,
    default_pictures_dir,
    discover_audio_prep_suite,
    looks_like_audio_prep_suite,
    audio_prep_status,
    find_audio_prep_launcher,
    find_ffmpeg,
    find_ffprobe,
)
from cot_core.local_llm import (
    chat_url_from_base,
    discover_local_llm,
    get_models as get_local_llm_models,
    normalize_base_url,
)

LAUNCHER_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, LAUNCHER_DIR)
os.environ["COT_SCRIPTS_DIR"] = LAUNCHER_DIR
os.environ.setdefault("COT_FORCE_IPV4", "1")

import customtkinter as ctk
from tkinter import filedialog, messagebox

# ── Windows process flags ─────────────────────────────────────
CREATE_NO_WINDOW    = 0x08000000   # hide console — for GUI tools
CREATE_NEW_CONSOLE  = 0x00000010   # new visible terminal — for CLI tools

_SINGLE_INSTANCE_HOST = "127.0.0.1"
_SINGLE_INSTANCE_PORT = 51337


def _send_show_to_existing_instance() -> bool:
    """Return True if an existing launcher was found and notified."""
    try:
        with socket.create_connection((_SINGLE_INSTANCE_HOST, _SINGLE_INSTANCE_PORT), timeout=0.25) as s:
            s.sendall(b"SHOW\n")
        return True
    except OSError:
        return False


def _start_single_instance_server(app) -> None:
    """Start a background server to receive SHOW requests and focus the window."""

    def _handle_show():
        try:
            app.deiconify()
        except Exception:
            pass
        _focus_window(app)

    def _server():
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((_SINGLE_INSTANCE_HOST, _SINGLE_INSTANCE_PORT))
            srv.listen(5)
        except OSError:
            return

        while True:
            try:
                conn, _addr = srv.accept()
            except OSError:
                break
            try:
                data = conn.recv(64)
                if b"SHOW" in data:
                    app.after(0, _handle_show)
            except Exception:
                pass
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

    threading.Thread(target=_server, daemon=True).start()

# ── Logging ───────────────────────────────────────────────────
def _log(msg):
    try:
        with open(os.path.join(LAUNCHER_DIR, "launcher_log.txt"), "a", encoding="utf-8") as f:
            f.write(f"[{time.asctime()}] {msg}\n")
    except Exception:
        try:
            log_exception(
                context="master_launcher:_log",
                exc=sys.exc_info()[1] or Exception("Unknown error"),
                log_path=os.path.join(LAUNCHER_DIR, "launcher_log.txt"),
            )
        except Exception:
            pass


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

# ── master_config.json ────────────────────────────────────────
MASTER_CONFIG_PATH = os.path.join(LAUNCHER_DIR, "master_config.json")
_MASTER_DEFAULTS = {
    "audio_suite_path": "",
    "images_path":      "",
    "theme":            "system",
    "accent":           "blue",
}

def _load_master():
    cfg = dict(_MASTER_DEFAULTS)
    if os.path.isfile(MASTER_CONFIG_PATH):
        try:
            with open(MASTER_CONFIG_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                cfg.update(loaded)
        except Exception:
            try:
                log_exception(
                    context="master_launcher:_load_master",
                    exc=sys.exc_info()[1] or Exception("Unknown error"),
                    log_path=os.path.join(LAUNCHER_DIR, "launcher_log.txt"),
                )
            except Exception:
                pass
    # Repair stale Audio Prep Suite path, then auto-discover in sibling folders.
    audio_path = (cfg.get("audio_suite_path") or "").strip()
    if audio_path and not looks_like_audio_prep_suite(audio_path):
        cfg["audio_suite_path"] = ""
        audio_path = ""
    if not audio_path:
        found = discover_audio_prep_suite(LAUNCHER_DIR)
        if found:
            cfg["audio_suite_path"] = found
            try:
                _save_master(cfg)
            except Exception:
                pass
    return cfg

def _save_master(cfg):
    try:
        with open(MASTER_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        _log(f"Failed to save master_config: {e}")

_mcfg = _load_master()

# ── cot_config.json ───────────────────────────────────────────
COT_CONFIG_PATH = os.path.join(LAUNCHER_DIR, "cot_config.json")
_COT_DEFAULTS = {
    "PICTURES_DIR":   default_pictures_dir(),
    "OUTPUT_DIR":     "",
    "AUDIO_DIR":      default_audio_dir(),
    "AUDIO_PREP_SUITE_PATH": "",
    "FFMPEG":         find_ffmpeg(""),
    "FFPROBE":        find_ffprobe(""),
    "WORKFLOW_MODE":  "single_or_batch",
    "LAST_PROJECT_ROOT": "",
    "LAST_PROJECT_FOLDER": "",
    "LLM_MODE":       "manual_only",
    "LOCAL_LLM_PROVIDER": "",
    "LOCAL_LLM_BASE_URL": "",
    "MODEL_NAME":     "",
    "CLIENT_SECRETS": os.path.join(LAUNCHER_DIR, "client_secrets.json"),
    "TOKEN_FILE":     os.path.join(LAUNCHER_DIR, "token.json"),
    "YT_CHANNEL_ID":  "",
    "CHANNEL_NAME":   "",
    "FIXED_TAGS":     [],
    "LLM_VOICE_STYLE":    "",
    "LLM_EXAMPLES_BLOCK": "",
    "MAKE_SHOW_FINAL_HOLD_SEC": 2.0,
    "MAKE_SHOW_FINAL_FADE_SEC": 2.0,
    "MAKE_SHOW_AUDIO_FADE_SEC": 2.0,
}

def _load_cot_config():
    cfg = dict(_COT_DEFAULTS)
    if os.path.isfile(COT_CONFIG_PATH):
        try:
            with open(COT_CONFIG_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                cfg.update(loaded)
                # Backward compatibility: old configs used LMSTUDIO_URL/lmstudio_local.
                if not cfg.get("LOCAL_LLM_BASE_URL") and loaded.get("LMSTUDIO_URL"):
                    cfg["LOCAL_LLM_BASE_URL"] = loaded.get("LMSTUDIO_URL")
                if cfg.get("LLM_MODE") == "lmstudio_local":
                    cfg["LLM_MODE"] = "local_llm"
        except Exception:
            try:
                log_exception(
                    context="master_launcher:_load_cot_config",
                    exc=sys.exc_info()[1] or Exception("Unknown error"),
                    log_path=os.path.join(LAUNCHER_DIR, "launcher_log.txt"),
                )
            except Exception:
                pass
    cfg["SCRIPTS_DIR"] = LAUNCHER_DIR
    if not (cfg.get("OUTPUT_DIR") or "").strip():
        cfg["OUTPUT_DIR"] = default_output_dir(cfg.get("PICTURES_DIR") or default_pictures_dir())
    if not (cfg.get("AUDIO_DIR") or "").strip():
        cfg["AUDIO_DIR"] = default_audio_dir()
    cfg["FFMPEG"] = find_ffmpeg(str(cfg.get("FFMPEG") or ""))
    cfg["FFPROBE"] = find_ffprobe(str(cfg.get("FFPROBE") or ""))
    audio_path = (cfg.get("AUDIO_PREP_SUITE_PATH") or "").strip()
    if audio_path and not looks_like_audio_prep_suite(audio_path):
        cfg["AUDIO_PREP_SUITE_PATH"] = ""
        audio_path = ""
    if not audio_path:
        found = discover_audio_prep_suite(LAUNCHER_DIR)
        if found:
            cfg["AUDIO_PREP_SUITE_PATH"] = found
    return cfg

def _save_cot_config(cfg):
    """Merge changes back into cot_config.json, preserving all other keys."""
    existing = {}
    if os.path.isfile(COT_CONFIG_PATH):
        try:
            with open(COT_CONFIG_PATH, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            pass
    existing.update(cfg)
    try:
        with open(COT_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2)
    except Exception as e:
        _log(f"Failed to save cot_config: {e}")
        raise

_ccfg = _load_cot_config()

# ── Theme ─────────────────────────────────────────────────────
ctk.set_appearance_mode(_mcfg.get("theme", "system"))
ctk.set_default_color_theme(_mcfg.get("accent", "blue"))

# ── Tool definitions ──────────────────────────────────────────
# CLI tools: open a real console window (user must interact with them)
# GUI tools: no console window needed

CLI_TOOLS = {
    # (empty) — GUI tools should not open a console window
}

COT_TOOLS = [
    ("Media Prep",        "media_prep_gui.py", "Curate backlog + generate flipbook previews",   "gui"),
    ("Advanced Video Creator", "make_show_gui.py",  "Open all video creation modes and settings",     "gui"),
    ("Playlist Manager",   "playlist_manager_gui.py", "Create playlists + suggest videos",      "gui"),
    ("Metadata",           "metadata_gui.py",   "YouTube titles, tags & descriptions",          "gui"),
    ("Upload to YouTube",  "upload_gui.py",     "YouTube upload with quota tracker",            "gui"),
    ("Analytics",          "analytics_gui.py",  "YouTube channel & video performance",          "gui"),
    ("View and Edit Live", "view_edit_gui.py",  "Edit published metadata (DEL=delete; Dry Run blocks)",           "gui"),
]



# ── Local LLM helper ──────────────────────────────────────────

def _fetch_local_llm_models(url: str) -> list[str]:
    """Query a provider-neutral Local LLM endpoint for model IDs."""
    try:
        return get_local_llm_models(url, timeout=4)
    except Exception:
        return []


def _discover_and_persist_local_llm() -> tuple[str, str, list[str]]:
    """Discover a running local endpoint and persist it into config."""
    global _ccfg
    status = discover_local_llm(_ccfg.get("LOCAL_LLM_BASE_URL") or _ccfg.get("LMSTUDIO_URL") or "")
    if not status.ok:
        return "", status.error or "No Local LLM endpoint found", []
    _ccfg["LLM_MODE"] = "local_llm"
    _ccfg["LOCAL_LLM_PROVIDER"] = status.provider
    _ccfg["LOCAL_LLM_BASE_URL"] = status.chat_url or chat_url_from_base(status.base_url)
    _ccfg["LMSTUDIO_URL"] = _ccfg["LOCAL_LLM_BASE_URL"]  # legacy compatibility only
    if status.models and not _ccfg.get("MODEL_NAME"):
        _ccfg["MODEL_NAME"] = status.models[0]
    try:
        _save_cot_config({
            "LLM_MODE": _ccfg["LLM_MODE"],
            "LOCAL_LLM_PROVIDER": _ccfg["LOCAL_LLM_PROVIDER"],
            "LOCAL_LLM_BASE_URL": _ccfg["LOCAL_LLM_BASE_URL"],
            "LMSTUDIO_URL": _ccfg["LOCAL_LLM_BASE_URL"],
            "MODEL_NAME": _ccfg.get("MODEL_NAME", ""),
        })
    except Exception:
        pass
    return _ccfg["LOCAL_LLM_BASE_URL"], status.provider, list(status.models)


# ── Folder row helper ─────────────────────────────────────────

def _folder_row(form, row_num, label_text, str_var, browse_title):
    ctk.CTkLabel(form, text=label_text, anchor="w", width=180).grid(
        row=row_num, column=0, sticky="w", padx=10, pady=5)
    fr = ctk.CTkFrame(form, fg_color="transparent")
    fr.grid(row=row_num, column=1, sticky="ew", padx=10, pady=5)
    fr.grid_columnconfigure(0, weight=1)
    ctk.CTkEntry(fr, textvariable=str_var).grid(
        row=0, column=0, sticky="ew", padx=(0, 6))
    def _browse(v=str_var, t=browse_title):
        folder = filedialog.askdirectory(title=t)
        if folder:
            v.set(folder)
    ctk.CTkButton(fr, text="Browse", width=70, command=_browse
                  ).grid(row=0, column=1)


def _file_row(form, row_num, label_text, str_var, browse_title, filetypes=("JSON files", "*.json")):
    ctk.CTkLabel(form, text=label_text, anchor="w", width=180).grid(
        row=row_num, column=0, sticky="w", padx=10, pady=5)
    fr = ctk.CTkFrame(form, fg_color="transparent")
    fr.grid(row=row_num, column=1, sticky="ew", padx=10, pady=5)
    fr.grid_columnconfigure(0, weight=1)
    ctk.CTkEntry(fr, textvariable=str_var).grid(
        row=0, column=0, sticky="ew", padx=(0, 6))

    def _browse(v=str_var, t=browse_title):
        path = filedialog.askopenfilename(title=t, filetypes=[filetypes, ("All files", "*")])
        if path:
            v.set(path)

    ctk.CTkButton(fr, text="Browse", width=70, command=_browse).grid(row=0, column=1)


# ── Card builder ──────────────────────────────────────────────

def _make_card(parent, title, desc, command):
    f = ctk.CTkFrame(parent)
    f.pack(fill="x", pady=4, padx=4)
    txt = ctk.CTkFrame(f, fg_color="transparent")
    txt.pack(side="left", padx=10, pady=8)
    ctk.CTkLabel(txt, text=title,
                 font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w")
    ctk.CTkLabel(txt, text=desc,
                 font=ctk.CTkFont(size=10), text_color="gray").pack(anchor="w")
    ctk.CTkButton(f, text="Launch", width=72,
                  command=command).pack(side="right", padx=10)


# ── Settings window ───────────────────────────────────────────

class SettingsWindow(ctk.CTkToplevel):
    def __init__(self, parent, on_save=None):
        super().__init__(parent)
        self.title("Settings")
        self.geometry("620x580")
        self.resizable(False, False)
        self.grab_set()
        self.on_save = on_save
        _focus_window(self)

        # Scroll container so nothing is clipped
        scroll = ctk.CTkScrollableFrame(self)
        scroll.pack(fill="both", expand=True, padx=0, pady=0)
        scroll.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(scroll, text="Master Launcher Settings",
                     font=ctk.CTkFont(size=14, weight="bold")
                     ).grid(row=0, column=0, columnspan=2,
                             pady=(14, 4), padx=10, sticky="w")

        # ── Section: Folders ────────────────────────────────────
        ctk.CTkLabel(scroll, text="FOLDERS",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color="gray"
                     ).grid(row=1, column=0, columnspan=2,
                             sticky="w", padx=10, pady=(10, 2))

        self._audio_path   = ctk.StringVar(value=_mcfg.get("audio_suite_path", ""))
        self._images_path  = ctk.StringVar(value=_mcfg.get("images_path", ""))
        self._pics_path    = ctk.StringVar(value=_ccfg.get("PICTURES_DIR", ""))
        self._output_path  = ctk.StringVar(value=_ccfg.get("OUTPUT_DIR", ""))
        self._audio_dir_path = ctk.StringVar(value=_ccfg.get("AUDIO_DIR", default_audio_dir()))
        self._ffmpeg_path = ctk.StringVar(value=_ccfg.get("FFMPEG", find_ffmpeg("")))
        self._ffprobe_path = ctk.StringVar(value=_ccfg.get("FFPROBE", find_ffprobe("")))
        self._workflow_mode = ctk.StringVar(value=_ccfg.get("WORKFLOW_MODE", "single_or_batch"))

        self._ms_hold_sec = ctk.StringVar(value=str(_ccfg.get("MAKE_SHOW_FINAL_HOLD_SEC", 2.0)))
        self._ms_fade_sec = ctk.StringVar(value=str(_ccfg.get("MAKE_SHOW_FINAL_FADE_SEC", 2.0)))
        self._ms_audio_fade_sec = ctk.StringVar(value=str(_ccfg.get("MAKE_SHOW_AUDIO_FADE_SEC", 2.0)))

        _folder_row(scroll, 2,  "Audio Prep Suite",     self._audio_path,  "Select Audio Prep Suite folder")
        ctk.CTkButton(scroll, text="Find", width=60, command=self._find_audio_suite
                      ).grid(row=2, column=2, padx=(0, 10), pady=5)
        _folder_row(scroll, 3,  "Images / Pictures",    self._images_path, "Select Images folder")
        _folder_row(scroll, 4,  "Source pictures folder",self._pics_path,   "Select source pictures folder")
        _folder_row(scroll, 5,  "Output videos folder",  self._output_path, "Select output videos folder")

        ctk.CTkLabel(
            scroll,
            text="Audio Prep Suite is optional and installed separately. Use Find/Browse if it is not auto-detected.",
            font=ctk.CTkFont(size=10),
            text_color="gray",
            justify="left",
            anchor="w",
        ).grid(row=6, column=1, sticky="w", padx=10, pady=(0, 6))

        _folder_row(scroll, 7,  "Audio files folder", self._audio_dir_path, "Select audio/music folder")
        _file_row(scroll, 8, "FFmpeg executable", self._ffmpeg_path, "Select ffmpeg executable", ("Executable", "*.exe"))
        _file_row(scroll, 9, "FFprobe executable", self._ffprobe_path, "Select ffprobe executable", ("Executable", "*.exe"))

        ctk.CTkLabel(scroll, text="Workflow", anchor="w", width=180
                     ).grid(row=10, column=0, sticky="w", padx=10, pady=5)
        ctk.CTkOptionMenu(
            scroll,
            values=["single_or_batch", "single_folder", "batch_folders", "project_workspace"],
            variable=self._workflow_mode,
            width=180,
        ).grid(row=10, column=1, sticky="w", padx=10, pady=5)

        # ── Section: LLM ────────────────────────────────────────
        ctk.CTkLabel(scroll, text="LLM / AI",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color="gray"
                     ).grid(row=11, column=0, columnspan=2,
                             sticky="w", padx=10, pady=(14, 2))

        # LLM mode — numbered radio style
        ctk.CTkLabel(scroll, text="LLM Mode", anchor="w", width=180
                     ).grid(row=12, column=0, sticky="w", padx=10, pady=5)

        mode_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        mode_frame.grid(row=12, column=1, sticky="w", padx=10, pady=5)

        current_mode = _ccfg.get("LLM_MODE", "manual_only")
        self._llm_mode = ctk.StringVar(
            value="1" if current_mode in ("local_llm", "lmstudio_local") else "2"
        )
        ctk.CTkRadioButton(mode_frame, text="1.  Use Local LLM (optional AI)",
                           variable=self._llm_mode, value="1",
                           command=self._on_llm_mode_change
                           ).pack(anchor="w", pady=2)
        ctk.CTkRadioButton(mode_frame, text="2.  Manual only (no AI)",
                           variable=self._llm_mode, value="2",
                           command=self._on_llm_mode_change
                           ).pack(anchor="w", pady=2)

        # Local LLM endpoint
        ctk.CTkLabel(scroll, text="Local LLM endpoint", anchor="w", width=180
                     ).grid(row=13, column=0, sticky="w", padx=10, pady=5)
        self._lm_url = ctk.StringVar(value=_ccfg.get("LOCAL_LLM_BASE_URL") or _ccfg.get("LMSTUDIO_URL") or "")
        self._url_entry = ctk.CTkEntry(scroll, textvariable=self._lm_url)
        self._url_entry.grid(row=13, column=1, sticky="ew", padx=10, pady=5)

        # Model selection
        ctk.CTkLabel(scroll, text="Model", anchor="w", width=180
                     ).grid(row=14, column=0, sticky="w", padx=10, pady=5)

        model_row = ctk.CTkFrame(scroll, fg_color="transparent")
        model_row.grid(row=14, column=1, sticky="ew", padx=10, pady=5)
        model_row.grid_columnconfigure(0, weight=1)

        self._model_var = ctk.StringVar(value=_ccfg.get("MODEL_NAME", ""))
        self._model_menu = ctk.CTkOptionMenu(
            model_row, variable=self._model_var, values=["(click Refresh to load)"],
            width=300)
        self._model_menu.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ctk.CTkButton(model_row, text="Refresh", width=80,
                      command=self._refresh_models
                      ).grid(row=0, column=1)
        ctk.CTkButton(model_row, text="Find", width=60,
                      command=self._find_local_llm
                      ).grid(row=0, column=2, padx=(6, 0))

        self._model_status = ctk.CTkLabel(scroll, text="",
                                          font=ctk.CTkFont(size=10),
                                          text_color="gray", anchor="w")
        self._model_status.grid(row=15, column=1, sticky="w", padx=10)

        # ── Section: YouTube ───────────────────────────────────
        ctk.CTkLabel(scroll, text="YOUTUBE",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color="gray"
                     ).grid(row=16, column=0, columnspan=2,
                             sticky="w", padx=10, pady=(14, 2))

        self._client_secrets = ctk.StringVar(value=_ccfg.get("CLIENT_SECRETS", os.path.join(LAUNCHER_DIR, "client_secrets.json")))
        self._token_file = ctk.StringVar(value=_ccfg.get("TOKEN_FILE", os.path.join(LAUNCHER_DIR, "token.json")))
        self._yt_channel_id = ctk.StringVar(value=_ccfg.get("YT_CHANNEL_ID", ""))
        self._channel_name = ctk.StringVar(value=_ccfg.get("CHANNEL_NAME", ""))
        tags_val = _ccfg.get("FIXED_TAGS", [])
        if isinstance(tags_val, list):
            tags_val = ", ".join(tags_val)
        self._fixed_tags = ctk.StringVar(value=str(tags_val or ""))

        self._voice_style = _ccfg.get("LLM_VOICE_STYLE", "") or ""
        self._examples_block = _ccfg.get("LLM_EXAMPLES_BLOCK", "") or ""

        _file_row(scroll, 17, "client_secrets.json", self._client_secrets, "Select client_secrets.json")
        _file_row(scroll, 18, "token.json", self._token_file, "Select token.json")

        # Channel selection row
        ctk.CTkLabel(scroll, text="Channel", anchor="w", width=180
                     ).grid(row=19, column=0, sticky="w", padx=10, pady=5)
        ch_row = ctk.CTkFrame(scroll, fg_color="transparent")
        ch_row.grid(row=19, column=1, sticky="ew", padx=10, pady=5)
        ch_row.grid_columnconfigure(0, weight=1)

        self._channels_menu_var = ctk.StringVar(value="(click Refresh)")
        self._channels_menu = ctk.CTkOptionMenu(ch_row, variable=self._channels_menu_var, values=["(click Refresh)"])
        self._channels_menu.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ctk.CTkButton(ch_row, text="Refresh", width=80,
                      command=self._refresh_channels
                      ).grid(row=0, column=1)

        self._yt_status = ctk.CTkLabel(scroll, text="",
                                       font=ctk.CTkFont(size=10),
                                       text_color="gray", anchor="w")
        self._yt_status.grid(row=20, column=1, sticky="w", padx=10)

        # Channel name/id fields
        ctk.CTkLabel(scroll, text="Channel name", anchor="w", width=180
                     ).grid(row=21, column=0, sticky="w", padx=10, pady=5)
        ctk.CTkEntry(scroll, textvariable=self._channel_name).grid(row=21, column=1, sticky="ew", padx=10, pady=5)

        ctk.CTkLabel(scroll, text="Channel ID", anchor="w", width=180
                     ).grid(row=22, column=0, sticky="w", padx=10, pady=5)
        ctk.CTkEntry(scroll, textvariable=self._yt_channel_id).grid(row=22, column=1, sticky="ew", padx=10, pady=5)

        ctk.CTkLabel(scroll, text="Fixed tags (comma-separated)", anchor="w", width=180
                     ).grid(row=23, column=0, sticky="w", padx=10, pady=5)
        ctk.CTkEntry(scroll, textvariable=self._fixed_tags).grid(row=23, column=1, sticky="ew", padx=10, pady=5)

        # LLM prompt template overrides
        ctk.CTkLabel(scroll, text="LLM voice/style override", anchor="w", width=180
                     ).grid(row=24, column=0, sticky="nw", padx=10, pady=5)
        self._voice_box = ctk.CTkTextbox(scroll, height=90)
        self._voice_box.grid(row=24, column=1, sticky="ew", padx=10, pady=5)
        self._voice_box.insert("1.0", self._voice_style)

        ctk.CTkLabel(scroll, text="LLM examples override", anchor="w", width=180
                     ).grid(row=25, column=0, sticky="nw", padx=10, pady=5)
        self._examples_box = ctk.CTkTextbox(scroll, height=120)
        self._examples_box.grid(row=25, column=1, sticky="ew", padx=10, pady=5)
        self._examples_box.insert("1.0", self._examples_block)

        # ── Section: Appearance ──────────────────────────────────
        ctk.CTkLabel(scroll, text="APPEARANCE",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color="gray"
                     ).grid(row=26, column=0, columnspan=2,
                             sticky="w", padx=10, pady=(14, 2))

        ctk.CTkLabel(scroll, text="Theme", anchor="w", width=180
                     ).grid(row=27, column=0, sticky="w", padx=10, pady=5)
        self._theme = ctk.CTkOptionMenu(
            scroll, values=["system", "dark", "light"],
            command=lambda v: ctk.set_appearance_mode(v))
        self._theme.set(_mcfg.get("theme", "system"))
        self._theme.grid(row=27, column=1, sticky="w", padx=10, pady=5)

        # ── Section: Make Show ──────────────────────────────────
        ctk.CTkLabel(scroll, text="MAKE SHOW",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color="gray"
                     ).grid(row=28, column=0, columnspan=2,
                             sticky="w", padx=10, pady=(14, 2))

        ctk.CTkLabel(scroll, text="Final hold (sec)", anchor="w", width=180
                     ).grid(row=29, column=0, sticky="w", padx=10, pady=5)
        ctk.CTkEntry(scroll, textvariable=self._ms_hold_sec, width=120
                     ).grid(row=29, column=1, sticky="w", padx=10, pady=5)

        ctk.CTkLabel(scroll, text="Final fade (sec)", anchor="w", width=180
                     ).grid(row=30, column=0, sticky="w", padx=10, pady=5)
        ctk.CTkEntry(scroll, textvariable=self._ms_fade_sec, width=120
                     ).grid(row=30, column=1, sticky="w", padx=10, pady=5)

        ctk.CTkLabel(scroll, text="Audio fade (sec)", anchor="w", width=180
                     ).grid(row=31, column=0, sticky="w", padx=10, pady=5)
        ctk.CTkEntry(scroll, textvariable=self._ms_audio_fade_sec, width=120
                     ).grid(row=31, column=1, sticky="w", padx=10, pady=5)

        # ── Save button ──────────────────────────────────────────
        ctk.CTkButton(self, text="Save and Close", height=36,
                      command=self._save).pack(pady=12, padx=20, fill="x")

        # Update UI state for current mode
        self._on_llm_mode_change()

        # Auto-refresh models if Local LLM mode is active
        if _ccfg.get("LLM_MODE") in ("local_llm", "lmstudio_local"):
            self.after(300, self._refresh_models)

    def _find_audio_suite(self):
        found = discover_audio_prep_suite(LAUNCHER_DIR)
        if found:
            self._audio_path.set(found)
            _mcfg["audio_suite_path"] = found
            _ccfg["AUDIO_PREP_SUITE_PATH"] = found
            try:
                self._yt_status.configure(text=f"Audio Prep Suite found: {found}", text_color="green")
            except Exception:
                pass
        else:
            messagebox.showinfo(
                "Audio Prep Suite not found",
                "I could not find Audio Prep Suite in this folder or sibling folders. Use Browse to select it manually."
            )

    def _find_local_llm(self):
        endpoint, provider, models = _discover_and_persist_local_llm()
        if endpoint:
            self._llm_mode.set("1")
            self._lm_url.set(endpoint)
            self._on_llm_mode_change()
            if models:
                self._model_menu.configure(values=models)
                self._model_var.set(_ccfg.get("MODEL_NAME") or models[0])
            self._model_status.configure(text=f"Found {provider} ({len(models)} model(s))", text_color="green")
        else:
            self._model_status.configure(text=provider or "No Local LLM endpoint found", text_color="orange")
            messagebox.showinfo(
                "Local LLM not found",
                "No running Local LLM endpoint was found. Start llama-server, LM Studio, or Ollama, then click Find again; or paste a custom OpenAI-compatible endpoint."
            )

    def _on_llm_mode_change(self):
        is_lm = self._llm_mode.get() == "1"
        state = "normal" if is_lm else "disabled"
        self._url_entry.configure(state=state)
        self._model_menu.configure(state=state)

    def _refresh_models(self):
        url = self._lm_url.get().strip()
        self._model_status.configure(text="Connecting to Local LLM...")
        self.update()
        models = _fetch_local_llm_models(url)
        if models:
            current = self._model_var.get()
            self._model_menu.configure(values=models)
            if current in models:
                self._model_var.set(current)
            else:
                self._model_var.set(models[0])
            self._model_status.configure(
                text=f"{len(models)} model(s) loaded", text_color="green")
        else:
            self._model_menu.configure(values=["(Local LLM not reachable)"])
            self._model_status.configure(
                text="Could not reach a Local LLM endpoint. Check llama-server, LM Studio, Ollama, or custom endpoint.",
                text_color="orange")

    def _refresh_channels(self):
        self._yt_status.configure(text="Connecting to YouTube...", text_color="gray")
        self.update()

        def _worker():
            try:
                try:
                    import cot_config as _cfg
                    _cfg.load(gui_mode=True)
                    _cfg.set("CLIENT_SECRETS", self._client_secrets.get().strip(), save_now=False)
                    _cfg.set("TOKEN_FILE", self._token_file.get().strip(), save_now=False)
                except Exception:
                    pass
                import youtube_upload as yt_upload
                youtube = yt_upload.authenticate()
                resp = youtube.channels().list(part="id,snippet", mine=True).execute()
                chans = []
                for it in resp.get("items", []):
                    cid = it.get("id", "")
                    title = (it.get("snippet") or {}).get("title", "")
                    if cid:
                        label = f"{title}  [{cid}]" if title else f"(no title)  [{cid}]"
                        chans.append((label, cid, title))
                return (chans, None)
            except Exception as e:
                return ([], e)

        def _done(result):
            chans, err = result
            if err:
                self._channels_menu.configure(values=["(refresh failed)"])
                self._yt_status.configure(text=f"YouTube error: {err}", text_color="orange")
                return
            if not chans:
                self._channels_menu.configure(values=["(no channels found)"])
                self._yt_status.configure(text="No channels found for this account.", text_color="orange")
                return

            self._channel_choices = {label: (cid, title) for (label, cid, title) in chans}
            labels = [label for (label, _cid, _title) in chans]
            self._channels_menu.configure(values=labels)

            preferred = (self._yt_channel_id.get() or "").strip()
            chosen_label = None
            if preferred:
                for label, cid, _title in chans:
                    if cid == preferred:
                        chosen_label = label
                        break
            if not chosen_label:
                chosen_label = labels[0]

            self._channels_menu_var.set(chosen_label)
            cid, title = self._channel_choices.get(chosen_label, ("", ""))
            if cid:
                self._yt_channel_id.set(cid)
            if title and not self._channel_name.get().strip():
                self._channel_name.set(title)

            self._yt_status.configure(text=f"{len(chans)} channel(s) found", text_color="green")

        def _bg():
            result = _worker()
            self.after(0, _done, result)

        threading.Thread(target=_bg, daemon=True).start()

    def _save(self):
        # Validate folder paths
        for key, var, label in [
            ("audio_suite_path", self._audio_path,  "Audio Prep Suite"),
            ("images_path",      self._images_path, "Images"),
        ]:
            path = var.get().strip()
            if path and not os.path.isdir(path):
                messagebox.showerror("Not found", f"{label} folder not found:\n{path}")
                return
            if key == "audio_suite_path" and path and not looks_like_audio_prep_suite(path):
                messagebox.showerror("Not recognized", f"This does not look like Audio Prep Suite:\n{path}\n\nChoose the app folder, not an internal subfolder.")
                return
            _mcfg[key] = path

        for key, var, label in [
            ("PICTURES_DIR", self._pics_path,   "Source pictures"),
            ("OUTPUT_DIR",   self._output_path, "Output videos"),
            ("AUDIO_DIR",    self._audio_dir_path, "Audio files"),
        ]:
            path = var.get().strip()
            if path and not os.path.isdir(path):
                messagebox.showerror("Not found", f"{label} folder not found:\n{path}")
                return
            _ccfg[key] = path

        for key, var, label in [
            ("FFMPEG", self._ffmpeg_path, "FFmpeg"),
            ("FFPROBE", self._ffprobe_path, "FFprobe"),
        ]:
            path = var.get().strip()
            # Allow bare executable names such as ffmpeg/ffprobe when available on PATH.
            has_path_sep = any(sep in path for sep in (os.path.sep, os.path.altsep or "", "/", "\\") if sep)
            if path and has_path_sep and not os.path.isfile(path):
                messagebox.showerror("Not found", f"{label} executable not found:\n{path}")
                return
            _ccfg[key] = path

        _ccfg["WORKFLOW_MODE"] = self._workflow_mode.get().strip() or "single_or_batch"

        # Keep both config files aligned for the optional Audio Prep Suite integration.
        _ccfg["AUDIO_PREP_SUITE_PATH"] = _mcfg.get("audio_suite_path", "")

        # LLM settings
        _ccfg["LLM_MODE"] = "local_llm" if self._llm_mode.get() == "1" else "manual_only"
        endpoint = self._lm_url.get().strip()
        _ccfg["LOCAL_LLM_BASE_URL"] = chat_url_from_base(endpoint) if endpoint else ""
        _ccfg["LMSTUDIO_URL"] = _ccfg["LOCAL_LLM_BASE_URL"]  # legacy compatibility only
        if _ccfg["LOCAL_LLM_BASE_URL"]:
            _ccfg["LOCAL_LLM_PROVIDER"] = _ccfg.get("LOCAL_LLM_PROVIDER") or "Custom/Saved Local LLM"
        model = self._model_var.get()
        if model and "(Local LLM" not in model and "(click" not in model:
            _ccfg["MODEL_NAME"] = model

        # Make Show timing (seconds)
        try:
            _ccfg["MAKE_SHOW_FINAL_HOLD_SEC"] = float(self._ms_hold_sec.get().strip() or "2.0")
            _ccfg["MAKE_SHOW_FINAL_FADE_SEC"] = float(self._ms_fade_sec.get().strip() or "2.0")
            _ccfg["MAKE_SHOW_AUDIO_FADE_SEC"] = float(self._ms_audio_fade_sec.get().strip() or "2.0")
        except Exception:
            messagebox.showerror("Invalid value", "Make Show timing fields must be numbers (seconds).")
            return

        # Appearance
        _mcfg["theme"] = self._theme.get()

        # YouTube / channel settings
        _ccfg["CLIENT_SECRETS"] = self._client_secrets.get().strip()
        _ccfg["TOKEN_FILE"] = self._token_file.get().strip()
        _ccfg["YT_CHANNEL_ID"] = self._yt_channel_id.get().strip()
        _ccfg["CHANNEL_NAME"] = self._channel_name.get().strip()
        tags_str = self._fixed_tags.get().strip()
        _ccfg["FIXED_TAGS"] = [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else []

        try:
            _ccfg["LLM_VOICE_STYLE"] = self._voice_box.get("1.0", "end").strip()
            _ccfg["LLM_EXAMPLES_BLOCK"] = self._examples_box.get("1.0", "end").strip()
        except Exception:
            pass

        try:
            _save_master(_mcfg)
            _save_cot_config({
                "PICTURES_DIR": _ccfg["PICTURES_DIR"],
                "OUTPUT_DIR":   _ccfg["OUTPUT_DIR"],
                "AUDIO_DIR":    _ccfg.get("AUDIO_DIR", ""),
                "CSV_PATH":     os.path.join(_ccfg["OUTPUT_DIR"], "youtube_uploads.csv") if _ccfg.get("OUTPUT_DIR") else "",
                "UPLOAD_LOG":   os.path.join(_ccfg["OUTPUT_DIR"], "upload_log.json") if _ccfg.get("OUTPUT_DIR") else "",
                "SEEDS_FILE":   os.path.join(_ccfg["OUTPUT_DIR"], "seeds.json") if _ccfg.get("OUTPUT_DIR") else "",
                "AUDIO_PREP_SUITE_PATH": _ccfg.get("AUDIO_PREP_SUITE_PATH", ""),
                "FFMPEG":       _ccfg.get("FFMPEG", ""),
                "FFPROBE":      _ccfg.get("FFPROBE", ""),
                "WORKFLOW_MODE": _ccfg.get("WORKFLOW_MODE", "single_or_batch"),
                "LLM_MODE":     _ccfg["LLM_MODE"],
                "LOCAL_LLM_PROVIDER": _ccfg.get("LOCAL_LLM_PROVIDER", ""),
                "LOCAL_LLM_BASE_URL": _ccfg.get("LOCAL_LLM_BASE_URL", ""),
                "LMSTUDIO_URL": _ccfg.get("LOCAL_LLM_BASE_URL", ""),
                "MODEL_NAME":   _ccfg["MODEL_NAME"],
                "CLIENT_SECRETS": _ccfg.get("CLIENT_SECRETS", ""),
                "TOKEN_FILE":     _ccfg.get("TOKEN_FILE", ""),
                "YT_CHANNEL_ID":  _ccfg.get("YT_CHANNEL_ID", ""),
                "CHANNEL_NAME":   _ccfg.get("CHANNEL_NAME", ""),
                "FIXED_TAGS":     _ccfg.get("FIXED_TAGS", []),
                "LLM_VOICE_STYLE":    _ccfg.get("LLM_VOICE_STYLE", ""),
                "LLM_EXAMPLES_BLOCK": _ccfg.get("LLM_EXAMPLES_BLOCK", ""),
                "MAKE_SHOW_FINAL_HOLD_SEC":  _ccfg["MAKE_SHOW_FINAL_HOLD_SEC"],
                "MAKE_SHOW_FINAL_FADE_SEC":  _ccfg["MAKE_SHOW_FINAL_FADE_SEC"],
                "MAKE_SHOW_AUDIO_FADE_SEC":  _ccfg["MAKE_SHOW_AUDIO_FADE_SEC"],
            })
        except Exception as e:
            messagebox.showerror("Save Error", f"Failed to save settings:\n{e}")
            return

        self.destroy()
        if self.on_save:
            self.on_save()



# ── Admin window ──────────────────────────────────────────────

class AdminWindow(ctk.CTkToplevel):
    """
    Admin panel with two modes:
      - Run Setup Wizard / Edit Config  → opens cot_config.py in a real console
      - Read-only checks (deps, auth, Local LLM, show config) → output shown inline
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Admin — Toolkit Configuration")
        self.geometry("700x580")
        self.minsize(600, 480)
        # No grab_set() — must allow subprocess windows to get focus
        self._build_ui()
        _focus_window(self)

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # ── Header ──────────────────────────────────────────────
        ctk.CTkLabel(self, text="Toolkit Admin Panel",
                     font=ctk.CTkFont(size=15, weight="bold"),
                     anchor="w"
                     ).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))
        ctk.CTkLabel(self,
                     text="Run checks and edit configuration for the YouTube video workflow.",
                     font=ctk.CTkFont(size=11), text_color="gray", anchor="w"
                     ).grid(row=1, column=0, sticky="w", padx=16, pady=(0, 8))

        # ── Button row ───────────────────────────────────────────
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 6))

        # Left: interactive / wizard buttons (open console window)
        interactive = ctk.CTkFrame(btn_frame, corner_radius=8)
        interactive.pack(side="left", fill="y", padx=(0, 8))

        ctk.CTkLabel(interactive, text="Interactive (opens terminal)",
                     font=ctk.CTkFont(size=11, weight="bold"), text_color="gray"
                     ).pack(anchor="w", padx=10, pady=(8, 4))

        ctk.CTkButton(interactive, text="Run Setup Wizard",
                      width=200, height=34,
                      command=lambda: self._run_cli("wizard")
                      ).pack(padx=10, pady=3, fill="x")
        ctk.CTkButton(interactive, text="Full Admin Menu",
                      width=200, height=34,
                      fg_color="transparent", border_width=1,
                      command=lambda: self._run_cli("admin")
                      ).pack(padx=10, pady=(3, 10), fill="x")

        # Right: read-only checks (output shown in log panel below)
        checks = ctk.CTkFrame(btn_frame, corner_radius=8)
        checks.pack(side="left", fill="both", expand=True)

        ctk.CTkLabel(checks, text="Live checks (output below)",
                     font=ctk.CTkFont(size=11, weight="bold"), text_color="gray"
                     ).pack(anchor="w", padx=10, pady=(8, 4))

        btn_row1 = ctk.CTkFrame(checks, fg_color="transparent")
        btn_row1.pack(fill="x", padx=6, pady=2)
        btn_row2 = ctk.CTkFrame(checks, fg_color="transparent")
        btn_row2.pack(fill="x", padx=6, pady=(0, 8))

        for text, fn, row in [
            ("Check Dependencies",    "deps",   btn_row1),
            ("Check Google Auth",     "auth",   btn_row1),
            ("LLM Check",       "llm",    btn_row2),
            ("Toolkit Check",       "preflight", btn_row2),
            ("Diagnostics",          "diagnostics", btn_row2),
            ("Kill Orphans",          "orphans_kill", btn_row2),
            ("Show Current Config",   "config", btn_row2),
        ]:
            ctk.CTkButton(row, text=text, height=32,
                          command=lambda f=fn: self._run_check(f)
                          ).pack(side="left", padx=4, pady=2, expand=True, fill="x")

        # ── Log output ───────────────────────────────────────────
        log_frame = ctk.CTkFrame(self)
        log_frame.grid(row=3, column=0, sticky="nsew", padx=14, pady=(4, 4))
        log_frame.grid_columnconfigure(0, weight=1)
        log_frame.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        self._log_box = ctk.CTkTextbox(
            log_frame,
            font=ctk.CTkFont(family="Courier New", size=11),
            wrap="word", state="disabled"
        )
        self._log_box.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)

        # ── Footer ───────────────────────────────────────────────
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=4, column=0, sticky="ew", padx=14, pady=(0, 10))
        footer.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(footer,
                     text=f"Config file: {COT_CONFIG_PATH}",
                     font=ctk.CTkFont(size=10), text_color="gray", anchor="w"
                     ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(footer, text="Clear Log", width=80, height=26,
                      fg_color="transparent", border_width=1,
                      command=self._clear_log
                      ).grid(row=0, column=1, sticky="e")

        # Auto-run show config on open
        self.after(200, lambda: self._run_check("config"))

    # ── Interactive: run cot_config.py in a real console ─────────

    def _run_cli(self, mode: str):
        """
        Open wizard/admin in a real visible terminal.
        Writes a .bat file and uses os.startfile() — the only reliable
        way to get a visible cmd window from a pythonw parent process.
        """
        cot_config_path = os.path.join(LAUNCHER_DIR, "cot_config.py")
        if not os.path.isfile(cot_config_path):
            messagebox.showerror("Not found",
                                 f"cot_config.py not found in:\n{LAUNCHER_DIR}")
            return

        label = "Setup Wizard" if mode == "wizard" else "Admin Menu"
        fn    = "cfg.run_wizard()" if mode == "wizard" else "cfg.run_admin()"
        runner = os.path.join(LAUNCHER_DIR, "_admin_runner.py")
        bat    = os.path.join(LAUNCHER_DIR, "_admin_launcher.bat")

        artifact = LastRunArtifact(
            path=os.path.join(LAUNCHER_DIR, "last_run_admin_cli.json"),
            tool="admin_cli",
            inputs={
                "mode": mode,
                "label": label,
            },
            log_path=os.path.join(LAUNCHER_DIR, "launcher_log.txt"),
        )

        # ── Write the Python runner — use explicit open/write, no escaping ──
        try:
            with open(runner, "w", encoding="utf-8") as rf:
                rf.write("# -*- coding: utf-8 -*-\n")
                rf.write("import sys, os\n")
                rf.write("sys.stdout.reconfigure(encoding='utf-8', errors='replace')\n")
                launcher_dir_literal = json.dumps(LAUNCHER_DIR)
                rf.write(f"sys.path.insert(0, {launcher_dir_literal})\n")
                rf.write(f"os.chdir({launcher_dir_literal})\n")
                rf.write("import cot_config as cfg\n")
                rf.write("cfg.load()\n")
                rf.write(f"{fn}\n")
                rf.write("input('\\nDone. Press Enter to close...')\n")
        except Exception as e:
            messagebox.showerror("Error", f"Could not write runner:\n{e}")
            return

        if sys.platform == "win32":
            # Use python.exe (not pythonw) so the child has stdout
            py_exe = sys.executable.replace("pythonw.exe", "python.exe")
            if not os.path.isfile(py_exe):
                py_exe = sys.executable

            try:
                # Withdraw Admin window briefly so it cannot steal focus
                # back from the terminal as it opens. Restore after 1.5s.
                self.withdraw()

                ok = launch_interactive_windows(
                    title=f"Toolkit {label}",
                    cmd=[py_exe, runner],
                    cwd=LAUNCHER_DIR,
                    env=os.environ.copy(),
                    bat_path=bat,
                    log_path=os.path.join(LAUNCHER_DIR, "launcher_log.txt"),
                )
                try:
                    artifact.finish(ok=bool(ok), return_code=None)
                except Exception:
                    pass
                if not ok:
                    raise RuntimeError("Failed to open terminal (see launcher_log.txt).")

                self._append_log(
                    f"{label} opened in a new terminal window.\n"
                    f"Changes to cot_config.json take effect on next launch.\n",
                    "info"
                )
                self.after(1500, self._restore)
            except Exception as e:
                self.deiconify()
                _log(f"os.startfile failed: {e}")
                messagebox.showerror("Launch Error",
                                     f"Failed to open terminal:\n{e}\n\n"
                                     f"Try running manually:\n{bat}")
        else:
            for term in ["x-terminal-emulator", "gnome-terminal", "xterm"]:
                try:
                    self.withdraw()
                    subprocess.Popen([term, "--", sys.executable, runner],
                                     cwd=LAUNCHER_DIR)
                    self._append_log(f"{label} opened in terminal.\n", "info")
                    self.after(1500, self._restore)
                    break
                except FileNotFoundError:
                    continue
            else:
                messagebox.showerror("No terminal",
                                     f"Run manually:\n  python {runner}")

    def _restore(self):
        """Restore Admin window after terminal has had time to take focus."""
        try:
            self.deiconify()
            self.lift()
        except Exception:
            pass
    # ── Read-only checks: capture output and show in log ─────────

    def _run_check(self, check: str):
        """Run a cot_config check function and capture its output into the log."""
        import io
        from contextlib import redirect_stdout

        if check == "orphans_kill":
            self._run_orphan_cleanup()
            return

        self._append_log(f"--- {check.upper()} ---\n", "header")

        # Build a tiny script that runs the check and prints results
        check_calls = {
            "deps":   "cfg.check_dependencies()",
            "auth":   "cfg.check_auth()",
            "llm":    "cfg.check_llm()",
            "preflight": "cfg.check_preflight()",
            "diagnostics": "cfg.show_diagnostics()",
            "config": "cfg.show_config()",
        }
        fn_call = check_calls.get(check, "")
        if not fn_call:
            return

        runner = os.path.join(LAUNCHER_DIR, "_check_runner.py")
        script = (
            f"import sys, io, json\n"
            # Force UTF-8 stdout so Unicode chars (checkmark, cross etc) don't crash on cp1252
            f"sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')\n"
            f"sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')\n"
            f"sys.path.insert(0, {json.dumps(LAUNCHER_DIR)})\n"
            f"import cot_config as cfg\n"
            f"cfg.load()\n"
            f"import contextlib\n"
            f"buf = io.StringIO()\n"
            f"with contextlib.redirect_stdout(buf):\n"
            f"    {fn_call}\n"
            f"sys.stdout.buffer.write(buf.getvalue().encode('utf-8', errors='replace'))\n"
            f"sys.stdout.buffer.flush()\n"
        )

        try:
            with open(runner, "w", encoding="utf-8") as f:
                f.write(script)
        except Exception as e:
            self._append_log(f"Could not write check script: {e}\n", "error")
            return

        check_env = os.environ.copy()
        check_env["PYTHONUTF8"] = "1"        # force UTF-8 mode on Windows
        check_env["PYTHONIOENCODING"] = "utf-8"

        def _do_check(_artifact: LastRunArtifact):
            result = subprocess.run(
                [sys.executable, runner],
                cwd=LAUNCHER_DIR,
                capture_output=True,
                env=check_env,
                timeout=15,
                creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            output = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
            errors = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""

            try:
                _artifact.set_output("stdout", output)
                _artifact.set_output("stderr", errors)
            except Exception:
                pass

            rc = int(result.returncode)
            try:
                _artifact.finish(ok=(rc == 0), return_code=rc)
            except Exception:
                pass

            return rc, output, errors

        try:
            rc, output, errors = run_with_artifact(
                artifact_path=os.path.join(LAUNCHER_DIR, "last_run_admin_check.json"),
                tool="admin_check",
                inputs={
                    "check": check,
                    "fn_call": fn_call,
                },
                log_path=os.path.join(LAUNCHER_DIR, "launcher_log.txt"),
                preflight=False,
                fn=_do_check,
            )
            if str(output).strip():
                self._append_log(str(output), "normal")
            if str(errors).strip():
                self._append_log(str(errors), "error")
            if not str(output).strip() and not str(errors).strip():
                self._append_log("(no output)\n", "info")
        except subprocess.TimeoutExpired:
            self._append_log("Check timed out (15s).\n", "error")
        except Exception as e:
            self._append_log(f"Check failed: {e}\n", "error")

    def _run_orphan_cleanup(self) -> None:
        self._append_log("--- ORPHAN CLEANUP ---\n", "header")

        try:
            report = cleanup_orphans(
                images=["ffmpeg.exe", "ffprobe.exe", "python.exe", "pythonw.exe"],
                detect_only=True,
                scope_substrings=[LAUNCHER_DIR],
                include_commandline=True,
                log_path=os.path.join(LAUNCHER_DIR, "launcher_log.txt"),
            )
        except Exception as e:
            self._append_log(f"Orphan scan failed: {e}\n", "error")
            return

        scoped = report.get("scoped") if isinstance(report, dict) else []
        scoped = scoped if isinstance(scoped, list) else []

        if not scoped:
            self._append_log("No scoped orphan processes found.\n", "info")
            try:
                LastRunArtifact(
                    path=os.path.join(LAUNCHER_DIR, "last_run_orphan_cleanup.json"),
                    tool="orphan_cleanup",
                    inputs={"detect_only": True, "scope": [LAUNCHER_DIR]},
                    log_path=os.path.join(LAUNCHER_DIR, "launcher_log.txt"),
                ).set_output("report", report)
            except Exception:
                pass
            return

        preview_lines = []
        for p in scoped[:20]:
            try:
                preview_lines.append(f"PID {p.get('pid')}  {p.get('image')}  {str(p.get('command_line',''))[:140]}")
            except Exception:
                continue
        more = "" if len(scoped) <= 20 else f"\n... plus {len(scoped) - 20} more"

        ok = messagebox.askyesno(
            "Kill Orphans?",
            "This will terminate ONLY processes whose command line contains this toolkit folder:\n"
            f"{LAUNCHER_DIR}\n\n"
            f"Scoped matches: {len(scoped)}\n\n"
            + "\n".join(preview_lines)
            + more
            + "\n\nProceed?",
        )
        if not ok:
            self._append_log("Canceled by user.\n", "info")
            return

        try:
            report2 = cleanup_orphans(
                images=["ffmpeg.exe", "ffprobe.exe", "python.exe", "pythonw.exe"],
                detect_only=False,
                scope_substrings=[LAUNCHER_DIR],
                include_commandline=True,
                log_path=os.path.join(LAUNCHER_DIR, "launcher_log.txt"),
            )
        except Exception as e:
            self._append_log(f"Orphan kill failed: {e}\n", "error")
            return

        killed = report2.get("killed") if isinstance(report2, dict) else []
        killed = killed if isinstance(killed, list) else []
        self._append_log(f"Killed processes: {len(killed)}\n", "info")
        for p in killed[:30]:
            try:
                self._append_log(f"  PID {p.get('pid')} {p.get('image')}\n", "info")
            except Exception:
                pass

        try:
            art = LastRunArtifact(
                path=os.path.join(LAUNCHER_DIR, "last_run_orphan_cleanup.json"),
                tool="orphan_cleanup",
                inputs={"detect_only": False, "scope": [LAUNCHER_DIR]},
                log_path=os.path.join(LAUNCHER_DIR, "launcher_log.txt"),
            )
            art.set_output("report", report2)
            art.finish(ok=True, return_code=None)
        except Exception:
            pass

    # ── Log helpers ───────────────────────────────────────────────

    def _append_log(self, msg: str, kind: str = "normal"):
        colors = {
            "error":  "#F44336",
            "info":   "#9E9E9E",
            "header": "#4FC3F7",
            "normal": "",
        }
        self._log_box.configure(state="normal")
        color = colors.get(kind, "")
        if color:
            tag = f"tag_{kind}"
            self._log_box.tag_config(tag, foreground=color)
            self._log_box.insert("end", msg, tag)
        else:
            self._log_box.insert("end", msg)
        self._log_box.configure(state="disabled")
        self._log_box.see("end")

    def _clear_log(self):
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")


# ── Main launcher ─────────────────────────────────────────────

class MasterLauncher(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("YouTube Video Toolkit")
        self.geometry("900x560")
        self.minsize(700, 440)
        self._build_ui()
        _focus_window(self)
        _start_single_instance_server(self)

        # Open settings on first run if paths not configured
        if not os.path.isfile(COT_CONFIG_PATH) or not (_ccfg.get("PICTURES_DIR") and _ccfg.get("OUTPUT_DIR")):
            self.after(500, lambda: SettingsWindow(self, on_save=self._refresh_cards))

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Header
        hdr = ctk.CTkFrame(self, height=54, corner_radius=0)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(hdr, text="YouTube Video Toolkit",
                     font=ctk.CTkFont(size=18, weight="bold")
                     ).grid(row=0, column=0, sticky="w", padx=20, pady=14)
        ctk.CTkLabel(hdr, text="Video workflow + optional Audio Prep Suite",
                     font=ctk.CTkFont(size=11), text_color="gray"
                     ).grid(row=0, column=1, sticky="w", padx=6, pady=14)
        self.audio_btn = ctk.CTkButton(hdr, text=self._audio_button_text(), width=130, height=30,
                      fg_color="transparent", border_width=1,
                      command=self._audio_button_action
                      )
        self.audio_btn.grid(row=0, column=2, padx=(0, 6), pady=12, sticky="e")
        ctk.CTkButton(hdr, text="Admin", width=75, height=30,
                      fg_color="transparent", border_width=1,
                      command=self._open_admin
                      ).grid(row=0, column=3, padx=(0, 6), pady=12, sticky="e")
        ctk.CTkButton(hdr, text="Settings", width=85, height=30,
                      command=self._open_settings
                      ).grid(row=0, column=4, padx=(0, 12), pady=12, sticky="e")

        # Two-column body
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=12, pady=10)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(body, text="VIDEO WORKFLOWS",
                     font=ctk.CTkFont(size=11, weight="bold"), anchor="w"
                     ).grid(row=0, column=0, sticky="w", padx=4, pady=(0, 4))

        self.v_frame = ctk.CTkScrollableFrame(body, fg_color="transparent")
        self.v_frame.grid(row=1, column=0, sticky="nsew", padx=(0, 0))

        # Footer
        ctk.CTkLabel(self, text="github.com/kchorst/youtube-movie-creator-toolkit",
                     font=ctk.CTkFont(size=10), text_color="gray"
                     ).grid(row=2, column=0, pady=(0, 8))

        self._refresh_cards()

    def _refresh_cards(self):
        try:
            self.audio_btn.configure(text=self._audio_button_text())
        except Exception:
            pass
        for w in self.v_frame.winfo_children():
            w.destroy()

        _make_card(
            self.v_frame,
            "Create Movie from Media Folder",
            "Point to one folder of images/clips, auto-detect or select audio, and render one MP4.",
            lambda: self._launch_make_show_mode("E"),
        )
        _make_card(
            self.v_frame,
            "Batch Create Movies from Folders",
            "Each immediate subfolder becomes one movie. Can use per-folder detected audio or one shared track.",
            lambda: self._launch_make_show_mode("F"),
        )
        _make_card(
            self.v_frame,
            "Add Sound to Existing Movie",
            "Select an existing MP4/MOV plus audio and create a new *_with_audio.mp4 output.",
            lambda: self._launch_add_sound("single"),
        )
        _make_card(
            self.v_frame,
            "Batch Add Sound to Movies",
            "Batch scan a folder of movies and add matching/same-folder audio without modifying originals.",
            lambda: self._launch_add_sound("batch"),
        )

        ctk.CTkLabel(
            self.v_frame,
            text="Other YouTube Toolkit tools",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="gray",
            anchor="w",
        ).pack(fill="x", padx=10, pady=(14, 4))
        for label, fname, desc, mode in COT_TOOLS:
            _make_card(self.v_frame, label, desc,
                       lambda f=fname, m=mode: self._launch_cot(f, m))

    # ── Launch handlers ──────────────────────────────────────────

    def _audio_status(self):
        audio_root = _mcfg.get("audio_suite_path", "") or _ccfg.get("AUDIO_PREP_SUITE_PATH", "")
        return audio_prep_status(audio_root) if audio_root else {"found": False, "runnable": False, "repair": ""}

    def _audio_button_text(self) -> str:
        status = self._audio_status()
        if status.get("found") and status.get("runnable"):
            return "Audio Prep Suite"
        if status.get("found"):
            return "Reconnect Audio Prep"
        return "Find Audio Prep"

    def _audio_button_action(self):
        status = self._audio_status()
        if status.get("found") and status.get("runnable"):
            self._launch_audio_suite()
        else:
            self._open_settings()

    def _launch_make_show_mode(self, mode_letter: str):
        env = {"COT_MAKE_SHOW_MODE": mode_letter}
        self._launch_cot("make_show_gui.py", "gui", extra_env=env)

    def _launch_add_sound(self, mode: str = "single"):
        env = {"YT_ADD_SOUND_MODE": "batch" if mode == "batch" else "single"}
        self._launch_cot("add_sound_gui.py", "gui", extra_env=env)

    def _launch_cot(self, filename: str, mode: str, extra_env: dict | None = None):
        """
        Launch a Video Toolkit tool.
        mode='cli'  -> CREATE_NEW_CONSOLE (user needs to interact in terminal)
        mode='gui'  -> CREATE_NO_WINDOW   (suppress console, GUI only)
        """
        # Look in cot_gui/ first, then directly in LAUNCHER_DIR
        candidates = [
            os.path.join(LAUNCHER_DIR, "cot_gui", filename),
            os.path.join(LAUNCHER_DIR, filename),
        ]
        target = next((p for p in candidates if os.path.isfile(p)), None)

        if not target:
            expected = os.path.join(LAUNCHER_DIR, "cot_gui", filename)
            messagebox.showerror(
                "Script not found",
                f"Could not find:  {filename}\n\n"
                f"Expected location:\n{expected}\n\n"
                f"Make sure the cot_gui folder is in:\n{LAUNCHER_DIR}"
            )
            _log(f"Launch failed: {filename} not found in {LAUNCHER_DIR}")
            return

        env = os.environ.copy()
        if extra_env:
            env.update({str(k): str(v) for k, v in extra_env.items()})
        env["COT_SCRIPTS_DIR"] = LAUNCHER_DIR
        env["PYTHONPATH"] = LAUNCHER_DIR
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        # For GUI tools, capture stderr to a log file so we can diagnose crashes
        stderr_log = os.path.join(LAUNCHER_DIR, "child_stderr.log")

        try:
            if mode == "cli":
                # CLI tool — open in a new console window and keep it open
                if sys.platform == "win32":
                    ok = launch_interactive_windows(
                        title=f"Video Toolkit Tool — {os.path.basename(target)}",
                        cmd=[sys.executable, target],
                        cwd=LAUNCHER_DIR,
                        env=env,
                        bat_path=os.path.join(LAUNCHER_DIR, "_launcher_cli.bat"),
                        log_path=os.path.join(LAUNCHER_DIR, "launcher_log.txt"),
                    )
                    if not ok:
                        raise RuntimeError("Failed to open CLI terminal (see launcher_log.txt).")
                    class _DummyProc:
                        pid = -1
                    proc = _DummyProc()
                else:
                    # For non-Windows, use previous behavior (or improve if needed for specific terminals)
                    proc = subprocess.Popen(
                        [sys.executable, target],
                        cwd=LAUNCHER_DIR,
                        env=env,
                        **({"start_new_session": True} if sys.platform != "win32" else {}),
                    )
            else:
                # GUI tool — capture stderr so crashes are visible in log
                stderr_f = open(stderr_log, "w", encoding="utf-8")
                proc = subprocess.Popen(
                    [sys.executable, target],
                    cwd=LAUNCHER_DIR,
                    env=env,
                    stderr=stderr_f,
                    creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                    **({"start_new_session": True} if sys.platform != "win32" else {}),
                )
                stderr_f.close()

            try:
                pid = getattr(proc, "pid", None)
            except Exception:
                pid = None
            if pid is None or int(pid) < 0:
                _log(f"Launched {mode.upper()} tool: {target}")
            else:
                _log(f"Launched {mode.upper()} tool: {target} (pid {pid})")

            # Minimize the launcher so the new tool can get focus
            self.iconify()

            if mode == "gui":
                # Check for quick exit (crash) and show error if so
                self.after(2000, lambda p=proc, t=target: self._check_exit(p, t))

        except Exception as e:
            _log(f"Popen failed: {target}: {e}")
            messagebox.showerror("Launch Error", f"Failed to start:\n{filename}\n\n{e}")

    def _check_exit(self, proc, target):
        """If a GUI tool exited within 2s, read stderr and show the error."""
        ret = proc.poll()
        if ret is not None and ret != 0:
            stderr_log = os.path.join(LAUNCHER_DIR, "child_stderr.log")
            err = ""
            try:
                err = open(stderr_log, encoding="utf-8", errors="replace").read().strip()
            except Exception:
                pass
            _log(f"Tool crashed (exit {ret}): {target}\n{err}")
            if err:
                # Show last 20 lines — most relevant part
                lines = err.strip().splitlines()
                snippet = "\n".join(lines[-20:])
                messagebox.showerror(
                    "Tool crashed",
                    f"{os.path.basename(target)} exited with error:\n\n{snippet}"
                )

    def _launch_audio_suite(self):
        audio_root = _mcfg.get("audio_suite_path", "") or _ccfg.get("AUDIO_PREP_SUITE_PATH", "")
        status = audio_prep_status(audio_root) if audio_root else {"found": False, "runnable": False}
        launcher = status.get("launcher", "")
        if not launcher:
            messagebox.showinfo(
                "Audio Prep Suite not configured",
                "Audio Prep Suite was not found or does not have a runnable launcher.\n\nOpen Settings, click Find, or browse to the Audio Prep Suite app folder."
            )
            return

        env = os.environ.copy()
        env["YT_TOOLKIT_PATH"] = LAUNCHER_DIR
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            if launcher.lower().endswith(".py"):
                cmd = [sys.executable, launcher]
            elif launcher.lower().endswith(".bat") and sys.platform == "win32":
                cmd = ["cmd", "/c", launcher]
            else:
                cmd = [launcher]
            subprocess.Popen(
                cmd,
                cwd=audio_root,
                env=env,
                creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                **({"start_new_session": True} if sys.platform != "win32" else {}),
            )
            _log(f"Launched Audio Prep Suite: {launcher}")
        except Exception as e:
            _log(f"Audio Prep Suite launch failed: {launcher}: {e}")
            messagebox.showerror("Launch Error", f"Failed to start Audio Prep Suite:\n{launcher}\n\n{e}")

    # ── Admin ────────────────────────────────────────────────────

    def _open_admin(self):
        win = AdminWindow(self)
        _focus_window(win)

    # ── Settings ─────────────────────────────────────────────────

    def _open_settings(self):
        win = SettingsWindow(self, on_save=self._refresh_cards)
        _focus_window(win)


if __name__ == "__main__":
    try:
        install_global_crash_handler(
            tool="master_launcher",
            out_dir=LAUNCHER_DIR,
            log_path=os.path.join(LAUNCHER_DIR, "launcher_log.txt"),
        )

        try:
            orphan_report = cleanup_orphans(
                images=["ffmpeg.exe", "ffprobe.exe", "python.exe", "pythonw.exe"],
                detect_only=True,
                scope_substrings=[LAUNCHER_DIR],
                include_commandline=True,
                log_path=os.path.join(LAUNCHER_DIR, "launcher_log.txt"),
            )
            try:
                LastRunArtifact(
                    path=os.path.join(LAUNCHER_DIR, "last_run_orphan_scan.json"),
                    tool="orphan_scan",
                    inputs={"detect_only": True},
                    log_path=os.path.join(LAUNCHER_DIR, "launcher_log.txt"),
                ).set_output("report", orphan_report)
            except Exception:
                pass
        except Exception:
            pass

        if _send_show_to_existing_instance():
            raise SystemExit(0)

        ctk.set_appearance_mode(_mcfg.get("theme", "system"))
        ctk.set_default_color_theme(_mcfg.get("accent", "blue"))
        app = MasterLauncher()
        app.mainloop()
    except Exception as e:
        _log(f"FATAL: {e}\n{traceback.format_exc()}")
        try:
            messagebox.showerror("Fatal Error", f"{e}\n\nSee launcher_log.txt")
        except Exception:
            print("Fatal:", e)
