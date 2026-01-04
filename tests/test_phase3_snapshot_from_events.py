import tempfile
import unittest
from pathlib import Path

import config
from core.db import apply_migrations, connect
from core.observability import get_plan_snapshot
from core.workflow_events import emit_workflow_event


class Phase3SnapshotFromEventsTest(unittest.TestCase):
    def test_snapshot_includes_active_job_step(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            # isolate deliverables so the snapshot is deterministic
            deliver_dir = td_path / "deliverables"
            deliver_dir.mkdir(parents=True, exist_ok=True)
            old_deliver = config.DELIVERABLES_DIR
            config.DELIVERABLES_DIR = deliver_dir
            conn = None
            try:
                db_path = td_path / "state.db"
                conn = connect(db_path)
                apply_migrations(conn, config.MIGRATIONS_DIR)

                plan_id = "p1"
                root_task_id = "t_root"
                conn.execute(
                    "INSERT INTO plans(plan_id, title, owner_agent_id, root_task_id, created_at, constraints_json) VALUES(?, ?, ?, ?, datetime('now'), '{}')",
                    (plan_id, "Top Task", "xiaobo", root_task_id),
                )
                conn.execute(
                    "INSERT INTO task_nodes(task_id, plan_id, node_type, title, goal_statement, rationale, owner_agent_id, priority, status, blocked_reason, attempt_count, confidence, active_branch, active_artifact_id, created_at, updated_at) VALUES(?, ?, 'GOAL', ?, 'X', '', 'xiaobo', 0, 'READY', NULL, 0, 0.5, 1, NULL, datetime('now'), datetime('now'))",
                    (root_task_id, plan_id, "Root Task"),
                )
                conn.commit()

                job_id = "job_run_1"
                emit_workflow_event(conn, workflow="RUN", event_type="JOB_STARTED", job_id=job_id, plan_id=plan_id, message="run started")
                emit_workflow_event(
                    conn,
                    workflow="RUN",
                    event_type="STEP_STARTED",
                    job_id=job_id,
                    plan_id=plan_id,
                    message="RUN_LOOP started",
                    payload={"step": "RUN_LOOP"},
                )

                snap = get_plan_snapshot(conn, plan_id, workflow_mode="v1")
                job = snap.get("job") or {}
                self.assertTrue(bool(job.get("active")))
                self.assertEqual(job.get("workflow"), "RUN")
                self.assertEqual(job.get("job_id"), job_id)
                self.assertEqual(job.get("current_step"), "RUN_LOOP")
            finally:
                if conn is not None:
                    conn.close()
                config.DELIVERABLES_DIR = old_deliver


if __name__ == "__main__":
    unittest.main()

