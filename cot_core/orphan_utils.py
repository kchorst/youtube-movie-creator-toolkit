import os
import subprocess
from typing import Any, Dict, List, Optional

from cot_core.logging_utils import log_exception


def _run(cmd: List[str]) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    return (r.stdout or "")


def get_command_line_windows(*, pid: int, log_path: Optional[str] = None) -> str:
    try:
        out = _run([
            "wmic",
            "process",
            "where",
            f"processid={int(pid)}",
            "get",
            "CommandLine",
            "/value",
        ])
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.lower().startswith("commandline="):
                return line.split("=", 1)[1].strip()
        return ""
    except Exception as e:
        log_exception(context="get_command_line_windows", exc=e, log_path=log_path)
        return ""


def _matches_scope(cmdline: str, scope_substrings: List[str]) -> bool:
    if not scope_substrings:
        return True
    cl = (cmdline or "").lower()
    for s in scope_substrings:
        ss = (s or "").strip().lower()
        if ss and ss in cl:
            return True
    return False


def list_processes_windows(*, log_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return list of processes using tasklist CSV.

    Each item has keys: image, pid, session_name, session_num, mem_usage
    """

    try:
        out = _run(["tasklist", "/FO", "CSV", "/NH"])
        items: List[Dict[str, Any]] = []
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            # CSV with quoted fields
            parts = [p.strip().strip('"') for p in line.split(",")]
            if len(parts) < 5:
                continue
            image, pid_s, sess_name, sess_num, mem = parts[:5]
            try:
                pid = int(pid_s)
            except Exception:
                continue
            items.append(
                {
                    "image": image,
                    "pid": pid,
                    "session_name": sess_name,
                    "session_num": sess_num,
                    "mem_usage": mem,
                }
            )
        return items
    except Exception as e:
        log_exception(context="list_processes_windows", exc=e, log_path=log_path)
        return []


def find_orphans_windows(
    *,
    images: List[str],
    log_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Find processes whose image name matches any of `images` (case-insensitive)."""

    img_set = {i.lower() for i in images if i}
    procs = list_processes_windows(log_path=log_path)
    out: List[Dict[str, Any]] = []
    for p in procs:
        try:
            if str(p.get("image", "")).lower() in img_set:
                out.append(p)
        except Exception:
            continue
    return out


def taskkill_windows(
    *,
    pid: int,
    log_path: Optional[str] = None,
    force: bool = True,
) -> bool:
    try:
        cmd = ["taskkill", "/PID", str(int(pid))]
        if force:
            cmd.append("/F")
        cmd.append("/T")
        r = subprocess.run(cmd, capture_output=True, text=True)
        return int(r.returncode) == 0
    except Exception as e:
        log_exception(context="taskkill_windows", exc=e, log_path=log_path)
        return False


def cleanup_orphans(
    *,
    images: List[str],
    detect_only: bool = True,
    scope_substrings: Optional[List[str]] = None,
    include_commandline: bool = True,
    log_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Detect (and optionally kill) orphan-ish processes.

    Conservative by default (detect-only).

    Returns summary dict.
    """

    if os.name != "nt":
        return {
            "ok": True,
            "platform": os.name,
            "detect_only": detect_only,
            "found": [],
            "killed": [],
        }

    found = find_orphans_windows(images=images, log_path=log_path)
    killed: List[Dict[str, Any]] = []

    scope = scope_substrings or []
    scoped: List[Dict[str, Any]] = []
    for p in found:
        try:
            pid = int(p.get("pid") or 0)
        except Exception:
            pid = 0
        cmdline = ""
        if include_commandline and pid > 0:
            cmdline = get_command_line_windows(pid=pid, log_path=log_path)
            p["command_line"] = cmdline

        if _matches_scope(cmdline, scope):
            scoped.append(p)

    if not detect_only:
        for p in scoped:
            pid = int(p.get("pid") or 0)
            if pid <= 0:
                continue
            ok = taskkill_windows(pid=pid, log_path=log_path, force=True)
            if ok:
                killed.append(p)

    return {
        "ok": True,
        "platform": os.name,
        "detect_only": detect_only,
        "found": found,
        "scoped": scoped,
        "scope_substrings": scope,
        "killed": killed,
    }
