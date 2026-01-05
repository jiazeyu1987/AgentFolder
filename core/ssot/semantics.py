from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import config
from core.graph import _parse_required_docs_md


ReasonCode = str

REASON_CODES: List[str] = [
    "WAITING_INPUT",
    "WAITING_REVIEW",
    "WAITING_EXTERNAL",
    "BLOCKED",
    "FAILED",
    "RUNNABLE",
    "DONE",
]


def compute_plan_completion(conn: sqlite3.Connection, plan_id: str) -> Dict[str, Any]:
    """
    Plan completion semantics (v1/v2 compatible):
    A plan is DONE when all ACTION nodes are DONE (or ABANDONED) on the active branch.
    """
    remaining = conn.execute(
        """
        SELECT COUNT(1) AS c
        FROM task_nodes
        WHERE plan_id = ?
          AND active_branch = 1
          AND node_type = 'ACTION'
          AND status NOT IN ('DONE', 'ABANDONED')
        """,
        (plan_id,),
    ).fetchone()
    remaining_cnt = int(remaining["c"] if remaining else 0)
    return {"is_done": remaining_cnt == 0, "remaining_action_count": remaining_cnt}


def classify_task_reason(
    task_row: Any,
    *,
    workflow_mode: str,
    latest_error: Optional[Dict[str, Any]] = None,
    required_docs_items: Optional[List[Dict[str, Any]]] = None,
) -> ReasonCode:
    """
    Classify a single task into a stable ReasonCode for UI/CLI.
    """
    node_type = str(task_row["node_type"] or "")
    status = str(task_row["status"] or "")
    blocked_reason = str(task_row["blocked_reason"] or "")

    if status == "DONE":
        return "DONE"
    if status == "FAILED":
        return "FAILED"

    if str(workflow_mode) == "v2":
        if node_type == "ACTION" and status == "READY_TO_CHECK":
            return "WAITING_REVIEW"
        if node_type == "CHECK" and status in {"READY", "IN_PROGRESS"} and str(task_row.get("review_target_task_id") or "").strip():
            return "WAITING_REVIEW"

    if status == "READY":
        return "RUNNABLE"
    if status == "BLOCKED":
        if blocked_reason == "WAITING_INPUT" or required_docs_items:
            return "WAITING_INPUT"
        if blocked_reason == "WAITING_EXTERNAL":
            return "WAITING_EXTERNAL"
        return "BLOCKED"

    # Anything else (PENDING/TO_BE_MODIFY/IN_PROGRESS/READY_TO_CHECK in v1) is not a "reason bucket".
    if latest_error:
        # Keep classification stable; errors are surfaced separately.
        pass
    return "BLOCKED"


def _node_item(
    *,
    task_title: str,
    node_type: str,
    status: str,
    blocked_reason: Optional[str],
    attempt_count: int,
    owner: str,
    reason: str = "",
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "task_title": task_title,
        "node_type": node_type,
        "status": status,
        "blocked_reason": blocked_reason or "",
        "attempt_count": int(attempt_count),
        "owner": owner,
    }
    if reason:
        out["reason"] = reason
    return out


def compute_node_buckets(
    conn: sqlite3.Connection,
    *,
    plan_id: str,
    workflow_mode: str,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Return categorized node lists used by report/snapshot.
    The categorization here is the single source of truth.
    """
    rows = conn.execute(
        """
        SELECT task_id, title, node_type, status, blocked_reason, attempt_count, owner_agent_id, review_target_task_id
        FROM task_nodes
        WHERE plan_id = ? AND active_branch = 1
        """,
        (plan_id,),
    ).fetchall()

    blocked: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    ready: List[Dict[str, Any]] = []
    waiting_review: List[Dict[str, Any]] = []

    for r in rows:
        nt = str(r["node_type"] or "")
        st = str(r["status"] or "")
        br = str(r["blocked_reason"] or "")
        tid = str(r["task_id"] or "")

        reason = ""
        if str(workflow_mode) == "v2" and nt in {"ACTION", "CHECK"}:
            # Surface stale review as a waiting_review sub-reason (helps users understand why re-review happens).
            if st in {"READY_TO_CHECK", "READY", "IN_PROGRESS"}:
                payload = _latest_error_payload(conn, plan_id=plan_id, task_id=tid)
                if payload and payload.get("error_code") == "STALE_REVIEW":
                    reason = "stale_review: 已有新候选版本，需要评审最新 artifact"

        item = _node_item(
            task_title=str(r["title"] or ""),
            node_type=nt,
            status=st,
            blocked_reason=br,
            attempt_count=int(r["attempt_count"] or 0),
            owner=str(r["owner_agent_id"] or ""),
            reason=reason,
        )

        if st == "BLOCKED":
            blocked.append(item)
        elif st == "FAILED":
            failed.append(item)
        elif st == "READY":
            ready.append(item)

    if str(workflow_mode) == "v2":
        waiting_review = compute_waiting_review(conn, plan_id=plan_id, workflow_mode=workflow_mode)

    # stable ordering (closest to "what to do next"): higher priority / recently updated first
    def _sort_key(it: Dict[str, Any]) -> Tuple[int, str]:
        # attempt_count descending first, then title.
        return (int(it.get("attempt_count") or 0), str(it.get("task_title") or ""))

    blocked.sort(key=_sort_key, reverse=True)
    failed.sort(key=_sort_key, reverse=True)
    ready.sort(key=_sort_key, reverse=True)
    waiting_review.sort(key=lambda it: (str(it.get("node_type") or ""), _sort_key(it)), reverse=True)

    return {"blocked": blocked, "failed": failed, "ready": ready, "waiting_review": waiting_review}


def compute_reasons(*, buckets: Dict[str, List[Dict[str, Any]]], is_done: bool, plan_title: str = "") -> List[Dict[str, Any]]:
    """
    Reduce detailed buckets into a stable reason summary list.
    """
    reasons: List[Dict[str, Any]] = []
    waiting_review = buckets.get("waiting_review") or []
    blocked = buckets.get("blocked") or []
    failed = buckets.get("failed") or []
    ready = buckets.get("ready") or []

    if waiting_review:
        reasons.append({"code": "WAITING_REVIEW", "count": len(waiting_review), "example": str(waiting_review[0].get("task_title") or "")})
    if blocked:
        waiting_input = [b for b in blocked if str(b.get("blocked_reason") or "") == "WAITING_INPUT"]
        waiting_external = [b for b in blocked if str(b.get("blocked_reason") or "") == "WAITING_EXTERNAL"]
        other = [b for b in blocked if b not in waiting_input and b not in waiting_external]
        if waiting_input:
            reasons.append({"code": "WAITING_INPUT", "count": len(waiting_input), "example": str(waiting_input[0].get("task_title") or "")})
        if waiting_external:
            reasons.append({"code": "WAITING_EXTERNAL", "count": len(waiting_external), "example": str(waiting_external[0].get("task_title") or "")})
        if other:
            reasons.append({"code": "BLOCKED", "count": len(other), "example": str(other[0].get("task_title") or "")})
    if failed:
        reasons.append({"code": "FAILED", "count": len(failed), "example": str(failed[0].get("task_title") or "")})
    if ready:
        reasons.append({"code": "RUNNABLE", "count": len(ready), "example": str(ready[0].get("task_title") or "")})
    if not reasons and bool(is_done):
        reasons.append({"code": "DONE", "count": 1, "example": str(plan_title or "")})
    return reasons


def _latest_error_payload(conn: sqlite3.Connection, *, plan_id: str, task_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        """
        SELECT created_at, payload_json
        FROM task_events
        WHERE plan_id = ? AND task_id = ? AND event_type = 'ERROR'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (plan_id, task_id),
    ).fetchone()
    if not row:
        return None
    try:
        payload = json.loads(row["payload_json"] or "{}")
    except Exception:
        payload = {"error_code": "UNKNOWN", "message": str(row["payload_json"] or "")}
    if not isinstance(payload, dict):
        payload = {"error_code": "UNKNOWN", "message": str(payload)}
    payload["_created_at"] = row["created_at"]
    return payload


def compute_waiting_review(conn: sqlite3.Connection, *, plan_id: str, workflow_mode: str) -> List[Dict[str, Any]]:
    if str(workflow_mode) != "v2":
        return []
    rows = conn.execute(
        """
        SELECT task_id, title, node_type, status, blocked_reason, attempt_count, owner_agent_id
        FROM task_nodes
        WHERE plan_id = ? AND active_branch = 1
          AND (
            (node_type = 'ACTION' AND status = 'READY_TO_CHECK')
            OR (node_type = 'CHECK' AND status IN ('READY', 'IN_PROGRESS') AND review_target_task_id IS NOT NULL)
          )
        ORDER BY node_type ASC, priority DESC, updated_at DESC
        """,
        (plan_id,),
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        payload = _latest_error_payload(conn, plan_id=plan_id, task_id=str(r["task_id"]))
        reason = ""
        if payload and payload.get("error_code") == "STALE_REVIEW":
            reason = "stale_review: 已有新候选版本，需要评审最新 artifact"
        out.append(
            _node_item(
                task_title=str(r["title"] or ""),
                node_type=str(r["node_type"] or ""),
                status=str(r["status"] or ""),
                blocked_reason=r["blocked_reason"],
                attempt_count=int(r["attempt_count"] or 0),
                owner=str(r["owner_agent_id"] or ""),
                reason=reason,
            )
        )
    return out


def compute_inputs_needed(conn: sqlite3.Connection, *, plan_id: str, required_docs_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """
    Prefer explicit required_docs files for BLOCKED(WAITING_INPUT) tasks.
    Additionally, surface missing required inputs proactively based on input_requirements/evidences.
    """
    required_docs_dir = required_docs_dir or config.REQUIRED_DOCS_DIR

    def _items_from_required_docs(tid: str) -> List[Dict[str, Any]]:
        req_path = required_docs_dir / f"{tid}.md"
        items = _parse_required_docs_md(req_path) if req_path.exists() else []
        out_items: List[Dict[str, Any]] = []
        for it in (items or []):
            if not isinstance(it, dict):
                continue
            out_items.append(
                {
                    "name": str(it.get("name") or ""),
                    "description": str(it.get("description") or ""),
                    "accepted_types": it.get("accepted_types") or [],
                    "suggested_path": str(it.get("suggested_path") or ""),
                }
            )
        return out_items

    def _items_from_input_requirements(tid: str) -> List[Dict[str, Any]]:
        rows2 = conn.execute(
            """
            SELECT
              r.name,
              r.allowed_types_json,
              COALESCE(r.min_count, 1) AS min_count,
              COALESCE(ev.cnt, 0) AS have
            FROM input_requirements r
            LEFT JOIN (
              SELECT requirement_id, COUNT(1) AS cnt
              FROM evidences
              GROUP BY requirement_id
            ) ev ON ev.requirement_id = r.requirement_id
            WHERE r.task_id = ?
              AND COALESCE(r.required, 0) = 1
              AND COALESCE(ev.cnt, 0) < COALESCE(r.min_count, 1)
            ORDER BY r.created_at ASC
            """,
            (tid,),
        ).fetchall()
        out_items: List[Dict[str, Any]] = []
        for rr in rows2:
            allowed: List[str] = []
            raw = rr["allowed_types_json"]
            if raw:
                try:
                    allowed = json.loads(raw)
                except Exception:
                    allowed = []
            name = str(rr["name"] or "")
            out_items.append(
                {
                    "name": name,
                    "description": "",
                    "accepted_types": allowed,
                    "suggested_path": f"workspace/inputs/{name}/" if name else "workspace/inputs/",
                    "have": int(rr["have"] or 0),
                    "need": int(rr["min_count"] or 1),
                }
            )
        return out_items

    blocked_rows = conn.execute(
        """
        SELECT task_id, title
        FROM task_nodes
        WHERE plan_id = ? AND active_branch = 1 AND status = 'BLOCKED' AND blocked_reason = 'WAITING_INPUT'
        ORDER BY priority DESC, updated_at DESC
        """,
        (plan_id,),
    ).fetchall()
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for r in blocked_rows:
        tid = str(r["task_id"])
        seen.add(tid)
        req_path = required_docs_dir / f"{tid}.md"
        out.append({"task_title": str(r["title"] or ""), "required_docs_path": str(req_path), "items": _items_from_required_docs(tid)})

    missing_tasks = conn.execute(
        """
        SELECT DISTINCT tn.task_id, tn.title
        FROM task_nodes tn
        JOIN input_requirements r ON r.task_id = tn.task_id AND COALESCE(r.required, 0) = 1
        LEFT JOIN (
          SELECT requirement_id, COUNT(1) AS cnt
          FROM evidences
          GROUP BY requirement_id
        ) ev ON ev.requirement_id = r.requirement_id
        WHERE tn.plan_id = ?
          AND tn.active_branch = 1
          AND COALESCE(ev.cnt, 0) < COALESCE(r.min_count, 1)
        ORDER BY tn.priority DESC, tn.updated_at DESC
        LIMIT 50
        """,
        (plan_id,),
    ).fetchall()
    for r in missing_tasks:
        tid = str(r["task_id"])
        if tid in seen:
            continue
        req_path = required_docs_dir / f"{tid}.md"
        items = _items_from_required_docs(tid)
        if not items:
            items = _items_from_input_requirements(tid)
        if not items:
            continue
        out.append({"task_title": str(r["title"] or ""), "required_docs_path": str(req_path), "items": items})
    return out


def compute_runnable_tasks(conn: sqlite3.Connection, *, plan_id: str, workflow_mode: str, limit: int = 10) -> Dict[str, List[str]]:
    """
    Return runnable task IDs for the current workflow_mode.
    This is a thin SSOT wrapper over the existing scheduler selection logic.
    """
    from core.scheduler import pick_v2_check_tasks, pick_xiaobo_tasks, pick_xiaojing_check_nodes, pick_xiaojing_tasks

    out: Dict[str, List[str]] = {"xiaobo": [], "xiaojing": [], "checks": [], "v2_checks": []}
    out["xiaobo"] = [str(t.task_id) for t in pick_xiaobo_tasks(conn, plan_id=plan_id, limit=int(limit))]
    out["xiaojing"] = [str(t.task_id) for t in pick_xiaojing_tasks(conn, plan_id=plan_id, limit=int(limit))]
    out["checks"] = [str(t.task_id) for t in pick_xiaojing_check_nodes(conn, plan_id=plan_id, limit=int(limit))]
    out["v2_checks"] = [str(t.task_id) for t in pick_v2_check_tasks(conn, plan_id=plan_id, limit=int(limit))]
    return out


def compute_plan_blocked_waiting_user(conn: sqlite3.Connection, plan_id: str) -> bool:
    """
    True when nothing is runnable and at least one task is BLOCKED waiting input/external.
    """
    runnable = conn.execute(
        """
        SELECT COUNT(1) FROM task_nodes
        WHERE plan_id = ? AND active_branch = 1 AND status IN ('READY', 'TO_BE_MODIFY', 'READY_TO_CHECK', 'IN_PROGRESS')
        """,
        (plan_id,),
    ).fetchone()[0]
    if int(runnable) > 0:
        return False
    blocked = conn.execute(
        """
        SELECT COUNT(1) FROM task_nodes
        WHERE plan_id = ? AND active_branch = 1 AND status = 'BLOCKED' AND blocked_reason IN ('WAITING_INPUT', 'WAITING_EXTERNAL')
        """,
        (plan_id,),
    ).fetchone()[0]
    return int(blocked) > 0

