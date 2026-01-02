from __future__ import annotations

import sqlite3
from typing import Any, Callable, Dict, Optional, Tuple

import config
from core.artifacts_v2 import set_approved_artifact
from core.runtime_config import get_runtime_config
from core.errors import apply_error_outcome, map_error_to_outcome, record_error
from core.events import emit_event
from core.reviews import insert_review, write_review_json
from core.util import utc_now_iso


ReviewerFn = Callable[[Dict[str, Any]], Dict[str, Any]]


class ReviewContractMismatch(RuntimeError):
    def __init__(self, message: str, *, hint: str = "Fix reviewer contract and retry.") -> None:
        super().__init__(message)
        self.hint = hint


def _set_status(conn: sqlite3.Connection, *, plan_id: str, task_id: str, status: str, blocked_reason: Optional[str] = None) -> None:
    row = conn.execute("SELECT status FROM task_nodes WHERE task_id = ?", (task_id,)).fetchone()
    before = str(row["status"]) if row and row["status"] is not None else None
    conn.execute(
        "UPDATE task_nodes SET status = ?, blocked_reason = ?, updated_at = ? WHERE task_id = ?",
        (status, blocked_reason, utc_now_iso(), task_id),
    )
    emit_event(conn, plan_id=plan_id, task_id=task_id, event_type="STATUS_CHANGED", payload={"status": status, "blocked_reason": blocked_reason})
    try:
        from core.audit_log import log_audit

        log_audit(
            conn,
            category="STATUS_CHANGED",
            action="TASK_STATUS_CHANGED",
            message=f"Task status changed: {before or '-'} -> {status}",
            plan_id=plan_id,
            task_id=task_id,
            status_before=before,
            status_after=status,
            ok=True,
            payload={"blocked_reason": blocked_reason, "source": "v2_review_gate"},
        )
    except Exception:
        pass


def _inc_attempt(conn: sqlite3.Connection, *, task_id: str) -> None:
    conn.execute("UPDATE task_nodes SET attempt_count = attempt_count + 1, updated_at = ? WHERE task_id = ?", (utc_now_iso(), task_id))


def _attempt_count(conn: sqlite3.Connection, *, task_id: str) -> int:
    row = conn.execute("SELECT attempt_count FROM task_nodes WHERE task_id = ?", (task_id,)).fetchone()
    return int(row["attempt_count"]) if row else 0


def _acquire_check_lock(conn: sqlite3.Connection, *, plan_id: str, check_task_id: str) -> bool:
    """
    Atomically move CHECK from READY -> IN_PROGRESS so multiple triggers don't double-run the same check.
    """
    cur = conn.execute(
        """
        UPDATE task_nodes
        SET status = 'IN_PROGRESS', blocked_reason = NULL, updated_at = ?
        WHERE plan_id = ?
          AND task_id = ?
          AND active_branch = 1
          AND node_type = 'CHECK'
          AND status = 'READY'
        """,
        (utc_now_iso(), plan_id, check_task_id),
    )
    acquired = int(getattr(cur, "rowcount", 0) or 0) == 1
    if acquired:
        try:
            from core.audit_log import log_audit

            log_audit(
                conn,
                category="STATUS_CHANGED",
                action="TASK_STATUS_CHANGED",
                message="Task status changed: READY -> IN_PROGRESS",
                plan_id=plan_id,
                task_id=check_task_id,
                status_before="READY",
                status_after="IN_PROGRESS",
                ok=True,
                payload={"source": "v2_check_lock"},
            )
        except Exception:
            pass
    return acquired


def _load_artifact_path(conn: sqlite3.Connection, *, artifact_id: str) -> Optional[str]:
    row = conn.execute("SELECT path FROM artifacts WHERE artifact_id = ?", (artifact_id,)).fetchone()
    if not row:
        return None
    return str(row["path"] or "")


def _current_active_artifact_id(conn: sqlite3.Connection, *, task_id: str) -> str:
    row = conn.execute("SELECT active_artifact_id FROM task_nodes WHERE task_id = ?", (task_id,)).fetchone()
    return str((row["active_artifact_id"] if row else "") or "").strip()


def _load_check_and_target(
    conn: sqlite3.Connection, *, plan_id: str, check_task_id: str
) -> Tuple[Optional[sqlite3.Row], Optional[sqlite3.Row]]:
    check = conn.execute(
        """
        SELECT task_id, title, status, blocked_reason, review_target_task_id, owner_agent_id
        FROM task_nodes
        WHERE plan_id = ? AND task_id = ? AND active_branch = 1 AND node_type = 'CHECK'
        """,
        (plan_id, check_task_id),
    ).fetchone()
    if not check:
        return None, None
    target_id = (check["review_target_task_id"] or "").strip() if isinstance(check["review_target_task_id"], str) else None
    if not target_id:
        return check, None
    target = conn.execute(
        """
        SELECT task_id, title, status, active_artifact_id, approved_artifact_id
        FROM task_nodes
        WHERE plan_id = ? AND task_id = ? AND active_branch = 1 AND node_type = 'ACTION'
        """,
        (plan_id, target_id),
    ).fetchone()
    return check, target


def run_check_once(
    conn: sqlite3.Connection,
    *,
    plan_id: str,
    check_task_id: str,
    reviewer_fn: ReviewerFn,
) -> Dict[str, Any]:
    """
    v2 minimal gate:
    - CHECK reviews the bound ACTION's current candidate artifact (active_artifact_id).
    - REVIEW is recorded with v2 traceability fields (check_task_id, review_target_task_id, reviewed_artifact_id, verdict).
    - APPROVED: ACTION -> DONE and approved_artifact_id points to the reviewed artifact.
    - REJECTED: ACTION -> TO_BE_MODIFY (candidate artifact preserved).
    - CHECK always ends DONE on a successful review attempt.
    """
    # Concurrency guard: if we cannot acquire the READY->IN_PROGRESS transition, treat as a benign skip.
    if not _acquire_check_lock(conn, plan_id=plan_id, check_task_id=check_task_id):
        return {"ok": True, "reason": "SKIPPED_LOCK_NOT_ACQUIRED"}

    check, target = _load_check_and_target(conn, plan_id=plan_id, check_task_id=check_task_id)
    if not check:
        record_error(conn, plan_id=plan_id, task_id=check_task_id, error_code="TASK_NOT_FOUND", message="CHECK task not found")
        _set_status(conn, plan_id=plan_id, task_id=check_task_id, status="READY", blocked_reason=None)
        return {"ok": False, "error_code": "TASK_NOT_FOUND"}

    target_id = (check["review_target_task_id"] or "").strip() if isinstance(check["review_target_task_id"], str) else ""
    if not target_id:
        record_error(
            conn,
            plan_id=plan_id,
            task_id=check_task_id,
            error_code="INPUT_MISSING",
            message="CHECK missing review_target_task_id (v2 binding)",
            context={"json_path": "$.task_nodes[task_id=<check>].review_target_task_id"},
        )
        apply_error_outcome(conn, plan_id=plan_id, task_id=check_task_id, outcome=map_error_to_outcome("INPUT_MISSING"))
        return {"ok": False, "error_code": "INPUT_MISSING", "hint": "Bind CHECK.review_target_task_id to an ACTION task_id."}

    if not target:
        record_error(
            conn,
            plan_id=plan_id,
            task_id=check_task_id,
            error_code="INPUT_MISSING",
            message=f"CHECK review_target_task_id does not exist or is not an ACTION: {target_id}",
            context={"target_task_id": target_id},
        )
        apply_error_outcome(conn, plan_id=plan_id, task_id=check_task_id, outcome=map_error_to_outcome("INPUT_CONFLICT"))
        return {"ok": False, "error_code": "INPUT_MISSING", "hint": "Fix review_target_task_id to reference an existing ACTION."}

    reviewed_artifact_id = (target["active_artifact_id"] or "").strip() if isinstance(target["active_artifact_id"], str) else ""
    if not reviewed_artifact_id:
        record_error(
            conn,
            plan_id=plan_id,
            task_id=check_task_id,
            error_code="INPUT_MISSING",
            message="Target ACTION has no active_artifact_id to review",
            context={"review_target_task_id": target_id},
        )
        apply_error_outcome(conn, plan_id=plan_id, task_id=check_task_id, outcome=map_error_to_outcome("INPUT_MISSING"))
        return {"ok": False, "error_code": "INPUT_MISSING", "hint": "Generate an artifact for the ACTION first."}

    idempotency_key = f"{check_task_id}:{reviewed_artifact_id}"
    already = conn.execute("SELECT review_id FROM reviews WHERE idempotency_key = ? LIMIT 1", (idempotency_key,)).fetchone()
    if already:
        # Idempotent no-op: do not change ACTION/CHECK states (restore CHECK to READY).
        _set_status(conn, plan_id=plan_id, task_id=check_task_id, status="READY", blocked_reason=None)
        return {"ok": True, "reason": "ALREADY_REVIEWED", "review_id": str(already["review_id"])}

    art_path = _load_artifact_path(conn, artifact_id=reviewed_artifact_id)
    if not art_path:
        record_error(
            conn,
            plan_id=plan_id,
            task_id=check_task_id,
            error_code="INPUT_MISSING",
            message="Locked artifact_id not found in artifacts table",
            context={"check_task_id": check_task_id, "review_target_task_id": target_id, "reviewed_artifact_id": reviewed_artifact_id},
        )
        apply_error_outcome(conn, plan_id=plan_id, task_id=check_task_id, outcome=map_error_to_outcome("INPUT_MISSING"))
        return {"ok": False, "error_code": "INPUT_MISSING", "hint": "Artifact record missing; regenerate the candidate artifact."}

    from pathlib import Path

    if not Path(art_path).exists():
        record_error(
            conn,
            plan_id=plan_id,
            task_id=check_task_id,
            error_code="INPUT_MISSING",
            message="Locked artifact file missing on disk",
            context={"reviewed_artifact_id": reviewed_artifact_id, "missing_path": art_path},
        )
        apply_error_outcome(conn, plan_id=plan_id, task_id=check_task_id, outcome=map_error_to_outcome("INPUT_MISSING"))
        return {"ok": False, "error_code": "INPUT_MISSING", "hint": f"Missing artifact file: {art_path}"}

    review_context = {
        "schema_version": "v2_check_gate_v1",
        "plan_id": plan_id,
        "check_task_id": check_task_id,
        "review_target_task_id": target_id,
        "reviewed_artifact_id": reviewed_artifact_id,
        "target_task_title": target["title"] if target and "title" in target.keys() else None,
    }
    try:
        review_payload = reviewer_fn(review_context)
    except ReviewContractMismatch as exc:
        record_error(
            conn,
            plan_id=plan_id,
            task_id=check_task_id,
            error_code="CONTRACT_MISMATCH",
            message=str(exc),
            context={
                "hint": getattr(exc, "hint", "Fix reviewer contract and retry."),
                "check_task_id": check_task_id,
                "review_target_task_id": target_id,
                "reviewed_artifact_id": reviewed_artifact_id,
            },
        )
        _inc_attempt(conn, task_id=check_task_id)
        cfg = get_runtime_config()
        if _attempt_count(conn, task_id=check_task_id) >= int(cfg.max_check_attempts_v2):
            apply_error_outcome(conn, plan_id=plan_id, task_id=check_task_id, outcome=map_error_to_outcome("MAX_ATTEMPTS_EXCEEDED"))
            return {"ok": False, "error_code": "MAX_ATTEMPTS_EXCEEDED", "hint": "Contract mismatch repeatedly; open LLM Explorer / fix prompt schema."}
        _set_status(conn, plan_id=plan_id, task_id=check_task_id, status="READY", blocked_reason=None)
        return {"ok": False, "error_code": "CONTRACT_MISMATCH", "hint": getattr(exc, "hint", "Fix reviewer contract and retry.")}
    except Exception as exc:
        record_error(
            conn,
            plan_id=plan_id,
            task_id=check_task_id,
            error_code="REVIEWER_FAILED",
            message=str(exc),
            context={
                "check_task_id": check_task_id,
                "review_target_task_id": target_id,
                "reviewed_artifact_id": reviewed_artifact_id,
            },
        )
        apply_error_outcome(conn, plan_id=plan_id, task_id=check_task_id, outcome=map_error_to_outcome("INPUT_CONFLICT"))
        return {"ok": False, "error_code": "REVIEWER_FAILED", "hint": "Reviewer crashed; check prompt/contracts or rerun later."}

    if not isinstance(review_payload, dict):
        record_error(conn, plan_id=plan_id, task_id=check_task_id, error_code="REVIEWER_BAD_OUTPUT", message="reviewer_fn must return a dict")
        _inc_attempt(conn, task_id=check_task_id)
        cfg = get_runtime_config()
        if _attempt_count(conn, task_id=check_task_id) >= int(cfg.max_check_attempts_v2):
            apply_error_outcome(conn, plan_id=plan_id, task_id=check_task_id, outcome=map_error_to_outcome("MAX_ATTEMPTS_EXCEEDED"))
            return {"ok": False, "error_code": "MAX_ATTEMPTS_EXCEEDED", "hint": "Reviewer output repeatedly invalid; please fix prompts/contracts."}
        _set_status(conn, plan_id=plan_id, task_id=check_task_id, status="READY", blocked_reason=None)
        return {"ok": False, "error_code": "REVIEWER_BAD_OUTPUT", "hint": "Reviewer output invalid; will retry."}

    verdict_raw = review_payload.get("verdict")
    verdict = str(verdict_raw or "").strip().upper()
    if verdict not in {"APPROVED", "REJECTED"}:
        score = int(review_payload.get("total_score") or 0)
        verdict = "APPROVED" if score >= 90 else "REJECTED"

    normalized_review: Dict[str, Any] = {
        "schema_version": str(review_payload.get("schema_version") or "v2_review_result_v1"),
        "total_score": int(review_payload.get("total_score") or (100 if verdict == "APPROVED" else 0)),
        "summary": str(review_payload.get("summary") or ""),
        "breakdown": review_payload.get("breakdown") or [],
        "suggestions": review_payload.get("suggestions") or [],
        "action_required": "APPROVE" if verdict == "APPROVED" else "MODIFY",
        "verdict": verdict,
        "acceptance_results": review_payload.get("acceptance_results") or [],
        "meta": {
            "review_target_task_id": target_id,
            "reviewed_artifact_id": reviewed_artifact_id,
        },
    }

    write_review_json(config.REVIEWS_DIR, task_id=check_task_id, review=normalized_review)
    insert_review(
        conn,
        plan_id=plan_id,
        task_id=check_task_id,
        reviewer_agent_id=str(check["owner_agent_id"] or "reviewer"),
        review=normalized_review,
        idempotency_key=idempotency_key,
        check_task_id=check_task_id,
        review_target_task_id=target_id,
        reviewed_artifact_id=reviewed_artifact_id,
        verdict=verdict,
        acceptance_results=normalized_review.get("acceptance_results"),
    )

    if verdict == "APPROVED":
        set_approved_artifact(conn, task_id=target_id, artifact_id=reviewed_artifact_id)
        # If the ACTION generated a newer candidate while we were reviewing, do not mark DONE.
        # Keep approved pointer on the reviewed version, but require reviewing the newest candidate.
        current_active = _current_active_artifact_id(conn, task_id=target_id)
        if current_active and current_active != reviewed_artifact_id:
            record_error(
                conn,
                plan_id=plan_id,
                task_id=target_id,
                error_code="STALE_REVIEW",
                message="Approved an older candidate while a newer candidate exists; ACTION still requires review of the latest artifact.",
                context={
                    "approved_artifact_id": reviewed_artifact_id,
                    "current_active_artifact_id": current_active,
                    "hint": "Run CHECK again to review the latest candidate artifact.",
                },
            )
            _set_status(conn, plan_id=plan_id, task_id=target_id, status="READY_TO_CHECK")
        else:
            _set_status(conn, plan_id=plan_id, task_id=target_id, status="DONE")
    else:
        _set_status(conn, plan_id=plan_id, task_id=target_id, status="TO_BE_MODIFY")

    _set_status(conn, plan_id=plan_id, task_id=check_task_id, status="DONE")
    return {"ok": True, "verdict": verdict, "review_target_task_id": target_id, "reviewed_artifact_id": reviewed_artifact_id}
