"""
make_show.py
Version : 2.5
Author  : YouTube Video Toolkit
Date    : 2026-03-20

Slideshow video generator — beat-synced to user-selected BPM

Modes:
  A. Normal     — folder by folder, interactive
  B. Batch silent — all folders automatic, no audio
  C. Batch audio  — all folders automatic, one shared audio track
  D. Add audio    — add/change audio on existing YouTubeVideos MP4s

Features:
  - BPM menu: 60/90/120/150/180 or custom with drift warning
  - Final image: configurable hold + fade (default 2s+2s)
  - Audio fade: starts N beats before final image (fully silent on final)
  - 1920x1080 H.264, blurred+darkened background fill
  - Unsharp mask for YouTube sharpness
  - Recursive folder support, 'exclude' folder skipped
  - final.jpg logic: one/multiple/none
  - MP3 and WAV audio support
  - Raw pipe: Pillow/numpy → FFmpeg stdin (no temp files)
  - Numpy fade-to-black

Usage:
    "C:\\Program Files\\Python312\\python.exe" make_show.py

Requirements:
    pip install Pillow numpy
"""

import os
import sys
import time
import math
import subprocess
import gc
import atexit
import shutil
from datetime import datetime

try:
    import tkinter as tk
    from tkinter import filedialog
except Exception:
    tk = None
    filedialog = None

from cot_core.temp_utils import prune_old_subdirs
from cot_core.process_utils import terminate_process

try:
    from cot_core.runtime import is_interactive as _is_interactive
    from cot_core.runtime import safe_input as _safe_input
except Exception:
    def _is_interactive() -> bool:
        try:
            stdin = sys.stdin
            return bool(stdin and hasattr(stdin, "isatty") and stdin.isatty())
        except Exception:
            return False

    def _safe_input(prompt: str = "", default: str = "") -> str:
        if not _is_interactive():
            return default
        try:
            return input(prompt)
        except EOFError:
            return default

try:
    from PIL import Image, ImageOps, ImageEnhance, ImageFilter
except ImportError:
    print("ERROR: Pillow not installed.")
    print(f'Run: "{sys.executable}" -m pip install Pillow')
    sys.exit(1)

try:
    import numpy as np
except ImportError:
    print("ERROR: numpy not installed.")
    print(f'Run: "{sys.executable}" -m pip install numpy')
    sys.exit(1)

# ─── VERSION ──────────────────────────────────────────────────────────────────
VERSION = "2.5"

# ─── CONFIG ───────────────────────────────────────────────────────────────────
# Runtime paths are loaded from cot_config.json so the app can be moved to a
# new machine and configured via Settings/Browse instead of edited in code.
try:
    import cot_config as _cfg
    _cfg.load(gui_mode=True)
except Exception:
    _cfg = None


def _cfg_get(key, default=""):
    try:
        return _cfg.get(key, default) if _cfg else default
    except Exception:
        return default


FFMPEG          = _cfg_get("FFMPEG", "ffmpeg") or "ffmpeg"
DEFAULT_ROOT    = _cfg_get("PICTURES_DIR", os.path.join(os.path.expanduser("~"), "Pictures"))
OUTPUT_DIR      = _cfg_get("OUTPUT_DIR", os.path.join(DEFAULT_ROOT, "YouTubeVideos"))
AUDIO_DIR       = _cfg_get("AUDIO_DIR", os.path.join(os.path.expanduser("~"), "Music"))
LOG_FILE        = os.path.join(OUTPUT_DIR, "log.txt")
LOG_MAX_BYTES   = 1 * 1024 * 1024
TEMP_DIR        = os.path.join(OUTPUT_DIR, "_temp_frames")
EXCLUDE_NAME    = "exclude"


def refresh_runtime_config() -> None:
    """Reload user-configurable paths after Settings changes."""
    global FFMPEG, DEFAULT_ROOT, OUTPUT_DIR, AUDIO_DIR, LOG_FILE, TEMP_DIR
    try:
        if _cfg:
            _cfg.load(gui_mode=True)
    except Exception:
        pass
    FFMPEG = _cfg_get("FFMPEG", FFMPEG or "ffmpeg") or "ffmpeg"
    DEFAULT_ROOT = _cfg_get("PICTURES_DIR", DEFAULT_ROOT) or DEFAULT_ROOT
    OUTPUT_DIR = _cfg_get("OUTPUT_DIR", OUTPUT_DIR) or OUTPUT_DIR
    AUDIO_DIR = _cfg_get("AUDIO_DIR", AUDIO_DIR) or AUDIO_DIR
    LOG_FILE = os.path.join(OUTPUT_DIR, "log.txt")
    TEMP_DIR = os.path.join(OUTPUT_DIR, "_temp_frames")


_NO_WINDOW_FLAGS = {}
if sys.platform == "win32":
    try:
        _NO_WINDOW_FLAGS["creationflags"] = subprocess.CREATE_NO_WINDOW
    except Exception:
        _NO_WINDOW_FLAGS = {}
    else:
        try:
            si = subprocess.STARTUPINFO()
            si.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
            si.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
            _NO_WINDOW_FLAGS["startupinfo"] = si
        except Exception:
            pass


def _find_single_file_anywhere(root_folder: str, filename: str):
    target = filename.lower()
    found = None
    for root_dir, dirnames, filenames in os.walk(root_folder):
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith(".") and d.lower() != EXCLUDE_NAME
        ]
        for f in filenames:
            if f.lower() == target:
                path = os.path.join(root_dir, f)
                if found is not None and os.path.normpath(found) != os.path.normpath(path):
                    return None
                found = path
    return found

# Video
WIDTH           = 1920
HEIGHT          = 1080
FPS             = 30

# Encoding
PRESET          = "ultrafast"
CRF             = "18"
PIX_FMT         = "yuv420p"
THREADS         = "0"

# Image processing
SATURATION      = 1.15
BLUR_RADIUS     = 40
BG_BRIGHTNESS   = 0.35          # darkened background (0.0=black 1.0=full)
SHARPEN_RADIUS  = 1.0
SHARPEN_PCT     = 30
SHARPEN_THRESH  = 3
GC_INTERVAL     = 50

# BPM presets — all perfect sync at 30fps (multiples of 30)
BPM_PRESETS = [
    (60,  "1.00s/image  — slow, meditative"),
    (90,  "0.67s/image  — relaxed, scenic"),
    (120, "0.50s/image  — moderate, travel  [default]"),
    (150, "0.40s/image  — fast, energetic"),
    (180, "0.33s/image  — very fast, action"),
]

# ─── STARTUP ──────────────────────────────────────────────────────────────────
os.makedirs(OUTPUT_DIR, exist_ok=True)

def startup_cleanup():
    if os.path.exists(TEMP_DIR):
        print("  Cleaning up leftover temp folder...")
        shutil.rmtree(TEMP_DIR, ignore_errors=True)

    try:
        temp_mixed_parent = os.path.join(OUTPUT_DIR, "_temp_mixed")
        prune_old_subdirs(parent_dir=temp_mixed_parent, older_than_sec=24.0 * 3600.0)
    except Exception:
        pass

    try:
        for name in os.listdir(OUTPUT_DIR):
            if name.lower() in {"_concat_list.txt"}:
                p = os.path.join(OUTPUT_DIR, name)
                try:
                    if os.path.isfile(p):
                        os.remove(p)
                except Exception:
                    pass
    except Exception:
        pass

    try:
        gc.collect()
    except Exception:
        pass


atexit.register(startup_cleanup)

# ─── LOGGING ──────────────────────────────────────────────────────────────────
def rotate_log():
    if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > LOG_MAX_BYTES:
        shutil.move(LOG_FILE, os.path.join(OUTPUT_DIR, "log_old.txt"))
        print("  Log rotated to log_old.txt")

def log(msg, also_print=True):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    if also_print:
        print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def log_skip(filename, reason):
    log(f"  SKIP: {filename} — {reason}")

# ─── SETTINGS SELECTION ───────────────────────────────────────────────────────
def select_bpm():
    """
    BPM menu. Returns (bpm, frames_per_image).
    Presets are all multiples of 30 = perfect frame sync.
    Custom BPM shows drift warning.
    """
    print("\n" + "─" * 60)
    print("  SELECT BPM")
    print("  Note: multiples of 30 give perfect frame-exact beat sync.")
    print()
    for i, (bpm, desc) in enumerate(BPM_PRESETS, 1):
        print(f"  {i}. {bpm:3} BPM — {desc}")
    print(f"  {len(BPM_PRESETS)+1}. Custom BPM")
    print()

    while True:
        c = input("  Choice [3=120 BPM]: ").strip()
        if c == "":
            bpm = 120
        else:
            try:
                idx = int(c)
                if 1 <= idx <= len(BPM_PRESETS):
                    bpm = BPM_PRESETS[idx - 1][0]
                elif idx == len(BPM_PRESETS) + 1:
                    bpm = _get_custom_bpm()
                    if bpm is None:
                        continue
                else:
                    print("  Invalid choice.")
                    continue
            except ValueError:
                print("  Invalid choice.")
                continue

        sec_per_image    = 60.0 / bpm
        frames_per_image = round(sec_per_image * FPS)
        actual_sec       = frames_per_image / FPS

        if bpm % 30 != 0:
            drift = abs(sec_per_image - actual_sec)
            print(f"\n  WARNING: {bpm} BPM is not a multiple of 30.")
            print(f"  Exact  : {sec_per_image:.4f}s ({sec_per_image*FPS:.2f} frames)")
            print(f"  Rounded: {actual_sec:.4f}s ({frames_per_image} frames)")
            print(f"  Drift  : {drift*1000:.1f}ms/image | "
                  f"100 images={drift*100:.2f}s | 500 images={drift*500:.2f}s off beat")
            yn = input("\n  Proceed? Y/N: ").strip().upper()
            if yn != "Y":
                continue
        else:
            print(f"\n  Perfect sync: {bpm} BPM → {frames_per_image} frames "
                  f"({actual_sec:.3f}s/image). No drift.")

        print(f"  Confirmed: {bpm} BPM | {actual_sec:.3f}s/image | "
              f"{frames_per_image} frames/image")
        log(f"BPM: {bpm} | {frames_per_image} frames/img | {actual_sec:.3f}s/img")
        return bpm, frames_per_image

def _get_custom_bpm():
    while True:
        val = input("  Enter BPM (20-300) or Q to cancel: ").strip().upper()
        if val == "Q":
            return None
        try:
            bpm = int(val)
            if 20 <= bpm <= 300:
                return bpm
            print("  Must be 20–300.")
        except ValueError:
            print("  Enter a number.")

def select_final_settings(bpm, frames_per_image):
    """
    Ask user for:
    - Final image hold time (default 2s)
    - Final image fade time (default 2s)
    - Audio fade: beats before final image (default 4 beats)
    Returns (frames_hold, frames_fade, audio_fade_beats, audio_fade_sec)
    """
    print("\n" + "─" * 60)
    print("  FINAL IMAGE SETTINGS")
    print()

    # Hold time
    while True:
        val = input("  Final image hold time in seconds [2]: ").strip()
        if val == "":
            hold_sec = 2.0
            break
        try:
            hold_sec = float(val)
            if hold_sec > 0:
                break
            print("  Must be > 0.")
        except ValueError:
            print("  Enter a number.")

    # Fade time
    while True:
        val = input("  Final image fade to black time in seconds [2]: ").strip()
        if val == "":
            fade_sec = 2.0
            break
        try:
            fade_sec = float(val)
            if fade_sec > 0:
                break
            print("  Must be > 0.")
        except ValueError:
            print("  Enter a number.")

    # Audio fade beats before final image
    beat_sec = 60.0 / bpm
    print(f"\n  At {bpm} BPM, 1 beat = {beat_sec:.3f}s")
    print(f"  Audio will fade OUT before final image appears (final image is silent).")
    while True:
        val = input("  Beats before final image to start audio fade [4]: ").strip()
        if val == "":
            fade_beats = 4
            break
        try:
            fade_beats = int(val)
            if fade_beats > 0:
                break
            print("  Must be > 0.")
        except ValueError:
            print("  Enter a number.")

    audio_fade_sec = fade_beats * beat_sec
    frames_hold    = round(hold_sec * FPS)
    frames_fade    = round(fade_sec * FPS)

    print(f"\n  Final image : {hold_sec}s hold + {fade_sec}s fade = "
          f"{hold_sec+fade_sec}s total")
    print(f"  Audio fade  : starts {fade_beats} beats ({audio_fade_sec:.2f}s) "
          f"before final image — silent on final")
    print()

    log(f"Final: {hold_sec}s hold + {fade_sec}s fade | "
        f"Audio fade: {fade_beats} beats ({audio_fade_sec:.2f}s) before final")

    return frames_hold, frames_fade, fade_beats, audio_fade_sec

# ─── IMAGE DATE ───────────────────────────────────────────────────────────────
def get_image_date(filepath):
    try:
        with Image.open(filepath) as img:
            exif = img._getexif()
            if exif:
                dt_str = exif.get(36867)
                if dt_str:
                    return datetime.strptime(dt_str, "%Y:%m:%d %H:%M:%S").timestamp()
    except Exception:
        pass
    return os.path.getmtime(filepath)

# ─── FOLDER SCANNING ──────────────────────────────────────────────────────────
def get_jpg_files_in_folder(folder):
    exts = {".jpg", ".jpeg"}
    return [
        os.path.join(folder, f)
        for f in os.listdir(folder)
        if os.path.splitext(f)[1].lower() in exts
        and os.path.isfile(os.path.join(folder, f))
    ]

def get_image_files(folder):
    """
    Returns (images, source_folders).
    Direct JPGs if present, else combines subfolders (skips 'exclude').
    Sorted by EXIF date, fallback filename.
    """
    thumb_path = _find_single_file_anywhere(folder, "thumbnail.jpg")
    final_path = _find_single_file_anywhere(folder, "final.jpg")

    def _is_special(p: str) -> bool:
        if not p:
            return False
        pn = os.path.normpath(p)
        if thumb_path and os.path.normpath(thumb_path) == pn:
            return True
        if final_path and os.path.normpath(final_path) == pn:
            return True
        return False

    def _collect_images_recursive(start_dir: str):
        imgs = []
        srcs = []
        try:
            for root_dir, dirnames, filenames in os.walk(start_dir):
                dirnames[:] = [
                    d for d in dirnames
                    if not d.startswith(".") and d.lower() != EXCLUDE_NAME
                ]
                files = [
                    os.path.join(root_dir, f)
                    for f in filenames
                    if os.path.splitext(f)[1].lower() in {".jpg", ".jpeg"}
                ]
                files = [p for p in files if os.path.isfile(p) and not _is_special(p)]
                if files:
                    if root_dir not in srcs:
                        srcs.append(root_dir)
                    imgs.extend(files)
        except Exception as e:
            log(f"  ERROR scanning folders: {e}")

        imgs.sort(key=lambda x: (get_image_date(x), os.path.basename(x).lower()))
        return imgs, srcs

    top_level_imgs = [
        p for p in get_jpg_files_in_folder(folder)
        if not _is_special(p)
    ]
    top_level_imgs.sort(key=lambda x: (get_image_date(x), os.path.basename(x).lower()))

    sub_imgs = []
    source_folders = []
    try:
        subs = sorted(
            [e for e in os.scandir(folder)
             if e.is_dir() and not e.name.startswith(".") and e.name.lower() != EXCLUDE_NAME],
            key=lambda e: e.name.lower()
        )
        for sub in subs:
            imgs, srcs = _collect_images_recursive(sub.path)
            if imgs:
                sub_imgs.extend(imgs)
                for s in srcs:
                    if s not in source_folders:
                        source_folders.append(s)
    except Exception as e:
        log(f"  ERROR scanning subfolders: {e}")

    all_files = []
    if top_level_imgs:
        all_files.extend(top_level_imgs)
        if folder not in source_folders:
            source_folders.insert(0, folder)
    if sub_imgs:
        all_files.extend(sub_imgs)

    return all_files, source_folders

def find_final_jpg(folder, source_folders):
    """
    Find final.jpg. One=use, multiple=user picks, none=return None.
    """
    path = _find_single_file_anywhere(folder, "final.jpg")
    if path and os.path.isfile(path):
        log(f"  final.jpg: {path}")
        return path
    return None

def get_subfolders(root):
    try:
        return [
            entry.path
            for entry in sorted(os.scandir(root), key=lambda e: e.name.lower())
            if entry.is_dir()
            and not entry.name.startswith(".")
            and entry.name.lower() != EXCLUDE_NAME
        ]
    except Exception as e:
        log(f"ERROR scanning {root}: {e}")
        return []

def count_images(folder):
    exts = {".jpg", ".jpeg"}
    try:
        total = 0
        for root_dir, dirnames, filenames in os.walk(folder):
            dirnames[:] = [
                d for d in dirnames
                if not d.startswith(".") and d.lower() != EXCLUDE_NAME
            ]
            for f in filenames:
                if os.path.splitext(f)[1].lower() in exts:
                    total += 1
        return total
    except Exception:
        return 0

def output_exists(folder_name):
    return os.path.isfile(os.path.join(OUTPUT_DIR, folder_name + ".mp4"))

# ─── ROOT FOLDER BROWSER ──────────────────────────────────────────────────────
def browse_root_folder(current):
    path = current
    while True:
        print(f"\n  Current: {path}")
        try:
            entries = sorted(
                [e for e in os.scandir(path)
                 if e.is_dir() and not e.name.startswith(".")],
                key=lambda e: e.name.lower()
            )
        except Exception as e:
            print(f"  Cannot read: {e}")
            return current
        for i, e in enumerate(entries, 1):
            print(f"  {i:3}. {e.name}")
        if not entries:
            print("  (no subfolders)")
        print("\n  U. Go up   S. Select this folder   Q. Cancel")
        c = input("  Choice: ").strip().upper()
        if c == "Q":
            return current
        if c == "S":
            return path
        if c == "U":
            parent = os.path.dirname(path)
            if parent != path:
                path = parent
            continue
        try:
            idx = int(c) - 1
            if 0 <= idx < len(entries):
                path = entries[idx].path
            else:
                print("  Out of range.")
        except ValueError:
            print("  Invalid choice.")

# ─── IMAGE FRAME PREPARATION ──────────────────────────────────────────────────
def prepare_frame(filepath):
    """
    Composite: blurred+darkened background fill + sharpened foreground fit.
    Returns numpy uint8 (HEIGHT, WIDTH, 3) or None on failure.
    """
    img = bg = fg = canvas = None
    try:
        img = Image.open(filepath)
        img = ImageOps.exif_transpose(img)
        if img.mode != "RGB":
            img = img.convert("RGB")
        if SATURATION != 1.0:
            img = ImageEnhance.Color(img).enhance(SATURATION)

        # Background: fill frame, blur, darken
        bg       = img.copy()
        bg_ratio = max(WIDTH / bg.width, HEIGHT / bg.height)
        bg_w     = int(bg.width  * bg_ratio)
        bg_h     = int(bg.height * bg_ratio)
        bg       = bg.resize((bg_w, bg_h), Image.LANCZOS)
        left     = (bg_w - WIDTH)  // 2
        top_crop = (bg_h - HEIGHT) // 2
        bg       = bg.crop((left, top_crop, left + WIDTH, top_crop + HEIGHT))
        bg       = bg.filter(ImageFilter.GaussianBlur(radius=BLUR_RADIUS))
        bg       = ImageEnhance.Brightness(bg).enhance(BG_BRIGHTNESS)

        # Foreground: fit frame, unsharp mask
        fg = img.copy()
        fg.thumbnail((WIDTH, HEIGHT), Image.LANCZOS)
        fg = fg.filter(ImageFilter.UnsharpMask(
            radius=SHARPEN_RADIUS,
            percent=SHARPEN_PCT,
            threshold=SHARPEN_THRESH
        ))

        # Composite
        canvas   = bg.copy()
        offset_x = (WIDTH  - fg.width)  // 2
        offset_y = (HEIGHT - fg.height) // 2
        canvas.paste(fg, (offset_x, offset_y))

        return np.array(canvas, dtype=np.uint8)

    except Exception as e:
        log_skip(os.path.basename(filepath), str(e))
        return None
    finally:
        for obj in [img, bg, fg, canvas]:
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
        del img, bg, fg, canvas

# ─── NUMPY FADE ───────────────────────────────────────────────────────────────
def make_fade_frames(arr, num_frames):
    """Yield fade-to-black frames via numpy vectorised multiply."""
    for i in range(num_frames):
        factor = 1.0 - (i / num_frames)
        faded  = (arr * factor).astype(np.uint8)
        yield faded.tobytes()
        del faded

# ─── VIDEO BUILD ──────────────────────────────────────────────────────────────
def build_video(folder, images, output_path, frames_per_image,
                frames_hold, frames_fade, *, stop_event=None, pause_event=None):
    """
    Pipe raw RGB frames into FFmpeg stdin.
    frames_hold/frames_fade from user settings.
    Returns (True, total_duration, final_image_start_sec) or False.
    final_image_start_sec is used to calculate audio fade start.
    """
    if not images:
        log("ERROR: No images.")
        return False

    frames_last      = frames_hold + frames_fade
    total_images     = len(images)
    est_total_frames = ((total_images - 1) * frames_per_image) + frames_last
    est_duration     = est_total_frames / FPS

    # Final image starts at this time in the video
    est_final_start  = ((total_images - 1) * frames_per_image) / FPS

    log(f"  Images         : {total_images}")
    log(f"  Frames/image   : {frames_per_image}")
    log(f"  Final hold/fade: {frames_hold}/{frames_fade} frames")
    log(f"  Est. frames    : {est_total_frames}")
    log(f"  Est. duration  : {est_duration:.2f}s")
    log(f"  Final img start: {est_final_start:.2f}s")

    cmd = [
        FFMPEG, "-y",
        "-f", "rawvideo", "-vcodec", "rawvideo",
        "-s", f"{WIDTH}x{HEIGHT}",
        "-pix_fmt", "rgb24",
        "-r", str(FPS),
        "-i", "pipe:0",
        "-c:v", "libx264",
        "-preset", PRESET,
        "-crf", CRF,
        "-pix_fmt", PIX_FMT,
        "-threads", THREADS,
        "-an",
        output_path
    ]

    log(f"  Encoding...")
    start_time = time.time()

    try:
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=10 * 1024 * 1024,
            **_NO_WINDOW_FLAGS,
        )

        frames_written = 0
        skipped        = 0

        for img_num, filepath in enumerate(images):
            if stop_event is not None and stop_event.is_set():
                try:
                    terminate_process(process)
                except Exception:
                    pass
                try:
                    process.wait(timeout=2)
                except Exception:
                    pass
                return False

            while pause_event is not None and pause_event.is_set():
                if stop_event is not None and stop_event.is_set():
                    try:
                        terminate_process(process)
                    except Exception:
                        pass
                    return False
                time.sleep(0.1)

            is_last = (img_num == total_images - 1)

            arr = prepare_frame(filepath)
            if arr is None:
                skipped += 1
                continue

            if is_last:
                raw = arr.tobytes()
                for _ in range(frames_hold):
                    if stop_event is not None and stop_event.is_set():
                        try:
                            terminate_process(process)
                        except Exception:
                            pass
                        return False
                    while pause_event is not None and pause_event.is_set():
                        if stop_event is not None and stop_event.is_set():
                            try:
                                terminate_process(process)
                            except Exception:
                                pass
                            return False
                        time.sleep(0.1)
                    process.stdin.write(raw)
                    frames_written += 1
                del raw
                for faded_bytes in make_fade_frames(arr, frames_fade):
                    if stop_event is not None and stop_event.is_set():
                        try:
                            terminate_process(process)
                        except Exception:
                            pass
                        return False
                    while pause_event is not None and pause_event.is_set():
                        if stop_event is not None and stop_event.is_set():
                            try:
                                terminate_process(process)
                            except Exception:
                                pass
                            return False
                        time.sleep(0.1)
                    process.stdin.write(faded_bytes)
                    frames_written += 1
            else:
                raw = arr.tobytes()
                for _ in range(frames_per_image):
                    if stop_event is not None and stop_event.is_set():
                        try:
                            terminate_process(process)
                        except Exception:
                            pass
                        return False
                    while pause_event is not None and pause_event.is_set():
                        if stop_event is not None and stop_event.is_set():
                            try:
                                terminate_process(process)
                            except Exception:
                                pass
                            return False
                        time.sleep(0.1)
                    process.stdin.write(raw)
                    frames_written += 1
                del raw

            elapsed   = time.time() - start_time
            valid_num = img_num + 1 - skipped
            valid_tot = total_images - skipped
            pct       = min(100, int(frames_written / est_total_frames * 100))
            print(
                f"    Image {valid_num}/{valid_tot} | "
                f"Frame {frames_written}/{est_total_frames} ({pct}%) | "
                f"{elapsed:.0f}s elapsed  ",
                end="\r"
            )

            del arr
            if img_num % GC_INTERVAL == 0:
                gc.collect()

        print()
        log(f"  Pipe complete. {frames_written} frames, {skipped} skipped.")
        log(f"  Waiting for FFmpeg...")

        process.stdin.close()
        stderr_output = process.stderr.read()
        process.wait()

        elapsed = time.time() - start_time
        log(f"  Encode time: {elapsed:.1f}s")

        if process.returncode != 0:
            log(f"  FFmpeg ERROR:\n{stderr_output.decode(errors='replace')[-2000:]}")
            return False

    except BrokenPipeError:
        log("  ERROR: FFmpeg pipe broke.")
        try:
            log(process.stderr.read().decode(errors='replace')[-2000:])
        except Exception:
            pass
        return False
    except Exception as e:
        log(f"  Exception: {e}")
        return False
    finally:
        gc.collect()

    actual_duration    = frames_written / FPS
    actual_final_start = ((total_images - 1 - skipped) * frames_per_image) / FPS

    log(f"  Actual duration   : {actual_duration:.2f}s")
    log(f"  Actual final start: {actual_final_start:.2f}s")
    log(f"  Saved: {os.path.basename(output_path)}")

    return True, actual_duration, actual_final_start

# ─── AUDIO FILE DIALOG ────────────────────────────────────────────────────────
def browse_audio():
    """Windows file dialog — MP3 and WAV. Starts in AUDIO_DIR."""
    if tk is None or filedialog is None:
        print("ERROR: tkinter is not available. Install/enable Tk support or run Make Show GUI Mode E/F (music selection built in).")
        return None
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    initial  = AUDIO_DIR if os.path.isdir(AUDIO_DIR) else os.path.expanduser("~")
    filepath = filedialog.askopenfilename(
        title="Select Audio Track (MP3 or WAV)",
        initialdir=initial,
        filetypes=[
            ("Audio files", "*.mp3 *.wav"),
            ("MP3 files",   "*.mp3"),
            ("WAV files",   "*.wav"),
            ("All files",   "*.*")
        ]
    )
    root.destroy()
    if filepath:
        log(f"  Selected audio: {os.path.basename(filepath)}")
        return filepath
    return None

# ─── AUDIO MERGE ──────────────────────────────────────────────────────────────
def add_audio(video_path, audio_path, total_duration,
              final_image_start, audio_fade_sec, output_path):
    """
    Merge audio into video.
    Audio fade starts (audio_fade_sec) before final image — final image is silent.
    Loops audio if short. Trims to video duration. Copies video stream.
    """
    # Audio fade starts N seconds before final image appears
    fade_start = max(0.0, final_image_start - audio_fade_sec)

    log(f"  Audio        : {os.path.basename(audio_path)}")
    log(f"  Final img at : {final_image_start:.2f}s")
    log(f"  Audio fade   : starts {fade_start:.2f}s, dur {audio_fade_sec:.2f}s")
    log(f"  Final image  : fully silent")

    af  = f"afade=t=out:st={fade_start:.3f}:d={audio_fade_sec:.3f}"
    cmd = [
        FFMPEG, "-y",
        "-i", video_path,
        "-stream_loop", "-1",
        "-i", audio_path,
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-af", af,
        "-t", f"{total_duration:.6f}",
        "-threads", THREADS,
        output_path
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, **_NO_WINDOW_FLAGS)
        if result.returncode != 0:
            log(f"  FFmpeg audio ERROR:\n{result.stderr[-2000:]}")
            return False
    except Exception as e:
        log(f"  Audio exception: {e}")
        return False

    log(f"  Merged: {os.path.basename(output_path)}")
    return True

# ─── PLAYBACK ─────────────────────────────────────────────────────────────────
def play_video(path):
    try:
        os.startfile(path)
        log(f"  Playing: {os.path.basename(path)}")
    except Exception as e:
        log(f"  Could not open: {e}")

# ─── OVERWRITE HANDLING ───────────────────────────────────────────────────────
def handle_existing(output_path):
    if not os.path.exists(output_path):
        return output_path
    base = os.path.splitext(output_path)[0]
    print(f"\n  WARNING: {os.path.basename(output_path)} already exists.")
    print("  O. Overwrite   R. Rename   X. Skip")
    while True:
        c = input("  Choice: ").strip().upper()
        if c == "O":
            log("  Overwriting.")
            return output_path
        if c == "R":
            i = 1
            while True:
                new_path = f"{base}_{i}.mp4"
                if not os.path.exists(new_path):
                    log(f"  Renamed: {os.path.basename(new_path)}")
                    return new_path
                i += 1
        if c == "X":
            log("  Skipped.")
            return None
        print("  Invalid choice.")

# ─── CORE: PROCESS ONE FOLDER ─────────────────────────────────────────────────
def process_folder(folder, frames_per_image, frames_hold, frames_fade,
                   audio_fade_sec, auto_audio_path=None, silent=False, *, stop_event=None, pause_event=None):
    """
    Build video for one folder.
    auto_audio_path: if set, apply this audio automatically (batch mode)
    silent: if True, skip audio entirely (batch silent mode)
    Returns output video path or None on failure.
    """
    folder_name = os.path.basename(folder)
    log(f"\n{'='*60}")
    log(f"Processing: {folder_name}")

    images, source_folders = get_image_files(folder)
    if not images:
        log("  No JPG images found.")
        return None

    log(f"  {len(images)} images from {len(source_folders)} source folder(s).")

    # Resolve final.jpg
    final_jpg = find_final_jpg(folder, source_folders)
    if final_jpg:
        images = [img for img in images
                  if os.path.normpath(img) != os.path.normpath(final_jpg)]
        images.append(final_jpg)
        log(f"  Last image: final.jpg")
    else:
        log(f"  Last image: {os.path.basename(images[-1])} (last by date)")

    raw_output   = os.path.join(OUTPUT_DIR, folder_name + ".mp4")
    final_output = handle_existing(raw_output)
    if final_output is None:
        return None

    result = build_video(
        folder,
        images,
        final_output,
        frames_per_image,
        frames_hold,
        frames_fade,
        stop_event=stop_event,
        pause_event=pause_event,
    )
    if not result:
        if stop_event is not None and stop_event.is_set():
            try:
                if os.path.isfile(final_output):
                    os.remove(final_output)
                    log(f"  Deleted partial: {os.path.basename(final_output)}")
            except Exception:
                pass
        log("  Video build failed.")
        return None

    _, total_duration, final_image_start = result

    # Silent — done
    if silent:
        log(f"  Saved (silent): {os.path.basename(final_output)}")
        return final_output

    # Auto audio (batch mode)
    if auto_audio_path:
        music_output = os.path.splitext(final_output)[0] + "_music.mp4"
        success = add_audio(
            final_output, auto_audio_path,
            total_duration, final_image_start,
            audio_fade_sec, music_output
        )
        if success:
            log(f"  Saved with audio: {os.path.basename(music_output)}")
            return music_output
        else:
            log("  Audio merge failed — returning silent video.")
            return final_output

    # Interactive audio menu
    current_video = final_output
    while True:
        print(f"\n  Ready: {os.path.basename(current_video)}")
        print(f"  Duration: {total_duration:.1f}s  |  Images: {len(images)}")
        print("  S. Silent   A. Add music   X. Next folder   Q. Quit")
        c = input("  Choice: ").strip().upper()

        if c == "Q":
            log("  User quit.")
            sys.exit(0)
        elif c == "X":
            log("  Next folder.")
            return current_video
        elif c == "S":
            while True:
                print("\n  P. Play   A. Accept   Q. Quit")
                c2 = input("  Choice: ").strip().upper()
                if c2 == "P":
                    play_video(current_video)
                elif c2 == "A":
                    log(f"  Accepted (silent): {os.path.basename(current_video)}")
                    return current_video
                elif c2 == "Q":
                    log("  User quit.")
                    sys.exit(0)
                else:
                    print("  Invalid choice.")
        elif c == "A":
            audio_path = browse_audio()
            if audio_path is None:
                print("  No audio selected.")
                continue
            music_output = os.path.splitext(final_output)[0] + "_music.mp4"
            success = add_audio(
                current_video, audio_path,
                total_duration, final_image_start,
                audio_fade_sec, music_output
            )
            if not success:
                print("  Audio merge failed.")
                continue
            current_video = music_output
            while True:
                print(f"\n  P. Play   C. Change music   A. Accept   Q. Quit")
                c2 = input("  Choice: ").strip().upper()
                if c2 == "P":
                    play_video(current_video)
                elif c2 == "C":
                    break
                elif c2 == "A":
                    log(f"  Accepted: {os.path.basename(current_video)}")
                    return current_video
                elif c2 == "Q":
                    log("  User quit.")
                    sys.exit(0)
                else:
                    print("  Invalid choice.")
        else:
            print("  Invalid choice.")

# ─── MODE A: NORMAL (interactive folder by folder) ────────────────────────────
def mode_normal(subfolders, frames_per_image, frames_hold, frames_fade, audio_fade_sec):
    i = 0
    while i < len(subfolders):
        folder      = subfolders[i]
        folder_name = os.path.basename(folder)
        img_count   = count_images(folder)
        done_tag    = "  [DONE]" if output_exists(folder_name) else ""

        print(f"\n{'─'*60}")
        print(f"  Folder : {folder_name}{done_tag}")
        print(f"  Images : {img_count}")
        print("  Y. Process   X. Next   Q. Quit")

        c = input("  Choice: ").strip().upper()
        if c == "Q":
            log("User quit.")
            print("Goodbye.")
            sys.exit(0)
        elif c == "X":
            i += 1
        elif c == "Y":
            if img_count == 0:
                print("  No images — skipping.")
                i += 1
            else:
                process_folder(folder, frames_per_image, frames_hold,
                               frames_fade, audio_fade_sec)
                i += 1
        else:
            print("  Invalid choice.")

# ─── MODE B: BATCH SILENT ─────────────────────────────────────────────────────
def mode_batch_silent(subfolders, frames_per_image, frames_hold,
                      frames_fade, audio_fade_sec, *, interactive=None, skip_done_default=True, stop_event=None, pause_event=None):
    """Process all folders silently, fully automatic."""
    if interactive is None:
        interactive = _is_interactive()
    if interactive:
        print("\n  Skip folders already marked [DONE]?")
        skip_done = _safe_input("  Y/N [Y]: ", default="").strip().upper()
        skip_done = (skip_done != "N")
    else:
        skip_done = bool(skip_done_default)

    total   = len(subfolders)
    done    = 0
    skipped = 0

    for folder in subfolders:
        if stop_event is not None and stop_event.is_set():
            log("  Stopped.")
            break

        while pause_event is not None and pause_event.is_set():
            if stop_event is not None and stop_event.is_set():
                log("  Stopped.")
                break
            time.sleep(0.1)

        folder_name = os.path.basename(folder)
        if skip_done and output_exists(folder_name):
            log(f"  Skipping [DONE]: {folder_name}")
            skipped += 1
            continue
        img_count = count_images(folder)
        if img_count == 0:
            log(f"  No images: {folder_name}")
            skipped += 1
            continue
        process_folder(
            folder,
            frames_per_image,
            frames_hold,
            frames_fade,
            audio_fade_sec,
            silent=True,
            stop_event=stop_event,
            pause_event=pause_event,
        )
        done += 1

    log(f"\nBatch silent complete. {done} processed, {skipped} skipped of {total}.")

# ─── MODE C: BATCH WITH AUDIO ─────────────────────────────────────────────────
def mode_batch_audio(subfolders, frames_per_image, frames_hold,
                     frames_fade, audio_fade_sec, *, interactive=None, skip_done_default=True, stop_event=None, pause_event=None):
    """Process all folders with one shared audio track."""
    print("\n  Select audio track to apply to all folders:")
    audio_path = browse_audio()
    if audio_path is None:
        print("  No audio selected — returning to menu.")
        return

    if interactive is None:
        interactive = _is_interactive()
    if interactive:
        print("\n  Skip folders already marked [DONE]?")
        skip_done = _safe_input("  Y/N [Y]: ", default="").strip().upper()
        skip_done = (skip_done != "N")
    else:
        skip_done = bool(skip_done_default)

    total   = len(subfolders)
    done    = 0
    skipped = 0

    for folder in subfolders:
        if stop_event is not None and stop_event.is_set():
            log("  Stopped.")
            break

        while pause_event is not None and pause_event.is_set():
            if stop_event is not None and stop_event.is_set():
                log("  Stopped.")
                break
            time.sleep(0.1)

        folder_name = os.path.basename(folder)
        if skip_done and output_exists(folder_name):
            log(f"  Skipping [DONE]: {folder_name}")
            skipped += 1
            continue

        img_count = count_images(folder)
        if img_count == 0:
            log(f"  No images: {folder_name}")
            skipped += 1
            continue

        process_folder(
            folder,
            frames_per_image,
            frames_hold,
            frames_fade,
            audio_fade_sec,
            auto_audio_path=audio_path,
            silent=False,
            stop_event=stop_event,
            pause_event=pause_event,
        )
        done += 1

    log(f"\nBatch audio complete. {done} processed, {skipped} skipped of {total}.")


def mode_add_audio_existing(audio_fade_sec):
    
    # ... (rest of the code remains the same)
    """
    Scan YouTubeVideos for MP4s (excluding _music versions).
    Let user pick audio track, apply to all or selected.
    """
    last_audio_path = None

    while True:
        try:
            mp4s = sorted([
                f for f in os.listdir(OUTPUT_DIR)
                if f.lower().endswith(".mp4")
                and not f.lower().endswith("_music.mp4")
                and os.path.isfile(os.path.join(OUTPUT_DIR, f))
            ])
        except Exception as e:
            log(f"ERROR reading output folder: {e}")
            return

        if not mp4s:
            print("  No MP4 files found in output folder.")
            return

        print(f"\n  Found {len(mp4s)} movie(s) in {OUTPUT_DIR}:")
        for i, f in enumerate(mp4s, 1):
            music_exists = os.path.isfile(
                os.path.join(OUTPUT_DIR, os.path.splitext(f)[0] + "_music.mp4")
            )
            tag = "  [HAS AUDIO]" if music_exists else ""
            print(f"  {i:3}. {f}{tag}")

        print("\n  A. Apply audio to ALL   S. Select specific   Q. Cancel")
        c_raw = input("  Choice: ").strip()
        c = c_raw.upper()
        if c == "Q":
            break

        if c == "A":
            targets = [os.path.join(OUTPUT_DIR, f) for f in mp4s]
        elif c == "S" or c_raw[:1].isdigit():
            if c == "S":
                sel = input("  Enter numbers separated by commas (e.g. 1,3,5): ").strip()
            else:
                sel = c_raw
            try:
                indices = [int(x.strip()) - 1 for x in sel.split(",")]
                targets = [os.path.join(OUTPUT_DIR, mp4s[i])
                           for i in indices if 0 <= i < len(mp4s)]
                if not targets:
                    print("  No valid selections.")
                    continue
            except Exception:
                print("  Invalid input.")
                continue
        else:
            print("  Invalid choice.")
            continue

        for video_path in targets:
            audio_path = None
            if last_audio_path and os.path.isfile(last_audio_path):
                print(f"\n  Movie: {os.path.basename(video_path)}")
                print(f"  Last audio: {os.path.basename(last_audio_path)}")
                print("  B. Browse new audio   R. Reuse last audio   S. Skip movie   Q. Quit")
                c3 = input("  Choice [B]: ").strip().upper()
                if c3 == "":
                    c3 = "B"
                if c3 == "Q":
                    targets = []
                    break
                if c3 == "S":
                    log(f"  Skipped: {os.path.basename(video_path)}")
                    continue
                if c3 == "R":
                    audio_path = last_audio_path
                elif c3 == "B":
                    print("\n  Select audio track:")
                    audio_path = browse_audio()
                else:
                    print("  Invalid choice, skipping movie.")
                    continue
            else:
                print(f"\n  Movie: {os.path.basename(video_path)}")
                print("  Select audio track (or Cancel to skip this movie):")
                audio_path = browse_audio()

            if audio_path is None:
                log(f"  Skipped: {os.path.basename(video_path)}")
                continue
            last_audio_path = audio_path

            ffprobe = os.path.join(os.path.dirname(FFMPEG), "ffprobe.exe")
            if not os.path.isfile(ffprobe):
                ffprobe = "ffprobe"
            total_duration = None
            duration_err = None
            for cmd in (
                [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video_path],
                [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            ):
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True, **_NO_WINDOW_FLAGS)
                    stdout = (result.stdout or "").strip()
                    stderr = (result.stderr or "").strip()

                    if result.returncode != 0:
                        duration_err = f"ffprobe rc={result.returncode} stderr={stderr[-400:]}"
                        continue

                    token = stdout.split()[0] if stdout else ""
                    total_duration = float(token)
                    break
                except Exception as e:
                    duration_err = str(e)
                    continue

            if total_duration is None:
                log(
                    f"  Could not get duration for {os.path.basename(video_path)}, skipping."
                    + (f" Details: {duration_err}" if duration_err else "")
                )
                continue

            final_image_start = max(0.0, total_duration - 4.0)

            music_output = os.path.splitext(video_path)[0] + "_music.mp4"
            if os.path.exists(music_output):
                print(f"\n  {os.path.basename(music_output)} already exists.")
                print("  O. Overwrite   S. Skip")
                c2 = input("  Choice: ").strip().upper()
                if c2 != "O":
                    log(f"  Skipped: {os.path.basename(music_output)}")
                    continue

            add_audio(video_path, audio_path, total_duration,
                      final_image_start, audio_fade_sec, music_output)

    log("Add audio to existing complete.")

# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    refresh_runtime_config()
    startup_cleanup()
    rotate_log()

    print("=" * 60)
    print(f"  make_show.py  v{VERSION}  — Slideshow Video Generator")
    print(f"  Output : {OUTPUT_DIR}")
    print("=" * 60)

    # ── Main mode menu ────────────────────────────────────────────────────────
    print("\n  SELECT MODE")
    print("  A. Normal      — folder by folder, interactive")
    print("  B. Batch silent — all folders, no audio, automatic")
    print("  C. Batch audio  — all folders, one shared audio track")
    print("  D. Add audio    — add audio to existing movies")
    print("  Q. Quit")
    while True:
        mode = input("\n  Choice: ").strip().upper()
        if mode in ("A", "B", "C", "D", "Q"):
            break
        print("  Invalid choice.")

    if mode == "Q":
        print("Goodbye.")
        sys.exit(0)

    # ── Settings (BPM + final image) needed for A/B/C ────────────────────────
    if mode in ("A", "B", "C"):
        bpm, frames_per_image = select_bpm()
        frames_hold, frames_fade, fade_beats, audio_fade_sec = \
            select_final_settings(bpm, frames_per_image)
    else:
        # Mode D: audio fade sec only (use default 2s)
        audio_fade_sec = 2.0
        bpm = 120

    # ── Root folder (not needed for mode D) ──────────────────────────────────
    if mode in ("A", "B", "C"):
        root = DEFAULT_ROOT
        print(f"\n  Pictures root : {root}")
        print("  U. Use this   B. Browse to different folder")
        c = input("  Choice: ").strip().upper()
        if c == "B":
            root = browse_root_folder(root)

        if not os.path.isdir(root):
            print(f"ERROR: Folder not found: {root}")
            sys.exit(1)

        log(f"Session start. Mode={mode} Root={root} BPM={bpm} "
            f"frames/img={frames_per_image if mode in ('A','B','C') else 'N/A'}")

        subfolders = get_subfolders(root)
        if not subfolders:
            print("  No subfolders found.")
            sys.exit(0)
        print(f"  Found {len(subfolders)} subfolders.\n")

    # ── Dispatch to mode ─────────────────────────────────────────────────────
    if mode == "A":
        mode_normal(subfolders, frames_per_image, frames_hold,
                    frames_fade, audio_fade_sec)
    elif mode == "B":
        mode_batch_silent(subfolders, frames_per_image, frames_hold,
                          frames_fade, audio_fade_sec)
    elif mode == "C":
        mode_batch_audio(subfolders, frames_per_image, frames_hold,
                         frames_fade, audio_fade_sec)
    elif mode == "D":
        mode_add_audio_existing(audio_fade_sec)

    print("\n  Done. Goodbye.")
    log("Session complete.")

# ─── ENTRY POINT ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    main()
