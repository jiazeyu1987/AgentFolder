from __future__ import annotations

import sqlite3
from typing import Optional

from core.util import utc_now_iso


def increment_counter(conn: sqlite3.Connection, *, plan_id: str, task_id: str, key: str) -> int:
    conn.execute(
        """
        INSERT INTO task_error_counters(plan_id, task_id, key, count, updated_at)
        VALUES(?, ?, ?, 1, ?)
        ON CONFLICT(plan_id, task_id, key) DO UPDATE SET
          count = count + 1,
          updated_at = excluded.updated_at
        """,
        (plan_id, task_id, key, utc_now_iso()),
    )
    row = conn.execute(
        "SELECT count FROM task_error_counters WHERE plan_id = ? AND task_id = ? AND key = ?",
        (plan_id, task_id, key),
    ).fetchone()
    return int(row["count"]) if row else 1


def reset_counter(conn: sqlite3.Connection, *, plan_id: str, task_id: str, key: str) -> None:
    conn.execute(
        "DELETE FROM task_error_counters WHERE plan_id = ? AND task_id = ? AND key = ?",
        (plan_id, task_id, key),
    )


def get_counter(conn: sqlite3.Connection, *, plan_id: str, task_id: str, key: str) -> int:
    row = conn.execute(
        "SELECT count FROM task_error_counters WHERE plan_id = ? AND task_id = ? AND key = ?",
        (plan_id, task_id, key),
    ).fetchone()
    return int(row["count"]) if row else 0

