import json
import tempfile
import unittest
from pathlib import Path

import config
from core.db import apply_migrations, connect
from core.workflow_events import fetch_workflow_events
from core.v2_review_gate import run_check_once


class Phase3RunExportEventsTest(unittest.TestCase):
    def test_run_v2_check_emits_workflow_events(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            db_path = td_path / "state.db"
            conn = connect(db_path)
            apply_migrations(conn, config.MIGRATIONS_DIR)
            try:
                plan_id = "p1"
                root_task_id = "t_root"
                action_task_id = "t_action"
                check_task_id = "t_check"
                artifact_id = "a1"
                job_id = "job_run_1"

                conn.execute(
                    "INSERT INTO plans(plan_id, title, owner_agent_id, root_task_id, created_at, constraints_json) VALUES(?, ?, ?, ?, datetime('now'), '{}')",
                    (plan_id, "Top Task", "xiaobo", root_task_id),
                )
                conn.execute(
                    "INSERT INTO task_nodes(task_id, plan_id, node_type, title, goal_statement, rationale, owner_agent_id, priority, status, blocked_reason, attempt_count, confidence, active_branch, active_artifact_id, created_at, updated_at) VALUES(?, ?, 'GOAL', ?, 'X', '', 'xiaobo', 0, 'READY', NULL, 0, 0.5, 1, NULL, datetime('now'), datetime('now'))",
                    (root_task_id, plan_id, "Root Task"),
                )
                conn.execute(
                    "INSERT INTO task_nodes(task_id, plan_id, node_type, title, goal_statement, rationale, owner_agent_id, priority, status, blocked_reason, attempt_count, confidence, active_branch, active_artifact_id, approved_artifact_id, created_at, updated_at) VALUES(?, ?, 'ACTION', ?, NULL, NULL, 'xiaobo', 0, 'READY_TO_CHECK', NULL, 0, 0.5, 1, ?, NULL, datetime('now'), datetime('now'))",
                    (action_task_id, plan_id, "Action", artifact_id),
                )
                conn.execute(
                    "INSERT INTO task_nodes(task_id, plan_id, node_type, title, goal_statement, rationale, owner_agent_id, priority, status, blocked_reason, attempt_count, confidence, active_branch, review_target_task_id, created_at, updated_at) VALUES(?, ?, 'CHECK', ?, NULL, NULL, 'xiaojing', 0, 'READY', NULL, 0, 0.5, 1, ?, datetime('now'), datetime('now'))",
                    (check_task_id, plan_id, "Check", action_task_id),
                )

                art_dir = td_path / "artifact"
                art_dir.mkdir(parents=True, exist_ok=True)
                art_path = art_dir / "candidate.txt"
                art_path.write_text("hello", encoding="utf-8")
                conn.execute(
                    "INSERT INTO artifacts(artifact_id, task_id, name, format, sha256, path, created_at) VALUES(?, ?, ?, ?, ?, ?, datetime('now'))",
                    (artifact_id, action_task_id, "candidate", "txt", "deadbeef", str(art_path)),
                )
                conn.commit()

                def reviewer_fn(ctx: dict) -> dict:
                    return {"verdict": "APPROVED", "total_score": 100, "summary": "ok"}

                res = run_check_once(conn, plan_id=plan_id, check_task_id=check_task_id, reviewer_fn=reviewer_fn, job_id=job_id)
                self.assertTrue(bool(res.get("ok")))

                events = fetch_workflow_events(conn, plan_id=plan_id, workflow="RUN", limit=200)
                event_types = [e.event_type for e in events]
                self.assertIn("STEP_STARTED", event_types)
                self.assertIn("REVIEW_WRITTEN", event_types)
                self.assertIn("ARTIFACT_APPROVED", event_types)
                self.assertIn("STATUS_CHANGED", event_types)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()

