from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import config
from core.db import apply_migrations, connect
from core.runtime_config import get_runtime_config, reset_runtime_config_cache
from core.util import ensure_dir, utc_now_iso
from tools.install_fixtures import CASES_DIR, list_cases, load_case, install_case


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    top_task: str
    expected_outcome: str
    exit_code_create_plan: int
    exit_code_doctor: Optional[int]
    exit_code_run: Optional[int]
    exit_code_snapshot: Optional[int]
    exit_code_export: Optional[int]
    plan_id: Optional[str]
    final_entrypoint: Optional[str]
    reasons: List[Dict[str, Any]]
    stdout_tail: str
    stderr_tail: str


def _python_exe() -> str:
    reset_runtime_config_cache()
    cfg = get_runtime_config()
    if cfg.python_executable and cfg.python_executable.strip():
        return cfg.python_executable.strip()
    return sys.executable


def _run(cmd: List[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=str(config.ROOT_DIR), capture_output=True, text=True, encoding="utf-8", errors="replace")


def _tail(s: str, n: int = 2000) -> str:
    if not s:
        return ""
    return s[-n:]


_PLAN_ID_RE = re.compile(r"plan_id:\\s*([0-9a-fA-F-]{36})")


def _find_plan_id_from_output(stdout: str, stderr: str) -> Optional[str]:
    m = _PLAN_ID_RE.search(stdout or "")
    if m:
        return m.group(1)
    m = _PLAN_ID_RE.search(stderr or "")
    if m:
        return m.group(1)
    return None


def _find_latest_plan_id_by_title(db_path: Path, *, title: str) -> Optional[str]:
    if not db_path.exists():
        return None
    conn = connect(db_path)
    try:
        apply_migrations(conn, config.MIGRATIONS_DIR)
        row = conn.execute(
            "SELECT plan_id FROM plans WHERE title = ? ORDER BY created_at DESC LIMIT 1",
            (title,),
        ).fetchone()
        return str(row["plan_id"]) if row else None
    finally:
        conn.close()


def run_case(case_id: str, *, max_attempts: int) -> CaseResult:
    case = load_case(case_id, cases_dir=CASES_DIR)
    install_case(case_id, dest_dir=config.BASELINE_INPUTS_DIR, cases_dir=CASES_DIR)

    py = _python_exe()
    cfg = get_runtime_config()

    # create-plan
    p_create = _run(
        [
            py,
            "agent_cli.py",
            "--db",
            str(config.DB_PATH_DEFAULT),
            "create-plan",
            "--top-task",
            case.top_task,
            "--max-attempts",
            str(int(max_attempts)),
        ]
    )
    plan_id = _find_plan_id_from_output(p_create.stdout, p_create.stderr) or _find_latest_plan_id_by_title(config.DB_PATH_DEFAULT, title=case.top_task)

    exit_doctor = None
    exit_run = None
    exit_snapshot = None
    exit_export = None
    final_entry = None
    reasons: List[Dict[str, Any]] = []

    if plan_id:
        p_doctor = _run([py, "agent_cli.py", "--db", str(config.DB_PATH_DEFAULT), "doctor", "--plan-id", plan_id])
        exit_doctor = p_doctor.returncode

        p_run = _run(
            [
                py,
                "agent_cli.py",
                "--db",
                str(config.DB_PATH_DEFAULT),
                "run",
                "--max-iterations",
                str(int(min(int(cfg.guardrails.max_run_iterations), 10_000))),
            ]
        )
        exit_run = p_run.returncode

        p_snap = _run([py, "agent_cli.py", "--db", str(config.DB_PATH_DEFAULT), "snapshot", "--plan-id", plan_id])
        exit_snapshot = p_snap.returncode

        # Try to read reasons/final_entrypoint from snapshot JSON in default folder (best-effort).
        snap_dir = config.WORKSPACE_DIR / "observability" / plan_id
        if snap_dir.exists():
            latest = sorted([p for p in snap_dir.glob("snapshot_*.json")], key=lambda p: p.stat().st_mtime, reverse=True)
            if latest:
                try:
                    obj = json.loads(latest[0].read_text(encoding="utf-8"))
                    if isinstance(obj, dict):
                        reasons = obj.get("reasons") or []
                        final_obj = obj.get("final_deliverable") or {}
                        if isinstance(final_obj, dict):
                            final_entry = final_obj.get("final_entrypoint")
                except Exception:
                    pass

        # export (best-effort; may fail if not DONE/approved)
        p_export = _run([py, "agent_cli.py", "--db", str(config.DB_PATH_DEFAULT), "export", "--plan-id", plan_id])
        exit_export = p_export.returncode

    return CaseResult(
        case_id=case.case_id,
        top_task=case.top_task,
        expected_outcome=case.expected_outcome,
        exit_code_create_plan=p_create.returncode,
        exit_code_doctor=exit_doctor,
        exit_code_run=exit_run,
        exit_code_snapshot=exit_snapshot,
        exit_code_export=exit_export,
        plan_id=plan_id,
        final_entrypoint=str(final_entry) if final_entry else None,
        reasons=[r for r in reasons if isinstance(r, dict)],
        stdout_tail=_tail(p_create.stdout),
        stderr_tail=_tail(p_create.stderr),
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Run S/M/L fixture regression (stable reproduction; does not require success).")
    p.add_argument("--case", dest="case_id", type=str, default=None, help="Case ID to run (e.g., S_2048)")
    p.add_argument("--all", action="store_true", help="Run all cases")
    p.add_argument("--max-attempts", type=int, default=3, help="create-plan max-attempts")
    args = p.parse_args(list(argv) if argv is not None else None)

    if not args.case_id and not args.all:
        p.print_help()
        return 2

    cases = [args.case_id] if args.case_id else list_cases(cases_dir=CASES_DIR)
    results: List[CaseResult] = []
    for cid in cases:
        results.append(run_case(cid, max_attempts=int(args.max_attempts)))

    out_dir = config.WORKSPACE_DIR / "regression"
    ensure_dir(out_dir)
    ts = utc_now_iso().replace(":", "").replace("-", "")
    out_path = out_dir / f"regression_{ts}.json"
    out_path.write_text(json.dumps({"ts": utc_now_iso(), "results": [asdict(r) for r in results]}, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"saved: {out_path}")
    for r in results:
        ok = "OK" if r.exit_code_create_plan == 0 else "FAIL"
        print(f"- {r.case_id}: {ok} plan_id={r.plan_id or '-'} export={r.exit_code_export}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

