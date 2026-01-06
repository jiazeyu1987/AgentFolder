from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from dashboard_backend.run_supervisor import RunSupervisor
from dashboard_backend.services.cli_runner import run_agent_cli
from dashboard_backend.services.processes import is_process_alive, read_json, start_run_process, stop_run_process


def start(*, run_state_path: Path, db_path: Path, plan_path: Path, max_iterations: int) -> Dict[str, Any]:
    state = read_json(run_state_path)
    if state and isinstance(state.get("pid"), int) and is_process_alive(int(state["pid"])):
        return {"started": False, "reason": "already running", "pid": state["pid"]}
    state2 = start_run_process(run_state_path=run_state_path, db_path=db_path, plan_path=plan_path, max_iterations=int(max_iterations))
    return {"started": True, **state2}


def stop(*, run_state_path: Path) -> Dict[str, Any]:
    return stop_run_process(run_state_path=run_state_path)


def status(*, run_state_path: Path) -> Dict[str, Any]:
    sup = RunSupervisor(run_state_path=run_state_path, is_process_alive=is_process_alive)
    return sup.get_status(cleanup_stale=True).to_dict()


def run_once(*, db_path: Path, plan_path: Path) -> Dict[str, Any]:
    # Keep behavior consistent with legacy app_helpers.run_once (returns stdout/stderr).
    res = run_agent_cli(db_path=db_path, args=["run", "--plan", str(plan_path), "--max-iterations", "1"])
    return {"exit_code": res.exit_code, "stdout": res.stdout, "stderr": res.stderr}


def reset_failed(*, db_path: Path, plan_id: str, include_blocked: bool, reset_attempts: bool) -> Dict[str, Any]:
    argv = ["reset-failed", "--plan-id", str(plan_id)]
    if include_blocked:
        argv.append("--include-blocked")
    if reset_attempts:
        argv.append("--reset-attempts")
    res = run_agent_cli(db_path=db_path, args=argv)
    return {"exit_code": res.exit_code, "stdout": res.stdout, "stderr": res.stderr}


def reset_to_plan(*, db_path: Path, plan_id: str) -> Dict[str, Any]:
    res = run_agent_cli(db_path=db_path, args=["reset-to-plan", "--plan-id", str(plan_id)])
    return {"exit_code": res.exit_code, "stdout": res.stdout, "stderr": res.stderr}
