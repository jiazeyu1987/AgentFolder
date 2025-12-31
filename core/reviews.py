from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Dict

from core.events import emit_event
from core.util import ensure_dir, utc_now_iso


def write_review_json(base_dir: Path, *, task_id: str, review: Dict[str, Any]) -> Path:
    task_dir = base_dir / task_id
    ensure_dir(task_dir)
    ts = utc_now_iso().replace(":", "").replace("-", "")
    path = task_dir / f"review_{ts}.json"
    path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def insert_review(conn: sqlite3.Connection, *, plan_id: str, task_id: str, reviewer_agent_id: str, review: Dict[str, Any]) -> str:
    review_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO reviews(review_id, task_id, reviewer_agent_id, total_score, breakdown_json, suggestions_json, summary, action_required, created_at)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            review_id,
            task_id,
            reviewer_agent_id,
            int(review.get("total_score") or 0),
            json.dumps(review.get("breakdown") or [], ensure_ascii=False),
            json.dumps(review.get("suggestions") or [], ensure_ascii=False),
            review.get("summary") or "",
            review.get("action_required") or "MODIFY",
            utc_now_iso(),
        ),
    )
    emit_event(conn, plan_id=plan_id, task_id=task_id, event_type="REVIEW_WRITTEN", payload={"review_id": review_id, "total_score": int(review.get("total_score") or 0)})
    return review_id

