from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

import config
from core.status_rules import StatusRuleError, validate_status_for_node_type


@dataclass(frozen=True)
class DoctorFinding:
    code: str
    message: str
    hint: str = ""
    task_id: Optional[str] = None
    task_title: Optional[str] = None
    json_path: Optional[str] = None

    def to_dict(self) -> dict:
        out = {"code": self.code, "message": self.message}
        if self.hint:
            out["hint"] = self.hint
        if self.task_id:
            out["task_id"] = self.task_id
        if self.task_title:
            out["task_title"] = self.task_title
        if self.json_path:
            out["json_path"] = self.json_path
        return out


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return bool(row)


def _table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.OperationalError:
        return []
    return [str(r[1]) for r in rows]


def _latest_migration_filename(migrations_dir: Path) -> str:
    files = sorted(p.name for p in migrations_dir.iterdir() if p.is_file() and p.suffix.lower() == ".sql")
    return files[-1] if files else ""


def doctor_db(conn: sqlite3.Connection, *, migrations_dir: Path = config.MIGRATIONS_DIR) -> Tuple[bool, List[DoctorFinding]]:
    findings: List[DoctorFinding] = []

    fk = conn.execute("PRAGMA foreign_keys").fetchone()
    if not fk or int(fk[0]) != 1:
        findings.append(
            DoctorFinding(
                code="DB_FOREIGN_KEYS_OFF",
                message="PRAGMA foreign_keys is OFF (expected ON)",
                hint="Reopen DB using core.db.connect() (it enables PRAGMA foreign_keys) or enable it manually.",
            )
        )

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
            findings.append(DoctorFinding(code="DB_MISSING_TABLE", message=f"missing table: {t}", hint="Run migrations: agent_cli.py doctor / tools/migration_drill.py --fresh|--upgrade"))

    # Latest migration applied (best-effort).
    latest = _latest_migration_filename(migrations_dir)
    if latest and _table_exists(conn, "schema_migrations"):
        row = conn.execute("SELECT 1 FROM schema_migrations WHERE filename = ?", (latest,)).fetchone()
        if not row:
            findings.append(
                DoctorFinding(
                    code="DB_MIGRATION_NOT_APPLIED",
                    message=f"latest migration not applied: {latest}",
                    hint="Run: tools/migration_drill.py --upgrade (or apply_migrations at startup).",
                )
            )

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
            findings.append(
                DoctorFinding(
                    code="DB_ORPHAN_TASK_NODES",
                    message=f"task_nodes.plan_id not found in plans: {int(bad)} row(s)",
                    hint="Run agent_cli.py repair-db, or reset-db if you want to restart clean.",
                )
            )

        bad = conn.execute(
            """
            SELECT COUNT(1)
            FROM plans p
            LEFT JOIN task_nodes n ON n.task_id = p.root_task_id
            WHERE n.task_id IS NULL
            """
        ).fetchone()[0]
        if int(bad) > 0:
            findings.append(
                DoctorFinding(
                    code="DB_BAD_ROOT_TASK",
                    message=f"plans.root_task_id missing in task_nodes: {int(bad)} plan(s)",
                    hint="Run agent_cli.py repair-db (it can create missing root stubs).",
                )
            )

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
            findings.append(
                DoctorFinding(
                    code="DB_ORPHAN_EDGES",
                    message=f"task_edges endpoints missing in task_nodes: {int(bad)} edge(s)",
                    hint="Run agent_cli.py repair-db, or regenerate plan via create-plan.",
                )
            )

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
            findings.append(
                DoctorFinding(
                    code="DB_ORPHAN_EVENTS",
                    message=f"task_events.plan_id not found in plans: {int(bad)} event(s)",
                    hint="This typically indicates a partially-reset DB; consider reset-db.",
                )
            )

    ok = len(findings) == 0
    return ok, findings


def doctor_plan(
    conn: sqlite3.Connection,
    *,
    plan_id: str,
    workflow_mode: str,
) -> Tuple[bool, List[DoctorFinding]]:
    findings: List[DoctorFinding] = []
    pid = str(plan_id or "").strip()
    if not pid:
        return False, [DoctorFinding(code="PLAN_ID_MISSING", message="plan_id is required", hint="Pass --plan-id or ensure tasks/plan.json is loaded into DB.")]

    if not _table_exists(conn, "plans") or not _table_exists(conn, "task_nodes"):
        return False, [DoctorFinding(code="DB_NOT_READY", message="plans/task_nodes tables missing", hint="Run migrations first.")]

    prow = conn.execute("SELECT plan_id, root_task_id, title FROM plans WHERE plan_id=?", (pid,)).fetchone()
    if not prow:
        return False, [DoctorFinding(code="PLAN_NOT_FOUND", message=f"plan_id not found in DB: {pid}", hint="Run create-plan first, or specify the correct --plan-id.")]

    root_task_id = str(prow["root_task_id"] or "")
    root = conn.execute("SELECT task_id, node_type, title, status FROM task_nodes WHERE task_id=?", (root_task_id,)).fetchone()
    if not root:
        findings.append(
            DoctorFinding(
                code="PLAN_ROOT_TASK_NOT_FOUND",
                message=f"root_task_id not found in task_nodes: {root_task_id}",
                hint="Run agent_cli.py repair-db (or recreate the plan).",
                task_id=root_task_id,
                json_path="$.plan.root_task_id",
            )
        )
    else:
        if str(root["node_type"] or "") != "GOAL":
            findings.append(
                DoctorFinding(
                    code="PLAN_ROOT_NOT_GOAL",
                    message=f"root task node_type must be GOAL (got {root['node_type']})",
                    hint="Regenerate plan with a GOAL root node.",
                    task_id=str(root["task_id"]),
                    task_title=str(root["title"] or ""),
                    json_path="$.nodes[task_id=<root>].node_type",
                )
            )

    action_cnt = conn.execute("SELECT COUNT(1) FROM task_nodes WHERE plan_id=? AND node_type='ACTION'", (pid,)).fetchone()[0]
    if int(action_cnt) <= 0:
        findings.append(
            DoctorFinding(
                code="PLAN_NO_ACTIONS",
                message="plan has no ACTION nodes",
                hint="Regenerate plan via create-plan; a runnable plan must include at least one ACTION.",
                json_path="$.nodes[*].node_type",
            )
        )

    # Status validity (P0.1)
    rows = conn.execute("SELECT task_id, title, node_type, status FROM task_nodes WHERE plan_id=?", (pid,)).fetchall()
    for r in rows:
        node_type = str(r["node_type"] or "")
        status = str(r["status"] or "")
        try:
            validate_status_for_node_type(node_type=node_type, status=status)
        except StatusRuleError as exc:
            findings.append(
                DoctorFinding(
                    code="PLAN_BAD_STATUS",
                    message=str(exc),
                    hint="Fix DB status manually or regenerate the plan; READY_TO_CHECK is only allowed for ACTION.",
                    task_id=str(r["task_id"]),
                    task_title=str(r["title"] or ""),
                    json_path="$.task_nodes[task_id=<id>].status",
                )
            )

    # Minimal plan integrity: DECOMPOSE should exist if there are multiple nodes.
    node_cnt = conn.execute("SELECT COUNT(1) FROM task_nodes WHERE plan_id=?", (pid,)).fetchone()[0]
    decompose_cnt = 0
    if _table_exists(conn, "task_edges"):
        decompose_cnt = conn.execute("SELECT COUNT(1) FROM task_edges WHERE plan_id=? AND edge_type='DECOMPOSE'", (pid,)).fetchone()[0]
    if int(node_cnt) > 1 and int(decompose_cnt) == 0:
        findings.append(
            DoctorFinding(
                code="PLAN_MISSING_DECOMPOSE",
                message=f"plan has {int(node_cnt)} nodes but 0 DECOMPOSE edges (root aggregation cannot complete)",
                hint="Run agent_cli.py repair-db to backfill DECOMPOSE edges, or regenerate the plan.",
                json_path="$.edges[*].edge_type",
            )
        )

    mode = str(workflow_mode or "v1").strip().lower()
    if mode == "v2":
        # Hard gate: v2 requires additional columns (not yet implemented in this repo).
        cols = set(_table_columns(conn, "task_nodes"))
        required_cols = {"estimated_person_days", "deliverable_spec_json", "acceptance_criteria_json", "review_target_task_id"}
        missing_cols = sorted([c for c in required_cols if c not in cols])
        if missing_cols:
            findings.append(
                DoctorFinding(
                    code="V2_NOT_READY",
                    message=f"workflow_mode=v2 requires DB columns not present: {', '.join(missing_cols)}",
                    hint="Upgrade DB migrations (when available) or set workflow_mode=v1 in runtime_config.json.",
                    json_path="$.runtime_config.workflow_mode",
                )
            )
        # Additional v2 constraints (1:1 CHECK binding, etc.) will be enforced after v2 schema exists.

    ok = len(findings) == 0
    return ok, findings


def run_doctor(conn: sqlite3.Connection, *, plan_id: Optional[str] = None, workflow_mode: str = "v1") -> List[DoctorFinding]:
    """
    Backward-compatible combined doctor used by agent_cli.py.
    """
    _, db_findings = doctor_db(conn)
    findings: List[DoctorFinding] = list(db_findings)
    if plan_id:
        _, plan_findings = doctor_plan(conn, plan_id=str(plan_id), workflow_mode=workflow_mode)
        findings.extend(plan_findings)
    return findings


def format_findings_human(findings: Sequence[DoctorFinding]) -> str:
    if not findings:
        return "OK"
    lines: List[str] = []
    for f in findings:
        head = f"- {f.code}: {f.message}"
        if f.task_title:
            head += f" (task={f.task_title})"
        lines.append(head)
        if f.hint:
            lines.append(f"  hint: {f.hint}")
        if f.task_id:
            lines.append(f"  task_id: {f.task_id}")
        if f.json_path:
            lines.append(f"  json_path: {f.json_path}")
    return "\n".join(lines)

