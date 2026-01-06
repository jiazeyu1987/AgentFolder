from __future__ import annotations

import sqlite3
from typing import Any, Callable, Dict, Optional, Tuple

import config
from core.artifacts_v2 import set_approved_artifact
from core.runtime_config import get_runtime_config
from core.errors import apply_error_outcome, map_error_to_outcome, record_error
from core.events import emit_event
from core.reviews import insert_review, write_review_json
from core.util import ensure_dir, utc_now_iso
from core.workflow_events import emit_workflow_event
from core.state_machine.task_status import compare_and_transition_task_status, transition_task_status


def _truncate_text(s: str, *, max_chars: int) -> str:
    if max_chars <= 0:
        return s
    if len(s) <= max_chars:
        return s
    return s[: max_chars - 1] + "…"


def _write_suggestions_md(*, task_id: str, text: str) -> None:
    p = config.REVIEWS_DIR / str(task_id) / "suggestions.md"
    ensure_dir(p.parent)
    p.write_text(text, encoding="utf-8")


ReviewerFn = Callable[[Dict[str, Any]], Dict[str, Any]]


class ReviewContractMismatch(RuntimeError):
    def __init__(self, message: str, *, hint: str = "Fix reviewer contract and retry.") -> None:
        super().__init__(message)
        self.hint = hint


def _set_status(
    conn: sqlite3.Connection,
    *,
    plan_id: str,
    task_id: str,
    status: str,
    blocked_reason: Optional[str] = None,
    job_id: Optional[str] = None,
) -> None:
    transition_task_status(
        conn,
        plan_id=plan_id,
        task_id=task_id,
        to_status=str(status),
        blocked_reason=blocked_reason,
        workflow="RUN",
        job_id=job_id,
        source="v2_review_gate",
    )


def _inc_attempt(conn: sqlite3.Connection, *, task_id: str) -> None:
    conn.execute("UPDATE task_nodes SET attempt_count = attempt_count + 1, updated_at = ? WHERE task_id = ?", (utc_now_iso(), task_id))


def _attempt_count(conn: sqlite3.Connection, *, task_id: str) -> int:
    row = conn.execute("SELECT attempt_count FROM task_nodes WHERE task_id = ?", (task_id,)).fetchone()
    return int(row["attempt_count"]) if row else 0


def _acquire_check_lock(conn: sqlite3.Connection, *, plan_id: str, check_task_id: str) -> bool:
    """
    Atomically move CHECK from READY -> IN_PROGRESS so multiple triggers don't double-run the same check.
    """
    # Keep the node_type='CHECK' guard while still centralizing the status transition + events.
    row = conn.execute("SELECT node_type, status FROM task_nodes WHERE task_id = ? AND plan_id = ? AND active_branch = 1", (check_task_id, plan_id)).fetchone()
    if not row or str(row["node_type"] or "") != "CHECK":
        return False
    if str(row["status"] or "") != "READY":
        return False
    return compare_and_transition_task_status(
        conn,
        plan_id=plan_id,
        task_id=check_task_id,
        from_status="READY",
        to_status="IN_PROGRESS",
        blocked_reason=None,
        workflow="RUN",
        source="v2_check_lock",
    )


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
    job_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    v2 minimal gate:
    - CHECK reviews the bound ACTION's current candidate artifact (active_artifact_id).
    - REVIEW is recorded with v2 traceability fields (check_task_id, review_target_task_id, reviewed_artifact_id, verdict).
    - APPROVED: ACTION -> DONE and approved_artifact_id points to the reviewed artifact.
    - REJECTED: ACTION -> TO_BE_MODIFY (candidate artifact preserved).
    - CHECK always ends DONE on a successful review attempt.
    """
    step = "V2_CHECK"
    emit_workflow_event(
        conn,
        workflow="RUN",
        event_type="STEP_STARTED",
        severity="INFO",
        message="V2_CHECK started",
        job_id=job_id,
        plan_id=plan_id,
        task_id=check_task_id,
        payload={"step": step, "check_task_id": check_task_id},
    )

    # Concurrency guard: if we cannot acquire the READY->IN_PROGRESS transition, treat as a benign skip.
    if not _acquire_check_lock(conn, plan_id=plan_id, check_task_id=check_task_id):
        emit_workflow_event(
            conn,
            workflow="RUN",
            event_type="DECISION_MADE",
            severity="INFO",
            message="check skipped: lock not acquired",
            job_id=job_id,
            plan_id=plan_id,
            task_id=check_task_id,
            payload={"step": step, "why": "LOCK_BUSY", "next": "SKIP"},
        )
        emit_workflow_event(
            conn,
            workflow="RUN",
            event_type="STEP_FINISHED",
            severity="INFO",
            message="V2_CHECK finished",
            job_id=job_id,
            plan_id=plan_id,
            task_id=check_task_id,
            payload={"step": step, "ok": True, "reason": "SKIPPED_LOCK_NOT_ACQUIRED"},
        )
        return {"ok": True, "reason": "SKIPPED_LOCK_NOT_ACQUIRED"}

    check, target = _load_check_and_target(conn, plan_id=plan_id, check_task_id=check_task_id)
    if not check:
        record_error(conn, plan_id=plan_id, task_id=check_task_id, error_code="TASK_NOT_FOUND", message="CHECK task not found")
        _set_status(conn, plan_id=plan_id, task_id=check_task_id, status="READY", blocked_reason=None, job_id=job_id)
        emit_workflow_event(
            conn,
            workflow="RUN",
            event_type="ERROR_RAISED",
            severity="ERROR",
            message="CHECK task not found",
            job_id=job_id,
            plan_id=plan_id,
            task_id=check_task_id,
            payload={"step": step, "error_code": "TASK_NOT_FOUND", "next": "FIX_PLAN_OR_DB"},
        )
        emit_workflow_event(
            conn,
            workflow="RUN",
            event_type="STEP_FINISHED",
            severity="WARN",
            message="V2_CHECK finished",
            job_id=job_id,
            plan_id=plan_id,
            task_id=check_task_id,
            payload={"step": step, "ok": False, "error_code": "TASK_NOT_FOUND"},
        )
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
        emit_workflow_event(
            conn,
            workflow="RUN",
            event_type="ERROR_RAISED",
            severity="ERROR",
            message="CHECK missing review_target_task_id",
            job_id=job_id,
            plan_id=plan_id,
            task_id=check_task_id,
            payload={"step": step, "error_code": "INPUT_MISSING", "next": "FIX_PLAN_BINDING"},
        )
        emit_workflow_event(
            conn,
            workflow="RUN",
            event_type="STEP_FINISHED",
            severity="WARN",
            message="V2_CHECK finished",
            job_id=job_id,
            plan_id=plan_id,
            task_id=check_task_id,
            payload={"step": step, "ok": False, "error_code": "INPUT_MISSING"},
        )
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
        emit_workflow_event(
            conn,
            workflow="RUN",
            event_type="ERROR_RAISED",
            severity="ERROR",
            message="CHECK points to missing ACTION",
            job_id=job_id,
            plan_id=plan_id,
            task_id=check_task_id,
            payload={"step": step, "error_code": "INPUT_MISSING", "review_target_task_id": target_id, "next": "FIX_PLAN_BINDING"},
        )
        emit_workflow_event(
            conn,
            workflow="RUN",
            event_type="STEP_FINISHED",
            severity="WARN",
            message="V2_CHECK finished",
            job_id=job_id,
            plan_id=plan_id,
            task_id=check_task_id,
            payload={"step": step, "ok": False, "error_code": "INPUT_MISSING"},
        )
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
        emit_workflow_event(
            conn,
            workflow="RUN",
            event_type="ERROR_RAISED",
            severity="WARN",
            message="Target ACTION has no active artifact",
            job_id=job_id,
            plan_id=plan_id,
            task_id=check_task_id,
            payload={"step": step, "error_code": "INPUT_MISSING", "review_target_task_id": target_id, "next": "RUN_ACTION_FIRST"},
        )
        emit_workflow_event(
            conn,
            workflow="RUN",
            event_type="STEP_FINISHED",
            severity="WARN",
            message="V2_CHECK finished",
            job_id=job_id,
            plan_id=plan_id,
            task_id=check_task_id,
            payload={"step": step, "ok": False, "error_code": "INPUT_MISSING"},
        )
        return {"ok": False, "error_code": "INPUT_MISSING", "hint": "Generate an artifact for the ACTION first."}

    idempotency_key = f"{check_task_id}:{reviewed_artifact_id}"
    already = conn.execute("SELECT review_id FROM reviews WHERE idempotency_key = ? LIMIT 1", (idempotency_key,)).fetchone()
    if already:
        # Idempotent no-op: do not change ACTION/CHECK states (restore CHECK to READY).
        _set_status(conn, plan_id=plan_id, task_id=check_task_id, status="READY", blocked_reason=None, job_id=job_id)
        emit_workflow_event(
            conn,
            workflow="RUN",
            event_type="DECISION_MADE",
            severity="INFO",
            message="check already reviewed; no-op",
            job_id=job_id,
            plan_id=plan_id,
            task_id=check_task_id,
            payload={"step": step, "why": "ALREADY_REVIEWED", "reviewed_artifact_id": reviewed_artifact_id, "next": "NOOP"},
        )
        emit_workflow_event(
            conn,
            workflow="RUN",
            event_type="STEP_FINISHED",
            severity="INFO",
            message="V2_CHECK finished",
            job_id=job_id,
            plan_id=plan_id,
            task_id=check_task_id,
            payload={"step": step, "ok": True, "reason": "ALREADY_REVIEWED"},
        )
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
        emit_workflow_event(
            conn,
            workflow="RUN",
            event_type="ERROR_RAISED",
            severity="ERROR",
            message="Locked artifact missing in DB",
            job_id=job_id,
            plan_id=plan_id,
            task_id=check_task_id,
            payload={"step": step, "error_code": "INPUT_MISSING", "reviewed_artifact_id": reviewed_artifact_id, "next": "REGENERATE_ARTIFACT"},
        )
        emit_workflow_event(
            conn,
            workflow="RUN",
            event_type="STEP_FINISHED",
            severity="WARN",
            message="V2_CHECK finished",
            job_id=job_id,
            plan_id=plan_id,
            task_id=check_task_id,
            payload={"step": step, "ok": False, "error_code": "INPUT_MISSING"},
        )
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
        emit_workflow_event(
            conn,
            workflow="RUN",
            event_type="ERROR_RAISED",
            severity="ERROR",
            message="Locked artifact file missing",
            job_id=job_id,
            plan_id=plan_id,
            task_id=check_task_id,
            payload={"step": step, "error_code": "INPUT_MISSING", "missing_path": art_path, "next": "REGENERATE_ARTIFACT"},
        )
        emit_workflow_event(
            conn,
            workflow="RUN",
            event_type="STEP_FINISHED",
            severity="WARN",
            message="V2_CHECK finished",
            job_id=job_id,
            plan_id=plan_id,
            task_id=check_task_id,
            payload={"step": step, "ok": False, "error_code": "INPUT_MISSING"},
        )
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
            emit_workflow_event(
                conn,
                workflow="RUN",
                event_type="DECISION_MADE",
                severity="ERROR",
                message="check blocked: max contract mismatch attempts",
                job_id=job_id,
                plan_id=plan_id,
                task_id=check_task_id,
                payload={"step": step, "why": "CONTRACT_MISMATCH", "next": "BLOCKED_WAITING_EXTERNAL"},
            )
            emit_workflow_event(
                conn,
                workflow="RUN",
                event_type="STEP_FINISHED",
                severity="WARN",
                message="V2_CHECK finished",
                job_id=job_id,
                plan_id=plan_id,
                task_id=check_task_id,
                payload={"step": step, "ok": False, "error_code": "MAX_ATTEMPTS_EXCEEDED"},
            )
            return {"ok": False, "error_code": "MAX_ATTEMPTS_EXCEEDED", "hint": "Contract mismatch repeatedly; open LLM Explorer / fix prompt schema."}
        _set_status(conn, plan_id=plan_id, task_id=check_task_id, status="READY", blocked_reason=None, job_id=job_id)
        emit_workflow_event(
            conn,
            workflow="RUN",
            event_type="DECISION_MADE",
            severity="WARN",
            message="check contract mismatch; retry later",
            job_id=job_id,
            plan_id=plan_id,
            task_id=check_task_id,
            payload={"step": step, "why": "CONTRACT_MISMATCH", "next": "RETRY_CHECK"},
        )
        emit_workflow_event(
            conn,
            workflow="RUN",
            event_type="STEP_FINISHED",
            severity="WARN",
            message="V2_CHECK finished",
            job_id=job_id,
            plan_id=plan_id,
            task_id=check_task_id,
            payload={"step": step, "ok": False, "error_code": "CONTRACT_MISMATCH"},
        )
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
        emit_workflow_event(
            conn,
            workflow="RUN",
            event_type="ERROR_RAISED",
            severity="ERROR",
            message="reviewer failed",
            job_id=job_id,
            plan_id=plan_id,
            task_id=check_task_id,
            payload={"step": step, "error_code": "REVIEWER_FAILED", "next": "BLOCKED_WAITING_EXTERNAL"},
        )
        emit_workflow_event(
            conn,
            workflow="RUN",
            event_type="STEP_FINISHED",
            severity="WARN",
            message="V2_CHECK finished",
            job_id=job_id,
            plan_id=plan_id,
            task_id=check_task_id,
            payload={"step": step, "ok": False, "error_code": "REVIEWER_FAILED"},
        )
        return {"ok": False, "error_code": "REVIEWER_FAILED", "hint": "Reviewer crashed; check prompt/contracts or rerun later."}

    if not isinstance(review_payload, dict):
        record_error(conn, plan_id=plan_id, task_id=check_task_id, error_code="REVIEWER_BAD_OUTPUT", message="reviewer_fn must return a dict")
        _inc_attempt(conn, task_id=check_task_id)
        cfg = get_runtime_config()
        if _attempt_count(conn, task_id=check_task_id) >= int(cfg.max_check_attempts_v2):
            apply_error_outcome(conn, plan_id=plan_id, task_id=check_task_id, outcome=map_error_to_outcome("MAX_ATTEMPTS_EXCEEDED"))
            emit_workflow_event(
                conn,
                workflow="RUN",
                event_type="DECISION_MADE",
                severity="ERROR",
                message="check blocked: max bad output attempts",
                job_id=job_id,
                plan_id=plan_id,
                task_id=check_task_id,
                payload={"step": step, "why": "REVIEWER_BAD_OUTPUT", "next": "BLOCKED_WAITING_EXTERNAL"},
            )
            emit_workflow_event(
                conn,
                workflow="RUN",
                event_type="STEP_FINISHED",
                severity="WARN",
                message="V2_CHECK finished",
                job_id=job_id,
                plan_id=plan_id,
                task_id=check_task_id,
                payload={"step": step, "ok": False, "error_code": "MAX_ATTEMPTS_EXCEEDED"},
            )
            return {"ok": False, "error_code": "MAX_ATTEMPTS_EXCEEDED", "hint": "Reviewer output repeatedly invalid; please fix prompts/contracts."}
        _set_status(conn, plan_id=plan_id, task_id=check_task_id, status="READY", blocked_reason=None, job_id=job_id)
        emit_workflow_event(
            conn,
            workflow="RUN",
            event_type="DECISION_MADE",
            severity="WARN",
            message="reviewer bad output; retry later",
            job_id=job_id,
            plan_id=plan_id,
            task_id=check_task_id,
            payload={"step": step, "why": "REVIEWER_BAD_OUTPUT", "next": "RETRY_CHECK"},
        )
        emit_workflow_event(
            conn,
            workflow="RUN",
            event_type="STEP_FINISHED",
            severity="WARN",
            message="V2_CHECK finished",
            job_id=job_id,
            plan_id=plan_id,
            task_id=check_task_id,
            payload={"step": step, "ok": False, "error_code": "REVIEWER_BAD_OUTPUT"},
        )
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
        "remediation_text": review_payload.get("remediation_text") or "",
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
    emit_workflow_event(
        conn,
        workflow="RUN",
        event_type="REVIEW_WRITTEN",
        severity="INFO",
        message=f"review {verdict}",
        job_id=job_id,
        plan_id=plan_id,
        task_id=check_task_id,
        payload={
            "step": step,
            "check_task_id": check_task_id,
            "review_target_task_id": target_id,
            "reviewed_artifact_id": reviewed_artifact_id,
            "verdict": verdict,
            "total_score": int(normalized_review.get("total_score") or 0),
        },
    )

    if verdict == "APPROVED":
        set_approved_artifact(conn, task_id=target_id, artifact_id=reviewed_artifact_id)
        try:
            from core.manifest import write_manifest_json

            write_manifest_json(conn, plan_id=str(plan_id), include_candidates=True)
        except Exception:
            pass
        emit_workflow_event(
            conn,
            workflow="RUN",
            event_type="ARTIFACT_APPROVED",
            severity="INFO",
            message="artifact approved",
            job_id=job_id,
            plan_id=plan_id,
            task_id=target_id,
            payload={"step": step, "approved_artifact_id": reviewed_artifact_id, "review_target_task_id": target_id},
        )
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
            emit_workflow_event(
                conn,
                workflow="RUN",
                event_type="DECISION_MADE",
                severity="WARN",
                message="approved older candidate; needs re-review",
                job_id=job_id,
                plan_id=plan_id,
                task_id=target_id,
                payload={
                    "step": step,
                    "why": "STALE_REVIEW",
                    "next": "REVIEW_LATEST",
                    "approved_artifact_id": reviewed_artifact_id,
                    "current_active_artifact_id": current_active,
                },
            )
            _set_status(conn, plan_id=plan_id, task_id=target_id, status="READY_TO_CHECK", job_id=job_id)
        else:
            emit_workflow_event(
                conn,
                workflow="RUN",
                event_type="DECISION_MADE",
                severity="INFO",
                message="action approved",
                job_id=job_id,
                plan_id=plan_id,
                task_id=target_id,
                payload={"step": step, "why": "APPROVED", "next": "ACTION_DONE", "approved_artifact_id": reviewed_artifact_id},
            )
            _set_status(conn, plan_id=plan_id, task_id=target_id, status="DONE", job_id=job_id)
    else:
        try:
            from core.manifest import write_manifest_json

            write_manifest_json(conn, plan_id=str(plan_id), include_candidates=True)
        except Exception:
            pass
        emit_workflow_event(
            conn,
            workflow="RUN",
            event_type="DECISION_MADE",
            severity="INFO",
            message="action rejected; needs modify",
            job_id=job_id,
            plan_id=plan_id,
            task_id=target_id,
            payload={"step": step, "why": "REJECTED", "next": "TO_BE_MODIFY", "reviewed_artifact_id": reviewed_artifact_id},
        )
        # Write remediation note for the executor (target ACTION), capped by config.
        try:
            cfg = get_runtime_config()
            max_chars = int(getattr(cfg, "task_review_notes_max_chars", 500) or 500)
            rt = str(normalized_review.get("remediation_text") or "").strip()
            if not rt:
                # Fallback: include summary + suggestions as plain text.
                rt = str(normalized_review.get("summary") or "").strip()
            if rt:
                _write_suggestions_md(task_id=target_id, text=_truncate_text(rt, max_chars=max_chars))
        except Exception:
            pass
        _set_status(conn, plan_id=plan_id, task_id=target_id, status="TO_BE_MODIFY", job_id=job_id)

    _set_status(conn, plan_id=plan_id, task_id=check_task_id, status="DONE", job_id=job_id)
    emit_workflow_event(
        conn,
        workflow="RUN",
        event_type="STEP_FINISHED",
        severity="INFO",
        message="V2_CHECK finished",
        job_id=job_id,
        plan_id=plan_id,
        task_id=check_task_id,
        payload={"step": step, "ok": True, "verdict": verdict, "reviewed_artifact_id": reviewed_artifact_id},
    )
    return {"ok": True, "verdict": verdict, "review_target_task_id": target_id, "reviewed_artifact_id": reviewed_artifact_id}
