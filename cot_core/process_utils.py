import os
import sys
import subprocess
import time
from typing import Optional

from cot_core.logging_utils import log_exception


def terminate_process(proc: subprocess.Popen, *, log_path: Optional[str] = None, timeout_sec: float = 2.0) -> None:
    try:
        if proc.poll() is not None:
            return
    except Exception:
        return

    try:
        proc.terminate()
    except Exception as e:
        log_exception(context="terminate_process:terminate", exc=e, log_path=log_path)

    t0 = time.time()
    while time.time() - t0 < float(timeout_sec):
        try:
            if proc.poll() is not None:
                return
        except Exception:
            break
        time.sleep(0.05)

    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return
        except Exception as e:
            log_exception(context="terminate_process:taskkill", exc=e, log_path=log_path)

    try:
        proc.kill()
    except Exception as e:
        log_exception(context="terminate_process:kill", exc=e, log_path=log_path)
