import json
import tempfile
import unittest
from pathlib import Path

import config
from core.db import apply_migrations, connect
from core.observability import SNAPSHOT_SCHEMA_SUMMARY, get_plan_snapshot, render_snapshot_brief


class Phase1SnapshotSsotTest(unittest.TestCase):
    def test_snapshot_schema_summary_matches_doc(self) -> None:
        doc_path = Path(__file__).resolve().parents[1] / "doc" / "code" / "Observability.md"
        text = doc_path.read_text(encoding="utf-8")
        start = "<!-- SNAPSHOT_SCHEMA_JSON_START -->"
        end = "<!-- SNAPSHOT_SCHEMA_JSON_END -->"
        self.assertIn(start, text)
        self.assertIn(end, text)
        payload = text.split(start, 1)[1].split(end, 1)[0].strip()
        doc_summary = json.loads(payload)
        self.assertEqual(doc_summary, SNAPSHOT_SCHEMA_SUMMARY)

    def test_snapshot_contains_required_keys_and_brief_is_derived(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            # isolate deliverables/required_docs so the snapshot is deterministic
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
                    "INSERT INTO task_nodes(task_id, plan_id, node_type, title, goal_statement, rationale, owner_agent_id, priority, status, blocked_reason, attempt_count, confidence, active_branch, active_artifact_id, created_at, updated_at) VALUES(?, ?, 'GOAL', ?, 'X', '', 'xiaobo', 0, 'DONE', NULL, 0, 0.5, 1, NULL, datetime('now'), datetime('now'))",
                    (root_task_id, plan_id, "Root Task"),
                )
                t1 = "t1"
                conn.execute(
                    "INSERT INTO task_nodes(task_id, plan_id, node_type, title, goal_statement, rationale, owner_agent_id, priority, status, blocked_reason, attempt_count, confidence, active_branch, active_artifact_id, created_at, updated_at) VALUES(?, ?, 'ACTION', ?, NULL, NULL, 'xiaobo', 0, 'BLOCKED', 'WAITING_INPUT', 0, 0.5, 1, NULL, datetime('now'), datetime('now'))",
                    (t1, plan_id, "Need Input"),
                )
                (req_dir / f"{t1}.md").write_text(
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
                conn.execute(
                    "INSERT INTO task_events(event_id, plan_id, task_id, event_type, payload_json, created_at) VALUES(?, ?, ?, 'ERROR', ?, datetime('now'))",
                    (
                        "e1",
                        plan_id,
                        t1,
                        json.dumps({"error_code": "INPUT_MISSING", "message": "Missing required input(s).", "context": {"hint": "write required_docs"}}),
                    ),
                )
                conn.commit()

                out_dir = deliver_dir / plan_id
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / "final.json").write_text(
                    json.dumps({"final_entrypoint": "artifacts/x/index.html", "how_to_run": ["open index.html"]}),
                    encoding="utf-8",
                )

                snap = get_plan_snapshot(conn, plan_id, workflow_mode="v1")
                for k in SNAPSHOT_SCHEMA_SUMMARY["required_top_level_keys"]:
                    self.assertIn(k, snap)
                self.assertEqual(snap.get("schema_version"), "plan_snapshot_v1")

                brief = render_snapshot_brief(snap)
                self.assertIn("plan:", brief)
                self.assertIn("plan_id:", brief)
                self.assertIn("status:", brief)
            finally:
                if conn is not None:
                    conn.close()
                config.DELIVERABLES_DIR = old_deliver
                config.REQUIRED_DOCS_DIR = old_req


if __name__ == "__main__":
    unittest.main()
