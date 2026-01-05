import json
import tempfile
import unittest
from pathlib import Path

import config
from core.db import apply_migrations, connect
from core.ssot.snapshot import get_plan_snapshot


class PhaseBWorkflowEventsStepsTest(unittest.TestCase):
    def test_snapshot_job_is_explained_by_workflow_events(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            deliver_dir = td_path / "deliverables"
            req_dir = td_path / "required_docs"
            deliver_dir.mkdir(parents=True, exist_ok=True)
            req_dir.mkdir(parents=True, exist_ok=True)

            old_deliver = config.DELIVERABLES_DIR
            old_req = config.REQUIRED_DOCS_DIR
            config.DELIVERABLES_DIR = deliver_dir
            config.REQUIRED_DOCS_DIR = req_dir
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

                def insert_event(*, created_at: str, event_type: str, payload: dict) -> None:
                    conn.execute(
                        """
                        INSERT INTO workflow_events(
                          event_id, created_at,
                          workflow, event_type, severity, message,
                          job_id, top_task_hash, plan_id, task_id, llm_call_id,
                          payload_json
                        )
                        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            f"e_{created_at}_{event_type}",
                            created_at,
                            "CREATE_PLAN",
                            event_type,
                            "INFO",
                            event_type,
                            "job1",
                            "hash1",
                            plan_id,
                            None,
                            None,
                            json.dumps(payload, ensure_ascii=False),
                        ),
                    )

                # Deterministic ordering: utc_now_iso() is second-granular, so tests must control created_at.
                insert_event(created_at="2026-01-01T00:00:01Z", event_type="JOB_STARTED", payload={"job": "start"})
                insert_event(created_at="2026-01-01T00:00:02Z", event_type="STEP_STARTED", payload={"step": "PLAN_RUBRIC", "attempt": 1})
                insert_event(created_at="2026-01-01T00:00:03Z", event_type="STEP_FINISHED", payload={"step": "PLAN_RUBRIC", "attempt": 1, "ok": True})
                insert_event(created_at="2026-01-01T00:00:04Z", event_type="STEP_STARTED", payload={"step": "STRUCTURE_GEN", "attempt": 1, "stage": "STRUCTURE"})
                insert_event(
                    created_at="2026-01-01T00:00:05Z",
                    event_type="DECISION_MADE",
                    payload={"why": "SCORE_BELOW_THRESHOLD", "next": "RETRY_REVIEW", "retry_reason": "validator_error"},
                )
                conn.commit()

                snap = get_plan_snapshot(conn, plan_id, workflow_mode="v1")
                job = snap.get("job") or {}
                self.assertTrue(bool(job.get("active")))
                self.assertEqual(job.get("workflow"), "CREATE_PLAN")
                self.assertEqual(job.get("job_id"), "job1")
                self.assertEqual(job.get("current_step"), "STRUCTURE_GEN")
                self.assertIsNotNone(job.get("last_decision"))
                self.assertIsNotNone(job.get("last_event"))

                insert_event(created_at="2026-01-01T00:00:06Z", event_type="JOB_FINISHED", payload={"status": "DONE"})
                conn.commit()

                snap2 = get_plan_snapshot(conn, plan_id, workflow_mode="v1")
                job2 = snap2.get("job") or {}
                self.assertFalse(bool(job2.get("active")))
                self.assertEqual(job2.get("workflow"), "CREATE_PLAN")
            finally:
                if conn is not None:
                    conn.close()
                config.DELIVERABLES_DIR = old_deliver
                config.REQUIRED_DOCS_DIR = old_req


if __name__ == "__main__":
    unittest.main()

