import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import config
from core.db import apply_migrations, connect


def _write_runtime_config_v2() -> str:
    """
    agent_cli.py doctor reads config.RUNTIME_CONFIG_PATH; update it for this test and restore later.
    """
    cfg = {
        "llm": {"provider": "llm_demo", "claude_code_bin": "claude_code", "timeout_s": 300},
        "workflow_mode": "v2",
    }
    config.RUNTIME_CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return json.dumps(cfg, ensure_ascii=False, indent=2)


def _insert_v2_ok_plan(db_path: Path, *, plan_id: str) -> None:
    conn = connect(db_path)
    try:
        apply_migrations(conn, config.MIGRATIONS_DIR)
        conn.execute(
            "INSERT INTO plans(plan_id, title, owner_agent_id, root_task_id, created_at, constraints_json) VALUES(?, 'Plan', 'xiaobo', ?, datetime('now'), '{}')",
            (plan_id, f"{plan_id}_root"),
        )
        conn.execute(
            "INSERT INTO task_nodes(task_id, plan_id, node_type, title, owner_agent_id, status, created_at, updated_at) VALUES(?, ?, 'GOAL', 'Root', 'xiaobo', 'PENDING', datetime('now'), datetime('now'))",
            (f"{plan_id}_root", plan_id),
        )
        deliverable = {"format": "html", "filename": "index.html", "single_file": True, "bundle_mode": "MANIFEST", "description": "deliver"}
        acceptance = [{"id": "a1", "type": "manual", "statement": "works", "check_method": "manual_review", "severity": "MED"}]
        conn.execute(
            """
            INSERT INTO task_nodes(
              task_id, plan_id, node_type, title, owner_agent_id, status, created_at, updated_at,
              estimated_person_days, deliverable_spec_json, acceptance_criteria_json
            )
            VALUES(?, ?, 'ACTION', 'Do', 'xiaobo', 'READY', datetime('now'), datetime('now'), 1.0, ?, ?)
            """,
            ("a1", plan_id, json.dumps(deliverable, ensure_ascii=False), json.dumps(acceptance, ensure_ascii=False)),
        )
        conn.execute(
            """
            INSERT INTO task_nodes(
              task_id, plan_id, node_type, title, owner_agent_id, status, created_at, updated_at,
              review_target_task_id
            )
            VALUES(?, ?, 'CHECK', 'Check A1', 'xiaojing', 'READY', datetime('now'), datetime('now'), ?)
            """,
            ("c1", plan_id, "a1"),
        )
        conn.execute(
            "INSERT INTO task_edges(edge_id, plan_id, from_task_id, to_task_id, edge_type, metadata_json, created_at) VALUES('e1', ?, ?, ?, 'DECOMPOSE', '{\"and_or\":\"AND\"}', datetime('now'))",
            (plan_id, f"{plan_id}_root", "a1"),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_v2_missing_fields_plan(db_path: Path, *, plan_id: str) -> None:
    conn = connect(db_path)
    try:
        apply_migrations(conn, config.MIGRATIONS_DIR)
        conn.execute(
            "INSERT INTO plans(plan_id, title, owner_agent_id, root_task_id, created_at, constraints_json) VALUES(?, 'Plan', 'xiaobo', ?, datetime('now'), '{}')",
            (plan_id, f"{plan_id}_root"),
        )
        conn.execute(
            "INSERT INTO task_nodes(task_id, plan_id, node_type, title, owner_agent_id, status, created_at, updated_at) VALUES(?, ?, 'GOAL', 'Root', 'xiaobo', 'PENDING', datetime('now'), datetime('now'))",
            (f"{plan_id}_root", plan_id),
        )
        # ACTION without v2 required fields.
        conn.execute(
            "INSERT INTO task_nodes(task_id, plan_id, node_type, title, owner_agent_id, status, created_at, updated_at) VALUES('a1', ?, 'ACTION', 'Do', 'xiaobo', 'READY', datetime('now'), datetime('now'))",
            (plan_id,),
        )
        conn.execute(
            """
            INSERT INTO task_nodes(
              task_id, plan_id, node_type, title, owner_agent_id, status, created_at, updated_at,
              review_target_task_id
            )
            VALUES(?, ?, 'CHECK', 'Check A1', 'xiaojing', 'READY', datetime('now'), datetime('now'), ?)
            """,
            ("c1", plan_id, "a1"),
        )
        conn.execute(
            "INSERT INTO task_edges(edge_id, plan_id, from_task_id, to_task_id, edge_type, metadata_json, created_at) VALUES('e1', ?, ?, ?, 'DECOMPOSE', '{\"and_or\":\"AND\"}', datetime('now'))",
            (plan_id, f"{plan_id}_root", "a1"),
        )
        conn.commit()
    finally:
        conn.close()


class P1DoctorCliAcceptanceTest(unittest.TestCase):
    def test_migrations_support_fresh_and_upgrade(self) -> None:
        from tools.migration_drill import upgrade_db

        fixture_sql = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "db" / "old_schema_v004.sql"
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "old.db"
            # create old schema
            conn = connect(db_path)
            try:
                conn.executescript(fixture_sql.read_text(encoding="utf-8"))
                conn.commit()
            finally:
                conn.close()
            # upgrade to latest
            upgrade_db(db_path)
            conn = connect(db_path)
            try:
                latest = sorted(p.name for p in config.MIGRATIONS_DIR.iterdir() if p.suffix.lower() == ".sql")[-1]
                row = conn.execute("SELECT 1 FROM schema_migrations WHERE filename=?", (latest,)).fetchone()
                self.assertIsNotNone(row)
            finally:
                conn.close()

    def test_agent_cli_doctor_ok_for_v2_plan(self) -> None:
        orig = config.RUNTIME_CONFIG_PATH.read_text(encoding="utf-8") if config.RUNTIME_CONFIG_PATH.exists() else None
        try:
            _write_runtime_config_v2()
            with tempfile.TemporaryDirectory() as td:
                db_path = Path(td) / "state.db"
                _insert_v2_ok_plan(db_path, plan_id="p_ok")
                proc = subprocess.run(
                    [sys.executable, "agent_cli.py", "--db", str(db_path), "doctor", "--plan-id", "p_ok"],
                    cwd=str(config.ROOT_DIR),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                self.assertEqual(proc.returncode, 0, proc.stdout + "\n" + proc.stderr)
                self.assertEqual(proc.stdout.strip(), "OK")
        finally:
            if orig is None:
                try:
                    config.RUNTIME_CONFIG_PATH.unlink(missing_ok=True)  # type: ignore[arg-type]
                except Exception:
                    pass
            else:
                config.RUNTIME_CONFIG_PATH.write_text(orig, encoding="utf-8")

    def test_agent_cli_doctor_reports_missing_fields_readably(self) -> None:
        orig = config.RUNTIME_CONFIG_PATH.read_text(encoding="utf-8") if config.RUNTIME_CONFIG_PATH.exists() else None
        try:
            _write_runtime_config_v2()
            with tempfile.TemporaryDirectory() as td:
                db_path = Path(td) / "state.db"
                _insert_v2_missing_fields_plan(db_path, plan_id="p_bad")
                proc = subprocess.run(
                    [sys.executable, "agent_cli.py", "--db", str(db_path), "doctor", "--plan-id", "p_bad"],
                    cwd=str(config.ROOT_DIR),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                self.assertNotEqual(proc.returncode, 0)
                out = (proc.stdout or "").strip()
                # Must contain task title and which field is missing.
                self.assertIn("task=Do", out)
                self.assertIn("V2_ACTION_MISSING_FIELD", out)
                self.assertIn("deliverable_spec_json", out)  # one of required fields
                # Should not print a big table (heuristic).
                self.assertNotIn("Tasks:", out)
        finally:
            if orig is None:
                try:
                    config.RUNTIME_CONFIG_PATH.unlink(missing_ok=True)  # type: ignore[arg-type]
                except Exception:
                    pass
            else:
                config.RUNTIME_CONFIG_PATH.write_text(orig, encoding="utf-8")


if __name__ == "__main__":
    unittest.main()

