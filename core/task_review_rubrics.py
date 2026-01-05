from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, Optional

from core.util import utc_now_iso


def get_task_review_rubric(conn: sqlite3.Connection, *, task_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        """
        SELECT rubric_json
        FROM task_review_rubrics
        WHERE task_id = ?
        """,
        (str(task_id),),
    ).fetchone()
    if not row or not row["rubric_json"]:
        return None
    try:
        obj = json.loads(row["rubric_json"])
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def upsert_task_review_rubric(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    plan_id: Optional[str],
    schema_version: str,
    pass_score: int,
    rubric: Dict[str, Any],
) -> None:
    now = utc_now_iso()
    conn.execute(
        """
        INSERT INTO task_review_rubrics(task_id, created_at, updated_at, plan_id, schema_version, pass_score, rubric_json)
        VALUES(?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(task_id) DO UPDATE SET
          updated_at = excluded.updated_at,
          plan_id = excluded.plan_id,
          schema_version = excluded.schema_version,
          pass_score = excluded.pass_score,
          rubric_json = excluded.rubric_json
        """,
        (
            str(task_id),
            now,
            now,
            str(plan_id) if plan_id else None,
            str(schema_version),
            int(pass_score),
            json.dumps(rubric, ensure_ascii=False),
        ),
    )

