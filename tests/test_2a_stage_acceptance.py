import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import config
from core.db import apply_migrations, connect
from core.deliverables import export_deliverables
from core.runtime_config import reset_runtime_config_cache
from core.scheduler import pick_v2_check_tasks
from core.v2_review_gate import run_check_once


def _insert_plan(conn: sqlite3.Connection, plan_id: str = "p") -> str:
    root_id = f"{plan_id}_root"
    conn.execute(
        "INSERT INTO plans(plan_id, title, owner_agent_id, root_task_id, created_at, constraints_json) VALUES(?, 'Plan', 'xiaobo', ?, datetime('now'), '{}')",
        (plan_id, root_id),
    )
    conn.execute(
        """
        INSERT INTO task_nodes(task_id, plan_id, node_type, title, owner_agent_id, status, created_at, updated_at)
        VALUES(?, ?, 'GOAL', 'Root', 'xiaobo', 'PENDING', datetime('now'), datetime('now'))
        """,
        (root_id, plan_id),
    )
    # Keep DECOMPOSE edges so plan looks structurally valid.
    for idx in (1, 2):
        conn.execute(
            "INSERT INTO task_edges(edge_id, plan_id, from_task_id, to_task_id, edge_type, metadata_json, created_at) VALUES(?, ?, ?, ?, 'DECOMPOSE', '{\"and_or\":\"AND\"}', datetime('now'))",
            (f"e{idx}", plan_id, root_id, f"{plan_id}_a{idx}"),
        )
    conn.commit()
    return plan_id


def _insert_v2_action(conn: sqlite3.Connection, plan_id: str, task_id: str) -> None:
    deliverable = {"format": "html", "filename": f"{task_id}.html", "single_file": True, "bundle_mode": "MANIFEST", "description": "deliver"}
    acceptance = [{"id": "a1", "type": "manual", "statement": "works", "check_method": "manual_review", "severity": "MED"}]
    conn.execute(
        """
        INSERT INTO task_nodes(
          task_id, plan_id, node_type, title, owner_agent_id, status, created_at, updated_at,
          estimated_person_days, deliverable_spec_json, acceptance_criteria_json
        )
        VALUES(?, ?, 'ACTION', ?, 'xiaobo', 'READY_TO_CHECK', datetime('now'), datetime('now'), ?, ?, ?)
        """,
        (task_id, plan_id, f"Action {task_id}", 1.0, json.dumps(deliverable, ensure_ascii=False), json.dumps(acceptance, ensure_ascii=False)),
    )


def _insert_v2_check(conn: sqlite3.Connection, plan_id: str, check_id: str, target_id: str) -> None:
    conn.execute(
        """
        INSERT INTO task_nodes(
          task_id, plan_id, node_type, title, owner_agent_id, status, created_at, updated_at,
          review_target_task_id
        )
        VALUES(?, ?, 'CHECK', ?, 'xiaojing', 'READY', datetime('now'), datetime('now'), ?)
        """,
        (check_id, plan_id, f"Check {check_id}", target_id),
    )


def _insert_artifact_and_activate(conn: sqlite3.Connection, *, task_id: str, artifact_id: str, path: Path) -> None:
    conn.execute(
        """
        INSERT INTO artifacts(artifact_id, task_id, name, path, format, version, sha256, created_at)
        VALUES(?, ?, 'x', ?, 'html', 1, 's', datetime('now'))
        """,
        (artifact_id, task_id, str(path)),
    )
    conn.execute("UPDATE task_nodes SET active_artifact_id = ? WHERE task_id = ?", (artifact_id, task_id))


class Stage2AAcceptanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._old_runtime_path = config.RUNTIME_CONFIG_PATH

    def tearDown(self) -> None:
        config.RUNTIME_CONFIG_PATH = self._old_runtime_path
        reset_runtime_config_cache()

    def _set_workflow_mode_v2(self, td: str) -> None:
        p = Path(td) / "runtime_config.json"
        p.write_text(json.dumps({"workflow_mode": "v2", "llm": {"provider": "llm_demo"}}, ensure_ascii=False), encoding="utf-8")
        config.RUNTIME_CONFIG_PATH = p
        reset_runtime_config_cache()

    def test_v2_min_plan_two_actions_two_checks_gate_and_export_approved_only(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self._set_workflow_mode_v2(td)
            db_path = Path(td) / "t.db"
            conn = connect(db_path)
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                plan_id = _insert_plan(conn, "p")

                a1, a2 = "p_a1", "p_a2"
                c1, c2 = "p_c1", "p_c2"
                _insert_v2_action(conn, plan_id, a1)
                _insert_v2_action(conn, plan_id, a2)
                _insert_v2_check(conn, plan_id, c1, a1)
                _insert_v2_check(conn, plan_id, c2, a2)

                art1_path = Path(td) / "a1.html"
                art2_path = Path(td) / "a2.html"
                art1_path.write_text("<html>v1</html>", encoding="utf-8")
                art2_path.write_text("<html>v2</html>", encoding="utf-8")
                _insert_artifact_and_activate(conn, task_id=a1, artifact_id="art1", path=art1_path)
                _insert_artifact_and_activate(conn, task_id=a2, artifact_id="art2", path=art2_path)
                conn.commit()

                # 1) Each ACTION has a CHECK bound via review_target_task_id.
                action_ids = {r["task_id"] for r in conn.execute("SELECT task_id FROM task_nodes WHERE plan_id=? AND node_type='ACTION'", (plan_id,)).fetchall()}
                check_rows = conn.execute(
                    "SELECT task_id, review_target_task_id FROM task_nodes WHERE plan_id=? AND node_type='CHECK'",
                    (plan_id,),
                ).fetchall()
                self.assertEqual(len(action_ids), 2)
                self.assertEqual(len(check_rows), 2)
                self.assertTrue(all(r["review_target_task_id"] for r in check_rows))
                self.assertEqual({r["review_target_task_id"] for r in check_rows}, action_ids)

                # 2) "run" one round (without real LLM): run all runnable v2 checks.
                runnable = pick_v2_check_tasks(conn, plan_id=plan_id, limit=10)
                self.assertEqual({r["check_task_id"] for r in runnable}, {c1, c2})

                def reviewer_fn(ctx):
                    # Approve a1, reject a2.
                    verdict = "APPROVED" if ctx.get("review_target_task_id") == a1 else "REJECTED"
                    return {"verdict": verdict, "total_score": 95 if verdict == "APPROVED" else 10, "summary": "ok" if verdict == "APPROVED" else "bad"}

                for r in runnable:
                    run_check_once(conn, plan_id=plan_id, check_task_id=r["check_task_id"], reviewer_fn=reviewer_fn)

                a1_row = conn.execute("SELECT status, approved_artifact_id FROM task_nodes WHERE task_id=?", (a1,)).fetchone()
                a2_row = conn.execute("SELECT status, approved_artifact_id FROM task_nodes WHERE task_id=?", (a2,)).fetchone()
                self.assertEqual(a1_row["status"], "DONE")
                self.assertEqual(a1_row["approved_artifact_id"], "art1")
                self.assertEqual(a2_row["status"], "TO_BE_MODIFY")
                self.assertIsNone(a2_row["approved_artifact_id"])

                for cid in (c1, c2):
                    c_row = conn.execute("SELECT status FROM task_nodes WHERE task_id=?", (cid,)).fetchone()
                    self.assertEqual(c_row["status"], "DONE")

                revs = conn.execute(
                    "SELECT task_id, review_target_task_id, reviewed_artifact_id, verdict FROM reviews WHERE task_id IN (?, ?) ORDER BY created_at ASC",
                    (c1, c2),
                ).fetchall()
                self.assertEqual(len(revs), 2)
                by_check = {r["task_id"]: r for r in revs}
                self.assertEqual(by_check[c1]["review_target_task_id"], a1)
                self.assertEqual(by_check[c1]["reviewed_artifact_id"], "art1")
                self.assertEqual(by_check[c1]["verdict"], "APPROVED")
                self.assertEqual(by_check[c2]["review_target_task_id"], a2)
                self.assertEqual(by_check[c2]["reviewed_artifact_id"], "art2")
                self.assertEqual(by_check[c2]["verdict"], "REJECTED")

                # 3) export should only include approved (i.e., only DONE actions, and prefer approved pointer).
                out_dir = Path(td) / "deliverables"
                res = export_deliverables(conn, plan_id=plan_id, out_dir=out_dir, include_reviews=False)
                self.assertEqual(res.files_copied, 1)
                manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
                self.assertEqual(len(manifest.get("files") or []), 1)
                exported = manifest["files"][0]
                self.assertEqual(exported["task_id"], a1)
                self.assertEqual(exported["artifact"]["artifact_id"], "art1")
                self.assertNotIn("art2", json.dumps(manifest, ensure_ascii=False))
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()

