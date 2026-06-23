import os
import sys
import argparse
import subprocess
from datetime import datetime
from typing import Optional, List


try:
    import cot_config as cfg
    cfg.load(gui_mode=True)
except Exception:
    cfg = None


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _choose_project(root: str) -> Optional[str]:
    try:
        entries = [
            d for d in os.listdir(root)
            if os.path.isdir(os.path.join(root, d))
            and not d.startswith(".")
        ]
    except Exception as e:
        print(f"\n  ERROR: Could not list root folder: {e}\n")
        return None

    entries.sort(key=lambda s: s.lower())

    print("\n  Select a project folder:")
    for i, name in enumerate(entries[:60], 1):
        print(f"    {i:>2}. {name}")
    if len(entries) > 60:
        print(f"    ... ({len(entries) - 60} more)")

    while True:
        sel = input("\n  Enter number, or paste a folder name/path (Enter to cancel): ").strip()
        if not sel:
            return None

        if sel.isdigit():
            idx = int(sel)
            if 1 <= idx <= len(entries):
                return os.path.join(root, entries[idx - 1])
            print("  Invalid number.")
            continue

        if os.path.isabs(sel) and os.path.isdir(sel):
            return sel

        p = os.path.join(root, sel)
        if os.path.isdir(p):
            return p

        print("  Not found. Try again.")


def _run(cmd: List[str]) -> int:
    print("\n  Running:")
    print("   ", " ".join(f'"{c}"' if " " in c else c for c in cmd))
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    p = subprocess.run(cmd, env=env)
    return int(p.returncode)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Media Prep — run backlog curation then generate flipbook summaries.",
    )
    parser.add_argument("--root", default=None, help="Root pictures folder (defaults to PICTURES_DIR from cot_config.json, else ~/Pictures)")
    parser.add_argument("--project", default=None, help="Project folder name under --root, or an absolute path to a project folder")

    parser.add_argument("--skip-curate", action="store_true", help="Skip curation stage")
    parser.add_argument("--skip-flipbook", action="store_true", help="Skip flipbook stage")

    parser.add_argument("--dry-run", action="store_true", help="Dry-run/analysis mode (propagated to child tools when supported)")
    parser.add_argument("--superbatch", action="store_true", help="Superbatch mode for curation (one confirmation instead of per-subfolder)")

    parser.add_argument("--curate-batch", action="store_true", help="Run curation in batch mode (no interactive prompts)")
    parser.add_argument("--curate-apply", action="store_true", help="When used with --curate-batch, actually move files (otherwise analyze only)")
    parser.add_argument(
        "--curate-keep-mode",
        default="balanced",
        choices=["keep_more", "balanced", "keep_less"],
        help="Curation preset strictness (batch mode)",
    )
    parser.add_argument(
        "--curate-skip-dupes",
        action="store_true",
        help="Skip duplicate detection during curation (faster for very large folders)",
    )
    parser.add_argument(
        "--curate-skip-faces",
        action="store_true",
        help="Skip face detection during curation (faster)",
    )
    parser.add_argument(
        "--curate-no-eye-verify",
        action="store_true",
        help="Disable eye verification during face detection (faster; may increase false positives)",
    )
    parser.add_argument(
        "--curate-analysis-max-size",
        type=int,
        default=None,
        help="Downscale images for analysis so max(width,height)<=N (faster)",
    )

    parser.add_argument("--flipbook-fps", type=float, default=None, help="Override flipbook output FPS")
    parser.add_argument("--flipbook-sec", type=float, default=None, help="Override flipbook output length (seconds)")
    parser.add_argument("--flipbook-overwrite", action="store_true", help="Overwrite existing flipbook outputs")

    args = parser.parse_args()

    if args.skip_curate and args.skip_flipbook:
        print("\n  ERROR: Both stages are disabled (--skip-curate and --skip-flipbook). Nothing to do.")
        raise SystemExit(2)

    default_root = ""
    if cfg is not None:
        try:
            default_root = cfg.get("PICTURES_DIR", "")
        except Exception:
            default_root = ""
    if not default_root:
        default_root = os.path.join(os.path.expanduser("~"), "Pictures")

    root = args.root or default_root
    if not os.path.isdir(root):
        print(f"\n  ERROR: Folder not found: {root}")
        raise SystemExit(1)

    project_path: Optional[str] = None
    if args.project:
        project = args.project
        if os.path.isabs(project) and os.path.isdir(project):
            project_path = project
        else:
            candidate = os.path.join(root, project)
            if os.path.isdir(candidate):
                project_path = candidate

        if not project_path:
            print(f"\n  ERROR: Project folder not found: {args.project}")
            raise SystemExit(1)
    else:
        try:
            is_tty = bool(getattr(sys.stdin, "isatty", lambda: False)())
        except Exception:
            is_tty = False

        if not is_tty:
            print("\n  ERROR: No TTY available for interactive prompts.")
            print("  Re-run with --project and optionally --root.")
            raise SystemExit(2)

        project_path = _choose_project(root)
        if not project_path:
            print("\n  Cancelled.")
            return

    project_path = os.path.abspath(project_path)
    print("\n  Media Prep")
    print(f"  Started: {_now_iso()}")
    print(f"  Project: {project_path}")

    py = sys.executable

    if not args.skip_curate:
        curate_cmd = [py, "-u", os.path.join(os.path.dirname(__file__), "cot_curate.py"), "--project", project_path]
        if args.dry_run:
            curate_cmd.append("--dry-run")
        if args.superbatch:
            curate_cmd.append("--superbatch")
        if args.curate_batch:
            curate_cmd += ["--batch", "--keep-mode", str(args.curate_keep_mode)]
            if args.curate_apply:
                curate_cmd.append("--apply")
        if bool(args.curate_skip_dupes):
            curate_cmd.append("--skip-dupes")
        if bool(args.curate_skip_faces):
            curate_cmd.append("--skip-faces")
        if bool(args.curate_no_eye_verify):
            curate_cmd.append("--no-eye-verify")
        if args.curate_analysis_max_size is not None:
            curate_cmd += ["--analysis-max-size", str(int(args.curate_analysis_max_size))]

        rc = _run(curate_cmd)
        if rc != 0:
            print(f"\n  ERROR: curation failed with exit code {rc}")
            raise SystemExit(rc)

    if not args.skip_flipbook:
        flip_cmd = [py, "-u", os.path.join(os.path.dirname(__file__), "cot_flipbook_clips.py"), "--project", project_path]
        if args.dry_run:
            flip_cmd.append("--dry-run")
        if args.flipbook_fps is not None:
            flip_cmd += ["--out-fps", str(args.flipbook_fps)]
        if args.flipbook_sec is not None:
            flip_cmd += ["--out-sec", str(args.flipbook_sec)]
        if bool(args.flipbook_overwrite):
            flip_cmd.append("--overwrite")

        rc = _run(flip_cmd)
        if rc != 0:
            print(f"\n  ERROR: flipbook failed with exit code {rc}")
            raise SystemExit(rc)

    print(f"\n  Media Prep complete. Finished: {_now_iso()}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  Cancelled.")
        raise SystemExit(1)
