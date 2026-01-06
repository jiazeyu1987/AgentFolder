from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from core.runtime_config import get_runtime_config
from core.util import ensure_dir, utc_now_iso

ROOT_DIR = Path(__file__).resolve().parents[2]


def python_executable() -> str:
    try:
        v = str(get_runtime_config().python_executable or "").strip()
        if v:
            return v
    except Exception:
        pass
    return sys.executable


def popen_hidden(cmd: list[str], *, cwd: str) -> subprocess.Popen:
    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # type: ignore[attr-defined]
        si.wShowWindow = 0  # SW_HIDE
        return subprocess.Popen(cmd, cwd=cwd, creationflags=creationflags, startupinfo=si)
    except Exception:
        return subprocess.Popen(cmd, cwd=cwd, creationflags=creationflags)


def is_process_alive(pid: int) -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
        return str(pid) in out
    except Exception:
        return False


def write_json(path: Path, obj: Dict[str, Any]) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def start_run_process(*, run_state_path: Path, db_path: Path, plan_path: Path, max_iterations: int) -> Dict[str, Any]:
    job_id = str(__import__("uuid").uuid4())
    cmd = [
        python_executable(),
        str(ROOT_DIR / "agent_cli.py"),
        "--db",
        str(db_path),
        "run",
        "--plan",
        str(plan_path),
        "--max-iterations",
        str(int(max_iterations)),
        "--job-id",
        str(job_id),
    ]
    proc = popen_hidden(cmd, cwd=str(ROOT_DIR))
    state = {
        "job_id": job_id,
        "pid": int(proc.pid),
        "cmd": cmd,
        "started_at": utc_now_iso(),
        "db_path": str(db_path),
        "plan_path": str(plan_path),
    }
    write_json(run_state_path, state)
    return state


def stop_run_process(*, run_state_path: Path) -> Dict[str, Any]:
    state = read_json(run_state_path)
    if not state:
        return {"stopped": False, "reason": "no run_process.json"}
    pid = state.get("pid")
    if not isinstance(pid, int):
        return {"stopped": False, "reason": "invalid pid in run_process.json"}
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    if is_process_alive(pid):
        return {"stopped": False, "reason": "process still alive", "pid": pid}
    try:
        run_state_path.unlink(missing_ok=True)  # type: ignore[arg-type]
    except Exception:
        pass
    return {"stopped": True, "pid": pid}

