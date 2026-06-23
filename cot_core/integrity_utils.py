import os
import subprocess
from typing import Any, Dict, Optional

from cot_core.logging_utils import log_exception


def check_file_basic(path: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "path": path,
        "exists": False,
        "size_bytes": None,
    }
    try:
        out["exists"] = bool(os.path.isfile(path))
        if out["exists"]:
            out["size_bytes"] = int(os.path.getsize(path))
    except Exception:
        pass
    return out


def probe_ffprobe_json(*, ffprobe: str, media_path: str, log_path: Optional[str] = None) -> Dict[str, Any]:
    """Return a dict with ok + optional ffprobe json output.

    Uses: ffprobe -v error -show_format -show_streams -of json <path>
    """

    base = {
        "ffprobe": ffprobe,
        "media_path": media_path,
        "ok": False,
        "return_code": None,
        "json": None,
        "error": None,
    }

    cmd = [ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", media_path]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
        base["return_code"] = int(r.returncode)
        if r.returncode != 0:
            base["error"] = (r.stderr or r.stdout or "").strip()[:2000]
            return base
        txt = (r.stdout or "").strip()
        if not txt:
            base["error"] = "ffprobe returned empty output"
            return base
        try:
            import json

            base["json"] = json.loads(txt)
        except Exception:
            base["json"] = txt
        base["ok"] = True
        return base
    except Exception as e:
        base["error"] = str(e)
        log_exception(context="probe_ffprobe_json", exc=e, log_path=log_path)
        return base


def summarize_media_integrity(
    *,
    path: str,
    ffprobe: Optional[str] = None,
    min_size_bytes: int = 1024,
    log_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Combined file + ffprobe check.

    Returns a dict suitable to be embedded in a last-run artifact.
    """

    basic = check_file_basic(path)
    ok = bool(basic.get("exists")) and (basic.get("size_bytes") or 0) >= int(min_size_bytes)

    out: Dict[str, Any] = {
        "basic": basic,
        "min_size_bytes": int(min_size_bytes),
        "ok_basic": ok,
        "ffprobe": None,
        "ok": ok,
    }

    if ffprobe:
        ff = probe_ffprobe_json(ffprobe=ffprobe, media_path=path, log_path=log_path)
        out["ffprobe"] = ff
        out["ok"] = bool(out["ok"]) and bool(ff.get("ok"))

    return out
