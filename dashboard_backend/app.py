from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import uuid
import hashlib
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import config
from core.db import apply_migrations, connect
from core.graph import build_plan_graph
from core.observability import get_plan_snapshot
from core.runtime_config import get_runtime_config
from core.util import ensure_dir, utc_now_iso
from core.workflow_graph import WorkflowQuery, build_workflow


ROOT_DIR = Path(__file__).resolve().parents[1]
RUN_STATE_PATH = config.STATE_DIR / "run_process.json"
CREATE_PLAN_STATE_PATH = config.STATE_DIR / "create_plan_process.json"

_DB_RESET_LOCK = threading.Lock()
_DB_RESETTING = False


@contextmanager
def _db_conn() -> sqlite3.Connection:
    global _DB_RESETTING
    with _DB_RESET_LOCK:
        if _DB_RESETTING:
            raise HTTPException(status_code=503, detail="DB reset in progress; retry in a moment")
    conn = _connect(config.DB_PATH_DEFAULT)
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass


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


def _write_create_plan_state(obj: Dict[str, Any]) -> None:
    ensure_dir(CREATE_PLAN_STATE_PATH.parent)
    CREATE_PLAN_STATE_PATH.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_create_plan_state() -> Optional[Dict[str, Any]]:
    if not CREATE_PLAN_STATE_PATH.exists():
        return None
    try:
        return json.loads(CREATE_PLAN_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def _hash_top_task(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8", errors="ignore")).hexdigest()


def _start_create_plan_process(*, db_path: Path, top_task: str, max_attempts: int, keep_trying: bool, max_total_attempts: Optional[int]) -> Dict[str, Any]:
    python_exe = _python_executable()
    job_id = str(uuid.uuid4())
    cmd = [
        python_exe,
        str(ROOT_DIR / "agent_cli.py"),
        "--db",
        str(db_path),
        "create-plan",
        "--top-task",
        top_task,
        "--max-attempts",
        str(int(max_attempts)),
    ]
    if keep_trying:
        cmd.append("--keep-trying")
    if max_total_attempts is not None:
        cmd += ["--max-total-attempts", str(int(max_total_attempts))]

    creationflags = 0x00000008  # CREATE_NO_WINDOW
    proc = subprocess.Popen(cmd, cwd=str(ROOT_DIR), creationflags=creationflags)
    state = {
        "job_id": job_id,
        "pid": int(proc.pid),
        "started_at": utc_now_iso(),
        "finished_at": None,
        "cmd": cmd,
        "plan_id": None,
        "status": "RUNNING",
        "last_error": None,
        "top_task_hash": _hash_top_task(top_task),
    }
    _write_create_plan_state(state)
    return state


def _coerce_int(v: Any, default: int) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _parse_meta_json(meta_json: Any) -> Dict[str, Any]:
    if meta_json is None:
        return {}
    if isinstance(meta_json, dict):
        return meta_json
    if isinstance(meta_json, str) and meta_json.strip():
        try:
            obj = json.loads(meta_json)
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}
    return {}


def infer_create_plan_progress(
    conn: sqlite3.Connection,
    *,
    plan_id: Optional[str],
) -> Dict[str, Any]:
    """
    Infer create-plan progress from llm_calls (PLAN_GEN/PLAN_REVIEW).
    Returns: {attempt, phase, review_attempt, last_llm_call, inferred_plan_id}
    """
    inferred_plan_id: Optional[str] = None
    if plan_id:
        inferred_plan_id = str(plan_id)
        rows = conn.execute(
            """
            SELECT created_at, plan_id, task_id, agent, scope, validator_error, error_code, error_message, meta_json
            FROM llm_calls
            WHERE plan_id = ? AND scope IN ('PLAN_GEN','PLAN_REVIEW')
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (str(plan_id),),
        ).fetchall()
    else:
        # MVP assumption (single-machine serial): most recent PLAN_GEN with plan_id NULL is the current attempt.
        rows = conn.execute(
            """
            SELECT created_at, plan_id, task_id, agent, scope, validator_error, error_code, error_message, meta_json
            FROM llm_calls
            WHERE plan_id IS NULL AND agent = 'xiaobo' AND scope = 'PLAN_GEN'
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchall()
        # Best-effort: if any PLAN_REVIEW exists, infer plan_id from it.
        row2 = conn.execute(
            """
            SELECT plan_id
            FROM llm_calls
            WHERE plan_id IS NOT NULL AND scope = 'PLAN_REVIEW'
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
        if row2 and row2["plan_id"]:
            inferred_plan_id = str(row2["plan_id"])

    last = dict(rows[0]) if rows else None
    if not last:
        return {"attempt": 1, "phase": "UNKNOWN", "review_attempt": 1, "last_llm_call": None, "inferred_plan_id": inferred_plan_id}

    meta = _parse_meta_json(last.get("meta_json"))
    attempt = _coerce_int(meta.get("attempt"), 1)
    review_attempt = _coerce_int(meta.get("review_attempt"), 1)
    phase = str(last.get("scope") or "UNKNOWN")
    if phase not in {"PLAN_GEN", "PLAN_REVIEW"}:
        phase = "UNKNOWN"

    last_llm_call = {
        "created_at": last.get("created_at"),
        "scope": last.get("scope"),
        "agent": last.get("agent"),
        "error_code": last.get("error_code"),
        "validator_error": last.get("validator_error"),
    }
    return {"attempt": attempt, "phase": phase, "review_attempt": review_attempt, "last_llm_call": last_llm_call, "inferred_plan_id": inferred_plan_id}


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


class RuntimeConfigUpdateIn(BaseModel):
    max_decomposition_depth: Optional[int] = None
    one_shot_threshold_person_days: Optional[float] = None


def _truncate(s: Optional[str], *, max_chars: int) -> Optional[str]:
    if s is None:
        return None
    if max_chars <= 0:
        return s
    if len(s) <= max_chars:
        return s
    return s[: max_chars - 1] + "..."


def resolve_prompt_sources(agent: str, scope: str) -> Dict[str, Any]:
    """
    Best-effort mapping from an agent/scope to shared/private prompt files.
    MVP convention:
    - shared: <repo>/shared_prompt.md
    - agent: <repo>/agents/<agent>_prompt.md
    """
    shared = ROOT_DIR / "shared_prompt.md"
    agent_file = ROOT_DIR / "agents" / f"{str(agent).strip()}_prompt.md"
    out: Dict[str, Any] = {
        "shared_prompt_path": str(shared) if shared.exists() else None,
        "agent_prompt_path": str(agent_file) if agent_file.exists() else None,
        "reason": None,
    }
    if not out["shared_prompt_path"]:
        out["reason"] = "shared_prompt.md not found"
    elif not out["agent_prompt_path"]:
        out["reason"] = f"agent prompt not found for {agent}"
    return out


def _safe_read_text_file(path_str: str, *, max_chars: int) -> Dict[str, Any]:
    """
    Read a text file from an allow-list of directories. Returns content (possibly truncated).
    """
    if not isinstance(path_str, str) or not path_str.strip():
        raise HTTPException(status_code=400, detail="path is required")
    p = Path(path_str).resolve()
    allow_roots = [ROOT_DIR.resolve(), (ROOT_DIR / "agents").resolve()]
    if not any(str(p).startswith(str(r)) for r in allow_roots):
        raise HTTPException(status_code=400, detail="path not allowed")
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    txt = p.read_text(encoding="utf-8", errors="replace")
    truncated = False
    if max_chars > 0 and len(txt) > max_chars:
        txt = txt[: max_chars - 1] + "..."
        truncated = True
    return {"path": str(p), "content": txt, "truncated": truncated, "ts": utc_now_iso()}


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
    with _db_conn() as conn:
        # Infer workflow version from stored nodes. v2 plans include CHECK nodes.
        rows = conn.execute(
            """
            SELECT
              p.plan_id,
              p.title,
              p.root_task_id,
              p.created_at,
              CASE
                WHEN EXISTS (
                  SELECT 1
                  FROM task_nodes tn
                  WHERE tn.plan_id = p.plan_id
                    AND (
                      tn.node_type = 'CHECK'
                      OR tn.review_target_task_id IS NOT NULL
                      OR tn.deliverable_spec_json IS NOT NULL
                      OR tn.acceptance_criteria_json IS NOT NULL
                      OR tn.estimated_person_days IS NOT NULL
                      OR tn.approved_artifact_id IS NOT NULL
                    )
                  LIMIT 1
                ) THEN 2
                ELSE 1
              END AS workflow_version
            FROM plans p
            ORDER BY p.created_at DESC
            """
        ).fetchall()
        return {"plans": [dict(r) for r in rows], "ts": utc_now_iso()}


@app.post("/api/runtime_config/update")
def update_runtime_config(body: RuntimeConfigUpdateIn) -> Dict[str, Any]:
    p = config.RUNTIME_CONFIG_PATH
    prev = p.read_text(encoding="utf-8") if p.exists() else None
    cur = _read_runtime_config()
    if not isinstance(cur, dict) or cur.get("_error"):
        cur = {}

    patch: Dict[str, Any] = {}
    if body.max_decomposition_depth is not None:
        patch["max_decomposition_depth"] = int(body.max_decomposition_depth)
    if body.one_shot_threshold_person_days is not None:
        patch["one_shot_threshold_person_days"] = float(body.one_shot_threshold_person_days)

    merged = dict(cur)
    merged.update(patch)

    try:
        p.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        # Validate (will raise on invalid values); rollback on failure.
        from core.runtime_config import load_runtime_config, reset_runtime_config_cache

        load_runtime_config(p)
        reset_runtime_config_cache()
    except Exception as exc:
        if prev is None:
            try:
                p.unlink(missing_ok=True)  # type: ignore[arg-type]
            except Exception:
                pass
        else:
            p.write_text(prev, encoding="utf-8")
        raise HTTPException(status_code=400, detail=f"invalid runtime_config update: {exc}")

    return {"ok": True, "runtime_config": _read_runtime_config(), "ts": utc_now_iso()}


@app.post("/api/plan/create_async")
def create_plan_async(body: CreatePlanIn) -> Dict[str, Any]:
    state = _read_create_plan_state()
    if state and isinstance(state.get("pid"), int) and _is_process_alive(int(state["pid"])):
        return {"started": False, "reason": "already running", "job_id": state.get("job_id"), "pid": state.get("pid")}
    state = _start_create_plan_process(
        db_path=config.DB_PATH_DEFAULT,
        top_task=body.top_task,
        max_attempts=int(body.max_attempts),
        keep_trying=bool(body.keep_trying),
        max_total_attempts=body.max_total_attempts,
    )
    return {"started": True, "job_id": state.get("job_id"), "pid": state.get("pid"), "ts": utc_now_iso()}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> Dict[str, Any]:
    state = _read_create_plan_state()
    if not state or str(state.get("job_id") or "") != str(job_id):
        raise HTTPException(status_code=404, detail="job not found")

    pid = state.get("pid")
    alive = bool(isinstance(pid, int) and _is_process_alive(int(pid)))
    status = "RUNNING" if alive else str(state.get("status") or "DONE")
    if status not in {"RUNNING", "DONE", "FAILED"}:
        status = "DONE" if not alive else "RUNNING"

    with _db_conn() as conn:
        plan_id = state.get("plan_id")
        prog = infer_create_plan_progress(conn, plan_id=str(plan_id) if isinstance(plan_id, str) and plan_id.strip() else None)
    inferred_plan_id = prog.get("inferred_plan_id")
    if not plan_id and isinstance(inferred_plan_id, str) and inferred_plan_id.strip():
        plan_id = inferred_plan_id.strip()
        state["plan_id"] = plan_id
        _write_create_plan_state(state)

    phase = prog.get("phase") or "UNKNOWN"
    attempt = int(prog.get("attempt") or 1)
    review_attempt = int(prog.get("review_attempt") or 1)

    hint = ""
    last_call = prog.get("last_llm_call")
    if status == "RUNNING":
        if phase == "PLAN_GEN":
            hint = "当前在 PLAN_GEN 生成计划。"
        elif phase == "PLAN_REVIEW":
            hint = "当前在 PLAN_REVIEW 审核计划。"
        else:
            hint = "正在运行（等待新的 LLM 调用记录）。"
    else:
        hint = "已结束。若未生成 plan_id，请查看 LLM Timeline 或 DB 的 llm_calls。"

    if isinstance(last_call, dict):
        ve = str(last_call.get("validator_error") or "").strip()
        ec = str(last_call.get("error_code") or "").strip()
        if ve or ec:
            hint = f"{hint} 建议打开 LLM Timeline 查看 validator_error/error_code。"

    return {
        "job_id": str(state.get("job_id")),
        "kind": "CREATE_PLAN",
        "status": status,
        "pid": pid,
        "started_at": state.get("started_at"),
        "finished_at": state.get("finished_at"),
        "plan_id": plan_id,
        "attempt": attempt,
        "phase": phase,
        "review_attempt": review_attempt,
        "last_llm_call": last_call,
        "hint": hint,
        "ts": utc_now_iso(),
    }


@app.get("/api/plan/{plan_id}/graph")
def get_plan_graph(plan_id: str) -> Dict[str, Any]:
    with _db_conn() as conn:
        try:
            res = build_plan_graph(conn, plan_id=plan_id)
        except Exception as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        return res.graph


@app.get("/api/plan_snapshot")
def plan_snapshot(plan_id: str = Query(..., min_length=1)) -> Dict[str, Any]:
    with _db_conn() as conn:
        cfg = get_runtime_config()
        try:
            snap = get_plan_snapshot(conn, str(plan_id), workflow_mode=str(cfg.workflow_mode))
        except Exception as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        return snap


@app.get("/api/task/{task_id}/llm")
def get_task_llm_calls(
    task_id: str,
    limit: int = Query(default=20, ge=1, le=200),
    max_chars: int = Query(default=50_000, ge=0, le=500_000),
) -> Dict[str, Any]:
    with _db_conn() as conn:
        rows = conn.execute(
        """
        SELECT
          llm_call_id,
          created_at,
          plan_id,
          task_id,
          agent,
          scope,
          prompt_text,
          response_text,
          parsed_json,
          normalized_json,
          validator_error,
          error_code,
          error_message
        FROM llm_calls
        WHERE task_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (task_id, int(limit)),
        ).fetchall()
        calls = []
        for r in rows:
            calls.append(
                {
                    "llm_call_id": r["llm_call_id"],
                    "created_at": r["created_at"],
                    "plan_id": r["plan_id"],
                    "task_id": r["task_id"],
                    "agent": r["agent"],
                    "scope": r["scope"],
                    "prompt_text": _truncate(r["prompt_text"], max_chars=max_chars),
                    "response_text": _truncate(r["response_text"], max_chars=max_chars),
                    "parsed_json": _truncate(r["parsed_json"], max_chars=max_chars),
                    "normalized_json": _truncate(r["normalized_json"], max_chars=max_chars),
                    "validator_error": r["validator_error"],
                    "error_code": r["error_code"],
                    "error_message": r["error_message"],
                }
            )
        return {"task_id": task_id, "calls": calls, "ts": utc_now_iso()}


@app.get("/api/llm_calls")
def get_llm_calls(
    llm_call_id: Optional[str] = Query(default=None),
    plan_id: Optional[str] = Query(default=None),
    scopes: Optional[str] = Query(default=None),
    agent: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    plan_id_missing: bool = Query(default=False),
    max_chars: int = Query(default=200_000, ge=0, le=500_000),
) -> Dict[str, Any]:
    with _db_conn() as conn:
        where: List[str] = []
        params: List[Any] = []

        if llm_call_id is not None and str(llm_call_id).strip():
            where.append("llm_call_id = ?")
            params.append(str(llm_call_id).strip())

        if plan_id_missing:
            where.append("plan_id IS NULL")
        elif plan_id is not None and str(plan_id).strip():
            where.append("plan_id = ?")
            params.append(str(plan_id).strip())

        if agent is not None and str(agent).strip():
            where.append("agent = ?")
            params.append(str(agent).strip())

        scope_list: List[str] = []
        if scopes:
            for s in str(scopes).split(","):
                s2 = s.strip()
                if s2:
                    scope_list.append(s2)
        if scope_list:
            where.append("scope IN (" + ",".join(["?"] * len(scope_list)) + ")")
            params.extend(scope_list)

        sql = """
            SELECT
              llm_call_id,
              created_at,
              plan_id,
              task_id,
              agent,
              scope,
              prompt_text,
              response_text,
              parsed_json,
              normalized_json,
              validator_error,
              error_code,
              error_message,
              meta_json
            FROM llm_calls
        """
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(int(limit))

        rows = conn.execute(sql, tuple(params)).fetchall()
        calls: List[Dict[str, Any]] = []
        for r in rows:
            src = resolve_prompt_sources(agent=r["agent"], scope=r["scope"])
            calls.append(
                {
                    "llm_call_id": r["llm_call_id"],
                    "created_at": r["created_at"],
                    "plan_id": r["plan_id"],
                    "task_id": r["task_id"],
                    "agent": r["agent"],
                    "scope": r["scope"],
                    "prompt_text": _truncate(r["prompt_text"], max_chars=max_chars),
                    "response_text": _truncate(r["response_text"], max_chars=max_chars),
                    "parsed_json": _truncate(r["parsed_json"], max_chars=max_chars),
                    "normalized_json": _truncate(r["normalized_json"], max_chars=max_chars),
                    "validator_error": r["validator_error"],
                    "error_code": r["error_code"],
                    "error_message": r["error_message"],
                    "meta_json": _truncate(r["meta_json"], max_chars=max_chars),
                    "shared_prompt_path": src.get("shared_prompt_path"),
                    "agent_prompt_path": src.get("agent_prompt_path"),
                    "prompt_source_reason": src.get("reason"),
                }
            )
        return {"calls": calls, "ts": utc_now_iso()}


@app.get("/api/prompt_file")
def get_prompt_file(
    path: str = Query(..., min_length=1),
    max_chars: int = Query(default=200_000, ge=0, le=500_000),
) -> Dict[str, Any]:
    return _safe_read_text_file(path, max_chars=int(max_chars))


@app.get("/api/workflow")
def get_workflow(
    plan_id: Optional[str] = Query(default=None),
    scopes: Optional[str] = Query(default=None),
    agent: Optional[str] = Query(default=None),
    only_errors: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=500),
    plan_id_missing: bool = Query(default=False),
) -> Dict[str, Any]:
    with _db_conn() as conn:
        scope_list: List[str] = []
        if scopes:
            for s in str(scopes).split(","):
                s2 = s.strip()
                if s2:
                    scope_list.append(s2)
        q = WorkflowQuery(
            plan_id=str(plan_id).strip() if plan_id and str(plan_id).strip() else None,
            plan_id_missing=bool(plan_id_missing),
            scopes=scope_list,
            agent=str(agent).strip() if agent and str(agent).strip() else None,
            only_errors=bool(only_errors),
            limit=int(limit),
        )
        return build_workflow(conn, q)


@app.get("/api/task/{task_id}/details")
def get_task_details(task_id: str) -> Dict[str, Any]:
    with _db_conn() as conn:
        node = conn.execute(
            """
            SELECT task_id, plan_id, title, node_type, status, owner_agent_id, blocked_reason, attempt_count, active_artifact_id
            FROM task_nodes
            WHERE task_id = ?
            """,
            (task_id,),
        ).fetchone()
        if not node:
            raise HTTPException(status_code=404, detail=f"task not found: {task_id}")

        active = None
        if node["active_artifact_id"]:
            a = conn.execute(
                "SELECT artifact_id, name, format, path, sha256, created_at FROM artifacts WHERE artifact_id=?",
                (node["active_artifact_id"],),
            ).fetchone()
            if a:
                active = dict(a)

        arts = conn.execute(
            """
            SELECT artifact_id, name, format, path, sha256, created_at
            FROM artifacts
            WHERE task_id = ?
            ORDER BY created_at DESC
            LIMIT 30
            """,
            (task_id,),
        ).fetchall()

        review_row = conn.execute(
            """
            SELECT total_score, action_required, summary, suggestions_json, created_at
            FROM reviews
            WHERE task_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (task_id,),
        ).fetchone()

        acceptance: list[str] = []
        review_obj = None
        if review_row:
            review_obj = {
                "total_score": int(review_row["total_score"] or 0),
                "action_required": review_row["action_required"],
                "summary": review_row["summary"],
                "created_at": review_row["created_at"],
            }
            sugs = []
            try:
                sugs = json.loads(review_row["suggestions_json"] or "[]")
            except Exception:
                sugs = []
            if isinstance(sugs, list):
                for s in sugs:
                    if not isinstance(s, dict):
                        continue
                    ac = s.get("acceptance_criteria")
                    if isinstance(ac, str) and ac.strip():
                        acceptance.append(ac.strip())

        if not acceptance:
            acceptance = ["xiaojing reviewer passed: total_score >= 90 and action_required = APPROVE"]

        required_docs_path = str(config.REQUIRED_DOCS_DIR / f"{task_id}.md")

        return {
            "task": dict(node),
            "active_artifact": active,
            "artifacts": [dict(a) for a in arts],
            "acceptance_criteria": acceptance[:10],
            "required_docs_path": required_docs_path,
            "artifact_dir": str(config.ARTIFACTS_DIR / task_id),
            "review_dir": str(config.REVIEWS_DIR / task_id),
            "last_review": review_obj,
            "ts": utc_now_iso(),
        }


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
    global _DB_RESETTING
    with _DB_RESET_LOCK:
        _DB_RESETTING = True
    python_exe = _python_executable()
    cmd = [python_exe, str(ROOT_DIR / "agent_cli.py"), "--db", str(config.DB_PATH_DEFAULT), "reset-db"]
    if body.purge_workspace:
        cmd.append("--purge-workspace")
    if body.purge_tasks:
        cmd.append("--purge-tasks")
    if body.purge_logs:
        cmd.append("--purge-logs")
    try:
        proc = subprocess.run(cmd, cwd=str(ROOT_DIR), capture_output=True, text=True, encoding="utf-8", errors="replace")
        return {"exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
    finally:
        with _DB_RESET_LOCK:
            _DB_RESETTING = False


@app.post("/api/export")
def export(body: ExportIn) -> Dict[str, Any]:
    python_exe = _python_executable()
    cmd = [python_exe, str(ROOT_DIR / "agent_cli.py"), "--db", str(config.DB_PATH_DEFAULT), "export", "--plan-id", body.plan_id]
    if body.include_reviews:
        cmd.append("--include-reviews")
    proc = subprocess.run(cmd, cwd=str(ROOT_DIR), capture_output=True, text=True, encoding="utf-8", errors="replace")
    return {"exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
