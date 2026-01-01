from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

import config
from core.status_rules import StatusRuleError, validate_status_for_node_type
from core.v2_models import (
    V2ModelError,
    parse_acceptance_criteria_json,
    parse_deliverable_spec_json,
    validate_acceptance_criteria,
    validate_deliverable_spec,
)


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


def _row_get(row: sqlite3.Row, key: str) -> object:
    try:
        return row[key]
    except Exception:
        return None


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
    # Include v2 fields if present; fallback to minimal columns for older DBs.
    try:
        rows = conn.execute(
            """
            SELECT
              task_id, title, node_type, status,
              estimated_person_days, deliverable_spec_json, acceptance_criteria_json,
              review_target_task_id, review_output_spec_json
            FROM task_nodes
            WHERE plan_id=?
            """,
            (pid,),
        ).fetchall()
    except sqlite3.OperationalError:
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
        else:
            # v2 minimal requirements (P1.1/P1.2/P1.3).
            action_rows = [r for r in rows if str(r["node_type"] or "") == "ACTION"]
            check_rows = [r for r in rows if str(r["node_type"] or "") == "CHECK"]

            # Validate ACTION required fields.
            for r in action_rows:
                tid = str(r["task_id"])
                title = str(r["title"] or "")
                epd = _row_get(r, "estimated_person_days")
                if epd is None:
                    findings.append(
                        DoctorFinding(
                            code="V2_ACTION_MISSING_FIELD",
                            message="ACTION missing estimated_person_days",
                            hint="Re-run create-plan (v2) so each ACTION includes an estimated person-days value (or set workflow_mode=v1).",
                            task_id=tid,
                            task_title=title,
                            json_path="$.task_nodes[task_id=<id>].estimated_person_days",
                        )
                    )
                else:
                    try:
                        if float(epd) <= 0:
                            raise ValueError("must be > 0")
                    except Exception:
                        findings.append(
                            DoctorFinding(
                                code="V2_ACTION_BAD_FIELD",
                                message=f"estimated_person_days invalid: {epd!r}",
                                hint="estimated_person_days must be a positive number (or set workflow_mode=v1).",
                                task_id=tid,
                                task_title=title,
                                json_path="$.task_nodes[task_id=<id>].estimated_person_days",
                            )
                        )

                ds_text = _row_get(r, "deliverable_spec_json")
                if ds_text is None or not str(ds_text).strip():
                    findings.append(
                        DoctorFinding(
                            code="V2_ACTION_MISSING_FIELD",
                            message="ACTION missing deliverable_spec_json",
                            hint="Re-run create-plan (v2) so each ACTION declares deliverable_spec (or set workflow_mode=v1).",
                            task_id=tid,
                            task_title=title,
                            json_path="$.task_nodes[task_id=<id>].deliverable_spec_json",
                        )
                    )
                else:
                    try:
                        ds = parse_deliverable_spec_json(str(ds_text))
                        ok2, reason2, path2 = validate_deliverable_spec(ds)
                        if not ok2:
                            findings.append(
                                DoctorFinding(
                                    code="V2_ACTION_BAD_FIELD",
                                    message=f"deliverable_spec invalid: {reason2}",
                                    hint="deliverable_spec must include format/filename/single_file/bundle_mode/description (or set workflow_mode=v1).",
                                    task_id=tid,
                                    task_title=title,
                                    json_path=f"$.task_nodes[task_id=<id>].deliverable_spec_json{path2[1:] if path2.startswith('$') else ''}",
                                )
                            )
                    except V2ModelError as exc:
                        findings.append(
                            DoctorFinding(
                                code="V2_ACTION_BAD_FIELD",
                                message=f"deliverable_spec_json parse failed: {exc}",
                                hint="deliverable_spec_json must be valid JSON object (or set workflow_mode=v1).",
                                task_id=tid,
                                task_title=title,
                                json_path="$.task_nodes[task_id=<id>].deliverable_spec_json",
                            )
                        )

                ac_text = _row_get(r, "acceptance_criteria_json")
                if ac_text is None or not str(ac_text).strip():
                    findings.append(
                        DoctorFinding(
                            code="V2_ACTION_MISSING_FIELD",
                            message="ACTION missing acceptance_criteria_json",
                            hint="Re-run create-plan (v2) so each ACTION includes acceptance_criteria list (or set workflow_mode=v1).",
                            task_id=tid,
                            task_title=title,
                            json_path="$.task_nodes[task_id=<id>].acceptance_criteria_json",
                        )
                    )
                else:
                    try:
                        ac = parse_acceptance_criteria_json(str(ac_text))
                        ok2, reason2, path2 = validate_acceptance_criteria(ac)
                        if not ok2:
                            findings.append(
                                DoctorFinding(
                                    code="V2_ACTION_BAD_FIELD",
                                    message=f"acceptance_criteria invalid: {reason2}",
                                    hint="acceptance_criteria must be a non-empty array of objects with id/type/statement/check_method/severity (or set workflow_mode=v1).",
                                    task_id=tid,
                                    task_title=title,
                                    json_path=f"$.task_nodes[task_id=<id>].acceptance_criteria_json{path2[1:] if path2.startswith('$') else ''}",
                                )
                            )
                    except V2ModelError as exc:
                        findings.append(
                            DoctorFinding(
                                code="V2_ACTION_BAD_FIELD",
                                message=f"acceptance_criteria_json parse failed: {exc}",
                                hint="acceptance_criteria_json must be valid JSON array (or set workflow_mode=v1).",
                                task_id=tid,
                                task_title=title,
                                json_path="$.task_nodes[task_id=<id>].acceptance_criteria_json",
                            )
                        )

            # Validate CHECK binding (review_target_task_id).
            action_ids = {str(r["task_id"]) for r in action_rows}
            target_counts: dict[str, int] = {}
            for r in check_rows:
                tid = str(r["task_id"])
                title = str(r["title"] or "")
                status = str(r["status"] or "")
                if status == "ABANDONED":
                    continue
                target = _row_get(r, "review_target_task_id")
                target_s = str(target or "").strip()
                if not target_s:
                    findings.append(
                        DoctorFinding(
                            code="V2_CHECK_MISSING_FIELD",
                            message="CHECK missing review_target_task_id",
                            hint="Re-run create-plan (v2) so each CHECK is bound to exactly one ACTION (or set workflow_mode=v1).",
                            task_id=tid,
                            task_title=title,
                            json_path="$.task_nodes[task_id=<id>].review_target_task_id",
                        )
                    )
                    continue
                if target_s not in action_ids:
                    findings.append(
                        DoctorFinding(
                            code="V2_CHECK_BAD_TARGET",
                            message=f"CHECK review_target_task_id not found among ACTION nodes: {target_s}",
                            hint="Regenerate the plan or fix review_target_task_id to reference an ACTION task_id (or set workflow_mode=v1).",
                            task_id=tid,
                            task_title=title,
                            json_path="$.task_nodes[task_id=<id>].review_target_task_id",
                        )
                    )
                target_counts[target_s] = target_counts.get(target_s, 0) + 1

            # Enforce 1:1 (each ACTION exactly one CHECK).
            for aid in action_ids:
                cnt = int(target_counts.get(aid, 0))
                if cnt == 0:
                    ar = next((r for r in action_rows if str(r["task_id"]) == aid), None)
                    findings.append(
                        DoctorFinding(
                            code="V2_ACTION_MISSING_CHECK",
                            message="ACTION has no CHECK bound via review_target_task_id",
                            hint="Re-run create-plan (v2) to auto-generate a 1:1 CHECK for each ACTION (or set workflow_mode=v1).",
                            task_id=aid,
                            task_title=str(ar["title"] or "") if ar is not None else None,
                            json_path="$.task_nodes[task_id=<id>]",
                        )
                    )
                elif cnt > 1:
                    ar = next((r for r in action_rows if str(r["task_id"]) == aid), None)
                    findings.append(
                        DoctorFinding(
                            code="V2_ACTION_MULTI_CHECK",
                            message=f"ACTION is bound by multiple CHECK nodes: {cnt}",
                            hint="Ensure exactly one CHECK points to each ACTION via review_target_task_id (or set workflow_mode=v1).",
                            task_id=aid,
                            task_title=str(ar["title"] or "") if ar is not None else None,
                            json_path="$.task_nodes[task_id=<id>]",
                        )
                    )

            # v2 consistency checks:
            # - DONE ACTION must have approved_artifact_id and it must exist in artifacts.
            # - DONE CHECK must have at least one review row with reviewed_artifact_id present.
            # - Warn if duplicate reviews exist for the same (check_task_id, reviewed_artifact_id).
            for r in action_rows:
                tid = str(r["task_id"])
                title = str(r["title"] or "")
                status = str(r["status"] or "")
                if status != "DONE":
                    continue
                try:
                    approved = conn.execute("SELECT approved_artifact_id FROM task_nodes WHERE task_id = ?", (tid,)).fetchone()
                except sqlite3.OperationalError:
                    approved = None
                approved_id = str((approved["approved_artifact_id"] if approved else "") or "").strip()
                if not approved_id:
                    findings.append(
                        DoctorFinding(
                            code="V2_ACTION_DONE_NO_APPROVED",
                            message="DONE ACTION missing approved_artifact_id",
                            hint="Re-run CHECK review to approve a candidate artifact, or set workflow_mode=v1.",
                            task_id=tid,
                            task_title=title,
                            json_path="$.task_nodes[task_id=<id>].approved_artifact_id",
                        )
                    )
                    continue
                art = conn.execute("SELECT artifact_id FROM artifacts WHERE artifact_id = ?", (approved_id,)).fetchone()
                if not art:
                    findings.append(
                        DoctorFinding(
                            code="V2_ACTION_DONE_BAD_APPROVED",
                            message=f"approved_artifact_id not found in artifacts: {approved_id}",
                            hint="DB points to a missing artifact; regenerate and re-approve, or fix the pointer.",
                            task_id=tid,
                            task_title=title,
                            json_path="$.task_nodes[task_id=<id>].approved_artifact_id",
                        )
                    )

            for r in check_rows:
                tid = str(r["task_id"])
                title = str(r["title"] or "")
                status = str(r["status"] or "")
                if status != "DONE":
                    continue
                latest = conn.execute(
                    "SELECT reviewed_artifact_id, verdict FROM reviews WHERE task_id = ? ORDER BY created_at DESC LIMIT 1",
                    (tid,),
                ).fetchone()
                if not latest:
                    findings.append(
                        DoctorFinding(
                            code="V2_CHECK_DONE_NO_REVIEW",
                            message="DONE CHECK has no corresponding review record",
                            hint="Re-run the CHECK review to create a review record, or set workflow_mode=v1.",
                            task_id=tid,
                            task_title=title,
                            json_path="$.reviews[task_id=<check>]",
                        )
                    )
                    continue
                reviewed = str((latest["reviewed_artifact_id"] or "") if "reviewed_artifact_id" in latest.keys() else "").strip()
                if not reviewed:
                    findings.append(
                        DoctorFinding(
                            code="V2_CHECK_DONE_BAD_REVIEW",
                            message="Latest review for DONE CHECK is missing reviewed_artifact_id",
                            hint="Review traceability fields are missing; run migrations and re-run review.",
                            task_id=tid,
                            task_title=title,
                            json_path="$.reviews[task_id=<check>].reviewed_artifact_id",
                        )
                    )
                    continue
                dup = conn.execute(
                    """
                    SELECT COUNT(1) AS cnt
                    FROM reviews
                    WHERE task_id = ? AND reviewed_artifact_id = ?
                    """,
                    (tid, reviewed),
                ).fetchone()
                if dup and int(dup["cnt"]) > 1:
                    findings.append(
                        DoctorFinding(
                            code="V2_REVIEW_DUPLICATE",
                            message=f"Duplicate reviews detected for same CHECK+artifact (count={int(dup['cnt'])})",
                            hint="Prefer the latest review and consider cleaning old duplicates; enable idempotency_key enforcement.",
                            task_id=tid,
                            task_title=title,
                            json_path="$.reviews[task_id=<check>]",
                        )
                    )

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
