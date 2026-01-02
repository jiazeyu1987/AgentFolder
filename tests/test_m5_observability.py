import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import config
from core.db import apply_migrations, connect
from core.observability import get_plan_snapshot, render_snapshot_brief


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


def _insert_action_ready_to_check(conn: sqlite3.Connection, *, plan_id: str, task_id: str, title: str, artifact_id: str) -> None:
    deliverable = {"format": "html", "filename": "index.html", "single_file": True, "bundle_mode": "MANIFEST", "description": "deliver"}
    acceptance = [{"id": "a1", "type": "manual", "statement": "works", "check_method": "manual_review", "severity": "MED"}]
    conn.execute(
        """
        INSERT INTO task_nodes(
          task_id, plan_id, node_type, title, owner_agent_id, status, created_at, updated_at,
          estimated_person_days, deliverable_spec_json, acceptance_criteria_json, active_artifact_id
        )
        VALUES(?, ?, 'ACTION', ?, 'xiaobo', 'READY_TO_CHECK', datetime('now'), datetime('now'), 1.0, ?, ?, ?)
        """,
        (task_id, plan_id, title, json.dumps(deliverable, ensure_ascii=False), json.dumps(acceptance, ensure_ascii=False), artifact_id),
    )
    conn.execute(
        "INSERT INTO artifacts(artifact_id, task_id, name, path, format, version, sha256, created_at) VALUES(?, ?, 'x', 'dummy.html', 'html', 1, 's', datetime('now'))",
        (artifact_id, task_id),
    )


def _insert_check(conn: sqlite3.Connection, *, plan_id: str, task_id: str, title: str, target_task_id: str, status: str = "READY", blocked_reason: str = "") -> None:
    conn.execute(
        """
        INSERT INTO task_nodes(
          task_id, plan_id, node_type, title, owner_agent_id, status, blocked_reason, created_at, updated_at, review_target_task_id
        )
        VALUES(?, ?, 'CHECK', ?, 'xiaojing', ?, ?, datetime('now'), datetime('now'), ?)
        """,
        (task_id, plan_id, title, status, blocked_reason or None, target_task_id),
    )


def _insert_blocked_waiting_input(conn: sqlite3.Connection, *, plan_id: str, task_id: str, title: str) -> None:
    conn.execute(
        """
        INSERT INTO task_nodes(
          task_id, plan_id, node_type, title, owner_agent_id, status, blocked_reason, created_at, updated_at
        )
        VALUES(?, ?, 'ACTION', ?, 'xiaobo', 'BLOCKED', 'WAITING_INPUT', datetime('now'), datetime('now'))
        """,
        (task_id, plan_id, title),
    )


class ObservabilityM5Test(unittest.TestCase):
    def setUp(self) -> None:
        self._old_required_docs = config.REQUIRED_DOCS_DIR
        self._old_deliverables = config.DELIVERABLES_DIR

    def tearDown(self) -> None:
        config.REQUIRED_DOCS_DIR = self._old_required_docs
        config.DELIVERABLES_DIR = self._old_deliverables

    def test_snapshot_reasons_inputs_errors_and_final_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            config.REQUIRED_DOCS_DIR = ws / "required_docs"
            config.REQUIRED_DOCS_DIR.mkdir(parents=True, exist_ok=True)
            config.DELIVERABLES_DIR = ws / "deliverables"
            (config.DELIVERABLES_DIR / "p").mkdir(parents=True, exist_ok=True)

            db_path = ws / "state.db"
            conn = connect(db_path)
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                plan_id = _insert_plan(conn, "p")

                _insert_action_ready_to_check(conn, plan_id=plan_id, task_id="a1", title="Action A1", artifact_id="art_a1")
                _insert_check(conn, plan_id=plan_id, task_id="c1", title="Check A1", target_task_id="a1", status="READY")

                _insert_blocked_waiting_input(conn, plan_id=plan_id, task_id="a2", title="Needs Input")
                req_path = config.REQUIRED_DOCS_DIR / "a2.md"
                req_path.write_text(
                    "\n".join(
                        [
                            "# Required Docs for a2",
                            "",
                            "- product_spec: What to build",
                            "  - accepted_types: ['md','txt']",
                            "  - suggested_path: workspace/inputs/product_spec/product_spec.md",
                            "",
                        ]
                    ),
                    encoding="utf-8",
                )

                payload = {"error_code": "CONTRACT_MISMATCH", "message": "missing key: summary", "context": {"hint": "Include summary in reviewer output."}}
                conn.execute(
                    "INSERT INTO task_events(event_id, plan_id, task_id, event_type, payload_json, created_at) VALUES('e1', ?, ?, 'ERROR', ?, datetime('now'))",
                    (plan_id, "c1", json.dumps(payload, ensure_ascii=False)),
                )

                final_json = {"final_entrypoint": "artifacts/Action_A1/index.html", "how_to_run": ["Open in browser"], "final_task_title": "Action A1", "final_artifact_id": "art_a1"}
                (config.DELIVERABLES_DIR / plan_id / "final.json").write_text(json.dumps(final_json, ensure_ascii=False, indent=2), encoding="utf-8")
                (config.DELIVERABLES_DIR / plan_id / "manifest.json").write_text(json.dumps({"files": []}, ensure_ascii=False, indent=2), encoding="utf-8")

                conn.commit()

                snap = get_plan_snapshot(conn, plan_id, workflow_mode="v2")

                codes = {r.get("code") for r in (snap.get("reasons") or []) if isinstance(r, dict)}
                self.assertIn("WAITING_REVIEW", codes)
                self.assertIn("WAITING_INPUT", codes)

                inputs = snap.get("inputs_needed") or []
                self.assertTrue(any(x.get("required_docs_path") == str(req_path) for x in inputs))

                errs = snap.get("recent_errors") or []
                self.assertTrue(any(e.get("error_code") == "CONTRACT_MISMATCH" and "summary" in (e.get("message") or "") for e in errs))
                self.assertTrue(any("Include summary" in (e.get("hint") or "") for e in errs))

                final = snap.get("final_deliverable") or {}
                self.assertEqual(final.get("final_entrypoint"), "artifacts/Action_A1/index.html")

                brief = render_snapshot_brief(snap)
                self.assertIn("final_entrypoint:", brief)
                self.assertIn("agent_cli.py doctor", brief)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()

