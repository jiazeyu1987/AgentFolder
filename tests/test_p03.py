import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import config
from core.db import connect
from core.runtime_config import load_runtime_config
from tools.migration_drill import create_fresh_db, doctor_db, upgrade_db


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {str(r[0]) for r in rows}


class P03MigrationDrillTest(unittest.TestCase):
    def test_fresh_db_init_and_doctor(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "fresh.db"
            create_fresh_db(db_path)
            ok, findings = doctor_db(db_path)
            self.assertTrue(ok, "\n".join(f"{f.code}:{f.message}" for f in findings))
            conn = connect(db_path)
            try:
                tables = _tables(conn)
                for t in ("plans", "task_nodes", "task_edges", "llm_calls", "schema_migrations"):
                    self.assertIn(t, tables)
            finally:
                conn.close()

    def test_upgrade_old_schema_fixture_to_latest(self) -> None:
        fixture_sql = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "db" / "old_schema_v004.sql"
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "old.db"
            conn = connect(db_path)
            try:
                conn.executescript(fixture_sql.read_text(encoding="utf-8"))
                conn.commit()
            finally:
                conn.close()

            upgrade_db(db_path)
            ok, findings = doctor_db(db_path)
            self.assertTrue(ok, "\n".join(f"{f.code}:{f.message}" for f in findings))

            conn = connect(db_path)
            try:
                # Data should still exist after upgrade.
                row = conn.execute("SELECT title FROM plans WHERE plan_id='p_old'").fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row[0], "Old Plan")
                # New tables should exist.
                tables = _tables(conn)
                self.assertIn("input_files", tables)
                self.assertIn("llm_calls", tables)
                # Latest migration should be recorded.
                latest = sorted(p.name for p in config.MIGRATIONS_DIR.iterdir() if p.suffix == ".sql")[-1]
                applied = conn.execute("SELECT 1 FROM schema_migrations WHERE filename=?", (latest,)).fetchone()
                self.assertIsNotNone(applied)
            finally:
                conn.close()

    def test_runtime_config_workflow_mode_parse_and_toggle(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "runtime_config.json"
            p.write_text(
                json.dumps({"llm": {"provider": "llm_demo", "claude_code_bin": "claude_code", "timeout_s": 300}, "workflow_mode": "v2"}),
                encoding="utf-8",
            )
            cfg = load_runtime_config(p)
            self.assertEqual(cfg.workflow_mode, "v2")

            p.write_text(
                json.dumps({"llm": {"provider": "llm_demo", "claude_code_bin": "claude_code", "timeout_s": 300}, "workflow_mode": "v1"}),
                encoding="utf-8",
            )
            cfg2 = load_runtime_config(p)
            self.assertEqual(cfg2.workflow_mode, "v1")


if __name__ == "__main__":
    unittest.main()

