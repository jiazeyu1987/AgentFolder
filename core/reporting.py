from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import config
from core.graph import _parse_required_docs_md
from core.util import utc_now_iso


@dataclass(frozen=True)
class ReportContext:
    plan_id: str
    workflow_mode: str


def _safe_json(obj: Any, *, max_len: int = 260) -> str:
    try:
        s = json.dumps(obj, ensure_ascii=False)
    except Exception:
        s = str(obj)
    if len(s) > max_len:
        return s[: max_len - 3] + "..."
    return s


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
        payload = {"error_code": "UNKNOWN", "message": _safe_json(payload)}
    payload["_created_at"] = row["created_at"]
    return payload


def _hint_from_error_payload(payload: Dict[str, Any]) -> str:
    ctx = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    if isinstance(ctx, dict):
        hint = ctx.get("hint")
        if isinstance(hint, str) and hint.strip():
            return hint.strip()
        # Common fallback keys used elsewhere in the repo.
        for k in ("validator_error", "missing_path"):
            v = ctx.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


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


def _is_plan_done(conn: sqlite3.Connection, *, plan_id: str, root_task_id: str) -> bool:
    row = conn.execute("SELECT status FROM task_nodes WHERE task_id = ?", (root_task_id,)).fetchone()
    return bool(row and str(row["status"] or "") == "DONE")


def _runnable_counts(conn: sqlite3.Connection, *, plan_id: str) -> Dict[str, Dict[str, int]]:
    rows = conn.execute(
        """
        SELECT node_type, status, COUNT(1) AS cnt
        FROM task_nodes
        WHERE plan_id = ? AND active_branch = 1
        GROUP BY node_type, status
        ORDER BY node_type ASC
        """,
        (plan_id,),
    ).fetchall()
    out: Dict[str, Dict[str, int]] = {}
    for r in rows:
        nt = str(r["node_type"] or "")
        st = str(r["status"] or "")
        out.setdefault(nt, {})[st] = int(r["cnt"])
    return out


def _in_progress(conn: sqlite3.Connection, *, plan_id: str) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT title, node_type, status, blocked_reason, attempt_count, owner_agent_id
        FROM task_nodes
        WHERE plan_id = ? AND active_branch = 1 AND status = 'IN_PROGRESS'
        ORDER BY priority DESC, updated_at DESC
        """,
        (plan_id,),
    ).fetchall()
    return [
        _node_item(
            task_title=str(r["title"] or ""),
            node_type=str(r["node_type"] or ""),
            status=str(r["status"] or ""),
            blocked_reason=r["blocked_reason"],
            attempt_count=int(r["attempt_count"] or 0),
            owner=str(r["owner_agent_id"] or ""),
        )
        for r in rows
    ]


def _waiting_review_nodes(conn: sqlite3.Connection, *, plan_id: str) -> List[Dict[str, Any]]:
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


def _blocked_failed_ready_nodes(conn: sqlite3.Connection, *, plan_id: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    rows = conn.execute(
        """
        SELECT task_id, title, node_type, status, blocked_reason, attempt_count, owner_agent_id
        FROM task_nodes
        WHERE plan_id = ? AND active_branch = 1
          AND status IN ('BLOCKED', 'FAILED', 'READY')
        ORDER BY priority DESC, updated_at DESC
        """,
        (plan_id,),
    ).fetchall()
    blocked: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    ready: List[Dict[str, Any]] = []
    for r in rows:
        item = _node_item(
            task_title=str(r["title"] or ""),
            node_type=str(r["node_type"] or ""),
            status=str(r["status"] or ""),
            blocked_reason=r["blocked_reason"],
            attempt_count=int(r["attempt_count"] or 0),
            owner=str(r["owner_agent_id"] or ""),
        )
        if item["status"] == "BLOCKED":
            blocked.append(item)
        elif item["status"] == "FAILED":
            failed.append(item)
        else:
            ready.append(item)
    return blocked, failed, ready


def _inputs_needed(conn: sqlite3.Connection, *, plan_id: str, required_docs_dir: Path) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT task_id, title, node_type, status, blocked_reason
        FROM task_nodes
        WHERE plan_id = ? AND active_branch = 1 AND status = 'BLOCKED' AND blocked_reason = 'WAITING_INPUT'
        ORDER BY priority DESC, updated_at DESC
        """,
        (plan_id,),
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        tid = str(r["task_id"])
        req_path = required_docs_dir / f"{tid}.md"
        items = _parse_required_docs_md(req_path) if req_path.exists() else []
        out.append(
            {
                "task_title": str(r["title"] or ""),
                "required_docs_path": str(req_path),
                "items": [
                    {
                        "name": str(it.get("name") or ""),
                        "accepted_types": it.get("accepted_types") or [],
                        "suggested_path": str(it.get("suggested_path") or ""),
                    }
                    for it in (items or [])
                    if isinstance(it, dict)
                ],
            }
        )
    return out


def _recent_errors(conn: sqlite3.Connection, *, plan_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT e.created_at, e.task_id, e.payload_json, n.title AS task_title
        FROM task_events e
        LEFT JOIN task_nodes n ON n.task_id = e.task_id
        WHERE e.plan_id = ? AND e.event_type = 'ERROR'
        ORDER BY e.created_at DESC
        LIMIT ?
        """,
        (plan_id, int(limit)),
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        try:
            payload = json.loads(r["payload_json"] or "{}")
        except Exception:
            payload = {"error_code": "UNKNOWN", "message": str(r["payload_json"] or "")}
        if not isinstance(payload, dict):
            payload = {"error_code": "UNKNOWN", "message": _safe_json(payload)}
        hint = _hint_from_error_payload(payload)
        ctx = payload.get("context") if isinstance(payload.get("context"), dict) else {}
        out.append(
            {
                "task_title": str(r["task_title"] or ""),
                "created_at": str(r["created_at"] or ""),
                "error_code": str(payload.get("error_code") or ""),
                "message": str(payload.get("message") or ""),
                "hint": hint,
                "context_excerpt": _safe_json(ctx),
            }
        )
    return out


def _review_trace(conn: sqlite3.Connection, *, plan_id: str) -> List[Dict[str, Any]]:
    actions = conn.execute(
        """
        SELECT task_id, title, active_artifact_id, approved_artifact_id
        FROM task_nodes
        WHERE plan_id = ? AND active_branch = 1 AND node_type = 'ACTION'
        ORDER BY priority DESC, updated_at DESC
        """,
        (plan_id,),
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for a in actions:
        aid = str(a["task_id"])
        latest = conn.execute(
            """
            SELECT verdict, reviewed_artifact_id, created_at
            FROM reviews
            WHERE review_target_task_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (aid,),
        ).fetchone()
        latest_review = None
        if latest:
            latest_review = {
                "verdict": str(latest["verdict"] or ""),
                "reviewed_artifact_id": str(latest["reviewed_artifact_id"] or ""),
                "created_at": str(latest["created_at"] or ""),
            }
        out.append(
            {
                "action_title": str(a["title"] or ""),
                "active_artifact_id": str(a["active_artifact_id"] or ""),
                "approved_artifact_id": str(a["approved_artifact_id"] or ""),
                "latest_review": latest_review,
            }
        )
    return out


def generate_plan_report(conn: sqlite3.Connection, plan_id: str, *, workflow_mode: str) -> Dict[str, Any]:
    plan = conn.execute("SELECT plan_id, title, root_task_id FROM plans WHERE plan_id = ?", (plan_id,)).fetchone()
    if not plan:
        raise RuntimeError(f"plan not found: {plan_id}")

    root_task_id = str(plan["root_task_id"])
    is_done = _is_plan_done(conn, plan_id=plan_id, root_task_id=root_task_id)
    is_blocked_waiting_input = (
        int(
            conn.execute(
                """
                SELECT COUNT(1) AS cnt
                FROM task_nodes
                WHERE plan_id = ? AND active_branch = 1 AND status = 'BLOCKED' AND blocked_reason = 'WAITING_INPUT'
                """,
                (plan_id,),
            ).fetchone()["cnt"]
        )
        > 0
    )

    blocked, failed, ready = _blocked_failed_ready_nodes(conn, plan_id=plan_id)
    waiting_review = _waiting_review_nodes(conn, plan_id=plan_id) if str(workflow_mode) == "v2" else []

    report: Dict[str, Any] = {
        "plan": {"plan_id": str(plan["plan_id"]), "title": str(plan["title"]), "workflow_mode": str(workflow_mode)},
        "summary": {
            "generated_at": utc_now_iso(),
            "is_done": bool(is_done),
            "is_blocked_waiting_input": bool(is_blocked_waiting_input),
            "runnable_counts": _runnable_counts(conn, plan_id=plan_id),
            "in_progress": _in_progress(conn, plan_id=plan_id),
        },
        "nodes": {
            "blocked": blocked,
            "failed": failed,
            "waiting_review": waiting_review,
            "ready": ready,
        },
        "inputs_needed": _inputs_needed(conn, plan_id=plan_id, required_docs_dir=config.REQUIRED_DOCS_DIR),
        "recent_errors": _recent_errors(conn, plan_id=plan_id, limit=20),
        "review_trace": _review_trace(conn, plan_id=plan_id) if str(workflow_mode) == "v2" else [],
        "next_steps": [],
    }

    # next_steps: deterministic, short, actionable.
    steps: List[Dict[str, str]] = []
    steps.append({"cmd": f"python agent_cli.py doctor --plan-id {plan_id}", "why": "检查数据结构/一致性问题（可定位缺字段/缺绑定/重复评审）"})
    if str(workflow_mode) == "v2":
        steps.append({"cmd": f"python agent_cli.py export --plan-id {plan_id}", "why": "导出最终交付物（默认只导出 approved 版本）"})
    if report["inputs_needed"]:
        first_path = str(report["inputs_needed"][0].get("required_docs_path") or "")
        if first_path:
            steps.append({"cmd": f"notepad \"{first_path}\"", "why": "打开缺输入清单并按 suggested_path 补齐文件（系统会先搜 baseline_inputs）"})
    if report["nodes"]["waiting_review"]:
        steps.append({"cmd": f"python agent_cli.py run --max-iterations 20", "why": "触发评审（CHECK）或推进可运行节点"})
    if report["nodes"]["ready"]:
        steps.append({"cmd": f"python agent_cli.py run --max-iterations 20", "why": "推进 READY 节点"})
    report["next_steps"] = steps
    return report


def render_plan_report_md(report: Dict[str, Any]) -> str:
    plan = report.get("plan") or {}
    summary = report.get("summary") or {}
    nodes = report.get("nodes") or {}
    inputs_needed = report.get("inputs_needed") or []
    recent_errors = report.get("recent_errors") or []
    next_steps = report.get("next_steps") or []

    lines: List[str] = []
    lines.append(f"# Plan Report: {plan.get('title','')}")
    lines.append("")
    lines.append(f"- plan_id: {plan.get('plan_id','')}")
    lines.append(f"- workflow_mode: {plan.get('workflow_mode','')}")
    lines.append(f"- generated_at: {summary.get('generated_at','')}")
    lines.append(f"- is_done: {bool(summary.get('is_done'))}")
    lines.append(f"- blocked_waiting_input: {bool(summary.get('is_blocked_waiting_input'))}")
    lines.append("")

    def section(title: str, items: List[Dict[str, Any]], *, max_items: int = 20) -> None:
        lines.append(f"## {title}")
        if not items:
            lines.append("- (none)")
            lines.append("")
            return
        for it in items[:max_items]:
            t = str(it.get("task_title") or "")
            nt = str(it.get("node_type") or "")
            st = str(it.get("status") or "")
            br = str(it.get("blocked_reason") or "")
            owner = str(it.get("owner") or "")
            attempts = int(it.get("attempt_count") or 0)
            reason = str(it.get("reason") or "")
            extra = f" reason={reason}" if reason else ""
            br_part = f", blocked_reason={br}" if br else ""
            lines.append(f"- {t} [{nt}] status={st}{br_part}, owner={owner}, attempts={attempts}{extra}")
        lines.append("")

    section("Waiting Review", nodes.get("waiting_review") or [])
    section("Blocked", nodes.get("blocked") or [])
    section("Failed", nodes.get("failed") or [])

    lines.append("## Inputs Needed")
    if not inputs_needed:
        lines.append("- (none)")
        lines.append("")
    else:
        for it in inputs_needed[:20]:
            lines.append(f"- {it.get('task_title','')}")
            lines.append(f"  - required_docs_path: {it.get('required_docs_path','')}")
            items = it.get("items") or []
            if items:
                lines.append("  - items:")
                for d in items[:12]:
                    nm = str(d.get("name") or "")
                    sp = str(d.get("suggested_path") or "")
                    at = str(d.get("accepted_types") or "")
                    line = f"    - {nm}"
                    if sp:
                        line += f" -> {sp}"
                    if at:
                        line += f" (types={at})"
                    lines.append(line)
        lines.append("")

    lines.append("## Recent Errors")
    if not recent_errors:
        lines.append("- (none)")
        lines.append("")
    else:
        for e in recent_errors[:20]:
            t = str(e.get("task_title") or "")
            code = str(e.get("error_code") or "")
            msg = str(e.get("message") or "")
            hint = str(e.get("hint") or "")
            when = str(e.get("created_at") or "")
            lines.append(f"- [{when}] {t}: {code} {msg}".strip())
            if hint:
                lines.append(f"  - hint: {hint}")
        lines.append("")

    lines.append("## Next Steps")
    if not next_steps:
        lines.append("- (none)")
        lines.append("")
    else:
        for s in next_steps[:12]:
            lines.append(f"- {s.get('cmd','')}")
            why = str(s.get("why") or "").strip()
            if why:
                lines.append(f"  - why: {why}")
        lines.append("")

    # Ensure the report does not contain raw task_id tokens outside file paths.
    return "\n".join(lines).rstrip() + "\n"
