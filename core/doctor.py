from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class DoctorIssue:
    code: str
    message: str


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return bool(row)


def run_doctor(conn: sqlite3.Connection, *, plan_id: Optional[str] = None) -> List[DoctorIssue]:
    issues: List[DoctorIssue] = []

    # Basic SQLite safety.
    fk = conn.execute("PRAGMA foreign_keys").fetchone()
    if not fk or int(fk[0]) != 1:
        issues.append(DoctorIssue(code="DB_FOREIGN_KEYS_OFF", message="PRAGMA foreign_keys is OFF (expected ON)"))

    # Expected tables (by migrations).
    expected_tables = [
        "schema_migrations",
        "plans",
        "task_nodes",
        "task_edges",
        "input_requirements",
        "evidences",
        "artifacts",
        "approvals",
        "reviews",
        "skill_runs",
        "task_events",
        "task_error_counters",
        "prompts",
        "input_files",
        "llm_calls",
    ]
    for t in expected_tables:
        if not _table_exists(conn, t):
            issues.append(DoctorIssue(code="DB_MISSING_TABLE", message=f"missing table: {t}"))

    # Lightweight referential integrity checks (without relying on FK constraints).
    if _table_exists(conn, "task_nodes") and _table_exists(conn, "plans"):
        bad = conn.execute(
            """
            SELECT COUNT(1)
            FROM task_nodes n
            LEFT JOIN plans p ON p.plan_id = n.plan_id
            WHERE p.plan_id IS NULL
            """
        ).fetchone()[0]
        if int(bad) > 0:
            issues.append(DoctorIssue(code="DB_ORPHAN_TASK_NODES", message=f"task_nodes.plan_id not found in plans: {int(bad)} row(s)"))

        bad = conn.execute(
            """
            SELECT COUNT(1)
            FROM plans p
            LEFT JOIN task_nodes n ON n.task_id = p.root_task_id
            WHERE n.task_id IS NULL
            """
        ).fetchone()[0]
        if int(bad) > 0:
            issues.append(DoctorIssue(code="DB_BAD_ROOT_TASK", message=f"plans.root_task_id missing in task_nodes: {int(bad)} plan(s)"))

    if _table_exists(conn, "task_edges") and _table_exists(conn, "task_nodes"):
        bad = conn.execute(
            """
            SELECT COUNT(1)
            FROM task_edges e
            LEFT JOIN task_nodes a ON a.task_id = e.from_task_id
            LEFT JOIN task_nodes b ON b.task_id = e.to_task_id
            WHERE a.task_id IS NULL OR b.task_id IS NULL
            """
        ).fetchone()[0]
        if int(bad) > 0:
            issues.append(DoctorIssue(code="DB_ORPHAN_EDGES", message=f"task_edges endpoints missing in task_nodes: {int(bad)} edge(s)"))

    if _table_exists(conn, "task_events") and _table_exists(conn, "plans"):
        bad = conn.execute(
            """
            SELECT COUNT(1)
            FROM task_events e
            LEFT JOIN plans p ON p.plan_id = e.plan_id
            WHERE p.plan_id IS NULL
            """
        ).fetchone()[0]
        if int(bad) > 0:
            issues.append(DoctorIssue(code="DB_ORPHAN_EVENTS", message=f"task_events.plan_id not found in plans: {int(bad)} event(s)"))

    if plan_id and _table_exists(conn, "plans"):
        row = conn.execute("SELECT 1 FROM plans WHERE plan_id=?", (plan_id,)).fetchone()
        if not row:
            issues.append(DoctorIssue(code="PLAN_NOT_FOUND", message=f"plan_id not found in DB: {plan_id}"))
        else:
            if _table_exists(conn, "task_nodes") and _table_exists(conn, "task_edges"):
                node_cnt = conn.execute("SELECT COUNT(1) FROM task_nodes WHERE plan_id=?", (plan_id,)).fetchone()[0]
                edge_cnt = conn.execute("SELECT COUNT(1) FROM task_edges WHERE plan_id=?", (plan_id,)).fetchone()[0]
                if int(node_cnt) > 1 and int(edge_cnt) == 0:
                    issues.append(DoctorIssue(code="PLAN_MISSING_EDGES", message=f"plan has {int(node_cnt)} nodes but 0 edges (missing DECOMPOSE tree)"))

    return issues
