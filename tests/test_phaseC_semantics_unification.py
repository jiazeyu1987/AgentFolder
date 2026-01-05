import json
import tempfile
import unittest
from pathlib import Path

import config
from core.db import apply_migrations, connect
from core.ssot.semantics import compute_inputs_needed, compute_plan_completion, compute_waiting_review
from core.ssot.snapshot import get_plan_snapshot


class PhaseCSemanticsUnificationTest(unittest.TestCase):
    def test_plan_completion_does_not_depend_on_root_goal(self) -> None:
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
                # root marked DONE, but an ACTION is still PENDING -> plan must NOT be done
                conn.execute(
                    "INSERT INTO task_nodes(task_id, plan_id, node_type, title, goal_statement, rationale, owner_agent_id, priority, status, blocked_reason, attempt_count, confidence, active_branch, active_artifact_id, created_at, updated_at) VALUES(?, ?, 'GOAL', ?, 'X', '', 'xiaobo', 0, 'DONE', NULL, 0, 0.5, 1, NULL, datetime('now'), datetime('now'))",
                    (root_task_id, plan_id, "Root Task"),
                )
                t1 = "t1"
                conn.execute(
                    "INSERT INTO task_nodes(task_id, plan_id, node_type, title, goal_statement, rationale, owner_agent_id, priority, status, blocked_reason, attempt_count, confidence, active_branch, active_artifact_id, created_at, updated_at) VALUES(?, ?, 'ACTION', ?, NULL, NULL, 'xiaobo', 0, 'PENDING', NULL, 0, 0.5, 1, NULL, datetime('now'), datetime('now'))",
                    (t1, plan_id, "Work"),
                )
                conn.commit()

                comp = compute_plan_completion(conn, plan_id)
                self.assertFalse(bool(comp.get("is_done")))

                snap = get_plan_snapshot(conn, plan_id, workflow_mode="v1")
                self.assertFalse(bool((snap.get("summary") or {}).get("is_done")))

                # Mark ACTION DONE -> plan DONE
                conn.execute("UPDATE task_nodes SET status='DONE' WHERE task_id = ?", (t1,))
                conn.commit()
                comp2 = compute_plan_completion(conn, plan_id)
                self.assertTrue(bool(comp2.get("is_done")))
                snap2 = get_plan_snapshot(conn, plan_id, workflow_mode="v1")
                self.assertTrue(bool((snap2.get("summary") or {}).get("is_done")))
            finally:
                if conn is not None:
                    conn.close()
                config.DELIVERABLES_DIR = old_deliver
                config.REQUIRED_DOCS_DIR = old_req

    def test_inputs_needed_and_waiting_review_are_stable(self) -> None:
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

                plan_id = "p2"
                root_task_id = "t_root2"
                conn.execute(
                    "INSERT INTO plans(plan_id, title, owner_agent_id, root_task_id, created_at, constraints_json) VALUES(?, ?, ?, ?, datetime('now'), '{}')",
                    (plan_id, "Top Task 2", "xiaobo", root_task_id),
                )
                conn.execute(
                    "INSERT INTO task_nodes(task_id, plan_id, node_type, title, goal_statement, rationale, owner_agent_id, priority, status, blocked_reason, attempt_count, confidence, active_branch, active_artifact_id, created_at, updated_at) VALUES(?, ?, 'GOAL', ?, 'X', '', 'xiaobo', 0, 'READY', NULL, 0, 0.5, 1, NULL, datetime('now'), datetime('now'))",
                    (root_task_id, plan_id, "Root Task"),
                )

                # BLOCKED waiting input with required_docs file
                t_in = "t_input"
                conn.execute(
                    "INSERT INTO task_nodes(task_id, plan_id, node_type, title, goal_statement, rationale, owner_agent_id, priority, status, blocked_reason, attempt_count, confidence, active_branch, active_artifact_id, created_at, updated_at) VALUES(?, ?, 'ACTION', ?, NULL, NULL, 'xiaobo', 0, 'BLOCKED', 'WAITING_INPUT', 0, 0.5, 1, NULL, datetime('now'), datetime('now'))",
                    (t_in, plan_id, "Need Input"),
                )
                (req_dir / f"{t_in}.md").write_text(
                    "\n".join(
                        [
                            "# Required Docs",
                            "",
                            "- product_spec: What to build",
                            "  - accepted_types: ['md','txt']",
                            "  - suggested_path: workspace/inputs/product_spec/spec.md",
                            "",
                        ]
                    ),
                    encoding="utf-8",
                )

                # v2 waiting review: ACTION READY_TO_CHECK + CHECK READY binding
                t_action = "t_action"
                t_check = "t_check"
                conn.execute(
                    "INSERT INTO task_nodes(task_id, plan_id, node_type, title, goal_statement, rationale, owner_agent_id, priority, status, blocked_reason, attempt_count, confidence, active_branch, active_artifact_id, created_at, updated_at, estimated_person_days, deliverable_spec_json, acceptance_criteria_json) VALUES(?, ?, 'ACTION', ?, NULL, NULL, 'xiaobo', 0, 'READY_TO_CHECK', NULL, 0, 0.5, 1, NULL, datetime('now'), datetime('now'), 1.0, '{}', '[]')",
                    (t_action, plan_id, "Action"),
                )
                conn.execute(
                    "INSERT INTO task_nodes(task_id, plan_id, node_type, title, goal_statement, rationale, owner_agent_id, priority, status, blocked_reason, attempt_count, confidence, active_branch, active_artifact_id, created_at, updated_at, review_target_task_id) VALUES(?, ?, 'CHECK', ?, NULL, NULL, 'xiaojing', 0, 'READY', NULL, 0, 0.5, 1, NULL, datetime('now'), datetime('now'), ?)",
                    (t_check, plan_id, "Check", t_action),
                )

                conn.commit()

                inputs = compute_inputs_needed(conn, plan_id=plan_id, required_docs_dir=req_dir)
                self.assertTrue(any(x.get("task_title") == "Need Input" for x in inputs))

                waiting = compute_waiting_review(conn, plan_id=plan_id, workflow_mode="v2")
                titles = [x.get("task_title") for x in waiting]
                self.assertIn("Action", titles)
                self.assertIn("Check", titles)

                snap = get_plan_snapshot(conn, plan_id, workflow_mode="v2")
                self.assertTrue(any(r.get("code") == "WAITING_REVIEW" for r in (snap.get("reasons") or [])))
            finally:
                if conn is not None:
                    conn.close()
                config.DELIVERABLES_DIR = old_deliver
                config.REQUIRED_DOCS_DIR = old_req


if __name__ == "__main__":
    unittest.main()

