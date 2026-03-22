# ------------------------------------------------------------
# cot_pipeline.py
# Version: 2.0.0
#
# Changes from 1.2.0:
#   - All config now read from cot_config.py / cot_config.json
#   - ADMIN menu added (A) — setup wizard, dep check, auth check
#   - Dashboard shows YT privacy status (public/private/unlisted)
#   - Quota tracker display in UC4
#   - First-run wizard auto-launches if no config found
#   - Module load errors show specific missing package names
#
# Purpose:
#   Unified launcher for the CatsofTravels video pipeline.
#   Chains make_show.py, youtube_meta.py, youtube_upload.py,
#   and cot_analytics.py into a single menu-driven workflow.
#
# Pipeline stages:
#   UC1 — Draft Videos    : render silent MP4s (make_show.py)
#   UC2 — Add Music       : add audio to approved videos (make_show.py Mode D)
#   UC3 — Metadata        : generate YouTube metadata (youtube_meta.py)
#   UC4 — Upload          : upload to YouTube (youtube_upload.py)
#   UC5 — Analytics       : pull YouTube Analytics (cot_analytics.py)
#   UC6 — View & Edit     : view/edit live YouTube metadata (youtube_meta.py)
#
# Usage:
#   python cot_pipeline.py
#
# Requirements:
#   cot_config.py, make_show.py, youtube_meta.py,
#   youtube_upload.py, cot_analytics.py — all in same folder.
# ------------------------------------------------------------

import os
import sys
import csv
import json
from datetime import datetime

# ── Add scripts directory to path ────────────────────────────
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

VERSION = "2.0.0"

# ── Load config first ─────────────────────────────────────────
try:
    import cot_config as cfg
    cfg.load()
except ImportError:
    print("\n  ERROR: cot_config.py not found.")
    print(f"  Expected in: {SCRIPTS_DIR}")
    print("  Make sure cot_config.py is in the same folder as cot_pipeline.py")
    sys.exit(1)

# ── Import pipeline modules ───────────────────────────────────
# Wrapped so pipeline still launches even if a module has
# a missing dependency — errors only at runtime when invoked.

try:
    import make_show
    HAS_MAKE_SHOW = True
    MAKE_SHOW_ERR = ""
except ImportError as e:
    HAS_MAKE_SHOW = False
    MAKE_SHOW_ERR = str(e)

try:
    import youtube_meta
    HAS_META = True
    META_ERR = ""
except ImportError as e:
    HAS_META = False
    META_ERR = str(e)

try:
    import youtube_upload
    HAS_UPLOAD = True
    UPLOAD_ERR = ""
except ImportError as e:
    HAS_UPLOAD = False
    UPLOAD_ERR = str(e)

try:
    import cot_analytics
    HAS_ANALYTICS = True
    ANALYTICS_ERR = ""
except ImportError as e:
    HAS_ANALYTICS = False
    ANALYTICS_ERR = str(e)


# ------------------------------------------------------------
# CONFIG ACCESSORS — read from cot_config at runtime
# ------------------------------------------------------------

def PICTURES_DIR():  return cfg.get("PICTURES_DIR", "")
def OUTPUT_DIR():    return cfg.get("OUTPUT_DIR", "")
def CSV_PATH():      return cfg.get("CSV_PATH", "")
def UPLOAD_LOG():    return cfg.get("UPLOAD_LOG", "")
EXCLUDE_NAME = "exclude"


# ------------------------------------------------------------
# FOLDER STATE DETECTION
# ------------------------------------------------------------

def has_exclude_images(folder_path):
    """Check if exclude subfolder contains images — needs re-render."""
    exclude_path = os.path.join(folder_path, EXCLUDE_NAME)
    if not os.path.isdir(exclude_path):
        return False
    image_exts = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp"}
    for f in os.listdir(exclude_path):
        if os.path.splitext(f)[1].lower() in image_exts:
            return True
    return False


def get_mp4_state(folder_name):
    """Returns (silent_exists, music_exists)."""
    out    = OUTPUT_DIR()
    silent = os.path.isfile(os.path.join(out, folder_name + ".mp4"))
    music  = os.path.isfile(os.path.join(out, folder_name + "_music.mp4"))
    return silent, music


def get_metadata_state(folder_name, csv_rows):
    """Check if folder_name has a row in the metadata CSV."""
    return any(r.get("folder_name", "").strip() == folder_name for r in csv_rows)


def get_upload_state(folder_name, upload_log):
    """Check if folder_name has been uploaded to YouTube."""
    return folder_name in upload_log


def get_yt_privacy(folder_name, upload_log):
    """Return privacy status for uploaded video, or '' if not uploaded."""
    entry = upload_log.get(folder_name)
    if not entry:
        return ""
    if isinstance(entry, dict):
        return entry.get("privacy", "uploaded")
    return "uploaded"


def load_csv_rows():
    """Load all rows from youtube_uploads.csv."""
    path = CSV_PATH()
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def load_upload_log():
    """Load upload_log.json."""
    path = UPLOAD_LOG()
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def get_all_folders(root):
    """
    Return sorted list of (folder_name, folder_path) tuples.
    Year folders first (sorted by year), then alphabetical.
    """
    import re
    EXCLUDE_DIRS = {"exclude", "_temp_frames", "cotmovies"}
    try:
        items = os.listdir(root)
    except Exception:
        return []

    folders = [
        f for f in items
        if os.path.isdir(os.path.join(root, f))
        and f.lower() not in EXCLUDE_DIRS
    ]

    def is_year(name):
        parts = name.split()
        return bool(parts and re.match(r"^\d{4}$", parts[0]))

    year_folders = sorted(
        [f for f in folders if is_year(f)],
        key=lambda x: int(x.split()[0])
    )
    non_year = sorted([f for f in folders if not is_year(f)])

    return [(name, os.path.join(root, name)) for name in year_folders + non_year]


# ------------------------------------------------------------
# STATUS DASHBOARD
# ------------------------------------------------------------

def show_dashboard(root):
    """
    Print status table showing pipeline state of every folder.

    Columns:
      FOLDER   — folder name (truncated)
      VIDEO    — silent MP4 status
      MUSIC    — music MP4 status
      EXCL     — exclude images present (needs re-render)
      META     — CSV row exists
      YT       — uploaded + privacy status (pub/priv/unlist)
    """
    folders    = get_all_folders(root)
    csv_rows   = load_csv_rows()
    upload_log = load_upload_log()

    if not folders:
        print("\n  No folders found.")
        return

    print(f"\n  {'FOLDER':<35} {'VIDEO':<8} {'MUSIC':<8} {'EXCL':<6} {'META':<6} {'YT'}")
    print(f"  {'─'*35} {'─'*8} {'─'*8} {'─'*6} {'─'*6} {'─'*8}")

    pub_count   = 0
    priv_count  = 0
    unlist_count = 0

    for folder_name, folder_path in folders:
        silent, music = get_mp4_state(folder_name)
        excl          = has_exclude_images(folder_path)
        meta          = get_metadata_state(folder_name, csv_rows)
        uploaded      = get_upload_state(folder_name, upload_log)
        privacy       = get_yt_privacy(folder_name, upload_log)

        video_sym = "done" if silent and not excl else ("REDO" if excl else "----")
        music_sym = "done" if music  else "----"
        excl_sym  = "YES!" if excl   else "  - "
        meta_sym  = "done" if meta   else "----"

        if uploaded:
            priv_short = {"public": "pub", "private": "priv", "unlisted": "unlist"}.get(privacy, "done")
            yt_sym = priv_short
            if privacy == "public":    pub_count += 1
            elif privacy == "private": priv_count += 1
            elif privacy == "unlisted": unlist_count += 1
        else:
            yt_sym = "----"

        name_trunc = folder_name[:34]
        print(f"  {name_trunc:<35} {video_sym:<8} {music_sym:<8} {excl_sym:<6} {meta_sym:<6} {yt_sym}")

    # Summary
    total      = len(folders)
    video_done = sum(1 for n, p in folders if get_mp4_state(n)[0] and not has_exclude_images(p))
    music_done = sum(1 for n, p in folders if get_mp4_state(n)[1])
    needs_redo = sum(1 for n, p in folders if has_exclude_images(p))
    meta_done  = sum(1 for n, p in folders if get_metadata_state(n, csv_rows))
    yt_done    = sum(1 for n, p in folders if get_upload_state(n, upload_log))

    print(f"\n  Total folders : {total}")
    print(f"  Video done    : {video_done}  |  Needs re-render : {needs_redo}")
    print(f"  Music done    : {music_done}")
    print(f"  Metadata done : {meta_done}")
    print(f"  Uploaded      : {yt_done}  (public: {pub_count}  private: {priv_count}  unlisted: {unlist_count})\n")


# ------------------------------------------------------------
# QUOTA DISPLAY
# ------------------------------------------------------------

def show_quota():
    """Show today's API quota usage if quota_log.json exists."""
    quota_log = os.path.join(OUTPUT_DIR(), "quota_log.json")
    if not os.path.isfile(quota_log):
        print("  No quota log found yet.")
        return
    try:
        with open(quota_log, "r", encoding="utf-8") as f:
            data = json.load(f)
        today = datetime.now().strftime("%Y-%m-%d")
        used  = data.get(today, {}).get("units_used", 0)
        limit = 10000
        print(f"\n  YouTube API quota today: {used} / {limit} units used")
        print(f"  Safe upload limit: ~{(limit - used) // 1650} more videos\n")
    except Exception as e:
        print(f"  Could not read quota log: {e}")


# ------------------------------------------------------------
# MODULE ERROR HELPER
# ------------------------------------------------------------

def _module_error(name, err):
    """Print a helpful error when a module fails to load."""
    print(f"\n  ERROR: {name} could not be loaded.")
    print(f"  Reason: {err}")
    if "No module named" in err:
        missing = err.split("No module named")[-1].strip().strip("'")
        print(f"  Fix: pip install {missing}")
    print()


# ------------------------------------------------------------
# STAGE RUNNERS
# ------------------------------------------------------------

def run_uc1_draft_videos():
    """UC1 — Draft Videos. Render silent MP4s via make_show.py."""
    if not HAS_MAKE_SHOW:
        _module_error("make_show.py", MAKE_SHOW_ERR)
        return

    print("\n  UC1 — DRAFT VIDEOS")
    print("  Launching make_show.py...")
    print("  Note: choose Mode A (normal) or B (batch silent) from the make_show menu.")
    input("\n  Press Enter to launch make_show.py...")

    try:
        make_show.main()
    except SystemExit:
        pass
    except Exception as e:
        print(f"\n  ERROR in make_show.py: {e}")
        return

    print("\n" + "─"*60)
    print("  Draft videos complete.")
    print("\n  What next?")
    print("  M. Generate metadata now")
    print("  Q. Return to pipeline menu")
    choice = input("\n  Choice: ").strip().upper()
    if choice == "M":
        run_uc3_metadata()


def run_uc2_add_music():
    """UC2 — Add Music. Launches make_show.py Mode D."""
    if not HAS_MAKE_SHOW:
        _module_error("make_show.py", MAKE_SHOW_ERR)
        return

    print("\n  UC2 — ADD MUSIC")
    print("  Launching make_show.py Mode D (add audio to existing movies)...")
    input("\n  Press Enter to launch...")

    try:
        audio_fade_sec = 2.0
        make_show.startup_cleanup()
        make_show.rotate_log()
        make_show.mode_add_audio_existing(audio_fade_sec)
    except Exception as e:
        print(f"\n  ERROR in make_show.py Mode D: {e}")


def run_uc3_metadata():
    """UC3 — Generate Metadata via youtube_meta.py."""
    if not HAS_META:
        _module_error("youtube_meta.py", META_ERR)
        return

    print("\n  UC3 — GENERATE METADATA")
    try:
        youtube_meta.main_metadata_menu()
    except Exception as e:
        print(f"\n  ERROR in youtube_meta.py: {e}")


def run_uc4_upload():
    """UC4 — Upload to YouTube via youtube_upload.py."""
    if not HAS_UPLOAD:
        _module_error("youtube_upload.py", UPLOAD_ERR)
        return

    print("\n  UC4 — UPLOAD TO YOUTUBE")
    show_quota()

    try:
        youtube_upload.main()
    except SystemExit:
        pass
    except Exception as e:
        print(f"\n  ERROR in youtube_upload.py: {e}")

    # Show updated quota after run
    show_quota()


def run_uc5_analytics():
    """UC5 — YouTube Analytics via cot_analytics.py."""
    if not HAS_ANALYTICS:
        _module_error("cot_analytics.py", ANALYTICS_ERR)
        return

    print("\n  UC5 — YOUTUBE ANALYTICS")
    print("  NOTE: Data has a 2-3 day delay after upload.")

    try:
        cot_analytics.main()
    except SystemExit:
        pass
    except Exception as e:
        print(f"\n  ERROR in cot_analytics.py: {e}")


def run_uc6_view_edit():
    """UC6 — View, Search & Edit Live YouTube Metadata."""
    if not HAS_META:
        _module_error("youtube_meta.py", META_ERR)
        return

    print("\n  UC6 — VIEW & EDIT LIVE YOUTUBE METADATA")

    try:
        youtube_meta.mode_review_live()
    except Exception as e:
        print(f"\n  ERROR in UC6: {e}")


# ------------------------------------------------------------
# MAIN MENU
# ------------------------------------------------------------

def main():
    print("╔══════════════════════════════════════════════════════╗")
    print("║         CatsofTravels — Pipeline Launcher            ║")
    print("║                  cot_pipeline.py                     ║")
    print(f"║                  Version {VERSION}                      ║")
    print("╚══════════════════════════════════════════════════════╝")
    print()

    # Module status
    modules = [
        ("cot_config.py",     True),
        ("make_show.py",      HAS_MAKE_SHOW),
        ("youtube_meta.py",   HAS_META),
        ("youtube_upload.py", HAS_UPLOAD),
        ("cot_analytics.py",  HAS_ANALYTICS),
    ]
    for name, available in modules:
        status = "OK" if available else "MISSING"
        print(f"  {name:<25} [{status}]")

    # LLM mode indicator
    llm_mode = cfg.get("LLM_MODE", "not configured")
    print(f"  {'LLM mode':<25} [{llm_mode}]")
    print()

    # Root folder
    default_root = PICTURES_DIR()
    root = input(f"  Root pictures folder [{default_root}]: ").strip()
    if not root:
        root = default_root
    if not os.path.isdir(root):
        print(f"\n  ERROR: Folder not found: {root}")
        print("  Run ADMIN (A) to update your PICTURES_DIR setting.")
        return

    while True:
        print("\n" + "="*60)
        print("  PIPELINE MENU")
        print("  S. Status dashboard — overview of all folders")
        print("  1. UC1 — Draft videos      (render silent MP4s)")
        print("  2. UC2 — Add music         (add audio to approved videos)")
        print("  3. UC3 — Generate metadata (title, description, tags)")
        print("  4. UC4 — Upload to YouTube")
        print("  5. UC5 — Analytics         (pull YouTube stats)")
        print("  6. UC6 — View & Edit live  (search, edit, push to YouTube)")
        print("  A. ADMIN                   (setup, config, dependency check)")
        print("  Q. Quit")
        print()

        choice = input("  Choice: ").strip().upper()

        if choice == "Q":
            print("\n  Goodbye.")
            break
        elif choice == "S":
            show_dashboard(root)
        elif choice == "1":
            run_uc1_draft_videos()
        elif choice == "2":
            run_uc2_add_music()
        elif choice == "3":
            run_uc3_metadata()
        elif choice == "4":
            run_uc4_upload()
        elif choice == "5":
            run_uc5_analytics()
        elif choice == "6":
            run_uc6_view_edit()
        elif choice == "A":
            cfg.run_admin()
        else:
            print("  Invalid choice.")


if __name__ == "__main__":
    main()
