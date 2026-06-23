import json
import os
import time
import traceback
from typing import Any, Dict, Optional

from cot_core.logging_utils import log_exception


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def write_json_atomic(path: str, payload: Dict[str, Any], *, log_path: Optional[str] = None) -> None:
    tmp_path = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp_path, path)
    except Exception as e:
        log_exception(context=f"write_json_atomic({path})", exc=e, log_path=log_path)


class LastRunArtifact:
    def __init__(
        self,
        *,
        path: str,
        tool: str,
        inputs: Optional[Dict[str, Any]] = None,
        log_path: Optional[str] = None,
    ) -> None:
        self.path = path
        self.log_path = log_path
        self.payload: Dict[str, Any] = {
            "tool": tool,
            "started_at": _now_iso(),
            "finished_at": None,
            "ok": None,
            "return_code": None,
            "error": None,
            "traceback": None,
            "inputs": inputs or {},
            "outputs": {},
        }
        write_json_atomic(self.path, self.payload, log_path=self.log_path)

    def set_output(self, key: str, value: Any) -> None:
        try:
            self.payload.setdefault("outputs", {})[key] = value
            write_json_atomic(self.path, self.payload, log_path=self.log_path)
        except Exception as e:
            log_exception(context="LastRunArtifact.set_output", exc=e, log_path=self.log_path)

    def finish(self, *, ok: bool, return_code: Optional[int] = None) -> None:
        try:
            self.payload["finished_at"] = _now_iso()
            self.payload["ok"] = bool(ok)
            self.payload["return_code"] = int(return_code) if return_code is not None else None
            write_json_atomic(self.path, self.payload, log_path=self.log_path)
        except Exception as e:
            log_exception(context="LastRunArtifact.finish", exc=e, log_path=self.log_path)

    def fail(self, exc: BaseException) -> None:
        try:
            self.payload["finished_at"] = _now_iso()
            self.payload["ok"] = False
            self.payload["error"] = str(exc)
            self.payload["traceback"] = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            write_json_atomic(self.path, self.payload, log_path=self.log_path)
        except Exception as e:
            log_exception(context="LastRunArtifact.fail", exc=e, log_path=self.log_path)
