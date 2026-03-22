# ------------------------------------------------------------
# cot_config.py
# Version: 1.0.0
#
# Purpose:
#   Shared configuration for the CatsofTravels pipeline.
#   All scripts import this module to get their settings.
#   Settings are stored in cot_config.json (gitignored).
#
# Features:
#   - First-run wizard: auto-launches if no config found
#   - ADMIN mode: re-run wizard to edit any setting
#   - Dependency checker: scans required packages per script
#   - Auth checker: verifies client_secrets.json / token.json
#   - LM Studio checker: optional, only if LLM_MODE=lmstudio_local
#   - LLM_MODE: lmstudio_local | manual_only
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

# ── Location of config file ───────────────────────────────────
SCRIPTS_DIR  = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH  = os.path.join(SCRIPTS_DIR, "cot_config.json")
VERSION      = "1.0.0"

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
    # Paths
    "PICTURES_DIR":       "",
    "OUTPUT_DIR":         "",
    "SCRIPTS_DIR":        SCRIPTS_DIR,
    "CLIENT_SECRETS":     os.path.join(SCRIPTS_DIR, "client_secrets.json"),
    "TOKEN_FILE":         os.path.join(SCRIPTS_DIR, "token.json"),
    "CSV_PATH":           "",   # derived from OUTPUT_DIR
    "UPLOAD_LOG":         "",   # derived from OUTPUT_DIR
    "SEEDS_FILE":         "",   # derived from OUTPUT_DIR

    # LLM
    "LLM_MODE":           "lmstudio_local",   # lmstudio_local | manual_only
    "LMSTUDIO_URL":       "http://127.0.0.1:1234/v1/chat/completions",
    "MODEL_NAME":         "",

    # YouTube channel defaults
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
    "CHANNEL_NAME":       "CatsofTravels",
    "FIXED_TAGS":         ["CatsofTravels", "travel", "travelvlog"],
}


# ------------------------------------------------------------
# LOAD / SAVE
# ------------------------------------------------------------

def load():
    """
    Load config from cot_config.json.
    If not found, launch first-run wizard automatically.
    Derives computed paths after loading.
    """
    global _config
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                _config = json.load(f)
            _derive_paths()
            return True
        except Exception as e:
            print(f"\n  WARNING: Could not read cot_config.json: {e}")
            print("  Launching setup wizard...\n")

    # No config found — run wizard
    run_wizard()
    return True


def save():
    """Save current config to cot_config.json."""
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(_config, f, indent=2)
    except Exception as e:
        print(f"\n  ERROR saving config: {e}")


def get(key, default=None):
    """Get a config value. Falls back to DEFAULTS then to default param."""
    return _config.get(key, DEFAULTS.get(key, default))


def _derive_paths():
    """Compute paths that depend on OUTPUT_DIR."""
    out = _config.get("OUTPUT_DIR", "")
    if out:
        if not _config.get("CSV_PATH"):
            _config["CSV_PATH"]   = os.path.join(out, "youtube_uploads.csv")
        if not _config.get("UPLOAD_LOG"):
            _config["UPLOAD_LOG"] = os.path.join(out, "upload_log.json")
        if not _config.get("SEEDS_FILE"):
            _config["SEEDS_FILE"] = os.path.join(out, "seeds.json")


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
    print("║       CatsofTravels — First Run Setup Wizard         ║")
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
        os.path.join(cfg["PICTURES_DIR"], "COTMovies") if cfg["PICTURES_DIR"] else ""
    )
    val = input(f"  Output/movies folder [{default_output}]: ").strip()
    cfg["OUTPUT_DIR"] = val if val else default_output

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
    print("    lmstudio_local — use LM Studio running on this machine")
    print("    manual_only    — no LLM, edit metadata manually")
    val = input(f"  LLM mode [{cfg['LLM_MODE']}]: ").strip().lower()
    if val in ("lmstudio_local", "manual_only"):
        cfg["LLM_MODE"] = val

    if cfg["LLM_MODE"] == "lmstudio_local":
        val = input(f"  LM Studio URL [{cfg['LMSTUDIO_URL']}]: ").strip()
        if val: cfg["LMSTUDIO_URL"] = val

        print("\n  Model name — tip: run check_lmstudio() or visit")
        print(f"  {cfg['LMSTUDIO_URL'].replace('/v1/chat/completions', '/v1/models')}")
        val = input(f"  Model name [{cfg['MODEL_NAME'] or 'e.g. meta-llama-3.1-8b-instruct'}]: ").strip()
        if val: cfg["MODEL_NAME"] = val

    # ── YouTube Defaults ──────────────────────────────────────
    print("\n  ── YOUTUBE CHANNEL DEFAULTS ────────────────────────")
    print("  These apply to every video — change per-video in UC6 if needed.\n")

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
        print("║              CatsofTravels — ADMIN                   ║")
        print("╚══════════════════════════════════════════════════════╝")
        print()
        print("  1. Edit configuration (re-run setup wizard)")
        print("  2. Check dependencies")
        print("  3. Check Google auth files")
        print("  4. Check LM Studio connection")
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
            check_lmstudio()
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
# LM STUDIO CHECKER
# ------------------------------------------------------------

def check_lmstudio():
    """
    Check LM Studio server and list available models.
    Only relevant if LLM_MODE == lmstudio_local.
    """
    print("\n  ── LM STUDIO CHECK ─────────────────────────────────")

    if get("LLM_MODE") != "lmstudio_local":
        print("  LLM_MODE is set to manual_only — LM Studio not required.")
        return True

    try:
        import requests
    except ImportError:
        print("  ERROR: requests library not installed.")
        print("  Run: pip install requests")
        return False

    url      = get("LMSTUDIO_URL", "http://127.0.0.1:1234/v1/chat/completions")
    base_url = url.replace("/v1/chat/completions", "")

    try:
        r = requests.get(f"{base_url}/v1/models", timeout=5)
        if r.status_code == 200:
            models = [m["id"] for m in r.json().get("data", [])]
            print(f"  ✓ LM Studio server reachable at {base_url}")
            print(f"  Models loaded ({len(models)}):")
            current = get("MODEL_NAME", "")
            for i, m in enumerate(models, 1):
                marker = "  ← configured" if m == current else ""
                print(f"    {i}. {m}{marker}")
            if current and current not in models:
                print(f"\n  WARNING: Configured model '{current}' is not loaded.")
                print(f"  Update MODEL_NAME in config or load the model in LM Studio.")
            elif not current:
                print(f"\n  TIP: No model configured. Run ADMIN → Edit configuration to set MODEL_NAME.")
            return True
        else:
            print(f"  ✗ LM Studio returned status {r.status_code}")
            return False
    except Exception:
        print(f"  ✗ Cannot reach LM Studio at {base_url}")
        print(f"  Please check:")
        print(f"    1. LM Studio is OPEN")
        print(f"    2. Local Server tab is active (the <-> icon)")
        print(f"    3. Server is STARTED — green indicator, port 1234")
        print(f"    4. URL in config matches: {url}")
        return False


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
    return get("LLM_MODE") == "lmstudio_local"


def get_fixed_tags():
    """Return FIXED_TAGS as a list."""
    tags = get("FIXED_TAGS", ["CatsofTravels", "travel", "travelvlog"])
    if isinstance(tags, str):
        return [t.strip() for t in tags.split(",") if t.strip()]
    return tags


# ------------------------------------------------------------
# STANDALONE — run directly for admin/setup
# ------------------------------------------------------------

if __name__ == "__main__":
    load()
    run_admin()
