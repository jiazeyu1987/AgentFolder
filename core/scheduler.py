from __future__ import annotations

import sqlite3
from typing import List


def pick_xiaobo_tasks(conn: sqlite3.Connection, *, plan_id: str, limit: int = 5) -> List[sqlite3.Row]:
    return conn.execute(
        """
        SELECT task_id, title, node_type, owner_agent_id, priority, status, attempt_count
        FROM task_nodes
        WHERE plan_id = ?
          AND active_branch = 1
          AND owner_agent_id = 'xiaobo'
          AND node_type = 'ACTION'
          AND status IN ('TO_BE_MODIFY', 'READY')
        ORDER BY
          CASE status WHEN 'TO_BE_MODIFY' THEN 0 ELSE 1 END,
          priority DESC,
          attempt_count ASC
        LIMIT ?
        """,
        (plan_id, limit),
    ).fetchall()


def pick_xiaojing_tasks(conn: sqlite3.Connection, *, plan_id: str, limit: int = 5) -> List[sqlite3.Row]:
    return conn.execute(
        """
        SELECT task_id, title, node_type, owner_agent_id, priority, status, attempt_count, active_artifact_id
        FROM task_nodes
        WHERE plan_id = ?
          AND active_branch = 1
          AND status = 'READY_TO_CHECK'
        ORDER BY priority DESC, attempt_count ASC
        LIMIT ?
        """,
        (plan_id, limit),
    ).fetchall()


def pick_xiaojing_check_nodes(conn: sqlite3.Connection, *, plan_id: str, limit: int = 5) -> List[sqlite3.Row]:
    return conn.execute(
        """
        SELECT task_id, title, node_type, owner_agent_id, priority, status, attempt_count
        FROM task_nodes
        WHERE plan_id = ?
          AND active_branch = 1
          AND node_type = 'CHECK'
          AND owner_agent_id = 'xiaojing'
          AND status = 'READY'
        ORDER BY priority DESC, attempt_count ASC
        LIMIT ?
        """,
        (plan_id, limit),
    ).fetchall()
