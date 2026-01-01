import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import config
from core.db import apply_migrations, connect
from core.reporting import generate_plan_report, render_plan_report_md


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
          estimated_person_days, deliverable_spec_json, acceptance_criteria_json, active_artifact_id
        )
        VALUES(?, ?, 'ACTION', ?, 'xiaobo', ?, datetime('now'), datetime('now'), 1.0, ?, ?, ?)
        """,
        (task_id, plan_id, f"Action {task_id}", status, json.dumps(deliverable, ensure_ascii=False), json.dumps(acceptance, ensure_ascii=False), "art1"),
    )
    conn.execute(
        "INSERT INTO artifacts(artifact_id, task_id, name, path, format, version, sha256, created_at) VALUES('art1', ?, 'x', 'dummy.html', 'html', 1, 's', datetime('now'))",
        (task_id,),
    )


def _insert_check(conn: sqlite3.Connection, plan_id: str, check_id: str, target_id: str, *, status: str = "READY", blocked_reason: str = "") -> None:
    conn.execute(
        """
        INSERT INTO task_nodes(
          task_id, plan_id, node_type, title, owner_agent_id, status, blocked_reason, created_at, updated_at, review_target_task_id
        )
        VALUES(?, ?, 'CHECK', ?, 'xiaojing', ?, ?, datetime('now'), datetime('now'), ?)
        """,
        (check_id, plan_id, f"Check {check_id}", status, blocked_reason or None, target_id),
    )


class Reporting3ATest(unittest.TestCase):
    def setUp(self) -> None:
        self._old_required_docs = config.REQUIRED_DOCS_DIR

    def tearDown(self) -> None:
        config.REQUIRED_DOCS_DIR = self._old_required_docs

    def test_waiting_review_classification(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            conn = connect(db_path)
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                plan_id = _insert_plan(conn, "p")
                _insert_action(conn, plan_id, "a1", status="READY_TO_CHECK")
                _insert_check(conn, plan_id, "c1", "a1", status="READY")
                conn.commit()

                report = generate_plan_report(conn, plan_id, workflow_mode="v2")
                waiting = report["nodes"]["waiting_review"]
                titles = {x["task_title"] for x in waiting}
                self.assertIn("Action a1", titles)
                self.assertIn("Check c1", titles)
            finally:
                conn.close()

    def test_inputs_needed_parses_required_docs_md(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            config.REQUIRED_DOCS_DIR = Path(td) / "required_docs"
            config.REQUIRED_DOCS_DIR.mkdir(parents=True, exist_ok=True)
            db_path = Path(td) / "t.db"
            conn = connect(db_path)
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                plan_id = _insert_plan(conn, "p")
                _insert_action(conn, plan_id, "a1", status="READY_TO_CHECK")
                _insert_check(conn, plan_id, "c1", "a1", status="BLOCKED", blocked_reason="WAITING_INPUT")
                conn.commit()

                req_path = config.REQUIRED_DOCS_DIR / "c1.md"
                req_path.write_text(
                    "\n".join(
                        [
                            "# Required Docs for c1",
                            "",
                            "- product_spec: What to build",
                            "  - accepted_types: ['md','txt']",
                            "  - suggested_path: workspace/inputs/product_spec/product_spec.md",
                            "",
                        ]
                    ),
                    encoding="utf-8",
                )

                report = generate_plan_report(conn, plan_id, workflow_mode="v2")
                inputs = report["inputs_needed"]
                self.assertEqual(len(inputs), 1)
                self.assertEqual(inputs[0]["required_docs_path"], str(req_path))
                self.assertEqual(len(inputs[0]["items"]), 1)
                self.assertEqual(inputs[0]["items"][0]["name"], "product_spec")
            finally:
                conn.close()

    def test_recent_errors_extracts_hint(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            conn = connect(db_path)
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                plan_id = _insert_plan(conn, "p")
                _insert_action(conn, plan_id, "a1", status="READY_TO_CHECK")
                _insert_check(conn, plan_id, "c1", "a1", status="READY")
                payload = {
                    "error_code": "CONTRACT_MISMATCH",
                    "message": "missing key: summary",
                    "context": {"hint": "Update reviewer output to include summary."},
                }
                conn.execute(
                    "INSERT INTO task_events(event_id, plan_id, task_id, event_type, payload_json, created_at) VALUES('e1', ?, ?, 'ERROR', ?, datetime('now'))",
                    (plan_id, "c1", json.dumps(payload, ensure_ascii=False)),
                )
                conn.commit()

                report = generate_plan_report(conn, plan_id, workflow_mode="v2")
                errs = report["recent_errors"]
                self.assertTrue(any(e["error_code"] == "CONTRACT_MISMATCH" and "summary" in e["message"] for e in errs))
                self.assertTrue(any("include summary" in (e.get("hint") or "") for e in errs))
            finally:
                conn.close()

    def test_render_md_has_next_steps_and_no_task_id_field(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            conn = connect(db_path)
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                plan_id = _insert_plan(conn, "p")
                _insert_action(conn, plan_id, "a1", status="READY_TO_CHECK")
                _insert_check(conn, plan_id, "c1", "a1", status="READY")
                conn.commit()

                report = generate_plan_report(conn, plan_id, workflow_mode="v2")
                md = render_plan_report_md(report)
                self.assertIn("## Next Steps", md)
                self.assertNotIn("task_id", md)  # field name should not appear
                # Node items must not contain task_id keys.
                for group in ("blocked", "failed", "waiting_review", "ready"):
                    for it in (report["nodes"][group] or []):
                        self.assertNotIn("task_id", it)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()

