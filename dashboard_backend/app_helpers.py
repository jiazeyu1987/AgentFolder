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
from typing import Any, Dict, List, Optional, Tuple, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import config
from core.db import apply_migrations, connect
from core.audit_log import AuditQuery, log_audit, query_audit_events, query_top_tasks
from core.graph import build_plan_graph
from core.observability import get_plan_snapshot
from core.runtime_config import get_runtime_config
from core.util import ensure_dir, utc_now_iso
from core.util import stable_hash_text
from core.workflow_graph import WorkflowQuery, build_workflow
from core.queries.run_events_query import get_last_run_guardrail_hit, get_last_run_job_finished
from dashboard_backend.services.processes import is_process_alive as _is_process_alive
from dashboard_backend.services.processes import python_executable as _python_executable
from dashboard_backend.services.processes import read_json as _read_json
from dashboard_backend.services.processes import stop_run_process as _stop_run_process
from dashboard_backend.services.processes import write_json as _write_json
from dashboard_backend.services.create_plan_service import read_state as _create_plan_read_state
from dashboard_backend.services.create_plan_service import start_async as _create_plan_start_async
from dashboard_backend.services.create_plan_service import stop as _create_plan_stop
from dashboard_backend.services.create_plan_service import write_state as _create_plan_write_state
from dashboard_backend.services.create_plan_service import run_sync as _create_plan_run_sync
from dashboard_backend.services.run_service import start as _run_start_service
from dashboard_backend.services.run_service import status as _run_status_service
from dashboard_backend.services.run_service import stop as _run_stop_service
from dashboard_backend.services.run_service import run_once as _run_once_service
from dashboard_backend.services.run_service import reset_failed as _reset_failed_service
from dashboard_backend.services.run_service import reset_to_plan as _reset_to_plan_service


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


def _read_run_state() -> Optional[Dict[str, Any]]:
    return _read_json(RUN_STATE_PATH)


def _write_run_state(obj: Dict[str, Any]) -> None:
    _write_json(RUN_STATE_PATH, obj)


def _read_create_plan_state() -> Optional[Dict[str, Any]]:
    return _create_plan_read_state(CREATE_PLAN_STATE_PATH)


def _write_create_plan_state(obj: Dict[str, Any]) -> None:
    _create_plan_write_state(CREATE_PLAN_STATE_PATH, obj)


def _hash_top_task(s: str) -> str:
    # Use the same stable hash function as other parts of the system (audit/top_task grouping).
    return stable_hash_text(s or "")


def _resolve_top_task_from_request(top_task: str) -> str:
    """
    If top_task is blank, fallback to workspace/inputs/current_task.txt (UTF-8).
    This avoids Windows cmd encoding issues and allows UI to omit the input.
    """
    s = str(top_task or "").strip()
    if s:
        return s
    p = config.INPUTS_DIR / "current_task.txt"
    if not p.exists():
        raise HTTPException(status_code=400, detail=f"top_task is empty and {p} not found")
    try:
        txt = p.read_text(encoding="utf-8").strip()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"failed to read {p}: {exc}") from exc
    if not txt:
        raise HTTPException(status_code=400, detail=f"{p} is empty")
    return txt




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


def _infer_create_plan_progress_from_workflow_events(conn: sqlite3.Connection, *, job_id: str) -> Optional[Dict[str, Any]]:
    """
    Prefer event-first progress (workflow_events) over llm_calls inference.
    Returns None if no events exist for this job_id.
    """
    try:
        from core.workflow_events import fetch_workflow_events
    except Exception:
        return None

    events = fetch_workflow_events(conn, job_id=str(job_id), workflow="CREATE_PLAN", limit=80)
    if not events:
        return None

    def first_payload_field(name: str):
        for e in events:
            if name in e.payload:
                return e.payload.get(name)
        return None

    # Determine current step + attempt/stage from the newest event that carries a step.
    current_step = None
    for e in events:
        step = e.payload.get("step")
        if isinstance(step, str) and step.strip():
            current_step = step.strip()
            break
        if e.event_type == "STEP_STARTED":
            step2 = e.payload.get("step")
            if isinstance(step2, str) and step2.strip():
                current_step = step2.strip()
                break

    attempt = int(first_payload_field("attempt") or 1)
    stage = str(first_payload_field("stage") or "").strip().upper() or "UNKNOWN"
    stage_attempt = int(first_payload_field("stage_attempt") or 1)
    review_attempt = int(first_payload_field("review_attempt") or 1)
    rubric_attempt = int(first_payload_field("rubric_attempt") or 1)

    # phase is for legacy UI labels; derive from step/scope.
    scope = first_payload_field("scope")
    if isinstance(scope, str) and scope.strip() in {"PLAN_RUBRIC", "PLAN_GEN", "PLAN_REVIEW"}:
        phase = scope.strip()
    elif isinstance(current_step, str):
        if current_step == "PLAN_RUBRIC":
            phase = "PLAN_RUBRIC"
        elif "REVIEW" in current_step:
            phase = "PLAN_REVIEW"
        else:
            phase = "PLAN_GEN"
    else:
        phase = "UNKNOWN"

    inferred_plan_id = None
    for e in events:
        if e.plan_id:
            inferred_plan_id = str(e.plan_id)
            break

    last_event = {
        "created_at": events[0].created_at,
        "event_type": events[0].event_type,
        "severity": events[0].severity,
        "message": events[0].message,
        "payload": events[0].payload,
    }

    last_decision = None
    for e in events:
        if e.event_type == "DECISION_MADE":
            last_decision = {
                "created_at": e.created_at,
                "message": e.message,
                "payload": e.payload,
            }
            break

    last_error = None
    for e in events:
        if e.event_type == "ERROR_RAISED":
            last_error = {
                "created_at": e.created_at,
                "message": e.message,
                "payload": e.payload,
            }
            break

    retry_reason = ""
    if isinstance(last_decision, dict):
        pr = last_decision.get("payload") or {}
        if isinstance(pr, dict):
            rr = pr.get("retry_reason")
            if isinstance(rr, str) and rr.strip():
                retry_reason = rr.strip()[:200]

    return {
        "attempt": attempt,
        "phase": phase,
        "stage": stage,
        "stage_attempt": stage_attempt,
        "review_attempt": review_attempt,
        "rubric_attempt": rubric_attempt,
        "current_step": current_step or "UNKNOWN",
        "retry_reason": retry_reason,
        "last_event": last_event,
        "last_decision": last_decision,
        "last_error": last_error,
        "inferred_plan_id": inferred_plan_id,
    }


def infer_create_plan_progress(
    conn: sqlite3.Connection,
    *,
    plan_id: Optional[str],
    started_at: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Infer create-plan progress from llm_calls (PLAN_RUBRIC/PLAN_GEN/PLAN_REVIEW).
    Returns: {attempt, phase, stage, stage_attempt, review_attempt, rubric_attempt, last_llm_call, inferred_plan_id}
    """
    inferred_plan_id: Optional[str] = None

    # Preferred: if we know when the job started, infer from the most recent PLAN_GEN/PLAN_REVIEW since then.
    # This also correctly handles the case where a new plan_id is generated on the next attempt.
    if isinstance(started_at, str) and started_at.strip():
        r = conn.execute(
            """
            SELECT created_at, plan_id, task_id, agent, scope, validator_error, error_code, error_message, meta_json
            FROM llm_calls
            WHERE created_at >= ? AND scope IN ('PLAN_RUBRIC','PLAN_GEN','PLAN_REVIEW')
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (started_at.strip(),),
        ).fetchone()
        rows = [r] if r else []
        if r and r["plan_id"]:
            inferred_plan_id = str(r["plan_id"])
        elif plan_id:
            inferred_plan_id = str(plan_id)
    elif plan_id:
        inferred_plan_id = str(plan_id)
        rows = conn.execute(
            """
            SELECT created_at, plan_id, task_id, agent, scope, validator_error, error_code, error_message, meta_json
            FROM llm_calls
            WHERE plan_id = ? AND scope IN ('PLAN_RUBRIC','PLAN_GEN','PLAN_REVIEW')
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
            WHERE plan_id IS NULL AND scope IN ('PLAN_RUBRIC','PLAN_GEN')
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
    rubric_attempt = _coerce_int(meta.get("rubric_attempt"), 1)
    stage = str(meta.get("stage") or "").strip().upper() or "UNKNOWN"
    stage_attempt = _coerce_int(meta.get("stage_attempt"), 1)
    phase = str(last.get("scope") or "UNKNOWN")
    if phase not in {"PLAN_RUBRIC", "PLAN_GEN", "PLAN_REVIEW"}:
        phase = "UNKNOWN"

    last_llm_call = {
        "created_at": last.get("created_at"),
        "scope": last.get("scope"),
        "agent": last.get("agent"),
        "error_code": last.get("error_code"),
        "validator_error": last.get("validator_error"),
    }
    return {
        "attempt": attempt,
        "phase": phase,
        "stage": stage,
        "stage_attempt": stage_attempt,
        "review_attempt": review_attempt,
        "rubric_attempt": rubric_attempt,
        "last_llm_call": last_llm_call,
        "inferred_plan_id": inferred_plan_id,
    }


class CreatePlanIn(BaseModel):
    top_task: str
    max_attempts: Optional[int] = None
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
    create_plan_max_attempts: Optional[int] = None
    task_max_attempts: Optional[int] = None
    task_review_pass_score: Optional[int] = None
    task_review_notes_max_chars: Optional[int] = None
    plan_review_pass_score: Optional[int] = None
    plan_review_notes_max_chars: Optional[int] = None


class ResetFailedIn(BaseModel):
    plan_id: str
    include_blocked: bool = False
    reset_attempts: bool = False


class ResetToPlanIn(BaseModel):
    plan_id: str


class GraphPlanResp(BaseModel):
    plan_id: str
    title: str
    root_task_id: str
    created_at: str


class GraphRunningResp(BaseModel):
    task_id: Optional[str]
    since: Optional[str]
    source: str


class GraphNodeResp(BaseModel):
    task_id: str
    title: str
    node_type: str
    status: str
    owner_agent_id: str
    priority: int
    blocked_reason: Optional[str]
    attempt_count: int
    tags: List[str]
    active_artifact: Optional[Dict[str, Any]]
    missing_inputs: List[Dict[str, Any]]
    required_docs_path: str
    last_error: Optional[Dict[str, Any]]
    last_review: Optional[Dict[str, Any]]
    artifact_dir: str
    review_dir: str
    is_running: bool


class GraphEdgeResp(BaseModel):
    edge_id: str
    from_task_id: str
    to_task_id: str
    edge_type: str
    metadata: Dict[str, Any]


class GraphV1Resp(BaseModel):
    schema_version: Literal["graph_v1"]
    plan: GraphPlanResp
    running: GraphRunningResp
    nodes: List[GraphNodeResp]
    edges: List[GraphEdgeResp]
    ts: str
    paths: Optional[Dict[str, str]] = None


class WorkflowPlanResp(BaseModel):
    plan_id: Optional[str]
    title: Optional[str]
    workflow_mode: str


class WorkflowNodeResp(BaseModel):
    llm_call_id: str
    source_llm_call_id: Optional[str] = None
    created_at: str
    plan_id: Optional[str]
    task_id: Optional[str]
    task_title: Optional[str]
    agent: str
    scope: str
    attempt: int
    review_attempt: int
    stage: Optional[str] = None
    stage_attempt: Optional[int] = None
    node_kind: Optional[str] = None
    error_code: Optional[str]
    validator_error: Optional[str]
    total_score: Optional[int] = None
    action_required: Optional[str] = None


class WorkflowEdgeResp(BaseModel):
    from_: str = Field(..., alias="from")
    to: str
    edge_type: Literal["NEXT", "PAIR", "STAGE_NEXT"]
    stage: Optional[str] = None


class WorkflowGroupResp(BaseModel):
    group_type: Literal["ATTEMPT"]
    id: str
    attempt: int
    node_ids: List[str]


class WorkflowV1Resp(BaseModel):
    schema_version: Literal["workflow_v1"]
    plan: WorkflowPlanResp
    nodes: List[WorkflowNodeResp]
    edges: List[WorkflowEdgeResp]
    groups: List[WorkflowGroupResp]
    total_rows: Optional[int] = None
    returned_rows: Optional[int] = None
    ts: str


def _job_status_from_state(*, alive: bool, state_status: Optional[str], last_error: Any) -> str:
    if alive:
        return "RUNNING"
    s = str(state_status or "").strip().upper()
    if s == "FAILED":
        return "FAILED"
    # If process is dead but state still says RUNNING, treat it as DONE unless we captured an error.
    if last_error:
        return "FAILED"
    return "DONE"


def _truncate(s: Optional[str], *, max_chars: int) -> Optional[str]:
    if s is None:
        return None
    if max_chars <= 0:
        return s
    if len(s) <= max_chars:
        return s
    return s[: max_chars - 1] + "..."


def resolve_prompt_sources(agent: str, scope: str, *, prompt_variant: Optional[str] = None) -> Dict[str, Any]:
    """
    Best-effort mapping from an agent/scope to shared/private prompt files.
    MVP convention:
    - shared: <repo>/shared_prompt.md
    - agent: <repo>/agents/<agent>_prompt.md
    """
    shared = ROOT_DIR / "shared_prompt.md"
    agent_file = ROOT_DIR / "agents" / f"{str(agent).strip()}_prompt.md"
    pv = str(prompt_variant or "").strip()
    if pv:
        mapping = {
            "TASK_ACTION_INIT": ROOT_DIR / "agents" / "xiaobo_task_action_init.md",
            "TASK_ACTION_ITERATE": ROOT_DIR / "agents" / "xiaobo_task_action_iterate.md",
            "TASK_REVIEW_RUBRIC_BUILD": ROOT_DIR / "agents" / "xiaojing_task_review_rubric_build.md",
            "TASK_REVIEW_SCORE": ROOT_DIR / "agents" / "xiaojing_task_review_score.md",
        }
        if pv in mapping:
            agent_file = mapping[pv]
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
    allow_roots = [ROOT_DIR.resolve(), (ROOT_DIR / "agents").resolve(), config.REVIEW_NOTES_DIR.resolve()]
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


def query_plan_errors(
    conn: sqlite3.Connection,
    *,
    plan_id: Optional[str],
    plan_id_missing: bool,
    task_id: Optional[str] = None,
    include_related: bool = False,
    limit: int,
) -> List[Dict[str, Any]]:
    """
    Unified error feed for UI.
    Sources:
      - task_events(event_type='ERROR')  [workflow/skill/input errors]
      - llm_calls(error_code/validator_error) [LLM contract/parse errors]
    """
    out: List[Dict[str, Any]] = []
    limit = max(1, min(int(limit), 500))
    task_id_norm = str(task_id).strip() if task_id and str(task_id).strip() else None
    task_ids: List[str] | None = None
    if task_id_norm:
        task_ids = [task_id_norm]
        if include_related and plan_id and str(plan_id).strip():
            # Include related CHECK nodes that review this task (v2 workflow).
            rows_rel = conn.execute(
                """
                SELECT task_id
                FROM task_nodes
                WHERE plan_id = ?
                  AND active_branch = 1
                  AND review_target_task_id = ?
                """,
                (str(plan_id).strip(), task_id_norm),
            ).fetchall()
            for r in rows_rel:
                tid = str(r["task_id"]) if r and r["task_id"] is not None else ""
                if tid and tid not in task_ids:
                    task_ids.append(tid)

    # 1) task_events ERROR
    if plan_id and str(plan_id).strip():
        if task_ids:
            placeholders = ",".join(["?"] * len(task_ids))
            rows = conn.execute(
                """
                SELECT
                  e.event_id,
                  e.created_at,
                  e.plan_id,
                  e.task_id,
                  tn.title AS task_title,
                  e.payload_json
                FROM task_events e
                LEFT JOIN task_nodes tn ON tn.task_id = e.task_id
                WHERE e.plan_id = ? AND e.event_type = 'ERROR' AND e.task_id IN (PLACEHOLDERS)
                ORDER BY e.created_at DESC
                LIMIT ?
                """.replace("PLACEHOLDERS", placeholders),
                (str(plan_id).strip(), *task_ids, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT
                  e.event_id,
                  e.created_at,
                  e.plan_id,
                  e.task_id,
                  tn.title AS task_title,
                  e.payload_json
                FROM task_events e
                LEFT JOIN task_nodes tn ON tn.task_id = e.task_id
                WHERE e.plan_id = ? AND e.event_type = 'ERROR'
                ORDER BY e.created_at DESC
                LIMIT ?
                """,
                (str(plan_id).strip(), limit),
            ).fetchall()
        for r in rows:
            payload: Dict[str, Any] = {}
            try:
                payload = json.loads(r["payload_json"] or "{}")
            except Exception:
                payload = {"raw": r["payload_json"]}
            err_code = payload.get("error_code")
            msg = payload.get("message")
            ctx = payload.get("context") if isinstance(payload.get("context"), dict) else {}
            hint = ctx.get("hint") if isinstance(ctx, dict) else None
            # Best-effort: if error was produced by an LLM step, allow linking back to llm_calls/workflow.
            scope = ctx.get("scope") if isinstance(ctx, dict) else None
            agent = ctx.get("agent") if isinstance(ctx, dict) else None
            llm_call_id = ctx.get("llm_call_id") if isinstance(ctx, dict) else None
            validator_error = ctx.get("validator_error") if isinstance(ctx, dict) else None
            out.append(
                {
                    "source": "TASK_EVENT",
                    "created_at": r["created_at"],
                    "plan_id": r["plan_id"],
                    "task_id": r["task_id"],
                    "task_title": r["task_title"],
                    "llm_call_id": llm_call_id,
                    "scope": scope,
                    "agent": agent,
                    "error_code": err_code,
                    "message": _truncate(str(msg) if msg is not None else None, max_chars=500),
                    "hint": hint,
                    "validator_error": _truncate(str(validator_error) if validator_error is not None else None, max_chars=500),
                    "error_message": _truncate(str(msg) if msg is not None else None, max_chars=500),
                }
            )

    # 2) llm_calls errors
    where: List[str] = ["((error_code IS NOT NULL AND error_code != '') OR (validator_error IS NOT NULL AND validator_error != ''))"]
    params: List[Any] = []
    if plan_id_missing:
        where.append("plan_id IS NULL")
    elif plan_id and str(plan_id).strip():
        where.append("plan_id = ?")
        params.append(str(plan_id).strip())
    if task_ids:
        where.append("task_id IN (" + ",".join(["?"] * len(task_ids)) + ")")
        params.extend(task_ids)
    rows = conn.execute(
        f"""
        SELECT
          llm_call_id,
          created_at,
          plan_id,
          task_id,
          agent,
          scope,
          error_code,
          error_message,
          validator_error
        FROM llm_calls
        WHERE {' AND '.join(where)}
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    for r in rows:
        task_title = None
        if r["task_id"]:
            tn = conn.execute("SELECT title FROM task_nodes WHERE task_id = ?", (r["task_id"],)).fetchone()
            if tn:
                task_title = tn["title"]
        msg = r["error_message"] or r["validator_error"]
        out.append(
            {
                "source": "LLM_CALL",
                "created_at": r["created_at"],
                "plan_id": r["plan_id"],
                "task_id": r["task_id"],
                "task_title": task_title,
                "llm_call_id": r["llm_call_id"],
                "scope": r["scope"],
                "agent": r["agent"],
                "error_code": r["error_code"] or ("VALIDATOR_ERROR" if r["validator_error"] else None),
                "message": _truncate(str(msg) if msg is not None else None, max_chars=500),
                "hint": None,
                "validator_error": _truncate(str(r["validator_error"]) if r["validator_error"] is not None else None, max_chars=500),
                "error_message": _truncate(str(r["error_message"]) if r["error_message"] is not None else None, max_chars=500),
            }
        )

    # merge + sort
    out.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return out[:limit]


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


def get_plans() -> Dict[str, Any]:
    with _db_conn() as conn:
        # Only return the latest plan per *normalized* title (hide historical versions).
        # Normalization intentionally ignores a trailing "(...)" id-like suffix and collapses whitespace.
        # (We do not delete history here; this is a display/listing policy.)
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

        from core.util import normalize_title, stable_hash_text

        seen: set[str] = set()
        out = []
        for r in rows:
            d = dict(r)
            key = normalize_title(d.get("title") or "")
            if not key:
                # Keep untitled plans, but dedupe by plan_id naturally.
                d["top_task_hash"] = None
                out.append(d)
                continue
            if key in seen:
                continue
            seen.add(key)
            # Prefer the real top_task_hash recorded during create-plan (llm_calls), because plan.title
            # may differ from the user's top_task (e.g. English titles, extra descriptors).
            h = None
            try:
                rr = conn.execute(
                    """
                    SELECT top_task_hash
                    FROM llm_calls
                    WHERE plan_id = ?
                      AND top_task_hash IS NOT NULL
                      AND top_task_hash != ''
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (d.get("plan_id"),),
                ).fetchone()
                if rr and rr[0]:
                    h = str(rr[0])
            except Exception:
                h = None
            # For legacy data (llm_calls.top_task_hash missing), fall back to a stable hash of the
            # normalized plan title, so UI can still group multiple versions by title.
            d["top_task_hash"] = h or stable_hash_text(key)
            out.append(d)

        return {"plans": out, "ts": utc_now_iso()}


def get_errors(
    plan_id: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    plan_id_missing: bool = Query(default=False),
    task_id: Optional[str] = Query(default=None),
    include_related: bool = Query(default=False),
) -> Dict[str, Any]:
    with _db_conn() as conn:
        items = query_plan_errors(
            conn,
            plan_id=str(plan_id).strip() if plan_id and str(plan_id).strip() else None,
            plan_id_missing=bool(plan_id_missing),
            task_id=str(task_id).strip() if task_id and str(task_id).strip() else None,
            include_related=bool(include_related),
            limit=int(limit),
        )
        return {"errors": items, "ts": utc_now_iso()}


def get_audit(
    top_task_hash: Optional[str] = Query(default=None),
    plan_id: Optional[str] = Query(default=None),
    job_id: Optional[str] = Query(default=None),
    category: Optional[str] = Query(default=None),
    limit: int = Query(default=300, ge=1, le=2000),
) -> Dict[str, Any]:
    with _db_conn() as conn:
        items = query_audit_events(
            conn,
            AuditQuery(
                top_task_hash=str(top_task_hash).strip() if top_task_hash and str(top_task_hash).strip() else None,
                plan_id=str(plan_id).strip() if plan_id and str(plan_id).strip() else None,
                job_id=str(job_id).strip() if job_id and str(job_id).strip() else None,
                category=str(category).strip() if category and str(category).strip() else None,
                limit=int(limit),
            ),
        )
        return {"events": items, "ts": utc_now_iso()}


def get_top_tasks(limit: int = Query(default=50, ge=1, le=200)) -> Dict[str, Any]:
    with _db_conn() as conn:
        items = query_top_tasks(conn, limit=int(limit))
        return {"top_tasks": items, "ts": utc_now_iso()}

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
    if body.create_plan_max_attempts is not None:
        patch["create_plan_max_attempts"] = int(body.create_plan_max_attempts)
    if body.task_max_attempts is not None:
        patch["task_max_attempts"] = int(body.task_max_attempts)
    if body.plan_review_pass_score is not None:
        patch["plan_review_pass_score"] = int(body.plan_review_pass_score)
    if body.plan_review_notes_max_chars is not None:
        patch["plan_review_notes_max_chars"] = int(body.plan_review_notes_max_chars)
    if body.task_review_pass_score is not None:
        patch["task_review_pass_score"] = int(body.task_review_pass_score)
    if body.task_review_notes_max_chars is not None:
        patch["task_review_notes_max_chars"] = int(body.task_review_notes_max_chars)

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

    try:
        with _db_conn() as conn:
            log_audit(
                conn,
                category="API_CALL",
                action="CONFIG_UPDATE",
                message="runtime_config updated",
                payload={k: v for k, v in patch.items()},
            )
    except Exception:
        pass

    return {"ok": True, "runtime_config": _read_runtime_config(), "ts": utc_now_iso()}


def create_plan_async(body: CreatePlanIn) -> Dict[str, Any]:
    resolved_top_task = _resolve_top_task_from_request(body.top_task)
    cfg = get_runtime_config()
    max_attempts_eff = int(body.max_attempts) if body.max_attempts is not None else int(cfg.create_plan_max_attempts)
    res = _create_plan_start_async(
        state_path=CREATE_PLAN_STATE_PATH,
        job_root=(config.STATE_DIR / "jobs"),
        root_dir=ROOT_DIR,
        db_path=config.DB_PATH_DEFAULT,
        top_task=resolved_top_task,
        max_attempts=max_attempts_eff,
        keep_trying=bool(body.keep_trying),
        max_total_attempts=body.max_total_attempts,
    )
    try:
        with _db_conn() as conn:
            log_audit(
                conn,
                category="API_CALL",
                action="CREATE_PLAN_START",
                message="create-plan start",
                top_task_hash=_hash_top_task(resolved_top_task),
                top_task_title=str(resolved_top_task).strip()[:200],
                job_id=str(res.get("job_id") or ""),
                payload={
                    "max_attempts": int(body.max_attempts),
                    "keep_trying": bool(body.keep_trying),
                    "max_total_attempts": body.max_total_attempts,
                },
            )
    except Exception:
        pass
    return res


def get_job(job_id: str) -> Dict[str, Any]:
    state = _read_create_plan_state()
    if not state or str(state.get("job_id") or "") != str(job_id):
        raise HTTPException(status_code=404, detail="job not found")

    pid = state.get("pid")
    alive = bool(isinstance(pid, int) and _is_process_alive(int(pid)))
    # If process exited, try to read persisted exit code from wrapper.
    exit_code = None
    exit_path = state.get("exit_path")
    if not alive and isinstance(exit_path, str) and exit_path.strip():
        p = Path(exit_path)
        if p.exists():
            try:
                obj = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(obj, dict):
                    exit_code = obj.get("exit_code")
            except Exception:
                exit_code = None
            if not state.get("finished_at"):
                try:
                    state["finished_at"] = utc_now_iso()
                except Exception:
                    pass
            if exit_code not in (None, 0, "0"):
                state["last_error"] = {"exit_code": exit_code, "log_path": state.get("log_path")}

    status = _job_status_from_state(alive=alive, state_status=state.get("status"), last_error=state.get("last_error"))
    if status != state.get("status"):
        state["status"] = status
    if not alive and not state.get("finished_at"):
        state["finished_at"] = utc_now_iso()
    _write_create_plan_state(state)

    with _db_conn() as conn:
        plan_id = state.get("plan_id")
        started_at = state.get("started_at")
        llm_prog = infer_create_plan_progress(
            conn,
            plan_id=str(plan_id) if isinstance(plan_id, str) and plan_id.strip() else None,
            started_at=str(started_at) if isinstance(started_at, str) and started_at.strip() else None,
        )
        events_prog = _infer_create_plan_progress_from_workflow_events(conn, job_id=str(job_id))
    prog = events_prog or llm_prog

    inferred_plan_id = (prog.get("inferred_plan_id") if isinstance(prog, dict) else None) or (llm_prog.get("inferred_plan_id") if isinstance(llm_prog, dict) else None)
    if not plan_id and isinstance(inferred_plan_id, str) and inferred_plan_id.strip():
        plan_id = inferred_plan_id.strip()
        state["plan_id"] = plan_id
        _write_create_plan_state(state)
    # Plan ID can change across attempts; keep state updated.
    if plan_id and isinstance(inferred_plan_id, str) and inferred_plan_id.strip() and inferred_plan_id.strip() != str(plan_id).strip():
        plan_id = inferred_plan_id.strip()
        state["plan_id"] = plan_id
        _write_create_plan_state(state)

    phase = (prog.get("phase") or "UNKNOWN") if isinstance(prog, dict) else "UNKNOWN"
    attempt = int((prog.get("attempt") or 1) if isinstance(prog, dict) else 1)
    review_attempt = int((prog.get("review_attempt") or 1) if isinstance(prog, dict) else 1)
    rubric_attempt = int((prog.get("rubric_attempt") or 1) if isinstance(prog, dict) else 1)
    stage = (prog.get("stage") or "UNKNOWN") if isinstance(prog, dict) else "UNKNOWN"
    stage_attempt = int((prog.get("stage_attempt") or 1) if isinstance(prog, dict) else 1)
    current_step = (prog.get("current_step") if isinstance(prog, dict) else None) or None
    last_event = (prog.get("last_event") if isinstance(prog, dict) else None) or None
    last_decision = (prog.get("last_decision") if isinstance(prog, dict) else None) or None
    last_error = (prog.get("last_error") if isinstance(prog, dict) else None) or None

    hint = ""
    last_call = llm_prog.get("last_llm_call") if isinstance(llm_prog, dict) else None
    retry_reason = str(prog.get("retry_reason") or "").strip() if isinstance(prog, dict) else ""
    if status == "RUNNING":
        if isinstance(current_step, str) and current_step.strip() and current_step.strip().upper() != "UNKNOWN":
            hint = f"当前步骤：{current_step}"
        elif phase == "PLAN_RUBRIC":
            hint = f"当前在 PLAN_RUBRIC（rubric_attempt={rubric_attempt}）生成评分标准。"
        elif phase == "PLAN_GEN":
            hint = f"当前在 PLAN_GEN（stage={stage}, stage_attempt={stage_attempt}）生成计划。"
        elif phase == "PLAN_REVIEW":
            hint = f"当前在 PLAN_REVIEW（stage={stage}, review_attempt={review_attempt}）审核计划。"
        else:
            hint = "正在运行（等待新的事件/LLM 记录）。"
    else:
        hint = "已结束。若未生成 plan_id，请查看 LLM Timeline 或 DB 的 llm_calls。"

    if isinstance(last_call, dict):
        ve = str(last_call.get("validator_error") or "").strip()
        ec = str(last_call.get("error_code") or "").strip()
        if not retry_reason and status == "RUNNING" and phase == "PLAN_REVIEW" and (ve or ec):
            retry_reason = (ec or ve)[:200]
        if ve or ec:
            hint = f"{hint} 建议打开 LLM Timeline 查看 validator_error/error_code。"

    return {
        "job_id": str(state.get("job_id")),
        "kind": "CREATE_PLAN",
        "status": status,
        "pid": pid,
        "started_at": state.get("started_at"),
        "finished_at": state.get("finished_at"),
        "exit_code": exit_code,
        "log_path": state.get("log_path"),
        "plan_id": plan_id,
        "attempt": attempt,
        "phase": phase,
        "stage": stage,
        "stage_attempt": stage_attempt,
        "rubric_attempt": rubric_attempt,
        "review_attempt": review_attempt,
        "current_step": current_step,
        "last_event": last_event,
        "last_decision": last_decision,
        "last_error": last_error,
        "last_llm_call": last_call,
        "hint": hint,
        "retry_reason": retry_reason,
        "ts": utc_now_iso(),
    }


def get_job_log(
    job_id: str = Query(..., min_length=1),
    max_chars: int = Query(default=50_000, ge=0, le=200_000),
) -> Dict[str, Any]:
    state = _read_create_plan_state()
    if not state or str(state.get("job_id") or "") != str(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    p = state.get("log_path")
    if not isinstance(p, str) or not p.strip():
        raise HTTPException(status_code=404, detail="job log not available")
    return _safe_read_text_file(p, max_chars=int(max_chars))


def get_plan_graph(plan_id: str) -> Dict[str, Any]:
    with _db_conn() as conn:
        try:
            res = build_plan_graph(conn, plan_id=plan_id)
        except Exception as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        return res.graph


def plan_snapshot(plan_id: str = Query(..., min_length=1)) -> Dict[str, Any]:
    with _db_conn() as conn:
        cfg = get_runtime_config()
        try:
            snap = get_plan_snapshot(conn, str(plan_id), workflow_mode=str(cfg.workflow_mode))
        except Exception as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        return snap


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
            try:
                meta_obj = json.loads(r["meta_json"] or "{}") if r["meta_json"] else {}
            except Exception:
                meta_obj = {}
            pv = meta_obj.get("prompt_variant") if isinstance(meta_obj, dict) else None
            src = resolve_prompt_sources(agent=r["agent"], scope=r["scope"], prompt_variant=pv)
            review_note_path = None
            if r["scope"] == "PLAN_REVIEW" and r["plan_id"]:
                try:
                    meta = meta_obj if isinstance(meta_obj, dict) else {}
                except Exception:
                    meta = {}
                attempt = meta.get("attempt")
                try:
                    attempt_i = int(attempt) if attempt is not None else None
                except Exception:
                    attempt_i = None
                if attempt_i and attempt_i > 0:
                    p = config.REVIEW_NOTES_DIR / str(r["plan_id"]) / f"plan_review_attempt_{attempt_i}.md"
                    if p.exists():
                        review_note_path = str(p)
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
                    "plan_review_attempt_path": review_note_path,
                }
            )
        return {"calls": calls, "ts": utc_now_iso()}


def get_prompt_file(
    path: str = Query(..., min_length=1),
    max_chars: int = Query(default=200_000, ge=0, le=500_000),
) -> Dict[str, Any]:
    return _safe_read_text_file(path, max_chars=int(max_chars))


def get_workflow(
    plan_id: Optional[str] = Query(default=None),
    job_id: Optional[str] = Query(default=None),
    top_task_hash: Optional[str] = Query(default=None),
    scopes: Optional[str] = Query(default=None),
    agent: Optional[str] = Query(default=None),
    only_errors: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=500),
    plan_id_missing: bool = Query(default=False),
) -> Dict[str, Any]:
    with _db_conn() as conn:
        started_at = None
        if job_id is not None and str(job_id).strip():
            st = _read_create_plan_state()
            if st and str(st.get("job_id") or "") == str(job_id).strip():
                sa = st.get("started_at")
                if isinstance(sa, str) and sa.strip():
                    started_at = sa.strip()
        scope_list: List[str] = []
        if scopes:
            for s in str(scopes).split(","):
                s2 = s.strip()
                if s2:
                    scope_list.append(s2)
        q = WorkflowQuery(
            plan_id=str(plan_id).strip() if plan_id and str(plan_id).strip() else None,
            plan_id_missing=bool(plan_id_missing),
            started_at=started_at,
            top_task_hash=str(top_task_hash).strip() if top_task_hash and str(top_task_hash).strip() else None,
            scopes=scope_list,
            agent=str(agent).strip() if agent and str(agent).strip() else None,
            only_errors=bool(only_errors),
            limit=int(limit),
        )
        return build_workflow(conn, q)


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
            from core.runtime_config import get_runtime_config

            cfg = get_runtime_config()
            acceptance = [f"xiaojing reviewer passed: total_score >= {cfg.plan_review_pass_score}"]

        required_docs_path = str(config.REQUIRED_DOCS_DIR / f"{task_id}.md")

        # Show runtime dependencies (DEPENDS_ON) so users can see which upstream tasks provide inputs.
        deps_rows = conn.execute(
            """
            SELECT
              e.from_task_id AS task_id,
              tn.title,
              tn.node_type,
              tn.status,
              tn.active_artifact_id,
              tn.approved_artifact_id
            FROM task_edges e
            JOIN task_nodes tn ON tn.task_id = e.from_task_id
            WHERE e.plan_id = ?
              AND e.to_task_id = ?
              AND e.edge_type = 'DEPENDS_ON'
              AND tn.active_branch = 1
            ORDER BY tn.priority DESC, tn.created_at ASC
            """,
            (node["plan_id"], task_id),
        ).fetchall()

        def _artifact_brief(artifact_id: Optional[str]) -> Optional[Dict[str, Any]]:
            if not artifact_id:
                return None
            a = conn.execute(
                "SELECT artifact_id, name, format, path, sha256, created_at FROM artifacts WHERE artifact_id=?",
                (artifact_id,),
            ).fetchone()
            return dict(a) if a else None

        depends_on = []
        for r in deps_rows:
            depends_on.append(
                {
                    "task_id": r["task_id"],
                    "title": r["title"],
                    "node_type": r["node_type"],
                    "status": r["status"],
                    "approved_artifact": _artifact_brief(r["approved_artifact_id"]) if "approved_artifact_id" in r.keys() else None,
                    "active_artifact": _artifact_brief(r["active_artifact_id"]),
                }
            )

        return {
            "task": dict(node),
            "active_artifact": active,
            "artifacts": [dict(a) for a in arts],
            "acceptance_criteria": acceptance[:10],
            "depends_on": depends_on,
            "required_docs_path": required_docs_path,
            "artifact_dir": str(config.ARTIFACTS_DIR / task_id),
            "review_dir": str(config.REVIEWS_DIR / task_id),
            "last_review": review_obj,
            "ts": utc_now_iso(),
        }


def run_start(body: RunStartIn) -> Dict[str, Any]:
    state = _run_start_service(
        run_state_path=RUN_STATE_PATH,
        db_path=config.DB_PATH_DEFAULT,
        plan_path=config.PLAN_PATH_DEFAULT,
        max_iterations=int(body.max_iterations),
    )
    try:
        with _db_conn() as conn:
            log_audit(conn, category="API_CALL", action="RUN_START", message="run start", payload={"max_iterations": int(body.max_iterations)})
    except Exception:
        pass
    return state


def run_stop() -> Dict[str, Any]:
    try:
        with _db_conn() as conn:
            log_audit(conn, category="API_CALL", action="RUN_STOP", message="run stop")
    except Exception:
        pass
    return _run_stop_service(run_state_path=RUN_STATE_PATH)


def run_once() -> Dict[str, Any]:
    proc = _run_once_service(db_path=config.DB_PATH_DEFAULT, plan_path=config.PLAN_PATH_DEFAULT)
    try:
        with _db_conn() as conn:
            log_audit(conn, category="API_CALL", action="RUN_ONCE", message="run once", payload={"exit_code": int(proc.get("exit_code") or 0)})
    except Exception:
        pass
    return proc


def run_status(plan_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Return a stable run process status payload.

    This endpoint must not raise 500; it is safe for UI polling.
    """
    try:
        base = _run_status_service(run_state_path=RUN_STATE_PATH)
        if plan_id:
            try:
                with _db_conn() as conn:
                    last_finished = get_last_run_job_finished(conn, plan_id=str(plan_id))
                    if last_finished is not None:
                        base["last_finished"] = last_finished
                    last_guardrail = get_last_run_guardrail_hit(conn, plan_id=str(plan_id))
                    if last_guardrail is not None:
                        base["last_guardrail_hit"] = last_guardrail
            except HTTPException as exc:
                base["last_finished_error"] = f"{exc.status_code} {exc.detail}"
            except Exception as exc:  # noqa: BLE001
                base["last_finished_error"] = str(exc)[:200]
        return base
    except Exception as exc:
        return {"alive": False, "pid": None, "job_id": None, "started_at": None, "reason": "ERROR", "detail": str(exc)[:200]}


def reset_failed(body: ResetFailedIn) -> Dict[str, Any]:
    proc = _reset_failed_service(
        db_path=config.DB_PATH_DEFAULT,
        plan_id=str(body.plan_id),
        include_blocked=bool(body.include_blocked),
        reset_attempts=bool(body.reset_attempts),
    )
    try:
        with _db_conn() as conn:
            log_audit(
                conn,
                category="API_CALL",
                action="RESET_FAILED",
                message="reset failed tasks",
                plan_id=str(body.plan_id),
                payload={"include_blocked": bool(body.include_blocked), "reset_attempts": bool(body.reset_attempts)},
            )
    except Exception:
        pass
    return proc


def reset_to_plan(body: ResetToPlanIn) -> Dict[str, Any]:
    proc = _reset_to_plan_service(db_path=config.DB_PATH_DEFAULT, plan_id=str(body.plan_id))
    try:
        with _db_conn() as conn:
            log_audit(
                conn,
                category="API_CALL",
                action="RESET_TO_PLAN",
                message="reset-to-plan (delete run outputs, keep plan)",
                plan_id=str(body.plan_id),
            )
    except Exception:
        pass
    return proc


def create_plan(body: CreatePlanIn) -> Dict[str, Any]:
    cfg = get_runtime_config()
    max_attempts_eff = int(body.max_attempts) if body.max_attempts is not None else int(cfg.create_plan_max_attempts)
    return _create_plan_run_sync(
        db_path=config.DB_PATH_DEFAULT,
        top_task=body.top_task,
        max_attempts=max_attempts_eff,
        keep_trying=bool(body.keep_trying),
        max_total_attempts=body.max_total_attempts,
    )


def reset_db(body: ResetDbIn) -> Dict[str, Any]:
    global _DB_RESETTING
    try:
        with _db_conn() as conn:
            log_audit(
                conn,
                category="API_CALL",
                action="RESET_DB",
                message="reset db",
                payload={"purge_workspace": bool(body.purge_workspace), "purge_tasks": bool(body.purge_tasks), "purge_logs": bool(body.purge_logs)},
            )
    except Exception:
        pass
    with _DB_RESET_LOCK:
        _DB_RESETTING = True
    # Best-effort: stop any background processes that may be holding or rewriting the DB.
    try:
        _create_plan_stop(state_path=CREATE_PLAN_STATE_PATH)
    except Exception:
        pass
    try:
        _stop_run_process(run_state_path=RUN_STATE_PATH)
    except Exception:
        pass
    try:
        RUN_STATE_PATH.unlink(missing_ok=True)  # type: ignore[arg-type]
    except Exception:
        pass
    try:
        CREATE_PLAN_STATE_PATH.unlink(missing_ok=True)  # type: ignore[arg-type]
    except Exception:
        pass
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


def export(body: ExportIn) -> Dict[str, Any]:
    python_exe = _python_executable()
    cmd = [python_exe, str(ROOT_DIR / "agent_cli.py"), "--db", str(config.DB_PATH_DEFAULT), "export", "--plan-id", body.plan_id]
    if body.include_reviews:
        cmd.append("--include-reviews")
    try:
        with _db_conn() as conn:
            log_audit(
                conn,
                category="API_CALL",
                action="EXPORT_START",
                message="export start",
                plan_id=str(body.plan_id),
                payload={"include_reviews": bool(body.include_reviews)},
            )
    except Exception:
        pass
    proc = subprocess.run(cmd, cwd=str(ROOT_DIR), capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        with _db_conn() as conn:
            log_audit(
                conn,
                category="API_CALL",
                action="EXPORT_DONE",
                message="export done",
                plan_id=str(body.plan_id),
                ok=proc.returncode == 0,
                payload={"exit_code": int(proc.returncode)},
            )
    except Exception:
        pass
    return {"exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
