from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import config
from core.db import apply_migrations, connect
from core.graph import build_plan_graph
from core.util import ensure_dir, utc_now_iso


ROOT_DIR = Path(__file__).resolve().parents[1]
RUN_STATE_PATH = config.STATE_DIR / "run_process.json"


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = connect(db_path)
    apply_migrations(conn, config.MIGRATIONS_DIR)
    return conn


def _read_runtime_config() -> Dict[str, Any]:
    p = config.RUNTIME_CONFIG_PATH
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"_error": "invalid runtime_config.json"}


def _python_executable() -> str:
    """
    Prefer a value from runtime_config.json, otherwise use the current interpreter.
    (Do not rely on env vars.)
    """
    cfg = _read_runtime_config()
    v = (cfg.get("python_executable") if isinstance(cfg, dict) else None) or None
    if isinstance(v, str) and v.strip():
        return v.strip()
    return sys.executable


def _write_run_state(obj: Dict[str, Any]) -> None:
    ensure_dir(RUN_STATE_PATH.parent)
    RUN_STATE_PATH.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_run_state() -> Optional[Dict[str, Any]]:
    if not RUN_STATE_PATH.exists():
        return None
    try:
        return json.loads(RUN_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def _is_process_alive(pid: int) -> bool:
    # Windows: `tasklist /FI "PID eq <pid>"`
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
        return str(pid) in out
    except Exception:
        return False


def _start_run_process(*, db_path: Path, plan_path: Path, max_iterations: int) -> Dict[str, Any]:
    python_exe = _python_executable()
    cmd = [python_exe, str(ROOT_DIR / "agent_cli.py"), "--db", str(db_path), "run", "--plan", str(plan_path), "--max-iterations", str(int(max_iterations))]
    # Start detached so UI can close and run continues.
    creationflags = 0x00000008  # CREATE_NO_WINDOW
    proc = subprocess.Popen(cmd, cwd=str(ROOT_DIR), creationflags=creationflags)
    state = {"pid": int(proc.pid), "cmd": cmd, "started_at": utc_now_iso(), "db_path": str(db_path), "plan_path": str(plan_path)}
    _write_run_state(state)
    return state


def _stop_run_process() -> Dict[str, Any]:
    state = _read_run_state()
    if not state:
        return {"stopped": False, "reason": "no run_process.json"}
    pid = state.get("pid")
    if not isinstance(pid, int):
        return {"stopped": False, "reason": "invalid pid in run_process.json"}
    # Try graceful stop via taskkill.
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    alive = _is_process_alive(pid)
    if alive:
        return {"stopped": False, "reason": "process still alive", "pid": pid}
    try:
        RUN_STATE_PATH.unlink(missing_ok=True)  # type: ignore[arg-type]
    except Exception:
        pass
    return {"stopped": True, "pid": pid}


app = FastAPI(title="Agent Dashboard Backend", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CreatePlanIn(BaseModel):
    top_task: str
    max_attempts: int = 3
    keep_trying: bool = False
    max_total_attempts: Optional[int] = None


class RunStartIn(BaseModel):
    max_iterations: int = 10_000


class ExportIn(BaseModel):
    plan_id: str
    include_reviews: bool = False


class ResetDbIn(BaseModel):
    purge_workspace: bool = False
    purge_tasks: bool = False
    purge_logs: bool = False


@app.get("/api/config")
def get_config() -> Dict[str, Any]:
    return {
        "runtime_config": _read_runtime_config(),
        "paths": {
            "inputs_dir": str(config.INPUTS_DIR),
            "baseline_inputs_dir": str(config.BASELINE_INPUTS_DIR),
            "deliverables_dir": str(config.DELIVERABLES_DIR),
            "artifacts_dir": str(config.ARTIFACTS_DIR),
            "db_path": str(config.DB_PATH_DEFAULT),
            "db_dir": str(config.STATE_DIR),
        },
    }


@app.get("/api/plans")
def get_plans() -> Dict[str, Any]:
    conn = _connect(config.DB_PATH_DEFAULT)
    rows = conn.execute("SELECT plan_id, title, root_task_id, created_at FROM plans ORDER BY created_at DESC").fetchall()
    return {"plans": [dict(r) for r in rows], "ts": utc_now_iso()}


@app.get("/api/plan/{plan_id}/graph")
def get_plan_graph(plan_id: str) -> Dict[str, Any]:
    conn = _connect(config.DB_PATH_DEFAULT)
    try:
        res = build_plan_graph(conn, plan_id=plan_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return res.graph


@app.post("/api/run/start")
def run_start(body: RunStartIn) -> Dict[str, Any]:
    # Ensure there is no running process.
    state = _read_run_state()
    if state and isinstance(state.get("pid"), int) and _is_process_alive(int(state["pid"])):
        return {"started": False, "reason": "already running", "pid": state["pid"]}
    state = _start_run_process(db_path=config.DB_PATH_DEFAULT, plan_path=config.PLAN_PATH_DEFAULT, max_iterations=body.max_iterations)
    return {"started": True, **state}


@app.post("/api/run/stop")
def run_stop() -> Dict[str, Any]:
    return _stop_run_process()


@app.post("/api/run/once")
def run_once() -> Dict[str, Any]:
    python_exe = _python_executable()
    cmd = [python_exe, str(ROOT_DIR / "agent_cli.py"), "--db", str(config.DB_PATH_DEFAULT), "run", "--plan", str(config.PLAN_PATH_DEFAULT), "--max-iterations", "1"]
    proc = subprocess.run(cmd, cwd=str(ROOT_DIR), capture_output=True, text=True, encoding="utf-8", errors="replace")
    return {"exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}


@app.post("/api/plan/create")
def create_plan(body: CreatePlanIn) -> Dict[str, Any]:
    python_exe = _python_executable()
    cmd = [
        python_exe,
        str(ROOT_DIR / "agent_cli.py"),
        "--db",
        str(config.DB_PATH_DEFAULT),
        "create-plan",
        "--top-task",
        body.top_task,
        "--max-attempts",
        str(int(body.max_attempts)),
    ]
    if body.keep_trying:
        cmd.append("--keep-trying")
    if body.max_total_attempts is not None:
        cmd += ["--max-total-attempts", str(int(body.max_total_attempts))]
    proc = subprocess.run(cmd, cwd=str(ROOT_DIR), capture_output=True, text=True, encoding="utf-8", errors="replace")
    return {"exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}


@app.post("/api/reset-db")
def reset_db(body: ResetDbIn) -> Dict[str, Any]:
    python_exe = _python_executable()
    cmd = [python_exe, str(ROOT_DIR / "agent_cli.py"), "--db", str(config.DB_PATH_DEFAULT), "reset-db"]
    if body.purge_workspace:
        cmd.append("--purge-workspace")
    if body.purge_tasks:
        cmd.append("--purge-tasks")
    if body.purge_logs:
        cmd.append("--purge-logs")
    proc = subprocess.run(cmd, cwd=str(ROOT_DIR), capture_output=True, text=True, encoding="utf-8", errors="replace")
    return {"exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}


@app.post("/api/export")
def export(body: ExportIn) -> Dict[str, Any]:
    python_exe = _python_executable()
    cmd = [python_exe, str(ROOT_DIR / "agent_cli.py"), "--db", str(config.DB_PATH_DEFAULT), "export", "--plan-id", body.plan_id]
    if body.include_reviews:
        cmd.append("--include-reviews")
    proc = subprocess.run(cmd, cwd=str(ROOT_DIR), capture_output=True, text=True, encoding="utf-8", errors="replace")
    return {"exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
