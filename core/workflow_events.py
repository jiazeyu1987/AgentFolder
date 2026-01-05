from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from core.util import utc_now_iso


MAX_EVENT_MESSAGE_CHARS = 500
MAX_EVENT_PAYLOAD_CHARS = 50_000


# Event types (string constants). Keep stable: UI/SSOT reads these values.
JOB_STARTED = "JOB_STARTED"
JOB_FINISHED = "JOB_FINISHED"
STEP_STARTED = "STEP_STARTED"
STEP_FINISHED = "STEP_FINISHED"
DECISION_MADE = "DECISION_MADE"
ERROR_RAISED = "ERROR_RAISED"
LLM_CALL_REQUESTED = "LLM_CALL_REQUESTED"
LLM_CALL_RECORDED = "LLM_CALL_RECORDED"
STATUS_CHANGED = "STATUS_CHANGED"
INPUT_REQUIRED = "INPUT_REQUIRED"
GUARDRAIL_HIT = "GUARDRAIL_HIT"
REVIEW_WRITTEN = "REVIEW_WRITTEN"
ARTIFACT_APPROVED = "ARTIFACT_APPROVED"
EXPORT_STARTED = "EXPORT_STARTED"
EXPORT_DONE = "EXPORT_DONE"
REWRITE_PROPOSED = "REWRITE_PROPOSED"
REWRITE_APPLIED = "REWRITE_APPLIED"
CLEANUP_DRY_RUN = "CLEANUP_DRY_RUN"
CLEANUP_APPLIED = "CLEANUP_APPLIED"

KNOWN_EVENT_TYPES = {
    JOB_STARTED,
    JOB_FINISHED,
    STEP_STARTED,
    STEP_FINISHED,
    DECISION_MADE,
    ERROR_RAISED,
    LLM_CALL_REQUESTED,
    LLM_CALL_RECORDED,
    STATUS_CHANGED,
    INPUT_REQUIRED,
    GUARDRAIL_HIT,
    REVIEW_WRITTEN,
    ARTIFACT_APPROVED,
    EXPORT_STARTED,
    EXPORT_DONE,
    REWRITE_PROPOSED,
    REWRITE_APPLIED,
    CLEANUP_DRY_RUN,
    CLEANUP_APPLIED,
}


def _truncate_message(message: Optional[str]) -> Optional[str]:
    if message is None:
        return None
    s = str(message).strip()
    if len(s) <= MAX_EVENT_MESSAGE_CHARS:
        return s
    return s[: MAX_EVENT_MESSAGE_CHARS - 12] + "…[TRUNCATED]"


def _truncate_payload(payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if payload is None:
        return None
    if not isinstance(payload, dict):
        return {"_payload": str(payload)[:500]}
    try:
        raw = json.dumps(payload, ensure_ascii=False)
    except Exception:
        # Best-effort: stringify any non-serializable values.
        safe: Dict[str, Any] = {}
        for k, v in payload.items():
            try:
                json.dumps(v, ensure_ascii=False)
                safe[str(k)] = v
            except Exception:
                safe[str(k)] = str(v)[:500]
        payload = safe
        try:
            raw = json.dumps(payload, ensure_ascii=False)
        except Exception:
            return {"_payload": "UNSERIALIZABLE"}
    if len(raw) <= MAX_EVENT_PAYLOAD_CHARS:
        return payload
    # Avoid huge payloads; keep head/tail to preserve context for debugging.
    head_len = MAX_EVENT_PAYLOAD_CHARS // 2
    tail_len = MAX_EVENT_PAYLOAD_CHARS - head_len - 40
    clipped = raw[:head_len] + "\n...[TRUNCATED]...\n" + raw[-tail_len:]
    return {"_truncated_json": clipped}


def emit_workflow_event(
    conn: sqlite3.Connection,
    *,
    workflow: str,
    event_type: str,
    severity: str = "INFO",
    message: Optional[str] = None,
    job_id: Optional[str] = None,
    top_task_hash: Optional[str] = None,
    plan_id: Optional[str] = None,
    task_id: Optional[str] = None,
    llm_call_id: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Best-effort workflow event logging.

    This is event-first observability for long-running serial jobs (create-plan/run/export).
    It must never raise, because it should not affect workflow execution.
    Returns event_id (or "UNKNOWN" on failure).
    """
    event_id = str(uuid.uuid4())
    try:
        payload2 = _truncate_payload(payload)
        conn.execute(
            """
            INSERT INTO workflow_events(
              event_id, created_at,
              workflow, event_type, severity, message,
              job_id, top_task_hash, plan_id, task_id, llm_call_id,
              payload_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                utc_now_iso(),
                str(workflow),
                str(event_type),
                str(severity),
                _truncate_message(message),
                str(job_id) if job_id else None,
                str(top_task_hash) if top_task_hash else None,
                str(plan_id) if plan_id else None,
                str(task_id) if task_id else None,
                str(llm_call_id) if llm_call_id else None,
                json.dumps(payload2, ensure_ascii=False) if payload2 is not None else None,
            ),
        )
        if not getattr(conn, "in_transaction", False):
            conn.commit()
    except Exception:
        return "UNKNOWN"
    return event_id


@dataclass(frozen=True)
class WorkflowEvent:
    event_id: str
    created_at: str
    workflow: str
    event_type: str
    severity: str
    message: Optional[str]
    job_id: Optional[str]
    top_task_hash: Optional[str]
    plan_id: Optional[str]
    task_id: Optional[str]
    llm_call_id: Optional[str]
    payload: Dict[str, Any]


def _parse_payload(payload_json: Any) -> Dict[str, Any]:
    if payload_json is None:
        return {}
    if isinstance(payload_json, dict):
        return payload_json
    if isinstance(payload_json, str) and payload_json.strip():
        try:
            obj = json.loads(payload_json)
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}
    return {}


def fetch_workflow_events(
    conn: sqlite3.Connection,
    *,
    job_id: Optional[str] = None,
    plan_id: Optional[str] = None,
    workflow: Optional[str] = None,
    event_types: Optional[Iterable[str]] = None,
    limit: int = 100,
) -> List[WorkflowEvent]:
    wheres: List[str] = []
    params: List[Any] = []
    if job_id:
        wheres.append("job_id = ?")
        params.append(str(job_id))
    if plan_id:
        wheres.append("plan_id = ?")
        params.append(str(plan_id))
    if workflow:
        wheres.append("workflow = ?")
        params.append(str(workflow))
    if event_types:
        ets = [str(x) for x in event_types if str(x).strip()]
        if ets:
            wheres.append("event_type IN (" + ",".join(["?"] * len(ets)) + ")")
            params.extend(ets)
    where_sql = ("WHERE " + " AND ".join(wheres)) if wheres else ""

    rows = conn.execute(
        f"""
        SELECT event_id, created_at, workflow, event_type, severity, message,
               job_id, top_task_hash, plan_id, task_id, llm_call_id, payload_json
        FROM workflow_events
        {where_sql}
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (*params, int(limit)),
    ).fetchall()
    out: List[WorkflowEvent] = []
    for r in rows:
        out.append(
            WorkflowEvent(
                event_id=str(r["event_id"]),
                created_at=str(r["created_at"]),
                workflow=str(r["workflow"]),
                event_type=str(r["event_type"]),
                severity=str(r["severity"]),
                message=str(r["message"]) if r["message"] is not None else None,
                job_id=str(r["job_id"]) if r["job_id"] else None,
                top_task_hash=str(r["top_task_hash"]) if r["top_task_hash"] else None,
                plan_id=str(r["plan_id"]) if r["plan_id"] else None,
                task_id=str(r["task_id"]) if r["task_id"] else None,
                llm_call_id=str(r["llm_call_id"]) if r["llm_call_id"] else None,
                payload=_parse_payload(r["payload_json"]),
            )
        )
    return out


def latest_event_payload_field(events: List[WorkflowEvent], key: str) -> Optional[Any]:
    for e in events:
        if key in e.payload:
            return e.payload.get(key)
    return None
