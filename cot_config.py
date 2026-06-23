# ------------------------------------------------------------
# cot_config.py
# Version: 1.0.0
#
# Purpose:
#   Shared configuration for the YouTube Video Toolkit pipeline.
#   All scripts import this module to get their settings.
#   Settings are stored in cot_config.json (gitignored).
#
# Features:
#   - First-run wizard: auto-launches if no config found
#   - ADMIN mode: re-run wizard to edit any setting
#   - Dependency checker: scans required packages per script
#   - Auth checker: verifies client_secrets.json / token.json
#   - Local LLM checker: optional, only if LLM_MODE=local_llm
#   - LLM_MODE: local_llm | manual_only
#
# Usage:
#   import cot_config as cfg
#   cfg.load()                   # call once at startup
#   cfg.get("PICTURES_DIR")      # read a value
#   cfg.run_admin()              # launch admin wizard
#
# Config file:
#   cot_config.json — saved in same folder as this script.
#   Add to .gitignore — contains personal paths and credentials.
# ------------------------------------------------------------

import os
import sys
import json
import subprocess
import time
from typing import Any

from cot_core.path_settings import (
    audio_prep_status,
    looks_like_audio_prep_suite,
    default_audio_dir,
    default_output_dir,
    default_pictures_dir,
    find_ffmpeg,
    find_ffprobe,
    discover_audio_prep_suite,
)
from cot_core.local_llm import (
    chat_url_from_base,
    check_endpoint as check_local_endpoint,
    discover_local_llm,
    get_models as get_local_models,
)

# ── Location of config file ───────────────────────────────────
SCRIPTS_DIR  = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH  = os.path.join(SCRIPTS_DIR, "cot_config.json")
VERSION      = "1.0.0"

# Force UTF-8 output so box-drawing chars work on Windows cp1252 terminals
import sys as _sys
if hasattr(_sys.stdout, "reconfigure"):
    try:
        _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── Loaded config dict ────────────────────────────────────────
_config = {}

# ── Required packages per script ─────────────────────────────
DEPENDENCIES = {
    "cot_pipeline.py": [],
    "youtube_meta.py": [
        ("requests",              "requests"),
        ("tkinter",               None),           # bundled with Python
        ("pyreadline3",           "pyreadline3"),  # optional, Windows only
    ],
    "youtube_upload.py": [
        ("google.oauth2",         "google-auth"),
        ("google_auth_oauthlib",  "google-auth-oauthlib"),
        ("google.auth.transport", "google-auth-httplib2"),
        ("googleapiclient",       "google-api-python-client"),
    ],
    "cot_analytics.py": [
        ("googleapiclient",       "google-api-python-client"),
        ("google.oauth2",         "google-auth"),
    ],
}

# ── Default config template ───────────────────────────────────
DEFAULTS = {
    # Paths. Keep these configurable; never bake a user's personal folders into code.
    "PICTURES_DIR":       default_pictures_dir(),
    "OUTPUT_DIR":         "",   # derived from PICTURES_DIR if blank
    "SCRIPTS_DIR":        SCRIPTS_DIR,
    "AUDIO_DIR":          default_audio_dir(),
    "AUDIO_PREP_SUITE_PATH": "",
    "FFMPEG":             find_ffmpeg(""),
    "FFPROBE":            find_ffprobe(""),
    "CLIENT_SECRETS":     os.path.join(SCRIPTS_DIR, "client_secrets.json"),
    "TOKEN_FILE":         os.path.join(SCRIPTS_DIR, "token.json"),
    "CSV_PATH":           "",   # derived from OUTPUT_DIR
    "UPLOAD_LOG":         "",   # derived from OUTPUT_DIR
    "SEEDS_FILE":         "",   # derived from OUTPUT_DIR

    # Workflow
    "WORKFLOW_MODE":      "single_or_batch",
    "LAST_PROJECT_ROOT":  "",
    "LAST_PROJECT_FOLDER": "",

    # LLM
    "LLM_MODE":           "manual_only",   # local_llm | manual_only
    "LOCAL_LLM_PROVIDER": "",
    "LOCAL_LLM_BASE_URL": "",
    "MODEL_NAME":         "",

    # YouTube channel defaults
    "YT_CHANNEL_ID":      "",
    "YT_CATEGORY":        "19",
    "YT_COMMENTS":        "allow",
    "YT_KIDS":            False,
    "YT_LICENSE":         "youtube",
    "YT_PUBLISH":         "immediate",
    "YT_EMBEDDABLE":      True,
    "YT_PUBLIC_STATS":    True,
    "YT_PAID_PROMO":      False,
    "YT_LANGUAGE":        "en",
    "YT_AUDIO_LANGUAGE":  "en",

    # Pipeline
    "CHANNEL_NAME":       "",
    "FIXED_TAGS":         ["travel", "video"],

    # LLM prompt defaults
    "LLM_VOICE_STYLE":     "",
    "LLM_EXAMPLES_BLOCK":  "",

    "MAKE_SHOW_FINAL_HOLD_SEC":  2.0,
    "MAKE_SHOW_FINAL_FADE_SEC":  2.0,
    "MAKE_SHOW_AUDIO_FADE_SEC":  2.0,
}


# ------------------------------------------------------------
# LOAD / SAVE
# ------------------------------------------------------------

def load(gui_mode=False):
    """
    Load config from cot_config.json.
    If not found and gui_mode=False, launch first-run wizard automatically.
    If gui_mode=True, skip wizard (GUI tools handle config via Settings).
    """
    global _config
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            # Merge with current defaults so new settings appear for older installs.
            _config = dict(DEFAULTS)
            _config.update(loaded if isinstance(loaded, dict) else {})
            if isinstance(loaded, dict):
                if not _config.get("LOCAL_LLM_BASE_URL") and loaded.get("LMSTUDIO_URL"):
                    _config["LOCAL_LLM_BASE_URL"] = loaded.get("LMSTUDIO_URL")
                if _config.get("LLM_MODE") == "lmstudio_local":
                    _config["LLM_MODE"] = "local_llm"
            _derive_paths()
            return True
        except Exception as e:
            try:
                ts = time.strftime("%Y%m%d_%H%M%S")
                corrupt_path = os.path.join(SCRIPTS_DIR, f"cot_config.corrupt.{ts}.json")
                try:
                    os.replace(CONFIG_PATH, corrupt_path)
                except Exception:
                    # If replace fails (e.g., permission), keep original and fall back.
                    pass
            except Exception:
                pass
            if not gui_mode:
                print(f"\n  WARNING: Could not read cot_config.json: {e}")
                print("  Launching setup wizard...\n")

    if gui_mode:
        # GUI mode: load defaults silently, do NOT run wizard
        _config = dict(DEFAULTS)
        _derive_paths()
        return True

    # CLI mode: run wizard
    run_wizard()
    return True


def save():
    """Save current config to cot_config.json."""
    try:
        tmp_path = CONFIG_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(_config, f, indent=2)
        os.replace(tmp_path, CONFIG_PATH)
    except Exception as e:
        print(f"\n  ERROR saving config: {e}")


def get(key, default=None):
    """Get a config value. Falls back to DEFAULTS then to default param."""
    return _config.get(key, DEFAULTS.get(key, default))


def set(key: str, value: Any, *, save_now: bool = False) -> None:
    """Set a config value in memory. Optionally save to disk immediately."""
    _config[key] = value
    _derive_paths()
    if save_now:
        save()


def _derive_paths():
    """Compute paths that depend on user-configurable folders."""
    # The toolkit source folder is always the folder containing cot_config.py.
    # This prevents copied configs from pinning the app to an old machine path.
    _config["SCRIPTS_DIR"] = SCRIPTS_DIR

    pics = (_config.get("PICTURES_DIR") or "").strip()
    if not pics:
        pics = default_pictures_dir()
        _config["PICTURES_DIR"] = pics

    out = (_config.get("OUTPUT_DIR") or "").strip()
    if not out:
        out = default_output_dir(pics)
        _config["OUTPUT_DIR"] = out

    if not (_config.get("AUDIO_DIR") or "").strip():
        _config["AUDIO_DIR"] = default_audio_dir()

    _config["FFMPEG"] = find_ffmpeg(str(_config.get("FFMPEG", "") or ""))
    _config["FFPROBE"] = find_ffprobe(str(_config.get("FFPROBE", "") or ""))

    audio_path = (_config.get("AUDIO_PREP_SUITE_PATH") or "").strip()
    if audio_path and not looks_like_audio_prep_suite(audio_path):
        _config["AUDIO_PREP_SUITE_PATH"] = ""
        audio_path = ""
    if not audio_path:
        found = discover_audio_prep_suite(SCRIPTS_DIR)
        if found:
            _config["AUDIO_PREP_SUITE_PATH"] = found

    # Derived files should follow OUTPUT_DIR unless the user deliberately changed
    # the filename to something custom. This prevents stale old-machine output paths.
    def _derive_file(key: str, filename: str) -> None:
        existing = str(_config.get(key, "") or "")
        if (not existing) or os.path.basename(existing).lower() == filename.lower():
            _config[key] = os.path.join(out, filename)

    if out:
        _derive_file("CSV_PATH", "youtube_uploads.csv")
        _derive_file("UPLOAD_LOG", "upload_log.json")
        _derive_file("SEEDS_FILE", "seeds.json")


# ------------------------------------------------------------
# FIRST-RUN WIZARD
# ------------------------------------------------------------

def run_wizard():
    """
    Interactive setup wizard.
    Walks user through all required settings.
    Called automatically on first run, or from ADMIN menu.
    """
    global _config

    print("╔══════════════════════════════════════════════════════╗")
    print("║       YouTube Video Toolkit — First Run Setup        ║")
    print("╚══════════════════════════════════════════════════════╝")
    print()
    print("  This wizard configures your pipeline.")
    print("  Press Enter to keep the current/default value.\n")

    # Start from existing config or defaults
    cfg = dict(DEFAULTS)
    cfg.update(_config)

    # ── Paths ─────────────────────────────────────────────────
    print("  ── PATHS ──────────────────────────────────────────")

    val = input(f"  Pictures folder [{cfg['PICTURES_DIR'] or 'e.g. C:\\Users\\You\\Pictures'}]: ").strip()
    if val: cfg["PICTURES_DIR"] = val

    default_output = cfg["OUTPUT_DIR"] or (
        os.path.join(cfg["PICTURES_DIR"], "YouTubeVideos") if cfg["PICTURES_DIR"] else ""
    )
    val = input(f"  Output/movies folder [{default_output}]: ").strip()
    cfg["OUTPUT_DIR"] = val if val else default_output

    val = input(f"  Audio folder [{cfg.get('AUDIO_DIR') or default_audio_dir()}]: ").strip()
    if val: cfg["AUDIO_DIR"] = val

    val = input(f"  FFmpeg executable [{cfg.get('FFMPEG') or 'ffmpeg'}]: ").strip()
    if val: cfg["FFMPEG"] = val

    val = input(f"  FFprobe executable [{cfg.get('FFPROBE') or 'ffprobe'}]: ").strip()
    if val: cfg["FFPROBE"] = val

    found_audio_suite = cfg.get('AUDIO_PREP_SUITE_PATH') or discover_audio_prep_suite(cfg.get('SCRIPTS_DIR') or SCRIPTS_DIR)
    val = input(f"  Audio Prep Suite folder (optional) [{found_audio_suite}]: ").strip()
    cfg["AUDIO_PREP_SUITE_PATH"] = val if val else found_audio_suite

    val = input(f"  Scripts folder [{cfg['SCRIPTS_DIR']}]: ").strip()
    if val: cfg["SCRIPTS_DIR"] = val

    # Derive CSV/log paths
    cfg["CSV_PATH"]   = os.path.join(cfg["OUTPUT_DIR"], "youtube_uploads.csv")
    cfg["UPLOAD_LOG"] = os.path.join(cfg["OUTPUT_DIR"], "upload_log.json")
    cfg["SEEDS_FILE"] = os.path.join(cfg["OUTPUT_DIR"], "seeds.json")

    # ── Google Auth ───────────────────────────────────────────
    print("\n  ── GOOGLE AUTH ─────────────────────────────────────")
    default_secrets = os.path.join(cfg["SCRIPTS_DIR"], "client_secrets.json")
    val = input(f"  client_secrets.json path [{default_secrets}]: ").strip()
    cfg["CLIENT_SECRETS"] = val if val else default_secrets

    default_token = os.path.join(cfg["SCRIPTS_DIR"], "token.json")
    val = input(f"  token.json path [{default_token}]: ").strip()
    cfg["TOKEN_FILE"] = val if val else default_token

    # ── LLM Mode ─────────────────────────────────────────────
    print("\n  ── LLM MODE ────────────────────────────────────────")
    print("  Options:")
    print("    local_llm — use a local LLM endpoint on this machine")
    print("    manual_only    — no LLM, edit metadata manually")
    val = input(f"  LLM mode [{cfg['LLM_MODE']}]: ").strip().lower()
    if val in ("local_llm", "manual_only"):
        cfg["LLM_MODE"] = val

    if cfg["LLM_MODE"] == "local_llm":
        val = input(f"  Local LLM endpoint [{cfg.get('LOCAL_LLM_BASE_URL') or 'auto-detect'}]: ").strip()
        if val:
            cfg["LOCAL_LLM_BASE_URL"] = chat_url_from_base(val)
        else:
            found = discover_local_llm(cfg.get("LOCAL_LLM_BASE_URL", ""))
            if found.ok:
                cfg["LOCAL_LLM_PROVIDER"] = found.provider
                cfg["LOCAL_LLM_BASE_URL"] = found.chat_url
                print(f"  Found {found.provider}: {found.chat_url}")

        models = []
        if cfg.get("LOCAL_LLM_BASE_URL"):
            try:
                models = get_local_models(cfg["LOCAL_LLM_BASE_URL"], timeout=4)
            except Exception:
                models = []
        if models:
            print("\n  Available models:")
            for i, model_id in enumerate(models, 1):
                print(f"    {i}. {model_id}")
        val = input(f"  Model name [{cfg['MODEL_NAME'] or (models[0] if models else 'optional')}]: ").strip()
        if val:
            cfg["MODEL_NAME"] = val
        elif models and not cfg.get("MODEL_NAME"):
            cfg["MODEL_NAME"] = models[0]

    # ── YouTube Defaults ──────────────────────────────────────
    print("\n  ── YOUTUBE CHANNEL DEFAULTS ────────────────────────")
    print("  These apply to every video — change per-video in UC6 if needed.\n")

    val = input(f"  Channel ID override (optional) [{cfg.get('YT_CHANNEL_ID','')}]: ").strip()
    if val: cfg["YT_CHANNEL_ID"] = val

    val = input(f"  Channel name [{cfg['CHANNEL_NAME']}]: ").strip()
    if val: cfg["CHANNEL_NAME"] = val

    val = input(f"  Category ID [{cfg['YT_CATEGORY']}] (19=Travel, 22=People, 24=Entertainment): ").strip()
    if val.isdigit(): cfg["YT_CATEGORY"] = val

    val = input(f"  Default language [{cfg['YT_LANGUAGE']}]: ").strip().lower()
    if val: cfg["YT_LANGUAGE"] = val

    val = input(f"  Made for kids [{('yes' if cfg['YT_KIDS'] else 'no')}] (yes/no): ").strip().lower()
    if val in ("yes", "y"): cfg["YT_KIDS"] = True
    elif val in ("no", "n"): cfg["YT_KIDS"] = False

    val = input(f"  Contains paid promotion [{('yes' if cfg['YT_PAID_PROMO'] else 'no')}] (yes/no): ").strip().lower()
    if val in ("yes", "y"): cfg["YT_PAID_PROMO"] = True
    elif val in ("no", "n"): cfg["YT_PAID_PROMO"] = False

    tags_str = ", ".join(cfg["FIXED_TAGS"]) if isinstance(cfg["FIXED_TAGS"], list) else cfg["FIXED_TAGS"]
    val = input(f"  Fixed tags always added [{tags_str}]: ").strip()
    if val:
        cfg["FIXED_TAGS"] = [t.strip() for t in val.split(",") if t.strip()]

    # ── LLM prompt customization ─────────────────────────────
    print("\n  ── LLM PROMPT CUSTOMIZATION ────────────────────────")
    print("  Optional: paste a custom voice/style block and examples.")
    print("  Leave blank to use the built-in defaults.\n")

    val = input("  Custom voice/style block (single line or short) [blank=default]: ").strip()
    if val:
        cfg["LLM_VOICE_STYLE"] = val

    val = input("  Custom examples block (single line or short) [blank=default]: ").strip()
    if val:
        cfg["LLM_EXAMPLES_BLOCK"] = val

    # ── Save ──────────────────────────────────────────────────
    _config = cfg
    _derive_paths()
    save()

    print("\n  ✓ Config saved to cot_config.json")
    print("  Add cot_config.json to .gitignore before pushing to GitHub.\n")


# ------------------------------------------------------------
# ADMIN MODE
# ------------------------------------------------------------

def run_admin():
    """
    ADMIN menu — setup, checks, dependency scanner.
    Called from cot_pipeline.py ADMIN option.
    """
    while True:
        print("\n╔══════════════════════════════════════════════════════╗")
        print("║              YouTube Video Toolkit — ADMIN           ║")
        print("╚══════════════════════════════════════════════════════╝")
        print()
        print("  1. Edit configuration (re-run setup wizard)")
        print("  2. Check dependencies")
        print("  3. Check Google auth files")
        print("  4. LLM Check")
        print("  5. Show current config")
        print("  Q. Back to pipeline")
        print()

        choice = input("  Choice: ").strip().upper()

        if choice == "Q":
            break
        elif choice == "1":
            run_wizard()
        elif choice == "2":
            check_dependencies()
        elif choice == "3":
            check_auth()
        elif choice == "4":
            check_llm()
        elif choice == "5":
            show_config()
        else:
            print("  Invalid choice.")


# ------------------------------------------------------------
# DEPENDENCY CHECKER
# ------------------------------------------------------------

def check_dependencies(script=None):
    """
    Check required Python packages.
    If script is given, check only that script's deps.
    Otherwise check all scripts.
    Prints exact pip install commands for anything missing.
    """
    print("\n  ── DEPENDENCY CHECK ────────────────────────────────")

    scripts = [script] if script else list(DEPENDENCIES.keys())
    all_ok  = True
    to_install = []

    for sc in scripts:
        deps = DEPENDENCIES.get(sc, [])
        if not deps:
            continue
        print(f"\n  {sc}:")
        for import_name, pip_name in deps:
            if import_name == "tkinter":
                # tkinter is bundled — check differently
                try:
                    import tkinter
                    print(f"    ✓ tkinter")
                except ImportError:
                    print(f"    ✗ tkinter — bundled with Python, reinstall Python with tcl/tk option")
                    all_ok = False
                continue

            try:
                __import__(import_name)
                print(f"    ✓ {import_name}")
            except ImportError:
                print(f"    ✗ {import_name}  ← MISSING")
                if pip_name:
                    to_install.append(pip_name)
                all_ok = False

    if to_install:
        print(f"\n  To install missing packages, run:")
        print(f"  pip install {' '.join(to_install)}")
        print()
    elif all_ok:
        print("\n  All dependencies satisfied.\n")

    return all_ok


# ------------------------------------------------------------
# AUTH CHECKER
# ------------------------------------------------------------

def check_auth():
    """
    Verify client_secrets.json and token.json exist and are readable.
    Gives specific instructions if either is missing.
    """
    print("\n  ── GOOGLE AUTH CHECK ───────────────────────────────")

    secrets = get("CLIENT_SECRETS")
    token   = get("TOKEN_FILE")

    # client_secrets.json
    if os.path.isfile(secrets):
        print(f"  ✓ client_secrets.json found: {secrets}")
        try:
            with open(secrets) as f:
                data = json.load(f)
            if "installed" in data or "web" in data:
                print(f"    Client type: {'Desktop/installed' if 'installed' in data else 'Web'}")
                client_id = data.get("installed", data.get("web", {})).get("client_id", "")
                print(f"    Client ID  : {client_id[:40]}...")
            else:
                print(f"    WARNING: Unexpected format in client_secrets.json")
        except Exception as e:
            print(f"    WARNING: Could not parse client_secrets.json: {e}")
    else:
        print(f"  ✗ client_secrets.json NOT FOUND")
        print(f"    Expected at: {secrets}")
        print(f"    To fix:")
        print(f"      1. Go to console.cloud.google.com")
        print(f"      2. APIs & Services → Credentials")
        print(f"      3. Download OAuth 2.0 Client ID (Desktop type)")
        print(f"      4. Rename to client_secrets.json")
        print(f"      5. Place in: {get('SCRIPTS_DIR')}")

    print()

    # token.json
    if os.path.isfile(token):
        print(f"  ✓ token.json found: {token}")
        try:
            with open(token) as f:
                data = json.load(f)
            expiry = data.get("expiry", "unknown")
            print(f"    Expiry: {expiry}")
            print(f"    Scopes: {len(data.get('scopes', []))} scope(s) granted")
        except Exception as e:
            print(f"    WARNING: Could not parse token.json: {e}")
    else:
        print(f"  ✗ token.json NOT FOUND")
        print(f"    This is created automatically on first successful login.")
        print(f"    Run any YouTube API function to trigger the login browser flow.")

    print()


# ------------------------------------------------------------
# LOCAL LLM CHECKER
# ------------------------------------------------------------

def check_llm():
    """Provider-neutral local LLM check for llama-server, LM Studio, Ollama, or custom endpoint."""
    print("\n  ── LOCAL LLM CHECK ─────────────────────────────────")

    if get("LLM_MODE") != "local_llm":
        print("  LLM_MODE is manual_only — AI metadata is optional and not required.")
        return True

    endpoint = str(get("LOCAL_LLM_BASE_URL", "") or get("LMSTUDIO_URL", "") or "").strip()
    status = discover_local_llm(endpoint)
    if not status.ok:
        print("  ✗ No running Local LLM endpoint found.")
        if status.error:
            print(f"    Last error: {status.error}")
        print("  Start llama-server, LM Studio, or Ollama, then run this check again.")
        print("  Common endpoints: http://127.0.0.1:8080, :1234, :11434")
        return False

    print(f"  ✓ {status.provider} reachable at {status.base_url}")
    print(f"  Chat endpoint: {status.chat_url}")
    print(f"  Models available ({len(status.models)}):")
    current = str(get("MODEL_NAME", "") or "").strip()
    for i, m in enumerate(status.models, 1):
        marker = "  ← configured" if m == current else ""
        print(f"    {i}. {m}{marker}")
    if current and current not in status.models:
        print(f"\n  WARNING: Configured model '{current}' is not listed by the endpoint.")
    elif not current and status.models:
        print("\n  TIP: No model configured. Choose one in Settings or setup wizard.")
    set("LOCAL_LLM_PROVIDER", status.provider, save_now=False)
    set("LOCAL_LLM_BASE_URL", status.chat_url, save_now=False)
    set("LMSTUDIO_URL", status.chat_url, save_now=False)  # legacy compatibility only
    return True

def check_preflight():
    print("\n  ── PREFLIGHT CHECK ─────────────────────────────────")
    try:
        from cot_core.preflight import run_preflight, summarize
    except Exception as e:
        print(f"  ERROR: Could not import preflight module: {e}")
        return False

    pictures_dir = (get("PICTURES_DIR", "") or "").strip() or None
    output_dir = (get("OUTPUT_DIR", "") or "").strip() or None
    client_secrets = (get("CLIENT_SECRETS", "") or "").strip() or None
    token_file = (get("TOKEN_FILE", "") or "").strip() or None

    ffmpeg = str(get("FFMPEG", "ffmpeg") or "ffmpeg")
    ffprobe = str(get("FFPROBE", "ffprobe") or "ffprobe")

    llm_mode = str(get("LLM_MODE", "") or "")
    lmstudio_url = str(get("LOCAL_LLM_BASE_URL", "") or get("LMSTUDIO_URL", "") or "")
    model_name = str(get("MODEL_NAME", "") or "")

    results = run_preflight(
        pictures_dir=pictures_dir,
        output_dir=output_dir,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        min_free_gb=2.0,
        min_free_ram_gb=2.0,
        client_secrets=client_secrets,
        token_file=token_file,
        llm_mode=llm_mode,
        lmstudio_url=lmstudio_url,
        model_name=model_name,
    )

    ok_all, text = summarize(results)
    if text.strip():
        print(text)
    print("  Preflight:", "OK" if ok_all else "FAIL")
    return bool(ok_all)


# ------------------------------------------------------------
# SHOW CONFIG
# ------------------------------------------------------------

def show_config():
    """Print current config values, masking sensitive fields."""
    print("\n  ── CURRENT CONFIGURATION ───────────────────────────")
    sensitive = {"CLIENT_SECRETS", "TOKEN_FILE"}
    for key, val in sorted(_config.items()):
        if key in sensitive:
            display = str(val)[:30] + "..." if len(str(val)) > 30 else str(val)
        else:
            display = val
        print(f"  {key:<22} : {display}")
    print(f"\n  Config file: {CONFIG_PATH}\n")


# ------------------------------------------------------------
# CONVENIENCE — called by other scripts
# ------------------------------------------------------------

def require_llm():
    """
    Return True if LLM is configured and available.
    Used by youtube_meta.py to decide whether to show LLM options.
    """
    return get("LLM_MODE") == "local_llm"


def get_fixed_tags():
    """Return FIXED_TAGS as a list."""
    tags = get("FIXED_TAGS", ["travel", "video"])
    if isinstance(tags, str):
        return [t.strip() for t in tags.split(",") if t.strip()]
    return tags



def show_diagnostics():
    """Print a compact diagnostics report that can be copied into a bug report."""
    print("\n  ── TOOLKIT DIAGNOSTICS ─────────────────────────────")
    print(f"  Python        : {sys.executable}")
    print(f"  Toolkit folder: {SCRIPTS_DIR}")
    print(f"  Config file   : {CONFIG_PATH}")
    print(f"  Pictures      : {get('PICTURES_DIR', '')}")
    print(f"  Output        : {get('OUTPUT_DIR', '')}")
    print(f"  FFmpeg        : {get('FFMPEG', '')}")
    print(f"  FFprobe       : {get('FFPROBE', '')}")
    audio = get('AUDIO_PREP_SUITE_PATH', '')
    status = audio_prep_status(audio) if audio else {'found': False, 'runnable': False, 'launcher': ''}
    print(f"  Audio Prep    : {audio or '(not configured)'}")
    print(f"  Audio runnable: {status.get('runnable')} {status.get('launcher', '')}")
    print(f"  LLM mode      : {get('LLM_MODE', '')}")
    print(f"  LLM provider  : {get('LOCAL_LLM_PROVIDER', '')}")
    print(f"  LLM endpoint  : {get('LOCAL_LLM_BASE_URL', '') or get('LMSTUDIO_URL', '')}")
    print(f"  LLM model     : {get('MODEL_NAME', '')}")
    print(f"  YouTube secret: {'set' if get('CLIENT_SECRETS', '') else 'missing'}")
    print(f"  YouTube token : {'set' if get('TOKEN_FILE', '') else 'missing'}")
    print()

# ------------------------------------------------------------
# STANDALONE — run directly for admin/setup
# ------------------------------------------------------------

if __name__ == "__main__":
    load()
    run_admin()
