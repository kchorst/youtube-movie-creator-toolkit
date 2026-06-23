import os
import sys
import importlib
from typing import Callable, Optional


def _ensure_repo_root_on_path() -> str:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    return repo_root


def show_status(*, reload_module: bool = True, log_cb: Optional[Callable[[str], None]] = None) -> None:
    _ensure_repo_root_on_path()

    if log_cb is not None:
        log_cb("Loading upload status...")

    import youtube_upload
    if reload_module:
        importlib.reload(youtube_upload)

    youtube_upload.show_status()


def show_quota_status(*, reload_module: bool = True, log_cb: Optional[Callable[[str], None]] = None) -> None:
    _ensure_repo_root_on_path()

    if log_cb is not None:
        log_cb("Loading quota status...")

    import youtube_upload
    if reload_module:
        importlib.reload(youtube_upload)

    youtube_upload.show_quota_status()


def run_uploads(
    *,
    dry_run: bool = False,
    reload_module: bool = True,
    log_cb: Optional[Callable[[str], None]] = None,
) -> None:
    _ensure_repo_root_on_path()

    if log_cb is not None:
        log_cb("Starting uploads..." if not dry_run else "Starting dry run uploads...")

    import youtube_upload
    if reload_module:
        importlib.reload(youtube_upload)

    youtube_upload.run_uploads(dry_run=dry_run)
