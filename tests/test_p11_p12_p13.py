import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import config
from core.artifacts_v2 import set_approved_artifact
from core.db import apply_migrations, connect
from core.doctor import doctor_plan


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(r[1]) for r in rows}


def _insert_plan(conn: sqlite3.Connection, plan_id: str = "p") -> str:
    conn.execute(
        "INSERT INTO plans(plan_id, title, owner_agent_id, root_task_id, created_at, constraints_json) VALUES(?, 'Plan', 'xiaobo', ?, datetime('now'), '{}')",
        (plan_id, f"{plan_id}_root"),
    )
    conn.execute(
        """
        INSERT INTO task_nodes(task_id, plan_id, node_type, title, owner_agent_id, status, created_at, updated_at)
        VALUES(?, ?, 'GOAL', 'Root', 'xiaobo', 'PENDING', datetime('now'), datetime('now'))
        """,
        (f"{plan_id}_root", plan_id),
    )
    conn.commit()
    return plan_id


def _insert_v2_action(conn: sqlite3.Connection, plan_id: str, task_id: str, *, with_fields: bool = True) -> None:
    if with_fields:
        deliverable = {"format": "html", "filename": "index.html", "single_file": True, "bundle_mode": "MANIFEST", "description": "deliver"}
        acceptance = [{"id": "a1", "type": "manual", "statement": "works", "check_method": "manual_review", "severity": "MED"}]
        conn.execute(
            """
            INSERT INTO task_nodes(
              task_id, plan_id, node_type, title, owner_agent_id, status, created_at, updated_at,
              estimated_person_days, deliverable_spec_json, acceptance_criteria_json
            )
            VALUES(?, ?, 'ACTION', 'Do', 'xiaobo', 'READY', datetime('now'), datetime('now'), ?, ?, ?)
            """,
            (task_id, plan_id, 1.0, json.dumps(deliverable, ensure_ascii=False), json.dumps(acceptance, ensure_ascii=False)),
        )
    else:
        conn.execute(
            """
            INSERT INTO task_nodes(task_id, plan_id, node_type, title, owner_agent_id, status, created_at, updated_at)
            VALUES(?, ?, 'ACTION', 'Do', 'xiaobo', 'READY', datetime('now'), datetime('now'))
            """,
            (task_id, plan_id),
        )


def _insert_check(conn: sqlite3.Connection, plan_id: str, check_id: str, target_id: str) -> None:
    conn.execute(
        """
        INSERT INTO task_nodes(
          task_id, plan_id, node_type, title, owner_agent_id, status, created_at, updated_at,
          review_target_task_id
        )
        VALUES(?, ?, 'CHECK', 'Check', 'xiaojing', 'READY', datetime('now'), datetime('now'), ?)
        """,
        (check_id, plan_id, target_id),
    )


class P11P12P13Test(unittest.TestCase):
    def test_migrations_add_required_columns(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            conn = connect(db_path)
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                task_cols = _cols(conn, "task_nodes")
                for c in (
                    "estimated_person_days",
                    "deliverable_spec_json",
                    "acceptance_criteria_json",
                    "review_target_task_id",
                    "approved_artifact_id",
                ):
                    self.assertIn(c, task_cols)
                review_cols = _cols(conn, "reviews")
                for c in ("check_task_id", "review_target_task_id", "reviewed_artifact_id", "verdict"):
                    self.assertIn(c, review_cols)
            finally:
                conn.close()

    def test_doctor_v2_fails_when_action_missing_v2_fields(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            conn = connect(db_path)
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                plan_id = _insert_plan(conn, "p1")
                _insert_v2_action(conn, plan_id, "a1", with_fields=False)
                _insert_check(conn, plan_id, "c1", "a1")
                conn.execute(
                    "INSERT INTO task_edges(edge_id, plan_id, from_task_id, to_task_id, edge_type, metadata_json, created_at) VALUES('e1', ?, ?, ?, 'DECOMPOSE', '{\"and_or\":\"AND\"}', datetime('now'))",
                    (plan_id, f"{plan_id}_root", "a1"),
                )
                conn.commit()
                ok, findings = doctor_plan(conn, plan_id=plan_id, workflow_mode="v2")
                self.assertFalse(ok)
                self.assertTrue(any(f.code in {"V2_ACTION_MISSING_FIELD", "V2_ACTION_BAD_FIELD"} for f in findings), [x.to_dict() for x in findings])
            finally:
                conn.close()

    def test_doctor_v2_enforces_one_to_one_check_binding(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            conn = connect(db_path)
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                plan_id = _insert_plan(conn, "p2")
                _insert_v2_action(conn, plan_id, "a1", with_fields=True)
                _insert_check(conn, plan_id, "c1", "a1")
                _insert_check(conn, plan_id, "c2", "a1")
                conn.execute(
                    "INSERT INTO task_edges(edge_id, plan_id, from_task_id, to_task_id, edge_type, metadata_json, created_at) VALUES('e1', ?, ?, ?, 'DECOMPOSE', '{\"and_or\":\"AND\"}', datetime('now'))",
                    (plan_id, f"{plan_id}_root", "a1"),
                )
                conn.commit()
                ok, findings = doctor_plan(conn, plan_id=plan_id, workflow_mode="v2")
                self.assertFalse(ok)
                self.assertTrue(any(f.code == "V2_ACTION_MULTI_CHECK" for f in findings), [x.to_dict() for x in findings])
            finally:
                conn.close()

    def test_doctor_v2_fails_when_check_target_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            conn = connect(db_path)
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                plan_id = _insert_plan(conn, "p3")
                _insert_v2_action(conn, plan_id, "a1", with_fields=True)
                _insert_check(conn, plan_id, "c1", "does_not_exist")
                conn.execute(
                    "INSERT INTO task_edges(edge_id, plan_id, from_task_id, to_task_id, edge_type, metadata_json, created_at) VALUES('e1', ?, ?, ?, 'DECOMPOSE', '{\"and_or\":\"AND\"}', datetime('now'))",
                    (plan_id, f"{plan_id}_root", "a1"),
                )
                conn.commit()
                ok, findings = doctor_plan(conn, plan_id=plan_id, workflow_mode="v2")
                self.assertFalse(ok)
                self.assertTrue(any(f.code == "V2_CHECK_BAD_TARGET" for f in findings), [x.to_dict() for x in findings])
            finally:
                conn.close()

    def test_set_approved_artifact_updates_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            conn = connect(db_path)
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                plan_id = _insert_plan(conn, "p4")
                _insert_v2_action(conn, plan_id, "a1", with_fields=True)
                conn.execute(
                    "INSERT INTO artifacts(artifact_id, task_id, name, path, format, version, sha256, created_at) VALUES('art1','a1','x','p','md',1,'s',datetime('now'))"
                )
                conn.commit()
                set_approved_artifact(conn, task_id="a1", artifact_id="art1")
                row = conn.execute("SELECT approved_artifact_id FROM task_nodes WHERE task_id='a1'").fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row["approved_artifact_id"], "art1")
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()

