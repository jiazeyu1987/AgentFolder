import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import config
from core.db import apply_migrations, connect
from core.readiness import recompute_readiness_for_plan
from core.runtime_config import reset_runtime_config_cache
from core.v2_review_gate import ReviewContractMismatch, run_check_once


def _set_runtime_v2(td: str, *, max_check_attempts_v2: int = 3) -> None:
    p = Path(td) / "runtime_config.json"
    p.write_text(
        json.dumps({"workflow_mode": "v2", "llm": {"provider": "llm_demo"}, "max_check_attempts_v2": max_check_attempts_v2}, ensure_ascii=False),
        encoding="utf-8",
    )
    config.RUNTIME_CONFIG_PATH = p
    reset_runtime_config_cache()


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
          estimated_person_days, deliverable_spec_json, acceptance_criteria_json
        )
        VALUES(?, ?, 'ACTION', ?, 'xiaobo', ?, datetime('now'), datetime('now'), 1.0, ?, ?)
        """,
        (task_id, plan_id, f"Action {task_id}", status, json.dumps(deliverable, ensure_ascii=False), json.dumps(acceptance, ensure_ascii=False)),
    )


def _insert_check(conn: sqlite3.Connection, plan_id: str, check_id: str, target_id: str, *, status: str = "READY") -> None:
    conn.execute(
        """
        INSERT INTO task_nodes(
          task_id, plan_id, node_type, title, owner_agent_id, status, created_at, updated_at, review_target_task_id
        )
        VALUES(?, ?, 'CHECK', ?, 'xiaojing', ?, datetime('now'), datetime('now'), ?)
        """,
        (check_id, plan_id, f"Check {check_id}", status, target_id),
    )


def _insert_artifact_and_activate(conn: sqlite3.Connection, *, task_id: str, artifact_id: str, path: Path) -> None:
    conn.execute(
        "INSERT INTO artifacts(artifact_id, task_id, name, path, format, version, sha256, created_at) VALUES(?, ?, 'x', ?, 'html', 1, 's', datetime('now'))",
        (artifact_id, task_id, str(path)),
    )
    conn.execute("UPDATE task_nodes SET active_artifact_id = ? WHERE task_id = ?", (artifact_id, task_id))


class Stage2BAcceptanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._old_runtime = config.RUNTIME_CONFIG_PATH

    def tearDown(self) -> None:
        config.RUNTIME_CONFIG_PATH = self._old_runtime
        reset_runtime_config_cache()

    def test_race_stale_approval_keeps_action_needing_latest_review(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _set_runtime_v2(td)
            conn = connect(Path(td) / "t.db")
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                plan_id = _insert_plan(conn, "p")
                _insert_action(conn, plan_id, "a1")
                _insert_check(conn, plan_id, "c1", "a1")

                p1 = Path(td) / "v1.html"
                p2 = Path(td) / "v2.html"
                p1.write_text("<html>v1</html>", encoding="utf-8")
                p2.write_text("<html>v2</html>", encoding="utf-8")
                _insert_artifact_and_activate(conn, task_id="a1", artifact_id="art_v1", path=p1)
                conn.commit()

                def reviewer_fn(_ctx):
                    # During review, a newer candidate is generated.
                    _insert_artifact_and_activate(conn, task_id="a1", artifact_id="art_v2", path=p2)
                    conn.execute("UPDATE task_nodes SET status='READY_TO_CHECK' WHERE task_id='a1'")
                    return {"verdict": "APPROVED", "total_score": 95, "summary": "ok"}

                run_check_once(conn, plan_id=plan_id, check_task_id="c1", reviewer_fn=reviewer_fn)
                a = conn.execute("SELECT status, approved_artifact_id, active_artifact_id FROM task_nodes WHERE task_id='a1'").fetchone()
                self.assertEqual(a["approved_artifact_id"], "art_v1")
                self.assertEqual(a["active_artifact_id"], "art_v2")
                self.assertEqual(a["status"], "READY_TO_CHECK")  # must still review v2 before DONE

                # After readiness recompute, CHECK should be READY again for the latest artifact.
                recompute_readiness_for_plan(conn, plan_id=plan_id)
                c = conn.execute("SELECT status FROM task_nodes WHERE task_id='c1'").fetchone()
                self.assertEqual(c["status"], "READY")
            finally:
                conn.close()

    def test_contract_mismatch_retries_and_becomes_readable_block(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _set_runtime_v2(td, max_check_attempts_v2=2)
            conn = connect(Path(td) / "t.db")
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                plan_id = _insert_plan(conn, "p")
                _insert_action(conn, plan_id, "a1")
                _insert_check(conn, plan_id, "c1", "a1")
                p = Path(td) / "v1.html"
                p.write_text("<html>v1</html>", encoding="utf-8")
                _insert_artifact_and_activate(conn, task_id="a1", artifact_id="art_v1", path=p)
                conn.commit()

                def bad_contract(_ctx):
                    raise ReviewContractMismatch("missing key: summary", hint="Update the reviewer output to include summary.")

                r1 = run_check_once(conn, plan_id=plan_id, check_task_id="c1", reviewer_fn=bad_contract)
                self.assertFalse(r1["ok"])
                c1 = conn.execute("SELECT status, attempt_count FROM task_nodes WHERE task_id='c1'").fetchone()
                self.assertEqual(c1["status"], "READY")
                self.assertEqual(int(c1["attempt_count"]), 1)

                # second failure => BLOCKED WAITING_EXTERNAL (max attempts exceeded)
                r2 = run_check_once(conn, plan_id=plan_id, check_task_id="c1", reviewer_fn=bad_contract)
                self.assertFalse(r2["ok"])
                c2 = conn.execute("SELECT status, blocked_reason FROM task_nodes WHERE task_id='c1'").fetchone()
                self.assertEqual(c2["status"], "BLOCKED")
                self.assertEqual(c2["blocked_reason"], "WAITING_EXTERNAL")

                # Must have a readable ERROR event persisted.
                evt = conn.execute(
                    "SELECT payload_json FROM task_events WHERE plan_id=? AND task_id=? AND event_type='ERROR' ORDER BY created_at DESC LIMIT 1",
                    (plan_id, "c1"),
                ).fetchone()
                self.assertIsNotNone(evt)
                payload = json.loads(evt["payload_json"])
                self.assertEqual(payload.get("error_code"), "CONTRACT_MISMATCH")
                self.assertIn("hint", (payload.get("context") or {}))
            finally:
                conn.close()

    def test_fixtures_top_task_does_not_include_retry_notes_marker(self) -> None:
        base = Path("tests") / "fixtures" / "cases"
        for case_id in ("S_2048", "M_doudizhu", "L_3d_shooter"):
            case_json = json.loads((base / case_id / "case.json").read_text(encoding="utf-8"))
            top_task = str(case_json.get("top_task") or "")
            self.assertNotIn("RETRY_NOTES", top_task)
            self.assertNotIn("schema_version mismatch", top_task)


if __name__ == "__main__":
    unittest.main()

