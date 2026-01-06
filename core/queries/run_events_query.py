from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, Optional


def _parse_payload(payload_json: Any) -> Dict[str, Any]:
    if not payload_json:
        return {}
    try:
        return json.loads(payload_json)
    except Exception:
        return {}


def get_last_run_job_finished(conn: sqlite3.Connection, *, plan_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Return the last RUN/JOB_FINISHED event as a small dict for UI status display.

    Shape (best-effort):
      {created_at, job_id, severity, message, ok?, reason?, llm_calls?}
    """
    conn.row_factory = sqlite3.Row
    if plan_id:
        row = conn.execute(
            """
            SELECT created_at, job_id, severity, message, payload_json
            FROM workflow_events
            WHERE workflow='RUN' AND event_type='JOB_FINISHED' AND plan_id=?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (str(plan_id),),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT created_at, job_id, severity, message, payload_json
            FROM workflow_events
            WHERE workflow='RUN' AND event_type='JOB_FINISHED'
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
    if not row:
        return None
    payload = _parse_payload(row["payload_json"])
    return {
        "created_at": row["created_at"],
        "job_id": row["job_id"],
        "severity": row["severity"],
        "message": row["message"],
        "ok": payload.get("ok"),
        "reason": payload.get("reason") or payload.get("why"),
        "llm_calls": payload.get("llm_calls"),
    }


def get_last_run_guardrail_hit(conn: sqlite3.Connection, *, plan_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Return the last RUN/GUARDRAIL_HIT event as a small dict for UI status display.

    Shape (best-effort):
      {created_at, job_id, severity, message, guardrail?, limit?, llm_calls?}
    """
    conn.row_factory = sqlite3.Row
    if plan_id:
        row = conn.execute(
            """
            SELECT created_at, job_id, severity, message, payload_json
            FROM workflow_events
            WHERE workflow='RUN' AND event_type='GUARDRAIL_HIT' AND plan_id=?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (str(plan_id),),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT created_at, job_id, severity, message, payload_json
            FROM workflow_events
            WHERE workflow='RUN' AND event_type='GUARDRAIL_HIT'
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
    if not row:
        return None
    payload = _parse_payload(row["payload_json"])
    return {
        "created_at": row["created_at"],
        "job_id": row["job_id"],
        "severity": row["severity"],
        "message": row["message"],
        "guardrail": payload.get("guardrail"),
        "limit": payload.get("limit"),
        "llm_calls": payload.get("llm_calls"),
    }

