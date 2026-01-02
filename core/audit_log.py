from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from core.util import stable_hash_text, utc_now_iso


def _truncate(s: Optional[str], *, max_chars: int) -> Optional[str]:
    if s is None:
        return None
    if max_chars <= 0:
        return s
    if len(s) <= max_chars:
        return s
    if max_chars < 16:
        return s[:max_chars]
    return s[: max_chars - 12] + "…[TRUNCATED]"


def _resolve_top_task_from_plan(conn: sqlite3.Connection, plan_id: str) -> tuple[Optional[str], Optional[str]]:
    try:
        row = conn.execute("SELECT title FROM plans WHERE plan_id = ?", (plan_id,)).fetchone()
    except Exception:
        row = None
    if not row:
        return None, None
    title = str(row["title"] or "").strip()
    if not title:
        return None, None
    return stable_hash_text(title), _truncate(title, max_chars=200)


def log_audit(
    conn: sqlite3.Connection,
    *,
    category: str,
    action: str,
    message: str,
    top_task_hash: Optional[str] = None,
    top_task_title: Optional[str] = None,
    plan_id: Optional[str] = None,
    task_id: Optional[str] = None,
    llm_call_id: Optional[str] = None,
    job_id: Optional[str] = None,
    status_before: Optional[str] = None,
    status_after: Optional[str] = None,
    ok: bool = True,
    payload: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Best-effort audit log: never raise (audit must not break workflows).
    Returns audit_id or "UNKNOWN".
    """
    audit_id = str(uuid.uuid4())
    try:
        # Fill top_task fields from plan title if missing.
        if (not top_task_hash or not top_task_title) and plan_id:
            h2, t2 = _resolve_top_task_from_plan(conn, str(plan_id))
            top_task_hash = top_task_hash or h2
            top_task_title = top_task_title or t2

        conn.execute(
            """
            INSERT INTO audit_events(
              audit_id, created_at,
              category, action,
              top_task_hash, top_task_title,
              plan_id, task_id, llm_call_id, job_id,
              status_before, status_after,
              ok, message, payload_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_id,
                utc_now_iso(),
                str(category or "").strip() or "UNKNOWN",
                str(action or "").strip() or "UNKNOWN",
                str(top_task_hash).strip() if isinstance(top_task_hash, str) and top_task_hash.strip() else None,
                _truncate(str(top_task_title).strip(), max_chars=200) if isinstance(top_task_title, str) and top_task_title.strip() else None,
                str(plan_id).strip() if isinstance(plan_id, str) and plan_id.strip() else None,
                str(task_id).strip() if isinstance(task_id, str) and task_id.strip() else None,
                str(llm_call_id).strip() if isinstance(llm_call_id, str) and llm_call_id.strip() else None,
                str(job_id).strip() if isinstance(job_id, str) and job_id.strip() else None,
                str(status_before).strip() if isinstance(status_before, str) and status_before.strip() else None,
                str(status_after).strip() if isinstance(status_after, str) and status_after.strip() else None,
                1 if ok else 0,
                _truncate(str(message or "").strip(), max_chars=500),
                json.dumps(payload, ensure_ascii=False) if payload is not None else None,
            ),
        )
    except Exception:
        return "UNKNOWN"
    return audit_id


@dataclass(frozen=True)
class AuditQuery:
    top_task_hash: Optional[str] = None
    plan_id: Optional[str] = None
    job_id: Optional[str] = None
    category: Optional[str] = None
    limit: int = 300


def query_audit_events(conn: sqlite3.Connection, q: AuditQuery) -> List[Dict[str, Any]]:
    where: List[str] = []
    params: List[Any] = []
    if q.top_task_hash:
        where.append("top_task_hash = ?")
        params.append(str(q.top_task_hash))
    if q.plan_id:
        where.append("plan_id = ?")
        params.append(str(q.plan_id))
    if q.job_id:
        where.append("job_id = ?")
        params.append(str(q.job_id))
    if q.category:
        where.append("category = ?")
        params.append(str(q.category))

    sql = """
    SELECT
      audit_id, created_at,
      category, action,
      top_task_hash, top_task_title,
      plan_id, task_id, llm_call_id, job_id,
      status_before, status_after,
      ok, message, payload_json
    FROM audit_events
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC LIMIT ?"
    limit = max(1, min(int(q.limit), 2000))
    rows = conn.execute(sql, (*params, limit)).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        obj = dict(r)
        # Keep payload_json as text for UI; don't force-parse.
        out.append(obj)
    return out


def query_top_tasks(conn: sqlite3.Connection, *, limit: int = 50) -> List[Dict[str, Any]]:
    limit = max(1, min(int(limit), 200))
    rows = conn.execute(
        """
        SELECT top_task_hash, top_task_title, MAX(created_at) AS last_seen
        FROM audit_events
        WHERE top_task_hash IS NOT NULL AND top_task_hash != ''
        GROUP BY top_task_hash, top_task_title
        ORDER BY last_seen DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]

