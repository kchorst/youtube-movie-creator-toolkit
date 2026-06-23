import os
import shutil
import subprocess
import sys
import time
import ctypes
import urllib.request
import json
from cot_core.local_llm import discover_local_llm
from typing import Dict, Optional, Tuple


def _fmt_bytes(n: int) -> str:
    try:
        n = int(n)
    except Exception:
        return str(n)
    units = ["B", "KB", "MB", "GB", "TB"]
    v = float(n)
    for u in units:
        if v < 1024.0 or u == units[-1]:
            if u == "B":
                return f"{int(v)} {u}"
            return f"{v:.2f} {u}"
        v /= 1024.0
    return f"{v:.2f} TB"


def check_executable(path: str, *, name: str) -> Tuple[bool, str]:
    p = (path or "").strip()
    if not p:
        return False, f"{name}: not configured"
    if shutil.which(p) is None and not os.path.isfile(p):
        return False, f"{name}: not found: {p}"
    return True, f"{name}: OK ({p})"


def check_disk_free(path: str, *, min_free_bytes: int) -> Tuple[bool, str]:
    p = (path or "").strip() or os.getcwd()
    try:
        usage = shutil.disk_usage(p)
        ok = int(usage.free) >= int(min_free_bytes)
        msg = f"Disk free at {p}: {_fmt_bytes(int(usage.free))} (min {_fmt_bytes(int(min_free_bytes))})"
        return ok, msg
    except Exception as e:
        return False, f"Disk check failed at {p}: {e}"


def check_writable_dir(path: str) -> Tuple[bool, str]:
    p = (path or "").strip()
    if not p:
        return False, "Writable dir: not set"
    try:
        os.makedirs(p, exist_ok=True)
    except Exception as e:
        return False, f"Writable dir: cannot create {p}: {e}"

    test_path = os.path.join(p, f".cot_write_test_{int(time.time())}.tmp")
    try:
        with open(test_path, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(test_path)
        return True, f"Writable dir: OK ({p})"
    except Exception as e:
        try:
            if os.path.exists(test_path):
                os.remove(test_path)
        except Exception:
            pass
        return False, f"Writable dir: not writable ({p}): {e}"


def check_file_readable(path: str, *, label: str) -> Tuple[bool, str]:
    p = (path or "").strip()
    if not p:
        return False, f"{label}: not set"
    if not os.path.isfile(p):
        return False, f"{label}: missing: {p}"
    try:
        with open(p, "rb") as f:
            f.read(64)
        return True, f"{label}: OK ({p})"
    except Exception as e:
        return False, f"{label}: not readable ({p}): {e}"


def check_ffmpeg(*, ffmpeg: str, ffprobe: str) -> Dict[str, Tuple[bool, str]]:
    out: Dict[str, Tuple[bool, str]] = {}
    out["ffmpeg"] = check_executable(ffmpeg, name="ffmpeg")
    out["ffprobe"] = check_executable(ffprobe, name="ffprobe")

    if out["ffmpeg"][0]:
        try:
            r = subprocess.run(
                [ffmpeg, "-version"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0,
            )
            ok = (r.returncode == 0)
            msg = "ffmpeg -version OK" if ok else f"ffmpeg -version failed ({r.returncode})"
            out["ffmpeg_run"] = (ok, msg)
        except Exception as e:
            out["ffmpeg_run"] = (False, f"ffmpeg -version failed: {e}")

    return out


def check_ram_free(*, min_free_bytes: int) -> Tuple[bool, str]:
    try:
        min_free_bytes = int(min_free_bytes)
    except Exception:
        min_free_bytes = 0

    if sys.platform == "win32":
        class _MEMSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = _MEMSTATUSEX()
        stat.dwLength = ctypes.sizeof(_MEMSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return False, "RAM check failed: GlobalMemoryStatusEx returned false"
        free_b = int(stat.ullAvailPhys)
        ok = free_b >= min_free_bytes
        return ok, f"RAM available: {_fmt_bytes(free_b)} (min {_fmt_bytes(min_free_bytes)})"

    # Best-effort POSIX fallback (no extra deps)
    try:
        pages = os.sysconf("SC_AVPHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        free_b = int(pages) * int(page_size)
        ok = free_b >= min_free_bytes
        return ok, f"RAM available: {_fmt_bytes(free_b)} (min {_fmt_bytes(min_free_bytes)})"
    except Exception as e:
        return False, f"RAM check not available: {e}"


def check_local_llm_models(*, llm_mode: str, lmstudio_url: str, model_name: str) -> Dict[str, Tuple[bool, str]]:
    """Check provider-neutral Local LLM endpoint. lmstudio_url is kept as an arg name for compatibility."""
    llm_mode = (llm_mode or "").strip()
    if llm_mode not in ("local_llm", "lmstudio_local"):
        return {"llm": (True, "Local LLM: not required (manual mode)")}

    endpoint = (lmstudio_url or "").strip()
    status = discover_local_llm(endpoint, timeout=4)
    if not status.ok:
        return {
            "llm": (False, f"Local LLM not reachable: {status.error}"),
            "llm_model": (False, "Local LLM model status unknown (endpoint unreachable)"),
        }

    models = list(status.models)
    msg = f"{status.provider} reachable at {status.base_url} (models: {len(models)})"
    out: Dict[str, Tuple[bool, str]] = {"llm": (True, msg)}

    current = (model_name or "").strip()
    if current:
        if current in models:
            out["llm_model"] = (True, f"Local LLM model available: {current}")
        else:
            out["llm_model"] = (False, f"Configured Local LLM model not listed: {current}")
    elif models:
        out["llm_model"] = (False, "Local LLM model not set; choose one in Settings")
    else:
        out["llm_model"] = (False, "Local LLM endpoint reached but returned no models")
    return out


def run_preflight(
    *,
    pictures_dir: Optional[str] = None,
    output_dir: Optional[str] = None,
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
    min_free_gb: float = 2.0,
    min_free_ram_gb: float = 2.0,
    client_secrets: Optional[str] = None,
    token_file: Optional[str] = None,
    llm_mode: Optional[str] = None,
    lmstudio_url: Optional[str] = None,
    model_name: Optional[str] = None,
) -> Dict[str, Tuple[bool, str]]:
    results: Dict[str, Tuple[bool, str]] = {}

    min_free_bytes = int(float(min_free_gb) * 1024.0 * 1024.0 * 1024.0)
    min_free_ram_bytes = int(float(min_free_ram_gb) * 1024.0 * 1024.0 * 1024.0)

    if output_dir:
        results["output_writable"] = check_writable_dir(output_dir)
        results["output_disk"] = check_disk_free(output_dir, min_free_bytes=min_free_bytes)

    if pictures_dir:
        results["pictures_exists"] = (os.path.isdir(pictures_dir), f"Pictures dir: {'OK' if os.path.isdir(pictures_dir) else 'missing'} ({pictures_dir})")

    results.update({f"ffmpeg:{k}": v for k, v in check_ffmpeg(ffmpeg=ffmpeg, ffprobe=ffprobe).items()})

    results["ram_free"] = check_ram_free(min_free_bytes=min_free_ram_bytes)

    if llm_mode is not None:
        results.update({f"llm:{k}": v for k, v in check_local_llm_models(
            llm_mode=str(llm_mode or ""),
            lmstudio_url=str(lmstudio_url or ""),
            model_name=str(model_name or ""),
        ).items()})

    if client_secrets is not None:
        results["youtube_client_secrets"] = check_file_readable(client_secrets, label="YouTube client secrets")
    if token_file is not None:
        results["youtube_token"] = check_file_readable(token_file, label="YouTube token")

    return results


def summarize(results: Dict[str, Tuple[bool, str]]) -> Tuple[bool, str]:
    ok_all = True
    lines = []
    for k in sorted(results.keys()):
        ok, msg = results[k]
        ok_all = ok_all and bool(ok)
        lines.append(("OK" if ok else "FAIL") + f"  {msg}")
    return ok_all, "\n".join(lines) + ("\n" if lines else "")
