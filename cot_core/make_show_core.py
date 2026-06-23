from __future__ import annotations

from typing import Callable, Optional


def run_batch_silent(
    *,
    root: str,
    bpm: int,
    frames_hold: int = 0,
    frames_fade: int = 0,
    audio_fade_sec: float = 2.0,
    skip_done: bool = True,
    width: Optional[int] = None,
    height: Optional[int] = None,
    stop_event=None,
    pause_event=None,
    log_cb: Optional[Callable[[str], None]] = None,
) -> None:
    if log_cb:
        log_cb("Loading make_show...")

    import make_show

    try:
        if width is not None and height is not None:
            make_show.WIDTH = int(width)
            make_show.HEIGHT = int(height)
    except Exception:
        pass

    try:
        import cot_config as cfg
        cfg.load(gui_mode=True)
    except Exception:
        cfg = None

    subfolders = make_show.get_subfolders(root)
    if not subfolders:
        # Support both batch roots and a single project folder containing images.
        try:
            if make_show.count_images(root) > 0:
                subfolders = [root]
        except Exception:
            pass
    if not subfolders:
        raise RuntimeError(f"No image folders found in: {root}")

    fps = make_show.FPS
    frames_per_image = round((60.0 / float(bpm)) * fps)

    if frames_hold <= 0:
        hold_sec = None
        if cfg is not None:
            try:
                hold_sec = float(cfg.get("MAKE_SHOW_FINAL_HOLD_SEC", 2.0))
            except Exception:
                hold_sec = None
        frames_hold = round((hold_sec if hold_sec is not None else 2.0) * fps)
    if frames_fade <= 0:
        fade_sec = None
        if cfg is not None:
            try:
                fade_sec = float(cfg.get("MAKE_SHOW_FINAL_FADE_SEC", 2.0))
            except Exception:
                fade_sec = None
        frames_fade = round((fade_sec if fade_sec is not None else 2.0) * fps)

    if audio_fade_sec <= 0:
        a_sec = None
        if cfg is not None:
            try:
                a_sec = float(cfg.get("MAKE_SHOW_AUDIO_FADE_SEC", 2.0))
            except Exception:
                a_sec = None
        audio_fade_sec = a_sec if a_sec is not None else 2.0

    if log_cb:
        log_cb(f"Found {len(subfolders)} subfolder(s)")
        log_cb(f"BPM={bpm} FPS={fps} frames_per_image={frames_per_image}")

    make_show.mode_batch_silent(
        subfolders,
        frames_per_image,
        frames_hold,
        frames_fade,
        audio_fade_sec,
        interactive=False,
        skip_done_default=skip_done,
        stop_event=stop_event,
        pause_event=pause_event,
    )
