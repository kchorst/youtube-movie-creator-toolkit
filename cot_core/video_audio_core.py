from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}
OUTPUT_SUFFIX = "_with_audio"


def _load_config_value(key: str, default: str = "") -> str:
    try:
        import cot_config as cfg
        cfg.load(gui_mode=True)
        value = cfg.get(key, default)
        return str(value or default)
    except Exception:
        return default


def find_ffmpeg() -> str:
    configured = _load_config_value("FFMPEG", "")
    if configured and os.path.isfile(configured):
        return configured
    return configured or "ffmpeg"


def find_ffprobe() -> str:
    configured = _load_config_value("FFPROBE", "")
    if configured and os.path.isfile(configured):
        return configured
    ffmpeg = find_ffmpeg()
    if ffmpeg and ffmpeg.lower().endswith("ffmpeg.exe"):
        candidate = os.path.join(os.path.dirname(ffmpeg), "ffprobe.exe")
        if os.path.isfile(candidate):
            return candidate
    return configured or "ffprobe"


def is_audio_file(path: str | os.PathLike[str]) -> bool:
    return Path(path).suffix.lower() in AUDIO_EXTS and Path(path).is_file()


def is_video_file(path: str | os.PathLike[str]) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXTS and Path(path).is_file()


def list_audio_files(folder: str | os.PathLike[str], recursive: bool = False) -> list[str]:
    root = Path(folder)
    if not root.is_dir():
        return []
    iterator: Iterable[Path] = root.rglob("*") if recursive else root.iterdir()
    files = [str(p) for p in iterator if p.is_file() and p.suffix.lower() in AUDIO_EXTS]
    return sorted(files, key=lambda p: (Path(p).stem.lower(), Path(p).suffix.lower()))


def list_video_files(folder: str | os.PathLike[str], recursive: bool = False) -> list[str]:
    root = Path(folder)
    if not root.is_dir():
        return []
    iterator: Iterable[Path] = root.rglob("*") if recursive else root.iterdir()
    files = []
    for p in iterator:
        if not p.is_file() or p.suffix.lower() not in VIDEO_EXTS:
            continue
        stem = p.stem.lower()
        if stem.endswith("_with_audio") or stem.endswith("_music"):
            continue
        files.append(str(p))
    return sorted(files, key=lambda p: Path(p).name.lower())


def detect_audio_for_folder(folder: str | os.PathLike[str]) -> Optional[str]:
    files = list_audio_files(folder, recursive=False)
    return files[0] if files else None


def detect_audio_for_video(video_path: str | os.PathLike[str]) -> Optional[str]:
    video = Path(video_path)
    if not video.is_file():
        return None
    folder = video.parent
    stem = video.stem.lower()
    candidates = list_audio_files(folder, recursive=False)
    if not candidates:
        return None
    for ap in candidates:
        if Path(ap).stem.lower() == stem:
            return ap
    return candidates[0]


def default_output_path(video_path: str | os.PathLike[str], output_dir: str | os.PathLike[str] | None = None) -> str:
    video = Path(video_path)
    folder = Path(output_dir) if output_dir else video.parent
    folder.mkdir(parents=True, exist_ok=True)
    return str(folder / f"{video.stem}{OUTPUT_SUFFIX}.mp4")


def avoid_overwrite(path: str | os.PathLike[str]) -> str:
    p = Path(path)
    if not p.exists():
        return str(p)
    base = p.with_suffix("")
    suffix = p.suffix or ".mp4"
    i = 1
    while True:
        candidate = Path(f"{base}_{i}{suffix}")
        if not candidate.exists():
            return str(candidate)
        i += 1


def probe_duration_sec(path: str | os.PathLike[str], ffprobe: str | None = None) -> Optional[float]:
    ffprobe = ffprobe or find_ffprobe()
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
        if result.returncode != 0:
            return None
        token = (result.stdout or "").strip().split()[0]
        return float(token)
    except Exception:
        return None


def video_has_audio(path: str | os.PathLike[str], ffprobe: str | None = None) -> bool:
    ffprobe = ffprobe or find_ffprobe()
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=index",
        "-of",
        "csv=p=0",
        str(path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
        return result.returncode == 0 and bool((result.stdout or "").strip())
    except Exception:
        return False


@dataclass
class AddAudioResult:
    ok: bool
    output_path: str = ""
    error: str = ""
    command: list[str] | None = None


def add_audio_to_video(
    *,
    video_path: str,
    audio_path: str,
    output_path: str | None = None,
    output_dir: str | None = None,
    mode: str = "replace",
    fade_sec: float = 2.0,
    overwrite: bool = False,
    ffmpeg: str | None = None,
    ffprobe: str | None = None,
) -> AddAudioResult:
    """Add music/audio to an existing video without modifying the original.

    mode='replace' replaces/sets the output audio from the chosen track.
    mode='mix' mixes the chosen track with the video's existing audio when present,
    and falls back to replace when the video has no audio stream.
    """
    video_path = str(video_path or "").strip()
    audio_path = str(audio_path or "").strip()
    if not is_video_file(video_path):
        return AddAudioResult(False, error=f"Video not found or unsupported: {video_path}")
    if not is_audio_file(audio_path):
        return AddAudioResult(False, error=f"Audio not found or unsupported: {audio_path}")

    ffmpeg = ffmpeg or find_ffmpeg()
    ffprobe = ffprobe or find_ffprobe()
    if not ffmpeg:
        return AddAudioResult(False, error="FFmpeg is not configured.")

    if not output_path:
        output_path = default_output_path(video_path, output_dir)
    if not overwrite:
        output_path = avoid_overwrite(output_path)

    duration = probe_duration_sec(video_path, ffprobe=ffprobe)
    fade_sec = max(0.0, float(fade_sec or 0.0))
    fade_filter = ""
    if duration and fade_sec > 0:
        fade_start = max(0.0, float(duration) - fade_sec)
        fade_filter = f"afade=t=out:st={fade_start:.3f}:d={fade_sec:.3f}"

    requested_mix = (mode or "replace").strip().lower() == "mix"
    do_mix = requested_mix and video_has_audio(video_path, ffprobe=ffprobe)

    if do_mix:
        audio_chain = f"[1:a]{fade_filter},volume=0.65[music]" if fade_filter else "[1:a]volume=0.65[music]"
        filter_complex = f"{audio_chain};[0:a][music]amix=inputs=2:duration=first:dropout_transition=2[a]"
        cmd = [
            ffmpeg,
            "-y" if overwrite else "-n",
            "-i",
            video_path,
            "-stream_loop",
            "-1",
            "-i",
            audio_path,
            "-map",
            "0:v:0",
            "-filter_complex",
            filter_complex,
            "-map",
            "[a]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            output_path,
        ]
    else:
        cmd = [
            ffmpeg,
            "-y" if overwrite else "-n",
            "-i",
            video_path,
            "-stream_loop",
            "-1",
            "-i",
            audio_path,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
        ]
        if fade_filter:
            cmd += ["-af", fade_filter]
        cmd += ["-shortest", output_path]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "Unknown FFmpeg error").strip()
            return AddAudioResult(False, output_path=output_path, error=err[-2500:], command=cmd)
        return AddAudioResult(True, output_path=output_path, command=cmd)
    except Exception as exc:
        return AddAudioResult(False, output_path=output_path, error=str(exc), command=cmd)
