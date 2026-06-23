"""Path/config helpers for the YouTube Video Toolkit.

This module keeps path defaults portable and gives the launcher one place to
find optional sister apps such as Audio Prep Suite.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Iterable, Optional


def app_dir() -> str:
    return str(Path(__file__).resolve().parents[1])


def user_home() -> str:
    return str(Path.home())


def first_existing(paths: Iterable[str]) -> str:
    for p in paths:
        if p and os.path.exists(os.path.expanduser(p)):
            return os.path.abspath(os.path.expanduser(p))
    return ""


def default_pictures_dir() -> str:
    return first_existing([
        os.path.join(user_home(), "Pictures"),
        os.path.join(user_home(), "My Pictures"),
        user_home(),
    ])


def default_output_dir(pictures_dir: str | None = None) -> str:
    root = pictures_dir or default_pictures_dir() or user_home()
    return os.path.join(root, "YouTubeVideos")


def default_audio_dir() -> str:
    return first_existing([
        os.path.join(user_home(), "Music", "VideoToolkitAudio"),
        os.path.join(user_home(), "Music"),
        user_home(),
    ])


def find_executable(name: str, configured: str = "") -> str:
    """Return configured path if valid, otherwise PATH lookup, otherwise name."""
    configured = (configured or "").strip().strip('"')
    if configured and os.path.isfile(configured):
        return configured
    found = shutil.which(name)
    return found or name


def find_ffmpeg(configured: str = "") -> str:
    return find_executable("ffmpeg", configured)


def find_ffprobe(configured: str = "") -> str:
    return find_executable("ffprobe", configured)


def audio_prep_launcher_candidates(path: str | os.PathLike[str]) -> list[Path]:
    root = Path(path)
    return [
        root / "audio_prep_suite.py",
        root / "main.py",
        root / "launcher.py",
        root / "Audio Prep Suite.exe",
        root / "AudioPrepSuite.exe",
        root / "audio_prep_suite.bat",
        root / "launch_audio_prep.bat",
    ]


def find_audio_prep_launcher(path: str | os.PathLike[str]) -> str:
    root = Path(path)
    if not root.is_dir():
        return ""
    for candidate in audio_prep_launcher_candidates(root):
        if candidate.is_file():
            return str(candidate.resolve())
    return ""


def looks_like_audio_prep_suite(path: str | os.PathLike[str]) -> bool:
    root = Path(path)
    if not root.is_dir():
        return False
    # Accept a real app launcher first. This keeps YouTube Toolkit from
    # exposing Audio Prep's internal tools/dependencies directly.
    if find_audio_prep_launcher(root):
        return True
    markers = [
        root / "pipeline" / "full_prep_gui.py",
        root / "bpm_tool" / "bpm_gui.py",
        root / "converters" / "wav_to_mp3.py",
        root / "trimmers" / "trim_silence.py",
        root / "key_detection" / "key_gui.py",
    ]
    return any(p.is_file() for p in markers)


def audio_prep_status(path: str | os.PathLike[str]) -> dict:
    root = Path(path) if path else Path("__missing__")
    found = root.is_dir() and looks_like_audio_prep_suite(root)
    launcher = find_audio_prep_launcher(root) if root.is_dir() else ""
    return {
        "found": bool(found),
        "configured": bool(path),
        "runnable": bool(launcher),
        "path": str(root.resolve()) if root.is_dir() else str(path or ""),
        "launcher": launcher,
        "repair": "" if launcher else "Select the Audio Prep Suite app folder. It should contain main.py, launcher.py, audio_prep_suite.py, audio_prep_suite.bat, or an app executable.",
    }


def discover_audio_prep_suite(start_dir: str | None = None) -> str:
    """Search likely sibling/nearby directories for Audio Prep Suite."""
    here = Path(start_dir or app_dir()).resolve()
    candidates: list[Path] = []

    # Current folder, siblings, parent, and common naming variations.
    roots = [here, here.parent]
    for base in roots:
        if not base.exists():
            continue
        candidates.append(base)
        try:
            candidates.extend([p for p in base.iterdir() if p.is_dir()])
        except Exception:
            pass

    likely_names = [
        "audio-prep-suite",
        "Audio Prep Suite",
        "audio_prep_suite",
        "audio prep suite",
        "AudioPrepSuite",
    ]
    for base in roots:
        for name in likely_names:
            candidates.append(base / name)

    seen: set[str] = set()
    for p in candidates:
        try:
            key = str(p.resolve()).lower()
        except Exception:
            key = str(p).lower()
        if key in seen:
            continue
        seen.add(key)
        if looks_like_audio_prep_suite(p):
            return str(p.resolve())
    return ""
