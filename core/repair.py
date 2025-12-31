from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Optional

from core.util import utc_now_iso


def repair_missing_root_tasks(conn: sqlite3.Connection, *, plan_id: Optional[str] = None) -> int:
    """
    For plans whose root_task_id doesn't exist in task_nodes, insert a minimal GOAL node.
    Returns number of inserted rows.
    """
    params: list[object] = []
    where = ""
    if plan_id:
        where = "WHERE p.plan_id = ?"
        params.append(plan_id)

    rows = conn.execute(
        f"""
        SELECT p.plan_id, p.title, p.owner_agent_id, p.root_task_id
        FROM plans p
        {where}
        ORDER BY p.created_at ASC
        """,
        tuple(params),
    ).fetchall()

    inserted = 0
    for r in rows:
        root_id = r["root_task_id"]
        exists = conn.execute("SELECT 1 FROM task_nodes WHERE task_id=?", (root_id,)).fetchone()
        if exists:
            continue
        conn.execute(
            """
            INSERT INTO task_nodes(
              task_id, plan_id, node_type, title,
              goal_statement, rationale, owner_agent_id,
              priority, status, blocked_reason,
              attempt_count, confidence, active_branch, active_artifact_id,
              created_at, updated_at, tags_json
            )
            VALUES(?, ?, 'GOAL', ?, NULL, ?, ?, 0, 'PENDING', NULL, 0, 0.5, 1, NULL, ?, ?, ?)
            """,
            (
                root_id,
                r["plan_id"],
                r["title"] or "Root Task",
                "DB_REPAIR (missing root_task_id referenced by plan)",
                r["owner_agent_id"] or "xiaobo",
                utc_now_iso(),
                utc_now_iso(),
                json.dumps(["autofix", "repaired"], ensure_ascii=False),
            ),
        )
        inserted += 1
    return inserted


def repair_missing_decompose_edges(conn: sqlite3.Connection, *, plan_id: Optional[str] = None) -> int:
    """
    If a plan has >1 node but has no DECOMPOSE edges, create a minimal DECOMPOSE tree: root -> all other nodes.
    Returns number of inserted edges.
    """
    params: list[object] = []
    where = ""
    if plan_id:
        where = "WHERE plan_id = ?"
        params.append(plan_id)

    plan_rows = conn.execute(
        f"SELECT plan_id, root_task_id FROM plans {where} ORDER BY created_at ASC",
        tuple(params),
    ).fetchall()

    inserted = 0
    for p in plan_rows:
        pid = p["plan_id"]
        root = p["root_task_id"]
        node_cnt = conn.execute("SELECT COUNT(1) FROM task_nodes WHERE plan_id=?", (pid,)).fetchone()[0]
        decompose_cnt = conn.execute("SELECT COUNT(1) FROM task_edges WHERE plan_id=? AND edge_type='DECOMPOSE'", (pid,)).fetchone()[0]
        if int(node_cnt) <= 1 or int(decompose_cnt) != 0:
            continue

        node_rows = conn.execute("SELECT task_id FROM task_nodes WHERE plan_id=? AND task_id != ?", (pid, root)).fetchall()
        now = utc_now_iso()
        for n in node_rows:
            exists = conn.execute(
                "SELECT 1 FROM task_edges WHERE plan_id=? AND from_task_id=? AND to_task_id=? AND edge_type='DECOMPOSE'",
                (pid, root, n["task_id"]),
            ).fetchone()
            if exists:
                continue
            conn.execute(
                """
                INSERT INTO task_edges(edge_id, plan_id, from_task_id, to_task_id, edge_type, metadata_json, created_at)
                VALUES(?, ?, ?, ?, 'DECOMPOSE', ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    pid,
                    root,
                    n["task_id"],
                    json.dumps({"and_or": "AND"}, ensure_ascii=False),
                    now,
                ),
            )
            inserted += 1
    return inserted
