import sqlite3
import tempfile
import unittest
from pathlib import Path

import config
from core.db import apply_migrations, connect
from core.doctor import doctor_db, doctor_plan


def _insert_minimal_plan(conn: sqlite3.Connection, *, plan_id: str = "p1") -> str:
    conn.execute(
        """
        INSERT INTO plans(plan_id, title, owner_agent_id, root_task_id, created_at, constraints_json)
        VALUES(?, 'Plan', 'xiaobo', ?, datetime('now'), '{}')
        """,
        (plan_id, f"{plan_id}_root"),
    )
    conn.execute(
        """
        INSERT INTO task_nodes(
          task_id, plan_id, node_type, title,
          goal_statement, rationale, owner_agent_id, tags_json,
          priority, status, blocked_reason, attempt_count, confidence, active_branch,
          active_artifact_id, created_at, updated_at
        )
        VALUES(?, ?, 'GOAL', 'Root', 'goal', NULL, 'xiaobo', '[]', 0, 'PENDING', NULL, 0, 0.5, 1, NULL, datetime('now'), datetime('now'))
        """,
        (f"{plan_id}_root", plan_id),
    )
    conn.execute(
        """
        INSERT INTO task_nodes(
          task_id, plan_id, node_type, title,
          goal_statement, rationale, owner_agent_id, tags_json,
          priority, status, blocked_reason, attempt_count, confidence, active_branch,
          active_artifact_id, created_at, updated_at
        )
        VALUES(?, ?, 'ACTION', 'Do', NULL, NULL, 'xiaobo', '[]', 0, 'READY', NULL, 0, 0.5, 1, NULL, datetime('now'), datetime('now'))
        """,
        (f"{plan_id}_a1", plan_id),
    )
    conn.execute(
        """
        INSERT INTO task_edges(edge_id, plan_id, from_task_id, to_task_id, edge_type, metadata_json, created_at)
        VALUES('e1', ?, ?, ?, 'DECOMPOSE', '{"and_or":"AND"}', datetime('now'))
        """,
        (plan_id, f"{plan_id}_root", f"{plan_id}_a1"),
    )
    conn.commit()
    return plan_id


class P05DoctorPreflightTest(unittest.TestCase):
    def test_doctor_ok_for_minimal_plan(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            conn = connect(db_path)
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                plan_id = _insert_minimal_plan(conn)
                ok_db, f_db = doctor_db(conn)
                self.assertTrue(ok_db, f_db)
                ok_plan, f_plan = doctor_plan(conn, plan_id=plan_id, workflow_mode="v1")
                self.assertTrue(ok_plan, [x.to_dict() for x in f_plan])
            finally:
                conn.close()

    def test_check_cannot_be_ready_to_check(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            conn = connect(db_path)
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                plan_id = _insert_minimal_plan(conn, plan_id="p2")
                conn.execute(
                    """
                    INSERT INTO task_nodes(
                      task_id, plan_id, node_type, title,
                      goal_statement, rationale, owner_agent_id, tags_json,
                      priority, status, blocked_reason, attempt_count, confidence, active_branch,
                      active_artifact_id, created_at, updated_at
                    )
                    VALUES(?, ?, 'CHECK', 'Check', NULL, NULL, 'xiaojing', '[]', 0, 'READY_TO_CHECK', NULL, 0, 0.5, 1, NULL, datetime('now'), datetime('now'))
                    """,
                    ("p2_c1", plan_id),
                )
                conn.commit()
                ok_plan, f_plan = doctor_plan(conn, plan_id=plan_id, workflow_mode="v1")
                self.assertFalse(ok_plan)
                self.assertTrue(any(f.code == "PLAN_BAD_STATUS" for f in f_plan), [x.to_dict() for x in f_plan])
                bad = next(f for f in f_plan if f.code == "PLAN_BAD_STATUS")
                self.assertEqual(bad.task_id, "p2_c1")
            finally:
                conn.close()

    def test_missing_root_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            conn = connect(db_path)
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                conn.execute(
                    "INSERT INTO plans(plan_id, title, owner_agent_id, root_task_id, created_at, constraints_json) VALUES('p3','Plan','xiaobo','missing_root',datetime('now'),'{}')"
                )
                conn.commit()
                ok_plan, f_plan = doctor_plan(conn, plan_id="p3", workflow_mode="v1")
                self.assertFalse(ok_plan)
                self.assertTrue(any(f.code == "PLAN_ROOT_TASK_NOT_FOUND" for f in f_plan), [x.to_dict() for x in f_plan])
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()

