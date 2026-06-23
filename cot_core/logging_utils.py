import os
import time
import traceback
from typing import Optional, Callable


def append_line(path: str, line: str) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except Exception:
        pass
    try:
        with open(path, "a", encoding="utf-8", errors="replace") as f:
            f.write(line + "\n")
    except Exception:
        pass


def log_exception(
    *,
    context: str,
    exc: BaseException,
    log_path: Optional[str] = None,
    log_cb: Optional[Callable[[str], None]] = None,
) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    header = f"[{ts}] EXCEPTION in {context}: {type(exc).__name__}: {exc}"
    tb = traceback.format_exc()

    if log_cb is not None:
        try:
            log_cb(header)
            for line in tb.splitlines():
                if line.strip():
                    log_cb(line)
        except Exception:
            pass

    if log_path:
        append_line(log_path, header)
        for line in tb.splitlines():
            append_line(log_path, line)
