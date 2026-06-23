import os
import sys
import time
import traceback
from typing import Optional, Callable

from cot_core.logging_utils import log_exception
from cot_core.last_run_utils import write_json_atomic


def _now_stamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def install_global_crash_handler(
    *,
    tool: str,
    out_dir: str,
    log_path: Optional[str] = None,
    on_fatal_message: Optional[Callable[[str], None]] = None,
) -> None:
    """Install global exception hooks.

    - sys.excepthook: uncaught exceptions on main thread
    - threading.excepthook (py>=3.8): uncaught exceptions in threads

    Writes a JSON crash artifact + traceback to disk.
    """

    os.makedirs(out_dir, exist_ok=True)

    def _write(exc_type, exc, tb, *, where: str) -> None:
        try:
            ts = _now_stamp()
            artifact_path = os.path.join(out_dir, f"crash_{tool}_{where}_{ts}.json")
            tb_str = "".join(traceback.format_exception(exc_type, exc, tb))
            payload = {
                "tool": tool,
                "where": where,
                "timestamp": ts,
                "exc_type": getattr(exc_type, "__name__", str(exc_type)),
                "error": str(exc),
                "traceback": tb_str,
                "argv": list(getattr(sys, "argv", []) or []),
            }
            write_json_atomic(artifact_path, payload, log_path=log_path)
        except Exception as e:
            try:
                log_exception(context="install_global_crash_handler(_write)", exc=e, log_path=log_path)
            except Exception:
                pass

        if on_fatal_message is not None:
            try:
                on_fatal_message(str(exc))
            except Exception:
                pass

    def _sys_hook(exc_type, exc, tb):
        try:
            _write(exc_type, exc, tb, where="main")
        finally:
            try:
                log_exception(context=f"FATAL({tool})", exc=exc, log_path=log_path)
            except Exception:
                pass

    sys.excepthook = _sys_hook

    try:
        import threading

        if hasattr(threading, "excepthook"):
            def _thread_hook(args):
                try:
                    _write(args.exc_type, args.exc_value, args.exc_traceback, where=f"thread_{getattr(args, 'thread', None) and args.thread.name or 'unknown'}")
                finally:
                    try:
                        log_exception(context=f"THREAD_FATAL({tool})", exc=args.exc_value, log_path=log_path)
                    except Exception:
                        pass

            threading.excepthook = _thread_hook  # type: ignore[attr-defined]
    except Exception as e:
        try:
            log_exception(context="install_global_crash_handler(threading)", exc=e, log_path=log_path)
        except Exception:
            pass
