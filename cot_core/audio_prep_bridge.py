from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional

from cot_core.path_settings import audio_prep_status, find_audio_prep_launcher


@dataclass
class AudioPrepLaunchResult:
    ok: bool
    command: list[str] | None = None
    error: str = ""


class AudioPrepBridge:
    """Bridge for launching the separately installed Audio Prep Suite.

    The YouTube Video Toolkit should not import Audio Prep internals or own its
    audio-analysis dependencies. This bridge launches the external app/CLI and
    passes environment context. When Audio Prep Suite exposes stable CLI tools,
    these commands can be used by YouTube workflows without adding librosa or
    other Audio Prep dependencies to this toolkit.
    """

    def __init__(self, audio_prep_root: str, toolkit_root: Optional[str] = None):
        self.audio_prep_root = os.path.abspath(audio_prep_root) if audio_prep_root else ""
        self.toolkit_root = os.path.abspath(toolkit_root or os.getcwd())

    def status(self) -> dict:
        return audio_prep_status(self.audio_prep_root) if self.audio_prep_root else {"found": False, "runnable": False}

    def launcher(self) -> str:
        return find_audio_prep_launcher(self.audio_prep_root) if self.audio_prep_root else ""

    def build_command(self, extra_args: Optional[list[str]] = None) -> list[str]:
        launcher = self.launcher()
        if not launcher:
            return []
        extra_args = list(extra_args or [])
        if launcher.lower().endswith(".py"):
            return [sys.executable, launcher, *extra_args]
        if launcher.lower().endswith(".bat") and sys.platform == "win32":
            return ["cmd", "/c", launcher, *extra_args]
        return [launcher, *extra_args]

    def launch(self, extra_args: Optional[list[str]] = None) -> AudioPrepLaunchResult:
        cmd = self.build_command(extra_args)
        if not cmd:
            return AudioPrepLaunchResult(False, error="Audio Prep Suite launcher was not found.")
        env = os.environ.copy()
        env["YT_TOOLKIT_PATH"] = self.toolkit_root
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            subprocess.Popen(
                cmd,
                cwd=self.audio_prep_root,
                env=env,
                creationflags=(subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0),
                **({"start_new_session": True} if sys.platform != "win32" else {}),
            )
            return AudioPrepLaunchResult(True, command=cmd)
        except Exception as exc:
            return AudioPrepLaunchResult(False, command=cmd, error=str(exc))

    def launch_tool(self, tool: str, input_path: str = "", output_path: str = "") -> AudioPrepLaunchResult:
        """Launch a future Audio Prep CLI tool command.

        Expected future Audio Prep CLI shape:
          --tool prepare|analyze|full-pipeline|handoff-csv --input PATH [--output PATH]

        If the current Audio Prep Suite does not support these flags yet, it may
        simply open its GUI or report its own usage error; this toolkit still
        avoids importing Audio Prep dependencies directly.
        """
        args = ["--tool", str(tool)]
        if input_path:
            args += ["--input", str(input_path)]
        if output_path:
            args += ["--output", str(output_path)]
        return self.launch(args)
