from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from core.events import emit_event
from core.util import utc_now_iso
import config


def _requirements_satisfied(conn: sqlite3.Connection, task_id: str) -> Tuple[bool, List[Dict[str, object]]]:
    req_rows = conn.execute(
        "SELECT requirement_id, name, required, min_count FROM input_requirements WHERE task_id = ?",
        (task_id,),
    ).fetchall()
    missing: List[Dict[str, object]] = []
    for req in req_rows:
        if int(req["required"]) != 1:
            continue
        count = conn.execute("SELECT COUNT(1) FROM evidences WHERE requirement_id = ?", (req["requirement_id"],)).fetchone()[0]
        if int(count) < int(req["min_count"]):
            missing.append(
                {
                    "requirement_id": req["requirement_id"],
                    "name": req["name"],
                    "min_count": int(req["min_count"]),
                    "have_count": int(count),
                }
            )
    return (len(missing) == 0), missing


def _deps_satisfied(conn: sqlite3.Connection, plan_id: str, task_id: str) -> bool:
    dep_rows = conn.execute(
        """
        SELECT from_task_id FROM task_edges
        WHERE plan_id = ? AND to_task_id = ? AND edge_type = 'DEPENDS_ON'
        """,
        (plan_id, task_id),
    ).fetchall()
    for dep in dep_rows:
        row = conn.execute("SELECT status FROM task_nodes WHERE task_id = ?", (dep["from_task_id"],)).fetchone()
        if not row or row["status"] != "DONE":
            return False
    return True


def _set_status(conn: sqlite3.Connection, *, plan_id: str, task_id: str, status: str, blocked_reason: Optional[str]) -> None:
    now = utc_now_iso()
    conn.execute(
        "UPDATE task_nodes SET status = ?, blocked_reason = ?, updated_at = ? WHERE task_id = ?",
        (status, blocked_reason, now, task_id),
    )
    emit_event(conn, plan_id=plan_id, task_id=task_id, event_type="STATUS_CHANGED", payload={"status": status, "blocked_reason": blocked_reason})


def _set_active_branch(conn: sqlite3.Connection, *, plan_id: str, task_id: str, active_branch: int, reason: str) -> None:
    conn.execute(
        "UPDATE task_nodes SET active_branch = ?, updated_at = ? WHERE task_id = ?",
        (int(active_branch), utc_now_iso(), task_id),
    )
    emit_event(conn, plan_id=plan_id, task_id=task_id, event_type="BRANCH_CHANGED", payload={"active_branch": int(active_branch), "reason": reason})


@dataclass(frozen=True)
class _AltEdge:
    from_task_id: str
    to_task_id: str
    group_id: str


def _load_alternative_groups(conn: sqlite3.Connection, plan_id: str) -> Dict[Tuple[str, str], List[str]]:
    rows = conn.execute(
        """
        SELECT from_task_id, to_task_id, metadata_json
        FROM task_edges
        WHERE plan_id = ? AND edge_type = 'ALTERNATIVE'
        """,
        (plan_id,),
    ).fetchall()
    groups: Dict[Tuple[str, str], List[str]] = {}
    for r in rows:
        meta = {}
        try:
            meta = json.loads(r["metadata_json"] or "{}")
        except Exception:
            meta = {}
        group_id = str(meta.get("group_id") or "").strip()
        if not group_id:
            continue
        key = (r["from_task_id"], group_id)
        groups.setdefault(key, []).append(r["to_task_id"])
    return groups


def _apply_alternative_selection(conn: sqlite3.Connection, *, plan_id: str) -> None:
    """
    Enforce ALTERNATIVE groups using task_nodes.active_branch:
    - If any child is DONE => others become inactive (active_branch=0). Optionally ABANDONED.
    - Else keep exactly one active candidate; choose deterministic by priority desc then attempt asc.
    """
    groups = _load_alternative_groups(conn, plan_id)
    if not groups:
        return

    for (parent_id, group_id), child_ids in groups.items():
        placeholders = ",".join("?" for _ in child_ids)
        rows = conn.execute(
            f"""
            SELECT task_id, status, blocked_reason, attempt_count, priority, active_branch
            FROM task_nodes
            WHERE task_id IN ({placeholders})
            """,
            tuple(child_ids),
        ).fetchall()
        by_id = {r["task_id"]: r for r in rows}

        done_children = [cid for cid in child_ids if by_id.get(cid) and by_id[cid]["status"] == "DONE"]
        if done_children:
            winner = done_children[0]
            for cid in child_ids:
                if cid == winner:
                    if int(by_id[cid]["active_branch"]) != 1:
                        _set_active_branch(conn, plan_id=plan_id, task_id=cid, active_branch=1, reason=f"alternative_winner:{group_id}")
                    continue
                if int(by_id[cid]["active_branch"]) != 0:
                    _set_active_branch(conn, plan_id=plan_id, task_id=cid, active_branch=0, reason=f"alternative_loser:{group_id}")
                if by_id[cid]["status"] not in {"DONE", "ABANDONED"}:
                    _set_status(conn, plan_id=plan_id, task_id=cid, status="ABANDONED", blocked_reason=None)
            continue

        # Prefer existing active if still viable
        active = [cid for cid in child_ids if by_id.get(cid) and int(by_id[cid]["active_branch"]) == 1 and by_id[cid]["status"] != "ABANDONED"]
        keep = active[0] if len(active) == 1 else None
        if keep:
            status = by_id[keep]["status"]
            blocked_reason = by_id[keep]["blocked_reason"]
            if status == "FAILED" or (status == "BLOCKED" and blocked_reason == "WAITING_EXTERNAL"):
                keep = None

        candidates = []
        for cid in child_ids:
            r = by_id.get(cid)
            if not r:
                continue
            if r["status"] == "ABANDONED":
                continue
            candidates.append(
                (
                    int(r["priority"]),
                    -int(r["attempt_count"]),  # fewer attempts first
                    cid,
                )
            )
        candidates.sort(reverse=True)
        chosen = keep or (candidates[0][2] if candidates else None)
        if not chosen:
            continue

        for cid in child_ids:
            target = 1 if cid == chosen else 0
            if int(by_id[cid]["active_branch"]) != target:
                _set_active_branch(conn, plan_id=plan_id, task_id=cid, active_branch=target, reason=f"alternative_select:{group_id}")


def _propagate_inactive(conn: sqlite3.Connection, *, plan_id: str) -> None:
    """
    Propagate inactive branches:
    - If a parent is inactive, its DECOMPOSE children are inactive.
    - If a prerequisite is inactive, its DEPENDS_ON dependents are inactive.
    """
    changed = True
    while changed:
        changed = False
        rows = conn.execute(
            """
            SELECT e.edge_type, e.from_task_id, e.to_task_id, n_from.active_branch AS from_active, n_to.active_branch AS to_active
            FROM task_edges e
            JOIN task_nodes n_from ON n_from.task_id = e.from_task_id
            JOIN task_nodes n_to ON n_to.task_id = e.to_task_id
            WHERE e.plan_id = ? AND e.edge_type IN ('DECOMPOSE','DEPENDS_ON')
            """,
            (plan_id,),
        ).fetchall()
        for r in rows:
            if int(r["from_active"]) == 0 and int(r["to_active"]) != 0:
                _set_active_branch(conn, plan_id=plan_id, task_id=r["to_task_id"], active_branch=0, reason=f"propagate_inactive:{r['edge_type']}")
                changed = True


def recompute_readiness_for_plan(conn: sqlite3.Connection, *, plan_id: str) -> int:
    _apply_alternative_selection(conn, plan_id=plan_id)
    _propagate_inactive(conn, plan_id=plan_id)

    # Plan-review CHECK nodes (tags include ["review","plan"]) reflect whether the plan has been approved.
    plan_review_check_rows = conn.execute(
        """
        SELECT task_id, status
        FROM task_nodes
        WHERE plan_id = ?
          AND active_branch = 1
          AND node_type = 'CHECK'
          AND tags_json LIKE '%\"review\"%'
          AND tags_json LIKE '%\"plan\"%'
        """,
        (plan_id,),
    ).fetchall()
    if plan_review_check_rows:
        approved = conn.execute(
            "SELECT COUNT(1) FROM task_events WHERE plan_id = ? AND event_type = 'PLAN_APPROVED'",
            (plan_id,),
        ).fetchone()[0]
        for r in plan_review_check_rows:
            desired = "DONE" if int(approved) > 0 else "READY"
            if r["status"] != desired:
                _set_status(conn, plan_id=plan_id, task_id=r["task_id"], status=desired, blocked_reason=None)

    # Mirror reviewer work status via dedicated CHECK node(s) tagged with ["review","node"].
    # These nodes are informational gates so the task tree can represent the reviewer phase.
    review_node_check_rows = conn.execute(
        """
        SELECT task_id, status
        FROM task_nodes
        WHERE plan_id = ?
          AND active_branch = 1
          AND node_type = 'CHECK'
          AND tags_json LIKE '%\"review\"%'
          AND tags_json LIKE '%\"node\"%'
        """,
        (plan_id,),
    ).fetchall()
    if review_node_check_rows:
        pending_reviews = conn.execute(
            """
            SELECT COUNT(1)
            FROM task_nodes
            WHERE plan_id = ? AND active_branch = 1 AND status = 'READY_TO_CHECK'
            """,
            (plan_id,),
        ).fetchone()[0]
        any_review_written = conn.execute(
            """
            SELECT COUNT(1)
            FROM reviews r
            JOIN task_nodes n ON n.task_id = r.task_id
            WHERE n.plan_id = ?
            """,
            (plan_id,),
        ).fetchone()[0]
        for r in review_node_check_rows:
            desired = "READY" if int(pending_reviews) > 0 else ("DONE" if int(any_review_written) > 0 else "PENDING")
            if r["status"] != desired:
                _set_status(conn, plan_id=plan_id, task_id=r["task_id"], status=desired, blocked_reason=None)

    changed = 0
    rows = conn.execute(
        "SELECT task_id, status, blocked_reason FROM task_nodes WHERE plan_id = ? AND active_branch = 1",
        (plan_id,),
    ).fetchall()
    for row in rows:
        task_id = row["task_id"]
        status = row["status"]
        blocked_reason = row["blocked_reason"]

        if status in {"DONE", "ABANDONED", "IN_PROGRESS", "READY_TO_CHECK"}:
            continue
        if status == "FAILED" and not config.FAILED_AUTO_RESET_READY:
            continue
        if status == "TO_BE_MODIFY":
            continue

        deps_ok = _deps_satisfied(conn, plan_id, task_id)
        req_ok, missing = _requirements_satisfied(conn, task_id)

        if deps_ok and req_ok:
            if status != "READY":
                _set_status(conn, plan_id=plan_id, task_id=task_id, status="READY", blocked_reason=None)
                changed += 1
        else:
            if status == "READY":
                _set_status(conn, plan_id=plan_id, task_id=task_id, status="PENDING", blocked_reason=None)
                changed += 1
            if status == "BLOCKED" and blocked_reason == "WAITING_INPUT" and req_ok:
                _set_status(conn, plan_id=plan_id, task_id=task_id, status="READY", blocked_reason=None)
                changed += 1
            if not req_ok:
                emit_event(conn, plan_id=plan_id, task_id=task_id, event_type="WAITING_INPUT", payload={"missing_requirements": missing})

    # Aggregate goal completion: parent GOAL becomes DONE when all DECOMPOSE children are DONE.
    parents = conn.execute(
        """
        SELECT task_id FROM task_nodes
        WHERE plan_id = ? AND node_type = 'GOAL' AND status != 'DONE' AND active_branch = 1
        """,
        (plan_id,),
    ).fetchall()
    for parent in parents:
        parent_id = parent["task_id"]
        child_rows = conn.execute(
            """
            SELECT to_task_id, metadata_json FROM task_edges
            WHERE plan_id = ? AND from_task_id = ? AND edge_type = 'DECOMPOSE'
            """,
            (plan_id, parent_id),
        ).fetchall()
        if not child_rows:
            continue
        # Default AND unless metadata.and_or=OR is set on any of the DECOMPOSE edges from this parent.
        and_or = "AND"
        for c in child_rows:
            try:
                meta = json.loads(c["metadata_json"] or "{}")
            except Exception:
                meta = {}
            edge_and_or = (meta or {}).get("and_or", "AND")
            if edge_and_or in {"AND", "OR"}:
                and_or = edge_and_or
                break

        child_ids = [c["to_task_id"] for c in child_rows]
        placeholders = ",".join("?" for _ in child_ids)
        done_count = conn.execute(
            f"SELECT COUNT(1) FROM task_nodes WHERE task_id IN ({placeholders}) AND active_branch = 1 AND status = 'DONE'",
            tuple(child_ids),
        ).fetchone()[0]
        active_count = conn.execute(
            f"SELECT COUNT(1) FROM task_nodes WHERE task_id IN ({placeholders}) AND active_branch = 1",
            tuple(child_ids),
        ).fetchone()[0]

        if and_or == "AND":
            if int(active_count) > 0 and int(done_count) == int(active_count):
                _set_status(conn, plan_id=plan_id, task_id=parent_id, status="DONE", blocked_reason=None)
                changed += 1
        else:  # OR
            if int(done_count) >= 1:
                _set_status(conn, plan_id=plan_id, task_id=parent_id, status="DONE", blocked_reason=None)
                changed += 1

    return changed
