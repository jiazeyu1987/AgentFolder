from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import HTTPException

from core.job_wrappers import render_create_plan_wrapper_py
from core.util import ensure_dir, utc_now_iso
from core.util import stable_hash_text
from dashboard_backend.services.cli_runner import run_agent_cli
from dashboard_backend.services.processes import is_process_alive, popen_hidden, python_executable, read_json, write_json


def read_state(path: Path) -> Optional[Dict[str, Any]]:
    return read_json(path)


def write_state(path: Path, obj: Dict[str, Any]) -> None:
    write_json(path, obj)


def stop(*, state_path: Path) -> Dict[str, Any]:
    state = read_state(state_path)
    if not state:
        return {"stopped": False, "reason": f"no {state_path.name}"}
    pid = state.get("pid")
    if not isinstance(pid, int):
        return {"stopped": False, "reason": f"invalid pid in {state_path.name}"}
    __import__("subprocess").run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    alive = is_process_alive(pid)
    try:
        state_path.unlink(missing_ok=True)  # type: ignore[arg-type]
    except Exception:
        pass
    return {"stopped": not alive, "pid": pid}


def start_process(
    *,
    state_path: Path,
    job_root: Path,
    root_dir: Path,
    db_path: Path,
    top_task: str,
    max_attempts: int,
    keep_trying: bool,
    max_total_attempts: Optional[int],
) -> Dict[str, Any]:
    """
    Start create-plan as a detached process and write a durable state json.
    """
    python_exe = python_executable()
    job_id = str(__import__("uuid").uuid4())
    job_dir = job_root / job_id
    ensure_dir(job_dir)

    log_path = job_dir / "create_plan.log"
    exit_path = job_dir / "exit.json"
    top_task_file = job_dir / "top_task.txt"
    top_task_file.write_text(str(top_task), encoding="utf-8")

    cmd = [
        python_exe,
        str(root_dir / "agent_cli.py"),
        "--db",
        str(db_path),
        "create-plan",
        "--top-task-file",
        str(top_task_file),
        "--max-attempts",
        str(int(max_attempts)),
        "--job-id",
        str(job_id),
    ]
    if keep_trying:
        cmd.append("--keep-trying")
    if max_total_attempts is not None:
        cmd += ["--max-total-attempts", str(int(max_total_attempts))]

    wrapper_py = job_dir / "run_create_plan_wrapper.py"
    wrapper_py.write_text(
        render_create_plan_wrapper_py(
            root_dir=str(root_dir),
            log_path=str(log_path),
            exit_path=str(exit_path),
            cmd=[str(x) for x in cmd],
            job_id=str(job_id),
        ),
        encoding="utf-8",
    )

    proc = popen_hidden([python_exe, str(wrapper_py)], cwd=str(root_dir))
    state = {
        "job_id": job_id,
        "pid": int(proc.pid),
        "started_at": utc_now_iso(),
        "finished_at": None,
        "cmd": cmd,
        "plan_id": None,
        "status": "RUNNING",
        "last_error": None,
        "top_task_hash": stable_hash_text(top_task or ""),
        "log_path": str(log_path),
        "exit_path": str(exit_path),
        "top_task_file": str(top_task_file),
    }
    write_state(state_path, state)
    return state


def start_async(
    *,
    state_path: Path,
    job_root: Path,
    root_dir: Path,
    db_path: Path,
    top_task: str,
    max_attempts: int,
    keep_trying: bool,
    max_total_attempts: Optional[int],
) -> Dict[str, Any]:
    state = read_state(state_path)
    if state and isinstance(state.get("pid"), int) and is_process_alive(int(state["pid"])):
        return {"started": False, "reason": "already running", "job_id": state.get("job_id"), "pid": state.get("pid")}
    if not str(top_task or "").strip():
        raise HTTPException(status_code=400, detail="top_task is empty")
    s = start_process(
        state_path=state_path,
        job_root=job_root,
        root_dir=root_dir,
        db_path=db_path,
        top_task=str(top_task),
        max_attempts=int(max_attempts),
        keep_trying=bool(keep_trying),
        max_total_attempts=max_total_attempts,
    )
    return {"started": True, "job_id": s.get("job_id"), "pid": s.get("pid"), "ts": utc_now_iso()}


def run_sync(*, db_path: Path, top_task: str, max_attempts: int, keep_trying: bool, max_total_attempts: Optional[int]) -> Dict[str, Any]:
    """
    Synchronous create-plan invocation (used by /api/plan/create).
    """
    argv = ["create-plan", "--top-task", str(top_task), "--max-attempts", str(int(max_attempts))]
    if keep_trying:
        argv.append("--keep-trying")
    if max_total_attempts is not None:
        argv += ["--max-total-attempts", str(int(max_total_attempts))]
    res = run_agent_cli(db_path=db_path, args=argv)
    return {"exit_code": res.exit_code, "stdout": res.stdout, "stderr": res.stderr}
