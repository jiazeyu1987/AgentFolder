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


def pick_v2_check_tasks(conn: sqlite3.Connection, *, plan_id: str, limit: int = 5) -> List[sqlite3.Row]:
    """
    v2 review gating:
    - CHECK nodes are runnable when they are READY and their bound ACTION is READY_TO_CHECK and has an active artifact.
    - The binding truth is task_nodes.review_target_task_id (not DEPENDS_ON edges).
    """
    return conn.execute(
        """
        SELECT
          c.task_id AS check_task_id,
          c.title AS check_title,
          c.owner_agent_id AS check_owner,
          c.status AS check_status,
          c.attempt_count AS check_attempt_count,
          c.review_target_task_id AS target_task_id,
          a.title AS target_title,
          a.status AS target_status,
          a.active_artifact_id AS reviewed_artifact_id
        FROM task_nodes c
        JOIN task_nodes a ON a.task_id = c.review_target_task_id
        WHERE c.plan_id = ?
          AND c.active_branch = 1
          AND c.node_type = 'CHECK'
          AND c.status = 'READY'
          AND c.review_target_task_id IS NOT NULL
          AND a.plan_id = c.plan_id
          AND a.active_branch = 1
          AND a.node_type = 'ACTION'
          AND a.status = 'READY_TO_CHECK'
          AND a.active_artifact_id IS NOT NULL
        ORDER BY c.priority DESC, c.attempt_count ASC
        LIMIT ?
        """,
        (plan_id, limit),
    ).fetchall()
