import os
import sys
import importlib
from typing import Callable, Optional


def _ensure_repo_root_on_path() -> str:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    return repo_root


def run_analytics(*, reload_module: bool = True, log_cb: Optional[Callable[[str], None]] = None) -> None:
    """Prompt-free entrypoint to pull analytics (same behavior as cot_analytics.run_analytics())."""
    _ensure_repo_root_on_path()

    if log_cb is not None:
        log_cb("Running analytics...")

    import cot_analytics
    if reload_module:
        importlib.reload(cot_analytics)

    cot_analytics.run_analytics()


def show_leaderboard(*, reload_module: bool = True, log_cb: Optional[Callable[[str], None]] = None) -> None:
    """Prompt-free entrypoint to show leaderboard from existing analytics.csv."""
    _ensure_repo_root_on_path()

    if log_cb is not None:
        log_cb("Loading analytics leaderboard...")

    import cot_analytics
    if reload_module:
        importlib.reload(cot_analytics)

    csv_path = cot_analytics.ANALYTICS_CSV()
    if not os.path.isfile(csv_path):
        raise FileNotFoundError(f"No analytics.csv found at: {csv_path}")

    sorted_rows = cot_analytics.load_analytics_csv()
    if not sorted_rows:
        return

    cot_analytics.print_leaderboard(sorted_rows)
