import os
import sys
import subprocess
from typing import List, Optional, Dict, Callable

from cot_core.logging_utils import log_exception


def _quote_bat_arg(s: str) -> str:
    return '"' + str(s).replace('"', '""') + '"'


def launch_interactive_windows(
    *,
    title: str,
    cmd: List[str],
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    bat_path: Optional[str] = None,
    log_path: Optional[str] = None,
    post_bat_lines: Optional[List[str]] = None,
) -> bool:
    if sys.platform != "win32":
        try:
            subprocess.Popen(cmd, cwd=cwd, env=env)
            return True
        except Exception as e:
            log_exception(context="launch_interactive_windows(nonwin)", exc=e, log_path=log_path)
            return False

    if not bat_path:
        try:
            base = cwd or os.getcwd()
        except Exception:
            base = os.getcwd()
        bat_path = os.path.join(base, "_launcher.bat")

    try:
        with open(bat_path, "w", encoding="utf-8") as f:
            f.write("@echo off\r\n")
            f.write(f"title {title}\r\n")
            f.write("set \"PYTHONUNBUFFERED=1\"\r\n")
            f.write(" ".join(_quote_bat_arg(c) for c in cmd) + "\r\n")
            f.write("echo.\r\n")
            f.write("echo ------------------------------------------------------------\r\n")
            f.write("echo Done. You can close this window.\r\n")
            f.write("echo ------------------------------------------------------------\r\n")
            if post_bat_lines:
                for line in post_bat_lines:
                    f.write(str(line).rstrip("\r\n") + "\r\n")
            f.write("pause\r\n")
    except Exception as e:
        log_exception(context="launch_interactive_windows(write_bat)", exc=e, log_path=log_path)
        return False

    try:
        os.startfile(bat_path)  # type: ignore[attr-defined]
        return True
    except Exception as e:
        log_exception(context="launch_interactive_windows(startfile)", exc=e, log_path=log_path)
        return False


def launch_streamed_hidden(
    *,
    cmd: List[str],
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    on_line: Optional[Callable[[str], None]] = None,
    creationflags: int = 0,
    log_path: Optional[str] = None,
) -> subprocess.Popen:
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=creationflags,
        )
        return proc
    except Exception as e:
        log_exception(context="launch_streamed_hidden(Popen)", exc=e, log_path=log_path)
        if on_line is not None:
            try:
                on_line(f"Launch failed: {e}")
            except Exception:
                pass
        raise
