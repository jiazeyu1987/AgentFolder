from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

import config
from core.db import transaction
from core.util import utc_now_iso


@dataclass(frozen=True)
class ResetToPlanResult:
    plan_id: str
    task_count: int
    deleted_artifacts: int
    deleted_reviews: int
    deleted_approvals: int
    deleted_skill_runs: int
    deleted_llm_calls: int
    deleted_task_events: int
    deleted_evidences: int
    deleted_audit_events: int
    deleted_files: int


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(r["name"]) for r in rows}


def _parse_tags(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw if isinstance(x, str) and str(x).strip()]
    if isinstance(raw, str) and raw.strip():
        try:
            obj = json.loads(raw)
            if isinstance(obj, list):
                return [str(x) for x in obj if isinstance(x, str) and str(x).strip()]
        except Exception:
            return []
    return []


def _is_plan_review_check_node(*, node_type: str, owner: str, tags_json: Any) -> bool:
    if node_type != "CHECK":
        return False
    if owner != "xiaojing":
        return False
    tags = set(t.lower() for t in _parse_tags(tags_json))
    return "review" in tags and "plan" in tags


def _safe_unlink(path: Path) -> int:
    try:
        if path.exists():
            path.unlink()
            return 1
    except Exception:
        return 0
    return 0


def _safe_rmtree(path: Path) -> int:
    # Return number of deleted filesystem entries (best-effort).
    if not path.exists():
        return 0
    n = 0
    try:
        for p in sorted(path.rglob("*"), key=lambda x: len(str(x)), reverse=True):
            try:
                if p.is_file() or p.is_symlink():
                    p.unlink()
                    n += 1
                elif p.is_dir():
                    p.rmdir()
                    n += 1
            except Exception:
                continue
        try:
            path.rmdir()
            n += 1
        except Exception:
            pass
    except Exception:
        return n
    return n


def reset_plan_to_pre_run(
    conn: sqlite3.Connection,
    *,
    plan_id: str,
    workspace_dir: Optional[Path] = None,
) -> ResetToPlanResult:
    """
    Delete "Run" side-effects while keeping the plan structure:
    - Keep plans/task_nodes/task_edges/input_requirements
    - Restore task_nodes to post-create-plan, pre-run baseline (mostly PENDING)
    - Remove run artifacts/reviews/llm_calls/task_events/skill_runs/evidences for the plan
    - Delete workspace artifacts/reviews/required_docs/deliverables for the plan (keep inputs/)
    """
    ws = workspace_dir or config.WORKSPACE_DIR
    artifacts_dir = ws / "artifacts"
    reviews_dir = ws / "reviews"
    required_docs_dir = ws / "required_docs"
    deliverables_dir = ws / "deliverables"

    task_rows = conn.execute(
        "SELECT task_id, node_type, owner_agent_id, tags_json FROM task_nodes WHERE plan_id = ? AND active_branch = 1",
        (plan_id,),
    ).fetchall()
    task_ids = [str(r["task_id"]) for r in task_rows]
    plan_review_check_ids = {
        str(r["task_id"])
        for r in task_rows
        if _is_plan_review_check_node(node_type=str(r["node_type"]), owner=str(r["owner_agent_id"]), tags_json=r["tags_json"])
    }

    cols_task_nodes = _table_columns(conn, "task_nodes")
    cols_reviews = _table_columns(conn, "reviews")

    deleted_files = 0
    for tid in task_ids:
        deleted_files += _safe_rmtree(artifacts_dir / tid)
        if tid not in plan_review_check_ids:
            deleted_files += _safe_rmtree(reviews_dir / tid)
        deleted_files += _safe_unlink(required_docs_dir / f"{tid}.md")
    deleted_files += _safe_rmtree(deliverables_dir / plan_id)

    deleted_approvals = 0
    deleted_artifacts = 0
    deleted_reviews = 0
    deleted_skill_runs = 0
    deleted_llm_calls = 0
    deleted_task_events = 0
    deleted_evidences = 0
    deleted_audit_events = 0

    now = utc_now_iso()
    with transaction(conn):
        if task_ids:
            placeholders = ",".join("?" for _ in task_ids)

            # approvals -> artifacts
            art_ids = [
                str(r["artifact_id"])
                for r in conn.execute(f"SELECT artifact_id FROM artifacts WHERE task_id IN ({placeholders})", tuple(task_ids)).fetchall()
            ]
            if art_ids:
                ph2 = ",".join("?" for _ in art_ids)
                deleted_approvals = conn.execute(f"DELETE FROM approvals WHERE artifact_id IN ({ph2})", tuple(art_ids)).rowcount
                deleted_artifacts = conn.execute(f"DELETE FROM artifacts WHERE artifact_id IN ({ph2})", tuple(art_ids)).rowcount

            # reviews: keep plan review CHECK node(s) produced during create-plan (tags: review+plan)
            keep_ids = list(plan_review_check_ids)
            if keep_ids:
                ph_keep = ",".join("?" for _ in keep_ids)
                deleted_reviews = conn.execute(
                    f"DELETE FROM reviews WHERE task_id IN ({placeholders}) AND task_id NOT IN ({ph_keep})",
                    tuple(task_ids + keep_ids),
                ).rowcount
            else:
                deleted_reviews = conn.execute(f"DELETE FROM reviews WHERE task_id IN ({placeholders})", tuple(task_ids)).rowcount

            # skill runs are only from execution, not from create-plan.
            deleted_skill_runs = conn.execute("DELETE FROM skill_runs WHERE plan_id = ?", (plan_id,)).rowcount

            # Delete evidences generated during run; keep requirements.
            req_ids = [
                str(r["requirement_id"])
                for r in conn.execute(
                    f"SELECT requirement_id FROM input_requirements WHERE task_id IN ({placeholders})",
                    tuple(task_ids),
                ).fetchall()
            ]
            if req_ids:
                ph_req = ",".join("?" for _ in req_ids)
                deleted_evidences = conn.execute(f"DELETE FROM evidences WHERE requirement_id IN ({ph_req})", tuple(req_ids)).rowcount

            # Delete llm_calls created during run; keep PLAN_GEN/PLAN_REVIEW history.
            cols_llm = _table_columns(conn, "llm_calls")
            if "scope" in cols_llm and "plan_id" in cols_llm:
                deleted_llm_calls = conn.execute(
                    "DELETE FROM llm_calls WHERE plan_id = ? AND scope NOT IN ('PLAN_GEN','PLAN_REVIEW')",
                    (plan_id,),
                ).rowcount

            # Delete task_events generated during run: keep only plan-level events (task_id IS NULL).
            deleted_task_events = conn.execute("DELETE FROM task_events WHERE plan_id = ? AND task_id IS NOT NULL", (plan_id,)).rowcount

            # Delete audit events linked to run for this plan; keep create-plan entries without plan_id.
            if "audit_events" in {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}:
                cols_audit = _table_columns(conn, "audit_events")
                if "plan_id" in cols_audit:
                    deleted_audit_events = conn.execute("DELETE FROM audit_events WHERE plan_id = ?", (plan_id,)).rowcount

            # Restore node state to post-create-plan baseline.
            base_update_cols: dict[str, Any] = {
                "blocked_reason": None,
                "attempt_count": 0,
                "active_artifact_id": None,
                "active_branch": 1,
                "updated_at": now,
            }
            for k in ("approved_artifact_id", "last_error_code", "last_error_message", "last_error_at", "last_validator_error", "missing_requirements", "waiting_skill_count"):
                if k in cols_task_nodes:
                    base_update_cols[k] = None
            if "confidence" in cols_task_nodes:
                base_update_cols["confidence"] = 0.5

            def _update_task(task_id: str, *, status: str) -> None:
                sets = ", ".join([f"{k} = ?" for k in ["status", *base_update_cols.keys()]])
                values = [status, *base_update_cols.values(), task_id]
                conn.execute(f"UPDATE task_nodes SET {sets} WHERE task_id = ?", tuple(values))

            for r in task_rows:
                tid = str(r["task_id"])
                if tid in plan_review_check_ids:
                    _update_task(tid, status="DONE")
                else:
                    _update_task(tid, status="PENDING")

        # Ensure tasks/plan.json stays as-is; this is a per-plan reset.

    return ResetToPlanResult(
        plan_id=str(plan_id),
        task_count=len(task_ids),
        deleted_artifacts=int(deleted_artifacts),
        deleted_reviews=int(deleted_reviews),
        deleted_approvals=int(deleted_approvals),
        deleted_skill_runs=int(deleted_skill_runs),
        deleted_llm_calls=int(deleted_llm_calls),
        deleted_task_events=int(deleted_task_events),
        deleted_evidences=int(deleted_evidences),
        deleted_audit_events=int(deleted_audit_events),
        deleted_files=int(deleted_files),
    )

