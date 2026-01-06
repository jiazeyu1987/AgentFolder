from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, Optional, Set

from core.events import emit_event
from core.util import utc_now_iso
from core.workflow_events import emit_workflow_event


class TaskStatusTransitionError(RuntimeError):
    def __init__(self, *, task_id: str, before: str, after: str) -> None:
        super().__init__(f"Invalid task status transition: {before} -> {after} (task_id={task_id})")
        self.task_id = task_id
        self.before = before
        self.after = after


ALLOWED_TRANSITIONS: Dict[str, Set[str]] = {
    "PENDING": {"READY", "BLOCKED", "ABANDONED"},
    # READY tasks may fail before being marked IN_PROGRESS (e.g., LLM bootstrap/provider errors).
    "READY": {"IN_PROGRESS", "BLOCKED", "PENDING", "ABANDONED", "FAILED"},
    "IN_PROGRESS": {"READY_TO_CHECK", "TO_BE_MODIFY", "DONE", "FAILED", "BLOCKED", "PENDING"},
    "READY_TO_CHECK": {"DONE", "TO_BE_MODIFY", "FAILED", "IN_PROGRESS"},
    "TO_BE_MODIFY": {"READY", "IN_PROGRESS", "ABANDONED"},
    "BLOCKED": {"READY", "PENDING", "ABANDONED", "FAILED"},
    "FAILED": {"READY", "BLOCKED", "ABANDONED"},
    "DONE": {"READY", "READY_TO_CHECK", "ABANDONED"},
    "ABANDONED": set(),
}


@dataclass(frozen=True)
class TransitionResult:
    before: str
    after: str
    blocked_reason_before: Optional[str]
    blocked_reason_after: Optional[str]


def transition_task_status(
    conn: sqlite3.Connection,
    *,
    plan_id: str,
    task_id: str,
    to_status: str,
    blocked_reason: Optional[str] = None,
    workflow: str = "RUN",
    job_id: Optional[str] = None,
    source: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> TransitionResult:
    row = conn.execute("SELECT status, blocked_reason FROM task_nodes WHERE task_id = ?", (task_id,)).fetchone()
    if not row:
        raise RuntimeError(f"task not found: {task_id}")

    before = str(row["status"] or "")
    before_blocked = row["blocked_reason"]
    after = str(to_status or "").strip().upper()
    after_blocked = blocked_reason

    if not after:
        raise RuntimeError("to_status is empty")

    if before == after and (before_blocked == after_blocked):
        return TransitionResult(before=before, after=after, blocked_reason_before=before_blocked, blocked_reason_after=after_blocked)

    allowed = ALLOWED_TRANSITIONS.get(before, set())
    if after not in allowed:
        raise TaskStatusTransitionError(task_id=task_id, before=before, after=after)

    conn.execute(
        "UPDATE task_nodes SET status = ?, blocked_reason = ?, updated_at = ? WHERE task_id = ?",
        (after, after_blocked, utc_now_iso(), task_id),
    )

    event_payload: Dict[str, Any] = {
        "status": after,
        "blocked_reason": after_blocked,
        "status_before": before,
        "status_after": after,
        "blocked_reason_before": before_blocked,
        "blocked_reason_after": after_blocked,
        "source": source,
    }
    if payload:
        event_payload.update(payload)
    emit_event(conn, plan_id=plan_id, task_id=task_id, event_type="STATUS_CHANGED", payload=event_payload)

    try:
        emit_workflow_event(
            conn,
            workflow=str(workflow or "RUN"),
            event_type="STATUS_CHANGED",
            severity="INFO",
            message=f"status: {before or '-'} -> {after}",
            job_id=job_id,
            plan_id=plan_id,
            task_id=task_id,
            payload={
                "status_before": before,
                "status_after": after,
                "blocked_reason": after_blocked,
                "source": source,
                **(payload or {}),
            },
        )
    except Exception:
        pass

    try:
        from core.audit_log import log_audit

        log_audit(
            conn,
            category="STATUS_CHANGED",
            action="TASK_STATUS_CHANGED",
            message=f"Task status changed: {before or '-'} -> {after}",
            plan_id=plan_id,
            task_id=task_id,
            status_before=before,
            status_after=after,
            ok=True,
            payload={"blocked_reason": after_blocked, "source": source, **(payload or {})},
        )
    except Exception:
        pass

    return TransitionResult(before=before, after=after, blocked_reason_before=before_blocked, blocked_reason_after=after_blocked)


def compare_and_transition_task_status(
    conn: sqlite3.Connection,
    *,
    plan_id: str,
    task_id: str,
    from_status: str,
    to_status: str,
    blocked_reason: Optional[str] = None,
    workflow: str = "RUN",
    job_id: Optional[str] = None,
    source: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Atomic compare-and-set status transition (e.g. lock acquisition).

    Returns True only if the row was updated.
    """
    before = str(from_status or "").strip().upper()
    after = str(to_status or "").strip().upper()
    if not before or not after:
        raise RuntimeError("from_status/to_status is empty")
    if after not in ALLOWED_TRANSITIONS.get(before, set()):
        raise TaskStatusTransitionError(task_id=task_id, before=before, after=after)

    cur = conn.execute(
        """
        UPDATE task_nodes
        SET status = ?, blocked_reason = ?, updated_at = ?
        WHERE plan_id = ?
          AND task_id = ?
          AND active_branch = 1
          AND status = ?
        """,
        (after, blocked_reason, utc_now_iso(), plan_id, task_id, before),
    )
    updated = int(getattr(cur, "rowcount", 0) or 0) == 1
    if not updated:
        return False

    event_payload: Dict[str, Any] = {
        "status": after,
        "blocked_reason": blocked_reason,
        "status_before": before,
        "status_after": after,
        "blocked_reason_before": None,
        "blocked_reason_after": blocked_reason,
        "source": source,
        **(payload or {}),
    }
    emit_event(conn, plan_id=plan_id, task_id=task_id, event_type="STATUS_CHANGED", payload=event_payload)
    try:
        emit_workflow_event(
            conn,
            workflow=str(workflow or "RUN"),
            event_type="STATUS_CHANGED",
            severity="INFO",
            message=f"status: {before} -> {after}",
            job_id=job_id,
            plan_id=plan_id,
            task_id=task_id,
            payload={"status_before": before, "status_after": after, "blocked_reason": blocked_reason, "source": source, **(payload or {})},
        )
    except Exception:
        pass
    try:
        from core.audit_log import log_audit

        log_audit(
            conn,
            category="STATUS_CHANGED",
            action="TASK_STATUS_CHANGED",
            message=f"Task status changed: {before} -> {after}",
            plan_id=plan_id,
            task_id=task_id,
            status_before=before,
            status_after=after,
            ok=True,
            payload={"blocked_reason": blocked_reason, "source": source, **(payload or {})},
        )
    except Exception:
        pass
    return True
