import os
import sys
import re
import shutil
import math
import argparse
import json
import gc
import subprocess
import shutil
import time
import gc
import atexit
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import threading


import make_show as ms
from cot_core.integrity_utils import summarize_media_integrity

from cot_core.temp_utils import prune_old_subdirs
from cot_core.process_utils import terminate_process


COT_DIR_NAME = ".cot"
EXCLUDE_DIR_NAME = "Exclude"


def _cleanup_mixed_temps() -> None:
    try:
        temp_mixed_parent = os.path.join(ms.OUTPUT_DIR, "_temp_mixed")
        prune_old_subdirs(parent_dir=temp_mixed_parent, older_than_sec=24.0 * 3600.0)
    except Exception:
        pass
    try:
        gc.collect()
    except Exception:
        pass


atexit.register(_cleanup_mixed_temps)


class StopRequested(Exception):
    pass


def _wait_while_paused(
    *,
    stop_event: Optional[threading.Event],
    pause_event: Optional[threading.Event],
) -> None:
    if pause_event is None:
        return
    while pause_event.is_set():
        if stop_event is not None and stop_event.is_set():
            raise StopRequested()
        time.sleep(0.15)


def _run_ffmpeg_checked(
    cmd: List[str],
    *,
    stop_event: Optional[threading.Event] = None,
    pause_event: Optional[threading.Event] = None,
) -> subprocess.CompletedProcess:
    _wait_while_paused(stop_event=stop_event, pause_event=pause_event)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        **ms._NO_WINDOW_FLAGS,
    )

    while proc.poll() is None:
        if stop_event is not None and stop_event.is_set():
            try:
                terminate_process(proc)
            except Exception:
                pass
            raise StopRequested()
        time.sleep(0.15)

    out, err = proc.communicate()
    return subprocess.CompletedProcess(cmd, int(proc.returncode or 0), out, err)


@dataclass
class TimelineItem:
    kind: str  # 'image' | 'flipbook'
    abs_path: str
    rel_path: str
    ts: float
    ts_source: str
    duration_sec: float
    note: str = ""


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _safe_relpath(path: str, root: str) -> str:
    try:
        return os.path.relpath(path, root)
    except Exception:
        return path


def _norm_rel(p: str) -> str:
    return p.replace("/", "\\").strip().lower()


def _read_lines(path: str) -> List[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return [ln.strip() for ln in f.read().splitlines()]
    except Exception:
        return []


def _parse_android_ts_from_name(name: str) -> Optional[datetime]:
    base = os.path.splitext(os.path.basename(name))[0]
    base = re.sub(r"^(img|vid|pano|dsc)[_\- ]*", "", base, flags=re.IGNORECASE)

    m = re.search(r"(\d{8})[_\-]?(\d{6})", base)
    if not m:
        return None

    s = m.group(1) + m.group(2)
    try:
        return datetime.strptime(s, "%Y%m%d%H%M%S")
    except Exception:
        return None


def _ts_for_media(path: str) -> Tuple[float, str]:
    dt = _parse_android_ts_from_name(os.path.basename(path))
    if dt is not None:
        return dt.timestamp(), "filename"

    try:
        if path.lower().endswith((".jpg", ".jpeg")):
            return float(ms.get_image_date(path)), "exif_or_mtime"
    except Exception:
        pass

    try:
        return float(os.path.getmtime(path)), "mtime"
    except Exception:
        return 0.0, "unknown"


def _find_ffprobe() -> str:
    if ms.FFMPEG.lower().endswith("ffmpeg.exe"):
        p = os.path.join(os.path.dirname(ms.FFMPEG), "ffprobe.exe")
        if os.path.isfile(p):
            return p
    return "ffprobe"


def _probe_duration_sec(ffprobe: str, path: str) -> Optional[float]:
    for cmd in (
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", path],
    ):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, **ms._NO_WINDOW_FLAGS)
            if r.returncode != 0:
                continue
            token = (r.stdout or "").strip().split()[0] if (r.stdout or "").strip() else ""
            if not token:
                continue
            return float(token)
        except Exception:
            continue
    return None


def _load_flipbooks(project_root: str) -> List[Tuple[str, str, Optional[float]]]:
    fb_json = os.path.join(project_root, COT_DIR_NAME, "flipbook.json")
    flip_root = os.path.join(project_root, COT_DIR_NAME, "flipbook")

    out: List[Tuple[str, str, Optional[float]]] = []

    if os.path.isfile(fb_json):
        try:
            with open(fb_json, "r", encoding="utf-8") as f:
                st = json.load(f) or {}
            clips = st.get("clips") if isinstance(st, dict) else None
            if isinstance(clips, dict):
                for rel, meta in clips.items():
                    if not isinstance(meta, dict):
                        continue
                    dest_rel = meta.get("dest")
                    src_rel = meta.get("src")
                    out_sec = None
                    try:
                        settings = meta.get("settings") if isinstance(meta.get("settings"), dict) else {}
                        if isinstance(settings, dict) and ("out_sec" in settings):
                            out_sec = float(settings.get("out_sec"))
                    except Exception:
                        out_sec = None
                    if not dest_rel:
                        continue
                    dest_abs = os.path.join(project_root, str(dest_rel))
                    if os.path.isfile(dest_abs):
                        out.append((dest_abs, str(src_rel or ""), out_sec))
        except Exception:
            pass

    if out:
        return out

    if os.path.isdir(flip_root):
        for cur, dirnames, filenames in os.walk(flip_root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fn in filenames:
                if fn.lower().endswith(".mp4"):
                    out.append((os.path.join(cur, fn), "", None))

    out.sort(key=lambda t: os.path.basename(t[0]).lower())
    return out


def _collect_images(project_root: str) -> List[str]:
    images, _srcs = ms.get_image_files(project_root)
    # Filter out Exclude + .cot defensively
    filtered: List[str] = []
    for p in images:
        parts = {x.lower() for x in os.path.normpath(p).split(os.sep) if x}
        if EXCLUDE_DIR_NAME.lower() in parts or COT_DIR_NAME.lower() in parts:
            continue
        filtered.append(p)
    return filtered


def _load_excludes(project_root: str) -> set:
    p = os.path.join(project_root, COT_DIR_NAME, "show_exclude.txt")
    items = set()
    for ln in _read_lines(p):
        if not ln or ln.startswith("#"):
            continue
        items.add(_norm_rel(ln))
    return items


def _build_timeline(
    *,
    project_root: str,
    flipbook_window_min: int,
    prefer_window_min: int,
    flipbook_target_sec: float,
    out_fps: int,
) -> List[TimelineItem]:
    excludes = _load_excludes(project_root)

    images = _collect_images(project_root)
    ffprobe = _find_ffprobe()

    img_items: List[TimelineItem] = []
    for p in images:
        rel = _safe_relpath(p, project_root)
        if _norm_rel(rel) in excludes:
            continue
        ts, src = _ts_for_media(p)
        img_items.append(
            TimelineItem(
                kind="image",
                abs_path=p,
                rel_path=rel,
                ts=ts,
                ts_source=src,
                duration_sec=0.0,
            )
        )

    img_items.sort(key=lambda it: (it.ts, it.rel_path.lower()))

    flip_raw = _load_flipbooks(project_root)
    flip_items: List[TimelineItem] = []
    for fb_abs, src_rel, out_sec in flip_raw:
        rel = _safe_relpath(fb_abs, project_root)
        if _norm_rel(rel) in excludes:
            continue

        ts_path = fb_abs
        if src_rel:
            p = str(src_rel)
            if not os.path.isabs(p):
                p = os.path.join(project_root, p)
            if os.path.isfile(p):
                ts_path = p

        ts, src = _ts_for_media(ts_path)
        probed = _probe_duration_sec(ffprobe, fb_abs)
        src_dur = float(probed) if probed is not None else None

        max_sec = float(flipbook_target_sec)
        try:
            if out_sec is not None and float(out_sec) > 0.1:
                max_sec = float(out_sec)
        except Exception:
            pass

        used_sec = max_sec
        if src_dur is not None:
            used_sec = min(float(src_dur), float(max_sec))
        flip_items.append(
            TimelineItem(
                kind="flipbook",
                abs_path=fb_abs,
                rel_path=rel,
                ts=ts,
                ts_source="filename" if src == "filename" else "mtime",
                duration_sec=float(used_sec),
                note=f"src_dur={src_dur:.3f}" if src_dur is not None else "",
            )
        )

    flip_items.sort(key=lambda it: (it.ts, it.rel_path.lower()))

    max_win = flipbook_window_min * 60.0
    prefer_win = max(0.0, float(prefer_window_min) * 60.0)

    out: List[TimelineItem] = []
    fi = 0

    def _parse_src_dur(note: str) -> Optional[float]:
        if not note:
            return None
        m = re.search(r"src_dur=(\d+\.?\d*)", note)
        if not m:
            return None
        try:
            return float(m.group(1))
        except Exception:
            return None

    def _choose_candidate(cands: List[TimelineItem], img_ts: float) -> TimelineItem:
        best = cands[0]
        best_delta = abs(best.ts - img_ts)
        best_dur = _parse_src_dur(best.note)

        for c in cands[1:]:
            d = abs(c.ts - img_ts)
            if d < best_delta:
                best = c
                best_delta = d
                best_dur = _parse_src_dur(c.note)
                continue
            if d == best_delta:
                c_dur = _parse_src_dur(c.note)
                if (c_dur is not None) and (best_dur is not None):
                    if c_dur > best_dur:
                        best = c
                        best_dur = c_dur
                elif (c_dur is not None) and (best_dur is None):
                    best = c
                    best_dur = c_dur
        return best

    for img in img_items:
        # Emit any flipbooks that should occur before/at this image position.
        while fi < len(flip_items) and flip_items[fi].ts <= img.ts:
            # Gather eligible flipbooks up to current image timestamp
            cands: List[TimelineItem] = []
            fj = fi
            while fj < len(flip_items) and flip_items[fj].ts <= img.ts:
                if abs(img.ts - flip_items[fj].ts) <= max_win:
                    cands.append(flip_items[fj])
                fj += 1

            if not cands:
                fi = fj
                continue

            within_prefer = [c for c in cands if abs(img.ts - c.ts) <= prefer_win] if prefer_win > 0 else []
            pick_from = within_prefer or cands
            chosen = _choose_candidate(pick_from, img.ts)
            out.append(chosen)
            # Remove chosen and continue (allows back-to-back flipbooks)
            flip_items = [x for x in flip_items if x is not chosen]
            # Reset fi to current position after removal
            fi = 0

        out.append(img)

    # Add remaining flipbooks that are close to the last image
    if img_items:
        last_ts = img_items[-1].ts
        remaining = [fb for fb in flip_items if abs(fb.ts - last_ts) <= max_win]
        remaining.sort(key=lambda it: abs(it.ts - last_ts))
        out.extend(remaining)

    return out


def _write_last_run(project_root: str, items: List[TimelineItem], settings: Dict) -> str:
    cot_dir = os.path.join(project_root, COT_DIR_NAME)
    os.makedirs(cot_dir, exist_ok=True)
    out_path = os.path.join(cot_dir, "show_last_run.json")

    payload = {
        "updated_at": _now_iso(),
        "settings": settings,
        "items": [
            {
                "kind": it.kind,
                "rel_path": it.rel_path,
                "ts": it.ts,
                "ts_source": it.ts_source,
                "duration_sec": it.duration_sec,
                "note": it.note,
            }
            for it in items
        ],
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    return out_path


def _update_last_run_output(
    *,
    last_run_path: str,
    output_path: str,
    ffprobe: str,
) -> None:
    try:
        from typing import Any
        with open(last_run_path, "r", encoding="utf-8") as f:
            payload: Any = json.load(f)
    except Exception:
        payload = {}

    if not isinstance(payload, dict):
        payload = {}

    try:
        payload["output"] = {
            "path": output_path,
            "integrity": summarize_media_integrity(path=output_path, ffprobe=ffprobe),
        }
        payload["updated_at"] = _now_iso()
        with open(last_run_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except Exception:
        pass


def _parse_num_ranges(text: str) -> List[int]:
    nums: List[int] = []
    for tok in text.replace(",", " ").split():
        tok = tok.strip()
        if not tok:
            continue
        if "-" in tok:
            a, b = tok.split("-", 1)
            if a.strip().isdigit() and b.strip().isdigit():
                start = int(a)
                end = int(b)
                if start > end:
                    start, end = end, start
                nums.extend(list(range(start, end + 1)))
            continue
        if tok.isdigit():
            nums.append(int(tok))

    seen = set()
    out: List[int] = []
    for n in nums:
        if n in seen:
            continue
        seen.add(n)
        out.append(n)
    return out


def _append_excludes_from_last_run(project_root: str, sel: str) -> None:
    last = os.path.join(project_root, COT_DIR_NAME, "show_last_run.json")
    if not os.path.isfile(last):
        print("\n  ERROR: No last-run file found. Run the mixed show once first.")
        raise SystemExit(2)

    with open(last, "r", encoding="utf-8") as f:
        st = json.load(f) or {}

    items = st.get("items") if isinstance(st, dict) else None
    if not isinstance(items, list) or not items:
        print("\n  ERROR: Last-run file has no items.")
        raise SystemExit(2)

    nums = _parse_num_ranges(sel)
    if not nums:
        print("\n  ERROR: No valid indices.")
        raise SystemExit(2)

    exclude_path = os.path.join(project_root, COT_DIR_NAME, "show_exclude.txt")
    existing = set(_read_lines(exclude_path))

    added = 0
    for n in nums:
        if not (1 <= n <= len(items)):
            continue
        rel = str(items[n - 1].get("rel_path") or "").strip()
        if not rel:
            continue
        if rel in existing:
            continue
        existing.add(rel)
        added += 1

    os.makedirs(os.path.dirname(exclude_path), exist_ok=True)
    with open(exclude_path, "w", encoding="utf-8") as f:
        for ln in sorted({ln.strip() for ln in existing if ln.strip()}, key=lambda s: s.lower()):
            f.write(ln + "\n")

    print(f"\n  Added {added} exclude(s). File: {exclude_path}")


def _open_from_last_run(project_root: str, sel: str) -> None:
    last = os.path.join(project_root, COT_DIR_NAME, "show_last_run.json")
    if not os.path.isfile(last):
        print("\n  ERROR: No last-run file found. Run the mixed show once first.")
        raise SystemExit(2)

    with open(last, "r", encoding="utf-8") as f:
        st = json.load(f) or {}

    items = st.get("items") if isinstance(st, dict) else None
    if not isinstance(items, list) or not items:
        print("\n  ERROR: Last-run file has no items.")
        raise SystemExit(2)

    nums = _parse_num_ranges(sel)
    if not nums:
        print("\n  ERROR: No valid indices.")
        raise SystemExit(2)

    for n in nums:
        if not (1 <= n <= len(items)):
            continue
        rel = str(items[n - 1].get("rel_path") or "").strip()
        if not rel:
            continue
        abs_path = os.path.join(project_root, rel)
        try:
            os.startfile(abs_path)  # type: ignore[attr-defined]
        except Exception as e:
            print(f"  Could not open {rel}: {e}")


def _build_image_segment(
    *,
    images: List[str],
    out_path: str,
    frames_per_image: int,
    fps: int,
    final_hold_frames: int,
    final_fade_frames: int,
    is_final_segment: bool,
    stop_event: Optional[threading.Event] = None,
    pause_event: Optional[threading.Event] = None,
) -> Tuple[bool, float, float]:
    if not images:
        return False, 0.0, 0.0

    frames_written = 0
    final_start_sec = 0.0

    cmd = [
        ms.FFMPEG,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostats",
        "-y",
        "-f",
        "rawvideo",
        "-vcodec",
        "rawvideo",
        "-s",
        f"{ms.WIDTH}x{ms.HEIGHT}",
        "-pix_fmt",
        "rgb24",
        "-r",
        str(fps),
        "-i",
        "pipe:0",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-shortest",
        "-c:v",
        "libx264",
        "-preset",
        ms.PRESET,
        "-crf",
        ms.CRF,
        "-pix_fmt",
        ms.PIX_FMT,
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        out_path,
    ]

    proc = None
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=10 * 1024 * 1024,
            **ms._NO_WINDOW_FLAGS,
        )

        def _terminate_ffmpeg() -> None:
            if proc is None:
                return
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=2.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=2.0)
                except Exception:
                    pass

        for idx, p in enumerate(images):
            if stop_event is not None and stop_event.is_set():
                _terminate_ffmpeg()
                raise StopRequested()

            if pause_event is not None:
                while pause_event.is_set():
                    if stop_event is not None and stop_event.is_set():
                        _terminate_ffmpeg()
                        raise StopRequested()
                    time.sleep(0.15)

            if (idx % 25) == 0 or idx == (len(images) - 1):
                print(f"    [{idx + 1}/{len(images)}] images", flush=True)
            arr = ms.prepare_frame(p)
            if arr is None:
                continue

            raw = arr.tobytes()
            is_last = (idx == len(images) - 1)
            if is_last and is_final_segment:
                final_start_sec = frames_written / fps

            reps = frames_per_image
            if is_last and is_final_segment:
                reps = frames_per_image + final_hold_frames

            for _ in range(reps):
                if stop_event is not None and stop_event.is_set():
                    _terminate_ffmpeg()
                    raise StopRequested()

                if pause_event is not None:
                    while pause_event.is_set():
                        if stop_event is not None and stop_event.is_set():
                            _terminate_ffmpeg()
                            raise StopRequested()
                        time.sleep(0.15)

                proc.stdin.write(raw)
                frames_written += 1

            del raw

            if is_last and is_final_segment and final_fade_frames > 0:
                for faded_bytes in ms.make_fade_frames(arr, final_fade_frames):
                    if stop_event is not None and stop_event.is_set():
                        _terminate_ffmpeg()
                        raise StopRequested()

                    if pause_event is not None:
                        while pause_event.is_set():
                            if stop_event is not None and stop_event.is_set():
                                _terminate_ffmpeg()
                                raise StopRequested()
                            time.sleep(0.15)

                    proc.stdin.write(faded_bytes)
                    frames_written += 1

            del arr
            if idx % int(getattr(ms, "GC_INTERVAL", 50)) == 0:
                gc.collect()

        proc.stdin.close()
        stderr = proc.stderr.read().decode(errors="replace")
        proc.wait()
        if proc.returncode != 0:
            print("\n  FFmpeg segment error:")
            print(stderr[-2000:])
            return False, 0.0, 0.0

    except StopRequested:
        raise
    except Exception as e:
        try:
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass
        except Exception:
            pass
        print(f"\n  Segment exception: {e}")
        return False, 0.0, 0.0

    dur = frames_written / fps
    return True, dur, final_start_sec


def _build_flipbook_segment(
    *,
    src_path: str,
    out_path: str,
    fps: int,
    target_sec: float,
    stop_event: Optional[threading.Event] = None,
    pause_event: Optional[threading.Event] = None,
) -> bool:
    vf = (
        f"fps={fps},"
        f"scale={ms.WIDTH}:{ms.HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={ms.WIDTH}:{ms.HEIGHT}:(ow-iw)/2:(oh-ih)/2,"
        "format=yuv420p"
    )

    cmd = [
        ms.FFMPEG,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostats",
        "-y",
        "-i",
        src_path,
        "-t",
        f"{target_sec:.6f}",
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        ms.PRESET,
        "-crf",
        ms.CRF,
        "-pix_fmt",
        ms.PIX_FMT,
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        out_path,
    ]

    r = _run_ffmpeg_checked(cmd, stop_event=stop_event, pause_event=pause_event)
    if r.returncode != 0:
        print("\n  FFmpeg flipbook segment error:")
        print((r.stderr or "")[-2000:])
        return False
    return True


def _concat_segments(
    segments: List[str],
    out_path: str,
    *,
    stop_event: Optional[threading.Event] = None,
    pause_event: Optional[threading.Event] = None,
) -> bool:
    list_path = os.path.join(os.path.dirname(out_path), "_concat_list.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for p in segments:
            safe = p.replace("'", "''")
            f.write(f"file '{safe}'\n")

    cmd = [
        ms.FFMPEG,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostats",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        list_path,
        "-c",
        "copy",
        out_path,
    ]

    r = _run_ffmpeg_checked(cmd, stop_event=stop_event, pause_event=pause_event)
    if r.returncode == 0:
        try:
            os.remove(list_path)
        except Exception:
            pass
        return True

    # Fallback to re-encode if stream copy fails
    cmd = [
        ms.FFMPEG,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostats",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        list_path,
        "-c:v",
        "libx264",
        "-preset",
        ms.PRESET,
        "-crf",
        ms.CRF,
        "-pix_fmt",
        ms.PIX_FMT,
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        out_path,
    ]

    r2 = _run_ffmpeg_checked(cmd, stop_event=stop_event, pause_event=pause_event)
    if r2.returncode != 0:
        print("\n  FFmpeg concat error:")
        print((r2.stderr or "")[-2000:])
        return False
    try:
        os.remove(list_path)
    except Exception:
        pass
    return True


def _add_audio_duck(
    *,
    video_path: str,
    audio_path: str,
    total_duration: float,
    final_image_start: float,
    audio_fade_sec: float,
    duck_db: float,
    output_path: str,
    stop_event: Optional[threading.Event] = None,
    pause_event: Optional[threading.Event] = None,
) -> bool:
    fade_start = max(0.0, final_image_start - audio_fade_sec)
    duck_gain = math.pow(10.0, duck_db / 20.0)

    fc = (
        f"[0:a]volume={duck_gain:.6f}[fb];"
        f"[1:a]atrim=0:{total_duration:.6f},asetpts=N/SR/TB[m];"
        f"[m][fb]amix=inputs=2:duration=first:dropout_transition=0[mix];"
        f"[mix]afade=t=out:st={fade_start:.3f}:d={audio_fade_sec:.3f}[aout]"
    )

    cmd = [
        ms.FFMPEG,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostats",
        "-y",
        "-i",
        video_path,
        "-stream_loop",
        "-1",
        "-i",
        audio_path,
        "-filter_complex",
        fc,
        "-map",
        "0:v:0",
        "-map",
        "[aout]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-t",
        f"{total_duration:.6f}",
        "-threads",
        ms.THREADS,
        output_path,
    ]

    r = _run_ffmpeg_checked(cmd, stop_event=stop_event, pause_event=pause_event)
    if r.returncode != 0:
        print("\n  FFmpeg audio mix error:")
        print((r.stderr or "")[-2000:])
        return False

    return True


def run_show(
    *,
    project_root: str,
    bpm: int,
    frames_per_image: int,
    final_hold_frames: int,
    final_fade_frames: int,
    audio_fade_sec: float,
    flipbook_window_min: int,
    prefer_window_min: int,
    flipbook_sec: float,
    output_fps: int,
    duck_db: float,
    audio_path: Optional[str],
    dry_run: bool,
    include_flipbooks: bool = True,
    stop_event: Optional[threading.Event] = None,
    pause_event: Optional[threading.Event] = None,
) -> None:
    settings = {
        "bpm": bpm,
        "output_fps": output_fps,
        "frames_per_image": frames_per_image,
        "flipbook_window_min": flipbook_window_min,
        "prefer_window_min": prefer_window_min,
        "flipbook_sec": flipbook_sec,
        "duck_db": duck_db,
        "dry_run": dry_run,
    }

    timeline = _build_timeline(
        project_root=project_root,
        flipbook_window_min=flipbook_window_min,
        prefer_window_min=prefer_window_min,
        flipbook_target_sec=flipbook_sec,
        out_fps=output_fps,
    )
    if not include_flipbooks:
        timeline = [it for it in timeline if it.kind == "image"]

    last_run_path = _write_last_run(project_root, timeline, settings)

    print("\n  Mixed Media Show")
    print(f"  Project: {project_root}")
    print(f"  Timeline items: {len(timeline)}")
    print(f"  Last-run report: {last_run_path}")

    img_ct = sum(1 for it in timeline if it.kind == "image")
    fb_ct = sum(1 for it in timeline if it.kind == "flipbook")
    print(f"  Images: {img_ct}")
    print(f"  Flipbooks: {fb_ct}")

    if dry_run:
        print("\n  DRY RUN — no movie will be rendered.")
        print("\n  Items:")
        for i, it in enumerate(timeline[:120], 1):
            stamp = datetime.fromtimestamp(it.ts).strftime("%Y-%m-%d %H:%M:%S") if it.ts else "?"
            print(f"  {i:>4}. {it.kind[:3].upper()}  {stamp}  {it.rel_path}")
        if len(timeline) > 120:
            print(f"\n  ... showing first 120 of {len(timeline)}")
        return

    folder_name = os.path.basename(os.path.normpath(project_root))
    out_base = os.path.join(ms.OUTPUT_DIR, folder_name + "_mixed.mp4")
    os.makedirs(ms.OUTPUT_DIR, exist_ok=True)

    temp_root = os.path.join(ms.OUTPUT_DIR, "_temp_mixed", folder_name + "_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    os.makedirs(temp_root, exist_ok=True)

    try:
        segments: List[str] = []
        pending_images: List[str] = []

        total_duration = 0.0
        final_image_start_global = 0.0

        def _flush_images(is_final: bool) -> None:
            nonlocal total_duration, final_image_start_global
            if not pending_images:
                return

            if stop_event is not None and stop_event.is_set():
                raise StopRequested()

            print(f"\n  Encoding images: {len(pending_images)}", flush=True)

            seg_path = os.path.join(temp_root, f"seg_img_{len(segments)+1:04d}.mp4")
            ok, dur, final_start = _build_image_segment(
                images=list(pending_images),
                out_path=seg_path,
                frames_per_image=frames_per_image,
                fps=output_fps,
                final_hold_frames=final_hold_frames,
                final_fade_frames=final_fade_frames,
                is_final_segment=is_final,
                stop_event=stop_event,
                pause_event=pause_event,
            )
            if not ok:
                raise RuntimeError("failed to build image segment")

            segments.append(seg_path)
            if is_final:
                final_image_start_global = total_duration + final_start
            total_duration += dur
            pending_images.clear()

        for it in timeline:
            if stop_event is not None and stop_event.is_set():
                raise StopRequested()
            if pause_event is not None:
                while pause_event.is_set():
                    if stop_event is not None and stop_event.is_set():
                        raise StopRequested()
                    time.sleep(0.15)

            if it.kind == "image":
                pending_images.append(it.abs_path)
                continue

            _flush_images(is_final=False)

            if stop_event is not None and stop_event.is_set():
                raise StopRequested()

            print(f"\n  Encoding flipbook: {it.rel_path}", flush=True)
            seg_path = os.path.join(temp_root, f"seg_vid_{len(segments)+1:04d}.mp4")
            fb_sec = float(it.duration_sec) if float(it.duration_sec) > 0.0 else float(flipbook_sec)
            ok = _build_flipbook_segment(
                src_path=it.abs_path,
                out_path=seg_path,
                fps=output_fps,
                target_sec=fb_sec,
                stop_event=stop_event,
                pause_event=pause_event,
            )
            if not ok:
                raise RuntimeError("failed to build flipbook segment")
            segments.append(seg_path)
            total_duration += fb_sec

        _flush_images(is_final=True)

        concat_out = os.path.join(temp_root, "_concat.mp4")
        print(f"\n  Concatenating {len(segments)} segments...", flush=True)
        if stop_event is not None and stop_event.is_set():
            raise StopRequested()
        if not _concat_segments(segments, concat_out, stop_event=stop_event, pause_event=pause_event):
            raise RuntimeError("failed to concat segments")

        ffprobe = _find_ffprobe()
        dur = _probe_duration_sec(ffprobe, concat_out)
        if dur is not None:
            total_duration = dur

        if not audio_path:
            shutil_out = out_base
            os.replace(concat_out, shutil_out)
            print(f"\n  Saved: {shutil_out}")
            try:
                _update_last_run_output(last_run_path=last_run_path, output_path=shutil_out, ffprobe=ffprobe)
            except Exception:
                pass
            return

        out_with_audio = out_base
        if stop_event is not None and stop_event.is_set():
            raise StopRequested()
        ok = _add_audio_duck(
            video_path=concat_out,
            audio_path=audio_path,
            total_duration=total_duration,
            final_image_start=final_image_start_global,
            audio_fade_sec=audio_fade_sec,
            duck_db=duck_db,
            output_path=out_with_audio,
            stop_event=stop_event,
            pause_event=pause_event,
        )
        if not ok:
            raise RuntimeError("failed to add audio")

        print(f"\n  Saved: {out_with_audio}")
        try:
            _update_last_run_output(last_run_path=last_run_path, output_path=out_with_audio, ffprobe=ffprobe)
        except Exception:
            pass
    finally:
        try:
            shutil.rmtree(temp_root, ignore_errors=True)
        except Exception:
            pass
        try:
            temp_parent = os.path.dirname(temp_root)
            if os.path.isdir(temp_parent) and not os.listdir(temp_parent):
                os.rmdir(temp_parent)
        except Exception:
            pass
        gc.collect()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True, help="Absolute path to a project folder")
    parser.add_argument("--bpm", type=int, default=120)
    parser.add_argument("--flipbook-window-min", type=int, default=10)
    parser.add_argument("--prefer-window-min", type=int, default=2)
    parser.add_argument("--flipbook-sec", type=float, default=6.0)
    parser.add_argument("--output-fps", type=int, default=30)
    parser.add_argument("--duck-db", type=float, default=-18.0)
    parser.add_argument("--audio", default=None, help="Audio track path (mp3/wav)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-flipbooks", action="store_true", help="Render images only (ignore .cot flipbooks)")

    parser.add_argument("--exclude", default=None, help="Append exclude(s) by last-run indices/ranges (e.g. 3 or 10-20)")
    parser.add_argument("--open", default=None, help="Open item(s) by last-run indices/ranges")

    args = parser.parse_args()

    project_root = os.path.abspath(args.project)
    if not os.path.isdir(project_root):
        print(f"\n  ERROR: Project not found: {project_root}")
        raise SystemExit(1)

    if args.exclude:
        _append_excludes_from_last_run(project_root, str(args.exclude))
        return

    if args.open:
        _open_from_last_run(project_root, str(args.open))
        return

    bpm = int(args.bpm)
    sec_per_image = 60.0 / bpm
    frames_per_image = round(sec_per_image * int(args.output_fps))
    audio_fade_sec = 4 * sec_per_image

    # Use make_show's final settings defaults
    final_hold_frames = round(2.0 * int(args.output_fps))
    final_fade_frames = round(2.0 * int(args.output_fps))

    run_show(
        project_root=project_root,
        bpm=bpm,
        frames_per_image=frames_per_image,
        final_hold_frames=final_hold_frames,
        final_fade_frames=final_fade_frames,
        audio_fade_sec=audio_fade_sec,
        flipbook_window_min=int(args.flipbook_window_min),
        prefer_window_min=int(args.prefer_window_min),
        flipbook_sec=float(args.flipbook_sec),
        output_fps=int(args.output_fps),
        duck_db=float(args.duck_db),
        audio_path=str(args.audio) if args.audio else None,
        dry_run=bool(args.dry_run),
        include_flipbooks=(not bool(args.no_flipbooks)),
    )


if __name__ == "__main__":
    main()
