import time
from typing import Any, Callable, Dict, Optional, Tuple

from cot_core.last_run_utils import LastRunArtifact
from cot_core.logging_utils import log_exception


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def try_run_preflight(*, log_path: Optional[str] = None) -> Tuple[bool, str]:
    """Best-effort preflight.

    Returns (ok, text). Never raises.

    Notes:
    - Uses cot_config.check_preflight() if available.
    - If not available, returns (True, "").
    """

    try:
        import cot_config as cfg  # type: ignore

        try:
            cfg.load(gui_mode=True)
        except Exception:
            pass

        try:
            ok = bool(cfg.check_preflight())
            return ok, "preflight: OK" if ok else "preflight: FAIL"
        except Exception as e:
            log_exception(context="try_run_preflight(check_preflight)", exc=e, log_path=log_path)
            return False, f"preflight: ERROR ({e})"
    except Exception:
        return True, ""


def run_with_artifact(
    *,
    artifact_path: str,
    tool: str,
    inputs: Optional[Dict[str, Any]] = None,
    log_path: Optional[str] = None,
    preflight: bool = False,
    fn: Callable[[LastRunArtifact], Any],
) -> Any:
    """Standard run lifecycle wrapper.

    - Optionally runs preflight (best-effort)
    - Writes/updates last-run artifact
    - Records duration
    - Records exceptions with traceback

    `fn` receives the artifact so it can add outputs during execution.
    """

    artifact = LastRunArtifact(
        path=artifact_path,
        tool=tool,
        inputs=inputs or {},
        log_path=log_path,
    )

    t0 = time.time()

    if preflight:
        ok_pf, pf_text = try_run_preflight(log_path=log_path)
        try:
            artifact.set_output("preflight", {"ok": ok_pf, "text": pf_text, "at": _now_iso()})
        except Exception:
            pass
        if not ok_pf:
            try:
                artifact.finish(ok=False, return_code=None)
            except Exception:
                pass
            raise RuntimeError("Preflight failed")

    try:
        result = fn(artifact)
        dur = time.time() - t0
        try:
            artifact.set_output("duration_sec", round(dur, 3))
        except Exception:
            pass
        return result
    except Exception as e:
        dur = time.time() - t0
        try:
            artifact.set_output("duration_sec", round(dur, 3))
        except Exception:
            pass
        try:
            artifact.fail(e)
        except Exception:
            pass
        raise
