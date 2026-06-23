import os
import sys
import json
import argparse
import subprocess
from datetime import datetime
from typing import Dict, List, Optional, Tuple


try:
    import cot_config as cfg
    cfg.load(gui_mode=True)
except Exception:
    cfg = None


EXCLUDE_DIR_NAME = "Exclude"
COT_DIR_NAME = ".cot"
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi"}


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _safe_relpath(path: str, root: str) -> str:
    try:
        return os.path.relpath(path, root)
    except Exception:
        return path


def _ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)


def _iter_video_files(project_root: str) -> List[str]:
    out: List[str] = []
    for cur, dirnames, filenames in os.walk(project_root):
        parts = {p.lower() for p in cur.split(os.sep) if p}
        if EXCLUDE_DIR_NAME.lower() in parts or COT_DIR_NAME.lower() in parts:
            dirnames[:] = []
            continue

        dirnames[:] = [d for d in dirnames if d.lower() not in (EXCLUDE_DIR_NAME.lower(), COT_DIR_NAME.lower())]

        for f in filenames:
            ext = os.path.splitext(f)[1].lower()
            if ext in VIDEO_EXTS:
                out.append(os.path.join(cur, f))

    out.sort(key=lambda p: os.path.basename(p).lower())
    return out


def _find_ffmpeg() -> str:
    # Prefer c:\ffmpeg\bin\ffmpeg.exe if present (matches your typical setup)
    candidate = r"C:\ffmpeg\bin\ffmpeg.exe"
    if os.path.isfile(candidate):
        return candidate
    return "ffmpeg"


def _find_ffprobe(ffmpeg_path: str) -> str:
    if ffmpeg_path.lower().endswith("ffmpeg.exe"):
        p = os.path.join(os.path.dirname(ffmpeg_path), "ffprobe.exe")
        if os.path.isfile(p):
            return p
    return "ffprobe"


def _probe_duration_sec(ffprobe: str, video_path: str) -> Optional[float]:
    cmds = (
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video_path],
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", video_path],
    )
    for cmd in cmds:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                continue
            token = (r.stdout or "").strip().split()[0] if (r.stdout or "").strip() else ""
            if not token:
                continue
            return float(token)
        except Exception:
            continue
    return None


def _load_project_state(project_root: str) -> Dict:
    cot_dir = os.path.join(project_root, COT_DIR_NAME)
    state_path = os.path.join(cot_dir, "curation.json")
    if os.path.isfile(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                return json.load(f) or {}
        except Exception:
            return {}
    return {}


def _save_project_state(project_root: str, state: Dict) -> None:
    cot_dir = os.path.join(project_root, COT_DIR_NAME)
    _ensure_dir(cot_dir)
    state_path = os.path.join(cot_dir, "curation.json")
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def _load_flipbook_state(project_root: str) -> Dict:
    cot_dir = os.path.join(project_root, COT_DIR_NAME)
    state_path = os.path.join(cot_dir, "flipbook.json")
    if os.path.isfile(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                st = json.load(f) or {}
            return st if isinstance(st, dict) else {}
        except Exception:
            return {}

    legacy = _load_project_state(project_root)
    if isinstance(legacy, dict):
        legacy_fb = legacy.get("flipbook")
        if isinstance(legacy_fb, dict):
            migrated = {"version": 1, "clips": legacy_fb.get("clips", {})}
            try:
                _save_flipbook_state(project_root, migrated)
            except Exception:
                pass
            return migrated
    return {"version": 1, "clips": {}}


def _save_flipbook_state(project_root: str, state: Dict) -> None:
    cot_dir = os.path.join(project_root, COT_DIR_NAME)
    _ensure_dir(cot_dir)
    state_path = os.path.join(cot_dir, "flipbook.json")
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def _choose_project(root: str) -> Optional[str]:
    try:
        entries = [
            d for d in os.listdir(root)
            if os.path.isdir(os.path.join(root, d))
            and not d.startswith(".")
        ]
    except Exception as e:
        print(f"\n  ERROR: Could not list root folder: {e}\n")
        return None

    entries.sort(key=lambda s: s.lower())

    print("\n  Select a project folder:")
    for i, name in enumerate(entries[:60], 1):
        print(f"    {i:>2}. {name}")
    if len(entries) > 60:
        print(f"    ... ({len(entries) - 60} more)")

    while True:
        sel = input("\n  Enter number, or paste a folder name/path (Enter to cancel): ").strip()
        if not sel:
            return None

        if sel.isdigit():
            idx = int(sel)
            if 1 <= idx <= len(entries):
                return os.path.join(root, entries[idx - 1])
            print("  Invalid number.")
            continue

        if os.path.isabs(sel) and os.path.isdir(sel):
            return sel

        p = os.path.join(root, sel)
        if os.path.isdir(p):
            return p

        print("  Not found. Try again.")


def _prompt_float(prompt: str, default: float, *, min_value: float = 0.0, max_value: float = 10_000.0) -> float:
    while True:
        raw = input(f"{prompt} (default {default}): ").strip()
        if not raw:
            return float(default)
        try:
            v = float(raw)
        except Exception:
            print("  Please enter a number.")
            continue
        if v < min_value or v > max_value:
            print(f"  Please enter a value between {min_value} and {max_value}.")
            continue
        return v


def build_flipbook(
    *,
    ffmpeg: str,
    ffprobe: str,
    src: str,
    dest: str,
    out_sec: float,
    out_fps: float,
    width: int,
    height: int,
    overwrite: bool,
    dry_run: bool,
) -> Tuple[bool, str]:
    dur = _probe_duration_sec(ffprobe, src)
    if not dur or dur <= 0.1:
        return False, "could not probe duration"

    # Time-compress the full clip down to out_sec, then downsample to out_fps.
    # This preserves a smooth-looking cadence (8fps really looks like 8fps), while still summarizing the entire clip.
    if out_sec <= 0.1 or out_fps <= 0.1:
        return False, "invalid out_sec/out_fps"

    speed = dur / out_sec
    if speed < 1.0:
        speed = 1.0
    if speed > 200.0:
        speed = 200.0

    vf = (
        f"setpts=PTS/{speed:.6f},"
        f"fps={out_fps:.6f},"
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
        "format=yuv420p"
    )

    cmd = [
        ffmpeg,
        "-y" if overwrite else "-n",
        "-i",
        src,
        "-an",
        "-vf",
        vf,
        "-frames:v",
        str(int(round(out_sec * out_fps))),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "22",
        dest,
    ]

    if dry_run:
        return True, f"dry-run (speed={speed:.3f}x, frames={int(round(out_sec * out_fps))})"

    try:
        _ensure_dir(os.path.dirname(dest))
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            err = (r.stderr or "").strip()
            return False, err[-800:] if err else f"ffmpeg rc={r.returncode}"
        return True, f"ok (speed={speed:.3f}x)"
    except Exception as e:
        return False, str(e)


def process_project(
    project_root: str,
    *,
    out_sec: float,
    out_fps: float,
    width: int,
    height: int,
    overwrite: bool,
    dry_run: bool,
) -> None:
    print("\n  Flipbook Clips — Backlog Crusher")
    if dry_run:
        print("  Mode: DRY-RUN / ANALYSIS (no ffmpeg will be executed, no files will be written)")
    print(f"  Project: {project_root}")

    ffmpeg = _find_ffmpeg()
    ffprobe = _find_ffprobe(ffmpeg)

    vids = _iter_video_files(project_root)
    if not vids:
        print("\n  No video clips found under this project folder.")
        return

    print(f"\n  Found {len(vids)} clip(s).")

    fb_state = _load_flipbook_state(project_root)
    fb_state = fb_state if isinstance(fb_state, dict) else {"version": 1, "clips": {}}
    fb_state.setdefault("version", 1)
    fb_state.setdefault("clips", {})

    flip_root = os.path.join(project_root, COT_DIR_NAME, "flipbook")

    updated = 0
    skipped = 0
    errors = 0

    for i, src in enumerate(vids, 1):
        rel = _safe_relpath(src, project_root)
        rel_noext = os.path.splitext(rel)[0]
        dest = os.path.join(flip_root, rel_noext + "__flipbook.mp4")

        print(f"  [{i}/{len(vids)}] {rel} ...", end=" ")

        try:
            st = os.stat(src)
            src_mtime = int(st.st_mtime)
            src_size = int(st.st_size)
        except Exception:
            src_mtime = None
            src_size = None

        settings_key = {
            "out_sec": float(out_sec),
            "out_fps": float(out_fps),
            "width": int(width),
            "height": int(height),
        }

        prev = fb_state.get("clips", {}).get(rel)
        if (not overwrite) and os.path.isfile(dest) and isinstance(prev, dict):
            prev_settings = prev.get("settings") if isinstance(prev.get("settings"), dict) else {}
            same_settings = (
                float(prev_settings.get("out_sec", -1)) == float(out_sec)
                and float(prev_settings.get("out_fps", -1)) == float(out_fps)
                and int(prev_settings.get("width", -1)) == int(width)
                and int(prev_settings.get("height", -1)) == int(height)
            )
            same_src = (
                (prev.get("src_mtime") == src_mtime if src_mtime is not None else False)
                and (prev.get("src_size") == src_size if src_size is not None else False)
            )
            if same_settings and same_src:
                print("skipped (up-to-date)")
                skipped += 1
                continue

        if (not overwrite) and os.path.isfile(dest) and not isinstance(prev, dict):
            print("skipped (exists)")
            skipped += 1
            continue

        ok, msg = build_flipbook(
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
            src=src,
            dest=dest,
            out_sec=out_sec,
            out_fps=out_fps,
            width=width,
            height=height,
            overwrite=overwrite,
            dry_run=dry_run,
        )

        if ok:
            print("analyzed" if dry_run else "done")
            updated += 1
            fb_state["clips"][rel] = {
                "src": rel,
                "dest": _safe_relpath(dest, project_root),
                "src_mtime": src_mtime,
                "src_size": src_size,
                "settings": settings_key,
                "updated_at": _now_iso(),
                "note": msg,
            }
        else:
            print("ERROR")
            errors += 1
            fb_state["clips"][rel] = {
                "src": rel,
                "dest": _safe_relpath(dest, project_root),
                "src_mtime": src_mtime,
                "src_size": src_size,
                "settings": settings_key,
                "updated_at": _now_iso(),
                "error": msg,
            }

        # Write state incrementally so long runs don't lose work
        fb_state["updated_at"] = _now_iso()
        _save_flipbook_state(project_root, fb_state)

    if dry_run:
        print(
            f"\n  Dry-run analysis complete: {updated} analyzed, {skipped} skipped, {errors} errors.\n"
            f"  Output folder (would be): {flip_root}"
        )
    else:
        print(
            f"\n  Flipbook complete: {updated} processed, {skipped} skipped, {errors} errors.\n"
            f"  Output folder: {flip_root}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Flipbook Clips — generate flipbook-summary MP4s from clips (default 6s @ 24fps).",
    )
    parser.add_argument("--root", default=None, help="Root pictures folder (defaults to PICTURES_DIR from cot_config.json, else ~/Pictures)")
    parser.add_argument("--project", default=None, help="Project folder name under --root, or an absolute path to a project folder")
    parser.add_argument("--out-sec", type=float, default=6.0, help="Output duration per clip in seconds")
    parser.add_argument("--out-fps", type=float, default=24.0, help="Output playback FPS")
    parser.add_argument("--width", type=int, default=1920, help="Output width")
    parser.add_argument("--height", type=int, default=1080, help="Output height")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing flipbook MP4s")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done, but do not run ffmpeg")
    args = parser.parse_args()

    default_root = ""
    if cfg is not None:
        try:
            default_root = cfg.get("PICTURES_DIR", "")
        except Exception:
            default_root = ""
    if not default_root:
        default_root = os.path.join(os.path.expanduser("~"), "Pictures")

    root = args.root or default_root

    is_tty = False
    try:
        is_tty = bool(getattr(sys.stdin, "isatty", lambda: False)())
    except Exception:
        is_tty = False

    out_sec = float(args.out_sec)
    out_sec_was_set = "--out-sec" in sys.argv
    out_fps = float(args.out_fps)
    out_fps_was_set = "--out-fps" in sys.argv
    if is_tty and (not out_fps_was_set):
        out_fps = _prompt_float("\n  Enter output FPS", out_fps, min_value=1.0, max_value=60.0)
    if is_tty and (not out_sec_was_set):
        out_sec = _prompt_float("  Enter output length (seconds)", out_sec, min_value=1.0, max_value=60.0)

    if not os.path.isdir(root):
        print(f"\n  ERROR: Folder not found: {root}")
        raise SystemExit(1)

    if args.project:
        project = args.project
        if not (os.path.isabs(project) and os.path.isdir(project)):
            project = os.path.join(root, project)
        if not os.path.isdir(project):
            print(f"\n  ERROR: Project folder not found: {project}")
            raise SystemExit(1)
        process_project(
            project,
            out_sec=out_sec,
            out_fps=out_fps,
            width=args.width,
            height=args.height,
            overwrite=bool(args.overwrite),
            dry_run=bool(args.dry_run),
        )
        return

    if not is_tty:
        print("\n  ERROR: No TTY available for interactive prompts.")
        print("  Re-run with --project and optionally --root.")
        raise SystemExit(2)

    while True:
        project = _choose_project(root)
        if not project:
            print("\n  Cancelled.")
            return

        process_project(
            project,
            out_sec=out_sec,
            out_fps=out_fps,
            width=args.width,
            height=args.height,
            overwrite=bool(args.overwrite),
            dry_run=bool(args.dry_run),
        )

        again = input("\n  Run another project? (y/N): ").strip().lower()
        if again != "y":
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  Cancelled.")
        raise SystemExit(1)
