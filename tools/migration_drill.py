from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import config
from core.db import apply_migrations, connect
from core.runtime_config import load_runtime_config


@dataclass(frozen=True)
class DoctorFinding:
    code: str
    message: str


def _list_tables(conn: sqlite3.Connection) -> List[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    return [str(r[0]) for r in rows]


def _table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [str(r[1]) for r in rows]


def _latest_migration_filename(migrations_dir: Path) -> str:
    files = sorted(p.name for p in migrations_dir.iterdir() if p.is_file() and p.suffix.lower() == ".sql")
    return files[-1] if files else ""


def create_fresh_db(db_path: Path, *, migrations_dir: Path = config.MIGRATIONS_DIR, overwrite: bool = False) -> None:
    db_path = Path(db_path)
    if db_path.exists():
        if not overwrite:
            raise RuntimeError(f"DB already exists: {db_path} (pass overwrite=True to replace)")
        db_path.unlink()
    conn = connect(db_path)
    try:
        apply_migrations(conn, migrations_dir)
    finally:
        conn.close()


def upgrade_db(db_path: Path, *, migrations_dir: Path = config.MIGRATIONS_DIR) -> None:
    db_path = Path(db_path)
    if not db_path.exists():
        raise RuntimeError(f"DB does not exist: {db_path}")
    conn = connect(db_path)
    try:
        apply_migrations(conn, migrations_dir)
    finally:
        conn.close()


def doctor_db(db_path: Path, *, migrations_dir: Path = config.MIGRATIONS_DIR) -> Tuple[bool, List[DoctorFinding]]:
    """
    Minimal DB self-check (P0.3):
    - Required core tables exist
    - A few critical columns exist
    - Latest migration applied
    """
    findings: List[DoctorFinding] = []
    db_path = Path(db_path)
    if not db_path.exists():
        return False, [DoctorFinding(code="DB_MISSING", message=f"DB not found: {db_path}")]

    conn = connect(db_path)
    try:
        tables = set(_list_tables(conn))
        required_tables = {"schema_migrations", "plans", "task_nodes", "task_edges", "llm_calls"}
        for t in sorted(required_tables):
            if t not in tables:
                findings.append(DoctorFinding(code="TABLE_MISSING", message=f"missing table: {t}"))

        # Only check columns if the table exists.
        if "task_nodes" in tables:
            cols = set(_table_columns(conn, "task_nodes"))
            for c in ("task_id", "plan_id", "node_type", "status", "tags_json"):
                if c not in cols:
                    findings.append(DoctorFinding(code="COLUMN_MISSING", message=f"task_nodes missing column: {c}"))

        if "llm_calls" in tables:
            cols = set(_table_columns(conn, "llm_calls"))
            for c in ("llm_call_id", "created_at", "agent", "scope", "prompt_text", "response_text"):
                if c not in cols:
                    findings.append(DoctorFinding(code="COLUMN_MISSING", message=f"llm_calls missing column: {c}"))

        # Latest migration.
        latest = _latest_migration_filename(migrations_dir)
        if latest:
            row = conn.execute("SELECT 1 FROM schema_migrations WHERE filename = ?", (latest,)).fetchone()
            if not row:
                findings.append(DoctorFinding(code="MIGRATION_NOT_APPLIED", message=f"latest migration not applied: {latest}"))

    finally:
        conn.close()

    ok = len(findings) == 0
    return ok, findings


def check_workflow_mode_rollbackable(runtime_config_path: Path = config.RUNTIME_CONFIG_PATH) -> Tuple[bool, str]:
    """
    Minimal P0.3 check: workflow_mode can be parsed and toggled without crashing.

    Real behavioral differences are implemented later; this guard is about safety.
    """
    cfg = load_runtime_config(runtime_config_path)
    if cfg.workflow_mode not in {"v1", "v2"}:
        return False, f"invalid workflow_mode: {cfg.workflow_mode}"
    return True, f"workflow_mode={cfg.workflow_mode}"


def _render_findings(findings: Sequence[DoctorFinding]) -> str:
    if not findings:
        return "OK"
    lines = []
    for f in findings:
        lines.append(f"- {f.code}: {f.message}")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description="DB migration drill + doctor (P0.3)")
    p.add_argument("--db", type=str, default=str(config.DB_PATH_DEFAULT), help="sqlite DB path")
    p.add_argument("--fresh", action="store_true", help="create a fresh DB and apply all migrations")
    p.add_argument("--upgrade", action="store_true", help="apply migrations to an existing DB")
    p.add_argument("--overwrite", action="store_true", help="overwrite db when used with --fresh")
    p.add_argument("--doctor", action="store_true", help="run minimal DB self-check")
    p.add_argument("--check-workflow-mode", action="store_true", help="validate workflow_mode parsing")
    p.add_argument("--json", action="store_true", help="emit JSON report")
    args = p.parse_args(list(argv) if argv is not None else None)

    db_path = Path(args.db)
    report: Dict[str, object] = {"db": str(db_path)}

    try:
        if args.fresh:
            create_fresh_db(db_path, overwrite=bool(args.overwrite))
            report["fresh"] = True
        if args.upgrade:
            upgrade_db(db_path)
            report["upgrade"] = True
        if args.doctor:
            ok, findings = doctor_db(db_path)
            report["doctor_ok"] = ok
            report["doctor_findings"] = [f.__dict__ for f in findings]
        if args.check_workflow_mode:
            ok, msg = check_workflow_mode_rollbackable()
            report["workflow_mode_ok"] = ok
            report["workflow_mode"] = msg

        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            if args.doctor:
                findings = [DoctorFinding(**f) for f in report.get("doctor_findings", [])]  # type: ignore[arg-type]
                print(_render_findings(findings))
            if args.check_workflow_mode:
                print(str(report.get("workflow_mode") or ""))
        return 0
    except Exception as exc:  # noqa: BLE001
        if args.json:
            print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        else:
            print(f"ERROR: {type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

