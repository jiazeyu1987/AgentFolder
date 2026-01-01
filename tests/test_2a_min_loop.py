import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import config
from core.db import apply_migrations, connect
from core.readiness import recompute_readiness_for_plan
from core.runtime_config import reset_runtime_config_cache
from core.v2_review_gate import run_check_once


def _insert_plan(conn: sqlite3.Connection, plan_id: str = "p") -> str:
    root_id = f"{plan_id}_root"
    conn.execute(
        "INSERT INTO plans(plan_id, title, owner_agent_id, root_task_id, created_at, constraints_json) VALUES(?, 'Plan', 'xiaobo', ?, datetime('now'), '{}')",
        (plan_id, root_id),
    )
    conn.execute(
        """
        INSERT INTO task_nodes(task_id, plan_id, node_type, title, owner_agent_id, status, created_at, updated_at)
        VALUES(?, ?, 'GOAL', 'Root', 'xiaobo', 'PENDING', datetime('now'), datetime('now'))
        """,
        (root_id, plan_id),
    )
    conn.execute(
        "INSERT INTO task_edges(edge_id, plan_id, from_task_id, to_task_id, edge_type, metadata_json, created_at) VALUES('e1', ?, ?, ?, 'DECOMPOSE', '{\"and_or\":\"AND\"}', datetime('now'))",
        (plan_id, root_id, f"{plan_id}_a1"),
    )
    conn.commit()
    return plan_id


def _insert_v2_action(conn: sqlite3.Connection, plan_id: str, task_id: str) -> None:
    deliverable = {"format": "html", "filename": "index.html", "single_file": True, "bundle_mode": "MANIFEST", "description": "deliver"}
    acceptance = [{"id": "a1", "type": "manual", "statement": "works", "check_method": "manual_review", "severity": "MED"}]
    conn.execute(
        """
        INSERT INTO task_nodes(
          task_id, plan_id, node_type, title, owner_agent_id, status, created_at, updated_at,
          estimated_person_days, deliverable_spec_json, acceptance_criteria_json
        )
        VALUES(?, ?, 'ACTION', 'Do', 'xiaobo', 'READY_TO_CHECK', datetime('now'), datetime('now'), ?, ?, ?)
        """,
        (task_id, plan_id, 1.0, json.dumps(deliverable, ensure_ascii=False), json.dumps(acceptance, ensure_ascii=False)),
    )


def _insert_v2_check(conn: sqlite3.Connection, plan_id: str, check_id: str, target_id: str) -> None:
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


def _insert_artifact_and_activate(conn: sqlite3.Connection, *, task_id: str, artifact_id: str, path: Path) -> None:
    conn.execute(
        """
        INSERT INTO artifacts(artifact_id, task_id, name, path, format, version, sha256, created_at)
        VALUES(?, ?, 'x', ?, 'html', 1, 's', datetime('now'))
        """,
        (artifact_id, task_id, str(path)),
    )
    conn.execute("UPDATE task_nodes SET active_artifact_id = ? WHERE task_id = ?", (artifact_id, task_id))


class MinLoop2ATest(unittest.TestCase):
    def setUp(self) -> None:
        self._old_runtime_path = config.RUNTIME_CONFIG_PATH

    def tearDown(self) -> None:
        config.RUNTIME_CONFIG_PATH = self._old_runtime_path
        reset_runtime_config_cache()

    def _set_workflow_mode_v2(self, td: str) -> None:
        p = Path(td) / "runtime_config.json"
        p.write_text(json.dumps({"workflow_mode": "v2", "llm": {"provider": "llm_demo"}}, ensure_ascii=False), encoding="utf-8")
        config.RUNTIME_CONFIG_PATH = p
        reset_runtime_config_cache()

    def test_approved_flow_sets_approved_pointer_and_marks_done(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self._set_workflow_mode_v2(td)
            db_path = Path(td) / "t.db"
            conn = connect(db_path)
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                plan_id = _insert_plan(conn, "p")
                action_id = "p_a1"
                check_id = "p_c1"
                _insert_v2_action(conn, plan_id, action_id)
                _insert_v2_check(conn, plan_id, check_id, action_id)
                art_path = Path(td) / "a1.html"
                art_path.write_text("<html></html>", encoding="utf-8")
                _insert_artifact_and_activate(conn, task_id=action_id, artifact_id="art1", path=art_path)
                conn.commit()

                def reviewer_fn(_ctx):
                    return {"verdict": "APPROVED", "total_score": 95, "summary": "ok", "breakdown": [], "suggestions": [], "acceptance_results": []}

                run_check_once(conn, plan_id=plan_id, check_task_id=check_id, reviewer_fn=reviewer_fn)

                a = conn.execute("SELECT status, approved_artifact_id FROM task_nodes WHERE task_id = ?", (action_id,)).fetchone()
                c = conn.execute("SELECT status FROM task_nodes WHERE task_id = ?", (check_id,)).fetchone()
                self.assertEqual(a["status"], "DONE")
                self.assertEqual(a["approved_artifact_id"], "art1")
                self.assertEqual(c["status"], "DONE")

                r = conn.execute(
                    "SELECT reviewed_artifact_id, verdict FROM reviews WHERE task_id = ? ORDER BY created_at DESC LIMIT 1",
                    (check_id,),
                ).fetchone()
                self.assertIsNotNone(r)
                self.assertEqual(r["reviewed_artifact_id"], "art1")
                self.assertEqual(r["verdict"], "APPROVED")
            finally:
                conn.close()

    def test_rejected_flow_marks_to_be_modify_and_does_not_set_approved(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self._set_workflow_mode_v2(td)
            db_path = Path(td) / "t.db"
            conn = connect(db_path)
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                plan_id = _insert_plan(conn, "p")
                action_id = "p_a1"
                check_id = "p_c1"
                _insert_v2_action(conn, plan_id, action_id)
                _insert_v2_check(conn, plan_id, check_id, action_id)
                art_path = Path(td) / "a1.html"
                art_path.write_text("<html></html>", encoding="utf-8")
                _insert_artifact_and_activate(conn, task_id=action_id, artifact_id="art1", path=art_path)
                conn.commit()

                def reviewer_fn(_ctx):
                    return {"verdict": "REJECTED", "total_score": 10, "summary": "bad", "breakdown": [], "suggestions": []}

                run_check_once(conn, plan_id=plan_id, check_task_id=check_id, reviewer_fn=reviewer_fn)

                a = conn.execute("SELECT status, approved_artifact_id FROM task_nodes WHERE task_id = ?", (action_id,)).fetchone()
                c = conn.execute("SELECT status FROM task_nodes WHERE task_id = ?", (check_id,)).fetchone()
                self.assertEqual(a["status"], "TO_BE_MODIFY")
                self.assertIsNone(a["approved_artifact_id"])
                self.assertEqual(c["status"], "DONE")

                r = conn.execute(
                    "SELECT reviewed_artifact_id, verdict FROM reviews WHERE task_id = ? ORDER BY created_at DESC LIMIT 1",
                    (check_id,),
                ).fetchone()
                self.assertIsNotNone(r)
                self.assertEqual(r["reviewed_artifact_id"], "art1")
                self.assertEqual(r["verdict"], "REJECTED")
            finally:
                conn.close()

    def test_recheck_resets_check_when_action_returns_to_ready_to_check(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self._set_workflow_mode_v2(td)
            db_path = Path(td) / "t.db"
            conn = connect(db_path)
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                plan_id = _insert_plan(conn, "p")
                action_id = "p_a1"
                check_id = "p_c1"
                _insert_v2_action(conn, plan_id, action_id)
                _insert_v2_check(conn, plan_id, check_id, action_id)

                art1 = Path(td) / "a1.html"
                art1.write_text("<html>v1</html>", encoding="utf-8")
                _insert_artifact_and_activate(conn, task_id=action_id, artifact_id="art1", path=art1)
                conn.commit()

                run_check_once(
                    conn,
                    plan_id=plan_id,
                    check_task_id=check_id,
                    reviewer_fn=lambda _ctx: {"verdict": "REJECTED", "total_score": 0, "summary": "bad"},
                )
                # After review, CHECK is DONE. Simulate new candidate artifact.
                art2 = Path(td) / "a2.html"
                art2.write_text("<html>v2</html>", encoding="utf-8")
                _insert_artifact_and_activate(conn, task_id=action_id, artifact_id="art2", path=art2)
                conn.execute("UPDATE task_nodes SET status = 'READY_TO_CHECK' WHERE task_id = ?", (action_id,))
                conn.execute("UPDATE task_nodes SET status = 'DONE' WHERE task_id = ?", (check_id,))
                conn.commit()

                # v2 readiness hook should reset CHECK from DONE -> READY.
                recompute_readiness_for_plan(conn, plan_id=plan_id)
                c = conn.execute("SELECT status FROM task_nodes WHERE task_id = ?", (check_id,)).fetchone()
                self.assertEqual(c["status"], "READY")

                run_check_once(
                    conn,
                    plan_id=plan_id,
                    check_task_id=check_id,
                    reviewer_fn=lambda _ctx: {"verdict": "APPROVED", "total_score": 95, "summary": "ok"},
                )
                a = conn.execute("SELECT status, approved_artifact_id FROM task_nodes WHERE task_id = ?", (action_id,)).fetchone()
                self.assertEqual(a["status"], "DONE")
                self.assertEqual(a["approved_artifact_id"], "art2")
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()

