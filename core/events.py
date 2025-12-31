from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any, Dict, Optional

from core.util import utc_now_iso


def emit_event(
    conn: sqlite3.Connection,
    *,
    plan_id: str,
    event_type: str,
    task_id: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> str:
    event_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO task_events(event_id, plan_id, task_id, event_type, payload_json, created_at)
        VALUES(?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            plan_id,
            task_id,
            event_type,
            json.dumps(payload or {}, ensure_ascii=False),
            utc_now_iso(),
        ),
    )
    return event_id

