import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import config
from core.db import apply_migrations, connect
from core.doctor import doctor_plan
from core.readiness import recompute_readiness_for_plan
from core.runtime_config import reset_runtime_config_cache
from core.v2_review_gate import run_check_once


def _set_runtime_v2(td: str, *, max_check_attempts_v2: int = 3) -> None:
    p = Path(td) / "runtime_config.json"
    p.write_text(
        json.dumps({"workflow_mode": "v2", "llm": {"provider": "llm_demo"}, "max_check_attempts_v2": max_check_attempts_v2}, ensure_ascii=False),
        encoding="utf-8",
    )
    config.RUNTIME_CONFIG_PATH = p
    reset_runtime_config_cache()


def _insert_plan(conn: sqlite3.Connection, plan_id: str = "p") -> str:
    root_id = f"{plan_id}_root"
    conn.execute(
        "INSERT INTO plans(plan_id, title, owner_agent_id, root_task_id, created_at, constraints_json) VALUES(?, 'Plan', 'xiaobo', ?, datetime('now'), '{}')",
        (plan_id, root_id),
    )
    conn.execute(
        "INSERT INTO task_nodes(task_id, plan_id, node_type, title, owner_agent_id, status, created_at, updated_at) VALUES(?, ?, 'GOAL', 'Root', 'xiaobo', 'PENDING', datetime('now'), datetime('now'))",
        (root_id, plan_id),
    )
    conn.commit()
    return plan_id


def _insert_action(conn: sqlite3.Connection, plan_id: str, task_id: str, *, status: str = "READY_TO_CHECK") -> None:
    deliverable = {"format": "html", "filename": f"{task_id}.html", "single_file": True, "bundle_mode": "MANIFEST", "description": "deliver"}
    acceptance = [{"id": "a1", "type": "manual", "statement": "works", "check_method": "manual_review", "severity": "MED"}]
    conn.execute(
        """
        INSERT INTO task_nodes(
          task_id, plan_id, node_type, title, owner_agent_id, status, created_at, updated_at,
          estimated_person_days, deliverable_spec_json, acceptance_criteria_json
        )
        VALUES(?, ?, 'ACTION', ?, 'xiaobo', ?, datetime('now'), datetime('now'), 1.0, ?, ?)
        """,
        (task_id, plan_id, f"Action {task_id}", status, json.dumps(deliverable, ensure_ascii=False), json.dumps(acceptance, ensure_ascii=False)),
    )


def _insert_check(conn: sqlite3.Connection, plan_id: str, check_id: str, target_id: str, *, status: str = "READY") -> None:
    conn.execute(
        """
        INSERT INTO task_nodes(
          task_id, plan_id, node_type, title, owner_agent_id, status, created_at, updated_at, review_target_task_id
        )
        VALUES(?, ?, 'CHECK', ?, 'xiaojing', ?, datetime('now'), datetime('now'), ?)
        """,
        (check_id, plan_id, f"Check {check_id}", status, target_id),
    )


def _insert_artifact_and_activate(conn: sqlite3.Connection, *, task_id: str, artifact_id: str, path: Path) -> None:
    conn.execute(
        "INSERT INTO artifacts(artifact_id, task_id, name, path, format, version, sha256, created_at) VALUES(?, ?, 'x', ?, 'html', 1, 's', datetime('now'))",
        (artifact_id, task_id, str(path)),
    )
    conn.execute("UPDATE task_nodes SET active_artifact_id = ? WHERE task_id = ?", (artifact_id, task_id))


class Reliability2BTest(unittest.TestCase):
    def setUp(self) -> None:
        self._old_runtime = config.RUNTIME_CONFIG_PATH

    def tearDown(self) -> None:
        config.RUNTIME_CONFIG_PATH = self._old_runtime
        reset_runtime_config_cache()

    def test_idempotency_already_reviewed_no_state_change(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _set_runtime_v2(td)
            conn = connect(Path(td) / "t.db")
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                plan_id = _insert_plan(conn, "p")
                _insert_action(conn, plan_id, "a1")
                _insert_check(conn, plan_id, "c1", "a1")
                p = Path(td) / "a1.html"
                p.write_text("<html></html>", encoding="utf-8")
                _insert_artifact_and_activate(conn, task_id="a1", artifact_id="art1", path=p)
                conn.commit()

                r1 = run_check_once(conn, plan_id=plan_id, check_task_id="c1", reviewer_fn=lambda _ctx: {"verdict": "APPROVED", "total_score": 95, "summary": "ok"})
                self.assertTrue(r1["ok"])

                # Reset statuses to simulate repeated trigger on same artifact.
                conn.execute("UPDATE task_nodes SET status='READY_TO_CHECK' WHERE task_id='a1'")
                conn.execute("UPDATE task_nodes SET status='READY' WHERE task_id='c1'")
                conn.commit()

                r2 = run_check_once(conn, plan_id=plan_id, check_task_id="c1", reviewer_fn=lambda _ctx: {"verdict": "APPROVED", "total_score": 95, "summary": "ok"})
                self.assertTrue(r2["ok"])
                self.assertEqual(r2.get("reason"), "ALREADY_REVIEWED")

                cnt = conn.execute("SELECT COUNT(1) AS c FROM reviews WHERE task_id='c1'").fetchone()
                self.assertEqual(int(cnt["c"]), 1)

                c_status = conn.execute("SELECT status FROM task_nodes WHERE task_id='c1'").fetchone()
                a_status = conn.execute("SELECT status FROM task_nodes WHERE task_id='a1'").fetchone()
                self.assertEqual(c_status["status"], "READY")
                self.assertEqual(a_status["status"], "READY_TO_CHECK")
            finally:
                conn.close()

    def test_lock_skips_when_in_progress(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _set_runtime_v2(td)
            conn = connect(Path(td) / "t.db")
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                plan_id = _insert_plan(conn, "p")
                _insert_action(conn, plan_id, "a1")
                _insert_check(conn, plan_id, "c1", "a1", status="IN_PROGRESS")
                p = Path(td) / "a1.html"
                p.write_text("<html></html>", encoding="utf-8")
                _insert_artifact_and_activate(conn, task_id="a1", artifact_id="art1", path=p)
                conn.commit()

                r = run_check_once(conn, plan_id=plan_id, check_task_id="c1", reviewer_fn=lambda _ctx: {"verdict": "APPROVED", "total_score": 95})
                self.assertTrue(r["ok"])
                self.assertEqual(r.get("reason"), "SKIPPED_LOCK_NOT_ACQUIRED")
                cnt = conn.execute("SELECT COUNT(1) AS c FROM reviews WHERE task_id='c1'").fetchone()
                self.assertEqual(int(cnt["c"]), 0)
            finally:
                conn.close()

    def test_locked_version_stays_even_if_action_active_artifact_changes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _set_runtime_v2(td)
            conn = connect(Path(td) / "t.db")
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                plan_id = _insert_plan(conn, "p")
                _insert_action(conn, plan_id, "a1")
                _insert_check(conn, plan_id, "c1", "a1")
                p1 = Path(td) / "a1_v1.html"
                p2 = Path(td) / "a1_v2.html"
                p1.write_text("<html>v1</html>", encoding="utf-8")
                p2.write_text("<html>v2</html>", encoding="utf-8")
                _insert_artifact_and_activate(conn, task_id="a1", artifact_id="art1", path=p1)
                conn.commit()

                def reviewer_fn(ctx):
                    # Mutate ACTION to point at a different candidate during review; gate must keep reviewing art1.
                    _insert_artifact_and_activate(conn, task_id="a1", artifact_id="art2", path=p2)
                    return {"verdict": "APPROVED", "total_score": 95, "summary": "ok"}

                run_check_once(conn, plan_id=plan_id, check_task_id="c1", reviewer_fn=reviewer_fn)
                rev = conn.execute("SELECT reviewed_artifact_id FROM reviews WHERE task_id='c1'").fetchone()
                self.assertEqual(rev["reviewed_artifact_id"], "art1")
                a = conn.execute("SELECT approved_artifact_id FROM task_nodes WHERE task_id='a1'").fetchone()
                self.assertEqual(a["approved_artifact_id"], "art1")
            finally:
                conn.close()

    def test_missing_artifact_file_blocks_waiting_input(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _set_runtime_v2(td)
            conn = connect(Path(td) / "t.db")
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                plan_id = _insert_plan(conn, "p")
                _insert_action(conn, plan_id, "a1")
                _insert_check(conn, plan_id, "c1", "a1")
                missing = Path(td) / "missing.html"
                _insert_artifact_and_activate(conn, task_id="a1", artifact_id="art1", path=missing)
                conn.commit()

                r = run_check_once(conn, plan_id=plan_id, check_task_id="c1", reviewer_fn=lambda _ctx: {"verdict": "APPROVED", "total_score": 95})
                self.assertFalse(r["ok"])
                c = conn.execute("SELECT status, blocked_reason FROM task_nodes WHERE task_id='c1'").fetchone()
                self.assertEqual(c["status"], "BLOCKED")
                self.assertEqual(c["blocked_reason"], "WAITING_INPUT")
            finally:
                conn.close()

    def test_retry_limit_blocks_waiting_external(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _set_runtime_v2(td, max_check_attempts_v2=2)
            conn = connect(Path(td) / "t.db")
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                plan_id = _insert_plan(conn, "p")
                _insert_action(conn, plan_id, "a1")
                _insert_check(conn, plan_id, "c1", "a1")
                p = Path(td) / "a1.html"
                p.write_text("<html></html>", encoding="utf-8")
                _insert_artifact_and_activate(conn, task_id="a1", artifact_id="art1", path=p)
                conn.commit()

                # Force bad output twice.
                r1 = run_check_once(conn, plan_id=plan_id, check_task_id="c1", reviewer_fn=lambda _ctx: "not a dict")
                self.assertFalse(r1["ok"])
                c1 = conn.execute("SELECT status FROM task_nodes WHERE task_id='c1'").fetchone()
                self.assertEqual(c1["status"], "READY")

                r2 = run_check_once(conn, plan_id=plan_id, check_task_id="c1", reviewer_fn=lambda _ctx: "not a dict")
                self.assertFalse(r2["ok"])
                c2 = conn.execute("SELECT status, blocked_reason FROM task_nodes WHERE task_id='c1'").fetchone()
                self.assertEqual(c2["status"], "BLOCKED")
                self.assertEqual(c2["blocked_reason"], "WAITING_EXTERNAL")
            finally:
                conn.close()

    def test_doctor_v2_flags_done_action_without_approved_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _set_runtime_v2(td)
            conn = connect(Path(td) / "t.db")
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                plan_id = _insert_plan(conn, "p")
                _insert_action(conn, plan_id, "a1", status="DONE")
                _insert_check(conn, plan_id, "c1", "a1", status="DONE")
                conn.commit()

                ok, findings = doctor_plan(conn, plan_id=plan_id, workflow_mode="v2")
                self.assertFalse(ok)
                codes = {f.code for f in findings}
                self.assertIn("V2_ACTION_DONE_NO_APPROVED", codes)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()

