import os
import sys
import importlib
from typing import Callable, Optional


def run_batch_metadata(
    root: str,
    *,
    reload_module: bool = True,
    log_cb: Optional[Callable[[str], None]] = None,
) -> None:
    """Prompt-free entrypoint for Metadata Mode C (batch).

    This is a thin core wrapper intended for GUI/automation callers.
    """

    if not root or not os.path.isdir(root):
        raise FileNotFoundError(f"Root folder not found: {root}")

    # Ensure repo root is importable so we can import youtube_meta from scripts.
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    if log_cb is not None:
        log_cb(f"Mode C (batch) root: {root}")

    import youtube_meta

    if reload_module:
        importlib.reload(youtube_meta)

    youtube_meta.mode_batch(root, offer_next_step=False)


def run_refresh_thumbnails(
    root: str,
    *,
    reload_module: bool = True,
    log_cb: Optional[Callable[[str], None]] = None,
) -> None:
    """Prompt-free entrypoint to refresh thumbnail paths in the CSV.

    Scans each folder and updates only the CSV 'thumbnail' column.
    """

    if not root or not os.path.isdir(root):
        raise FileNotFoundError(f"Root folder not found: {root}")

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    if log_cb is not None:
        log_cb(f"Refresh thumbnails root: {root}")

    import youtube_meta
    if reload_module:
        importlib.reload(youtube_meta)

    youtube_meta.refresh_thumbnails(root, offer_next_step=False)
