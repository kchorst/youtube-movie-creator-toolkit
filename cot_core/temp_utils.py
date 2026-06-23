import os
import shutil
import time
from typing import Optional


def prune_old_subdirs(
    *,
    parent_dir: str,
    older_than_sec: float,
    prefix: Optional[str] = None,
) -> int:
    if not os.path.isdir(parent_dir):
        return 0

    now = time.time()
    removed = 0

    try:
        names = os.listdir(parent_dir)
    except Exception:
        return 0

    for name in names:
        if prefix and not str(name).startswith(prefix):
            continue
        p = os.path.join(parent_dir, name)
        if not os.path.isdir(p):
            continue
        try:
            age = now - float(os.path.getmtime(p))
        except Exception:
            continue
        if age < float(older_than_sec):
            continue
        try:
            shutil.rmtree(p, ignore_errors=True)
            removed += 1
        except Exception:
            pass

    try:
        if os.path.isdir(parent_dir) and not os.listdir(parent_dir):
            os.rmdir(parent_dir)
    except Exception:
        pass

    return removed
