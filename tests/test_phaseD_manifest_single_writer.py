import json
import tempfile
import unittest
from pathlib import Path

import config
from core.cleanup import compute_keeper_artifact_ids, trim_artifacts
from core.db import apply_migrations, connect
from core.deliverables import export_deliverables
from core.reviews import insert_review
from core.ssot.types import FINAL_SCHEMA_SUMMARY, MANIFEST_SCHEMA_SUMMARY


class PhaseDManifestSingleWriterTest(unittest.TestCase):
    def test_deliverables_schema_summaries_match_doc(self) -> None:
        doc_path = Path(__file__).resolve().parents[1] / "doc" / "code" / "Deliverables.md"
        text = doc_path.read_text(encoding="utf-8")

        def read_block(start: str, end: str) -> dict:
            self.assertIn(start, text)
            self.assertIn(end, text)
            payload = text.split(start, 1)[1].split(end, 1)[0].strip()
            return json.loads(payload)

        manifest_doc = read_block("<!-- MANIFEST_SCHEMA_JSON_START -->", "<!-- MANIFEST_SCHEMA_JSON_END -->")
        final_doc = read_block("<!-- FINAL_SCHEMA_JSON_START -->", "<!-- FINAL_SCHEMA_JSON_END -->")
        self.assertEqual(manifest_doc, MANIFEST_SCHEMA_SUMMARY)
        self.assertEqual(final_doc, FINAL_SCHEMA_SUMMARY)

    def test_export_default_only_exports_approved(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
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

                # Two DONE ACTION nodes: one approved, one only active.
                t1 = "t1"
                t2 = "t2"
                conn.execute(
                    "INSERT INTO task_nodes(task_id, plan_id, node_type, title, goal_statement, rationale, owner_agent_id, priority, status, blocked_reason, attempt_count, confidence, active_branch, active_artifact_id, approved_artifact_id, created_at, updated_at) VALUES(?, ?, 'ACTION', ?, NULL, NULL, 'xiaobo', 0, 'DONE', NULL, 0, 0.5, 1, ?, ?, datetime('now'), datetime('now'))",
                    (t1, plan_id, "Approved Task", "a1", "a1"),
                )
                conn.execute(
                    "INSERT INTO task_nodes(task_id, plan_id, node_type, title, goal_statement, rationale, owner_agent_id, priority, status, blocked_reason, attempt_count, confidence, active_branch, active_artifact_id, approved_artifact_id, created_at, updated_at) VALUES(?, ?, 'ACTION', ?, NULL, NULL, 'xiaobo', 0, 'DONE', NULL, 0, 0.5, 1, ?, NULL, datetime('now'), datetime('now'))",
                    (t2, plan_id, "Candidate Only", "a2"),
                )

                src1 = td_path / "src1.html"
                src2 = td_path / "src2.html"
                src1.write_text("<html>ok</html>", encoding="utf-8")
                src2.write_text("<html>candidate</html>", encoding="utf-8")

                conn.execute(
                    "INSERT INTO artifacts(artifact_id, task_id, name, format, path, sha256, created_at) VALUES(?, ?, ?, ?, ?, ?, datetime('now'))",
                    ("a1", t1, "index", "html", str(src1), "sha1",),
                )
                conn.execute(
                    "INSERT INTO artifacts(artifact_id, task_id, name, format, path, sha256, created_at) VALUES(?, ?, ?, ?, ?, ?, datetime('now'))",
                    ("a2", t2, "index", "html", str(src2), "sha2",),
                )
                conn.commit()

                out_dir = deliver_dir / plan_id
                res = export_deliverables(conn, plan_id=plan_id, out_dir=out_dir, include_reviews=False, include_candidates=False, job_id="jobx")
                self.assertEqual(res.plan_id, plan_id)

                manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
                self.assertEqual(manifest.get("schema_version"), MANIFEST_SCHEMA_SUMMARY["schema_version"])
                files = manifest.get("files") or []
                self.assertEqual(len(files), 1)
                self.assertEqual(files[0]["artifact"]["artifact_id"], "a1")

                final = json.loads((out_dir / "final.json").read_text(encoding="utf-8"))
                self.assertEqual(final.get("schema_version"), FINAL_SCHEMA_SUMMARY["schema_version"])
                self.assertEqual(final.get("final_artifact_id"), "a1")
                self.assertTrue(str(final.get("final_entrypoint") or "").strip())
            finally:
                if conn is not None:
                    conn.close()
                config.DELIVERABLES_DIR = old_deliver

    def test_cleanup_keepers_preserve_final_manifest_and_reviews(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            deliver_dir = td_path / "deliverables"
            deliver_dir.mkdir(parents=True, exist_ok=True)

            old_deliver = config.DELIVERABLES_DIR
            config.DELIVERABLES_DIR = deliver_dir
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

                task_id = "tkeep"
                conn.execute(
                    "INSERT INTO task_nodes(task_id, plan_id, node_type, title, goal_statement, rationale, owner_agent_id, priority, status, blocked_reason, attempt_count, confidence, active_branch, active_artifact_id, approved_artifact_id, created_at, updated_at) VALUES(?, ?, 'ACTION', ?, NULL, NULL, 'xiaobo', 0, 'DONE', NULL, 0, 0.5, 1, ?, ?, datetime('now'), datetime('now'))",
                    (task_id, plan_id, "Final Package", "keep", "keep"),
                )

                # Many versions for the same task.
                versions_dir = td_path / "versions"
                versions_dir.mkdir(parents=True, exist_ok=True)
                for i in range(60):
                    aid = f"v{i:02d}"
                    p = versions_dir / f"{aid}.txt"
                    p.write_text(f"v{i}", encoding="utf-8")
                    conn.execute(
                        "INSERT INTO artifacts(artifact_id, task_id, name, format, path, sha256, created_at) VALUES(?, ?, ?, ?, ?, ?, datetime('now','-%d seconds'))"
                        % (60 - i),
                        (aid, task_id, "d", "txt", str(p), f"sha{aid}"),
                    )

                # Approved artifact + reviewed artifact (old).
                keep_path = versions_dir / "keep.txt"
                keep_path.write_text("keep", encoding="utf-8")
                reviewed_path = versions_dir / "reviewed.txt"
                reviewed_path.write_text("reviewed", encoding="utf-8")
                conn.execute(
                    "INSERT INTO artifacts(artifact_id, task_id, name, format, path, sha256, created_at) VALUES(?, ?, ?, ?, ?, ?, datetime('now','-999 seconds'))",
                    ("keep", task_id, "k", "txt", str(keep_path), "shakeep"),
                )
                conn.execute(
                    "INSERT INTO artifacts(artifact_id, task_id, name, format, path, sha256, created_at) VALUES(?, ?, ?, ?, ?, ?, datetime('now','-1000 seconds'))",
                    ("reviewed", task_id, "r", "txt", str(reviewed_path), "shareviewed"),
                )
                insert_review(
                    conn,
                    plan_id=plan_id,
                    task_id=task_id,
                    reviewer_agent_id="xiaojing",
                    review={"total_score": 100, "summary": "ok", "suggestions": [], "breakdown": [], "action_required": "APPROVE"},
                    check_task_id=task_id,
                    review_target_task_id=task_id,
                    reviewed_artifact_id="reviewed",
                    verdict="APPROVED",
                )
                conn.commit()

                # Export writes final.json + manifest.json; cleanup should keep their artifact IDs.
                export_deliverables(conn, plan_id=plan_id, out_dir=deliver_dir / plan_id, include_reviews=False, include_candidates=False, job_id="jobx")

                keep_ids = compute_keeper_artifact_ids(conn, deliverables_dir=deliver_dir)
                self.assertIn("keep", keep_ids)
                self.assertIn("reviewed", keep_ids)

                deleted, _files_deleted = trim_artifacts(conn, max_versions_per_task=10, keep_artifact_ids=keep_ids, dry_run=False)
                self.assertGreaterEqual(int(deleted), 1)
                remaining = conn.execute("SELECT COUNT(1) AS c FROM artifacts WHERE task_id = ?", (task_id,)).fetchone()["c"]
                self.assertLessEqual(int(remaining), 12)  # latest 10 + keepers (keep/reviewed)
                left = {r["artifact_id"] for r in conn.execute("SELECT artifact_id FROM artifacts WHERE task_id = ?", (task_id,)).fetchall()}
                self.assertIn("keep", left)
                self.assertIn("reviewed", left)
            finally:
                if conn is not None:
                    conn.close()
                config.DELIVERABLES_DIR = old_deliver


if __name__ == "__main__":
    unittest.main()
