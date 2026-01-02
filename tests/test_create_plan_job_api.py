import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import config
from core.db import apply_migrations, connect
from dashboard_backend.app import infer_create_plan_progress
from dashboard_backend.app import _job_status_from_state


class CreatePlanJobApiTest(unittest.TestCase):
    def test_infer_attempt_phase_and_review_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            conn = connect(db_path)
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)

                # PLAN_GEN (plan_id NULL), attempt=2
                conn.execute(
                    """
                    INSERT INTO llm_calls(
                      llm_call_id, created_at, plan_id, task_id, agent, scope, prompt_text, response_text, meta_json
                    ) VALUES('g1', datetime('now','-2 seconds'), NULL, NULL, 'xiaobo', 'PLAN_GEN', 'p', 'r', ?)
                    """,
                    (json.dumps({"attempt": 2}, ensure_ascii=False),),
                )
                # PLAN_REVIEW with plan_id, attempt=2 review_attempt=3
                conn.execute(
                    """
                    INSERT INTO llm_calls(
                      llm_call_id, created_at, plan_id, task_id, agent, scope, prompt_text, response_text, meta_json
                    ) VALUES('r1', datetime('now','-1 seconds'), 'plan123', NULL, 'xiaojing', 'PLAN_REVIEW', 'p2', 'r2', ?)
                    """,
                    (json.dumps({"attempt": 2, "review_attempt": 3}, ensure_ascii=False),),
                )
                conn.commit()

                # With known plan_id -> should infer PLAN_REVIEW with attempt=2, review_attempt=3
                out = infer_create_plan_progress(conn, plan_id="plan123")
                self.assertEqual(out["phase"], "PLAN_REVIEW")
                self.assertEqual(out["attempt"], 2)
                self.assertEqual(out["review_attempt"], 3)

                # With plan_id unknown -> should infer from latest PLAN_GEN null row
                out2 = infer_create_plan_progress(conn, plan_id=None)
                self.assertEqual(out2["phase"], "PLAN_GEN")
                self.assertEqual(out2["attempt"], 2)
            finally:
                conn.close()

    def test_infer_progress_prefers_started_at_and_tracks_plan_id_changes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            conn = connect(db_path)
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                # Old attempt (planA) review.
                conn.execute(
                    """
                    INSERT INTO llm_calls(llm_call_id, created_at, plan_id, task_id, agent, scope, prompt_text, response_text, meta_json)
                    VALUES('r_old', '2026-01-01T00:00:02Z', 'planA', NULL, 'xiaojing', 'PLAN_REVIEW', 'p', 'r', ?)
                    """,
                    (json.dumps({"attempt": 1, "review_attempt": 2}, ensure_ascii=False),),
                )
                # New attempt starts and generates a different plan_id (planB).
                conn.execute(
                    """
                    INSERT INTO llm_calls(llm_call_id, created_at, plan_id, task_id, agent, scope, prompt_text, response_text, meta_json)
                    VALUES('g_new', '2026-01-01T00:00:10Z', 'planB', NULL, 'xiaobo', 'PLAN_GEN', 'p2', 'r2', ?)
                    """,
                    (json.dumps({"attempt": 2}, ensure_ascii=False),),
                )
                conn.commit()

                out = infer_create_plan_progress(conn, plan_id="planA", started_at="2026-01-01T00:00:05Z")
                self.assertEqual(out["phase"], "PLAN_GEN")
                self.assertEqual(out["attempt"], 2)
                self.assertEqual(out["inferred_plan_id"], "planB")
            finally:
                conn.close()

    def test_job_status_from_state(self) -> None:
        self.assertEqual(_job_status_from_state(alive=True, state_status="RUNNING", last_error=None), "RUNNING")
        self.assertEqual(_job_status_from_state(alive=False, state_status="RUNNING", last_error=None), "DONE")
        self.assertEqual(_job_status_from_state(alive=False, state_status="FAILED", last_error=None), "FAILED")
        self.assertEqual(_job_status_from_state(alive=False, state_status="RUNNING", last_error={"x": 1}), "FAILED")


if __name__ == "__main__":
    unittest.main()
