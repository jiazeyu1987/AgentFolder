from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from dashboard_backend.services.processes import python_executable


ROOT_DIR = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class CliRunResult:
    exit_code: int
    stdout: str
    stderr: str
    cmd: List[str]


def run_agent_cli(
    *,
    db_path: Path,
    args: List[str],
    cwd: Optional[Path] = None,
    hide_window: bool = True,
) -> CliRunResult:
    """
    Run agent_cli.py synchronously using the configured python executable.
    """
    py = python_executable()
    cmd = [py, str(ROOT_DIR / "agent_cli.py"), "--db", str(db_path), *[str(x) for x in args]]
    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)) if hide_window else 0
    proc = subprocess.run(
        cmd,
        cwd=str(cwd or ROOT_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
    )
    return CliRunResult(exit_code=int(proc.returncode), stdout=str(proc.stdout or ""), stderr=str(proc.stderr or ""), cmd=cmd)

