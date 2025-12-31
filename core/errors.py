from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, Optional

from core.events import emit_event
from core.util import utc_now_iso


@dataclass(frozen=True)
class ErrorOutcome:
    status: Optional[str] = None
    blocked_reason: Optional[str] = None
    attempt_delta: int = 0


def record_error(
    conn: sqlite3.Connection,
    *,
    plan_id: str,
    task_id: Optional[str],
    error_code: str,
    message: str,
    context: Optional[Dict[str, Any]] = None,
) -> None:
    payload = {"error_code": error_code, "message": message, "context": context or {}}
    emit_event(conn, plan_id=plan_id, task_id=task_id, event_type="ERROR", payload=payload)


def apply_error_outcome(conn: sqlite3.Connection, *, plan_id: str, task_id: str, outcome: ErrorOutcome) -> None:
    if outcome.attempt_delta:
        conn.execute(
            "UPDATE task_nodes SET attempt_count = attempt_count + ?, updated_at = ? WHERE task_id = ?",
            (int(outcome.attempt_delta), utc_now_iso(), task_id),
        )
    if outcome.status:
        conn.execute(
            "UPDATE task_nodes SET status = ?, blocked_reason = ?, updated_at = ? WHERE task_id = ?",
            (outcome.status, outcome.blocked_reason, utc_now_iso(), task_id),
        )
        emit_event(conn, plan_id=plan_id, task_id=task_id, event_type="STATUS_CHANGED", payload={"status": outcome.status, "blocked_reason": outcome.blocked_reason})


def map_error_to_outcome(error_code: str) -> ErrorOutcome:
    """
    Map Error_Recovery_Spec error_code -> task.status + blocked_reason + attempt_count delta.
    """
    if error_code in {"LLM_UNPARSEABLE", "LLM_TIMEOUT", "LLM_FAILED"}:
        return ErrorOutcome(status="FAILED", blocked_reason=None, attempt_delta=1)
    if error_code == "LLM_REFUSAL":
        return ErrorOutcome(status="BLOCKED", blocked_reason="WAITING_EXTERNAL", attempt_delta=0)
    if error_code in {"SKILL_FAILED", "SKILL_TIMEOUT"}:
        return ErrorOutcome(status="BLOCKED", blocked_reason="WAITING_SKILL", attempt_delta=0)
    if error_code in {"SKILL_BAD_INPUT"}:
        return ErrorOutcome(status="BLOCKED", blocked_reason="WAITING_INPUT", attempt_delta=0)
    if error_code in {"INPUT_MISSING"}:
        return ErrorOutcome(status="BLOCKED", blocked_reason="WAITING_INPUT", attempt_delta=0)
    if error_code in {"INPUT_CONFLICT"}:
        return ErrorOutcome(status="BLOCKED", blocked_reason="WAITING_EXTERNAL", attempt_delta=0)
    if error_code in {"MAX_ATTEMPTS_EXCEEDED"}:
        return ErrorOutcome(status="BLOCKED", blocked_reason="WAITING_EXTERNAL", attempt_delta=0)
    return ErrorOutcome(status="FAILED", blocked_reason=None, attempt_delta=1)


def maybe_reset_failed_to_ready(conn: sqlite3.Connection, *, plan_id: str) -> int:
    """
    Optional recovery: reset FAILED -> READY when config allows it.
    This is intentionally conservative and only toggles status, without clearing evidence history.
    """
    import config  # local import to avoid cycles

    if not config.FAILED_AUTO_RESET_READY:
        return 0
    rows = conn.execute(
        "SELECT task_id FROM task_nodes WHERE plan_id = ? AND active_branch = 1 AND status = 'FAILED'",
        (plan_id,),
    ).fetchall()
    for r in rows:
        conn.execute(
            "UPDATE task_nodes SET status='READY', blocked_reason=NULL, updated_at=? WHERE task_id=?",
            (utc_now_iso(), r["task_id"]),
        )
        emit_event(conn, plan_id=plan_id, task_id=r["task_id"], event_type="STATUS_CHANGED", payload={"status": "READY", "blocked_reason": None})
    return len(rows)
