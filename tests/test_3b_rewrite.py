import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import config
from core.db import apply_migrations, connect
from core.doctor import doctor_plan
from core.rewriter_v2 import apply_rewrite, propose_rewrite
from core.runtime_config import reset_runtime_config_cache


def _set_runtime_v2(td: str, *, max_depth: int = 5, threshold: float = 10.0) -> None:
    p = Path(td) / "runtime_config.json"
    p.write_text(
        json.dumps(
            {
                "workflow_mode": "v2",
                "llm": {"provider": "llm_demo"},
                "max_decomposition_depth": max_depth,
                "one_shot_threshold_person_days": threshold,
            },
            ensure_ascii=False,
        ),
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


def _insert_action_missing_fields(conn: sqlite3.Connection, plan_id: str, task_id: str) -> None:
    conn.execute(
        "INSERT INTO task_nodes(task_id, plan_id, node_type, title, owner_agent_id, status, created_at, updated_at) VALUES(?, ?, 'ACTION', ?, 'xiaobo', 'READY', datetime('now'), datetime('now'))",
        (task_id, plan_id, f"Action {task_id}"),
    )


def _insert_action_with_fields(conn: sqlite3.Connection, plan_id: str, task_id: str, *, epd: float = 1.0) -> None:
    deliverable = {"format": "md", "filename": "deliverable.md", "single_file": True, "bundle_mode": "MANIFEST", "description": "d"}
    acceptance = [{"id": "ac1", "type": "manual", "statement": "ok", "check_method": "manual_review", "severity": "MED"}]
    conn.execute(
        """
        INSERT INTO task_nodes(
          task_id, plan_id, node_type, title, owner_agent_id, status, created_at, updated_at,
          estimated_person_days, deliverable_spec_json, acceptance_criteria_json
        )
        VALUES(?, ?, 'ACTION', ?, 'xiaobo', 'READY', datetime('now'), datetime('now'), ?, ?, ?)
        """,
        (task_id, plan_id, f"Action {task_id}", float(epd), json.dumps(deliverable, ensure_ascii=False), json.dumps(acceptance, ensure_ascii=False)),
    )


def _insert_check(conn: sqlite3.Connection, plan_id: str, check_id: str, target_id: str) -> None:
    conn.execute(
        """
        INSERT INTO task_nodes(
          task_id, plan_id, node_type, title, owner_agent_id, status, created_at, updated_at, review_target_task_id
        )
        VALUES(?, ?, 'CHECK', ?, 'xiaojing', 'READY', datetime('now'), datetime('now'), ?)
        """,
        (check_id, plan_id, f"Check {check_id}", target_id),
    )


class Rewriter3BTest(unittest.TestCase):
    def setUp(self) -> None:
        self._old_runtime = config.RUNTIME_CONFIG_PATH

    def tearDown(self) -> None:
        config.RUNTIME_CONFIG_PATH = self._old_runtime
        reset_runtime_config_cache()

    def test_add_missing_v2_fields_apply_makes_doctor_pass_fields(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _set_runtime_v2(td)
            conn = connect(Path(td) / "t.db")
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                plan_id = _insert_plan(conn, "p")
                _insert_action_missing_fields(conn, plan_id, "a1")
                _insert_check(conn, plan_id, "c1", "a1")
                conn.commit()

                ok0, f0 = doctor_plan(conn, plan_id=plan_id, workflow_mode="v2")
                self.assertFalse(ok0)

                patch_plan = propose_rewrite(conn, plan_id, workflow_mode="v2", one_shot_threshold_person_days=10, max_depth=5)
                self.assertTrue(any(p.get("type") == "ADD_MISSING_V2_FIELDS" for p in patch_plan.get("patches") or []))
                res = apply_rewrite(conn, patch_plan, dry_run=False)
                self.assertIsNotNone(res.snapshot_path)

                ok1, f1 = doctor_plan(conn, plan_id=plan_id, workflow_mode="v2")
                # doctor may still fail due to other reasons, but missing fields should be fixed
                self.assertFalse(any(x.code in {"V2_ACTION_MISSING_FIELD", "V2_ACTION_BAD_FIELD"} for x in f1), [x.to_dict() for x in f1])
            finally:
                conn.close()

    def test_add_check_binding_apply_creates_check(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _set_runtime_v2(td)
            conn = connect(Path(td) / "t.db")
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                plan_id = _insert_plan(conn, "p")
                _insert_action_with_fields(conn, plan_id, "a1", epd=1.0)
                conn.commit()

                patch_plan = propose_rewrite(conn, plan_id, workflow_mode="v2", one_shot_threshold_person_days=10, max_depth=5)
                self.assertTrue(any(p.get("type") == "ADD_CHECK_BINDING" for p in patch_plan.get("patches") or []))
                apply_rewrite(conn, patch_plan, dry_run=False)
                cnt = conn.execute(
                    "SELECT COUNT(1) AS cnt FROM task_nodes WHERE plan_id=? AND node_type='CHECK' AND review_target_task_id='a1' AND active_branch=1",
                    (plan_id,),
                ).fetchone()
                self.assertEqual(int(cnt["cnt"]), 1)
            finally:
                conn.close()

    def test_split_oversized_action_creates_children_and_checks(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _set_runtime_v2(td, threshold=10.0)
            conn = connect(Path(td) / "t.db")
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                plan_id = _insert_plan(conn, "p")
                _insert_action_with_fields(conn, plan_id, "a1", epd=25.0)
                _insert_check(conn, plan_id, "c1", "a1")
                conn.commit()

                patch_plan = propose_rewrite(conn, plan_id, workflow_mode="v2", one_shot_threshold_person_days=10, max_depth=5)
                split = next((p for p in (patch_plan.get("patches") or []) if p.get("type") == "SPLIT_OVERSIZED_ACTION"), None)
                self.assertIsNotNone(split)
                self.assertGreaterEqual(int(split["targets"][0]["parts"]), 3)

                apply_rewrite(conn, patch_plan, dry_run=False)

                # Parent converted to GOAL
                parent = conn.execute("SELECT node_type FROM task_nodes WHERE task_id='a1'").fetchone()
                self.assertEqual(parent["node_type"], "GOAL")

                # Children exist and each has one CHECK
                children = conn.execute(
                    "SELECT to_task_id FROM task_edges WHERE plan_id=? AND from_task_id='a1' AND edge_type='DECOMPOSE'",
                    (plan_id,),
                ).fetchall()
                self.assertGreaterEqual(len(children), 3)
                child_ids = [str(r["to_task_id"]) for r in children]
                for cid in child_ids:
                    epd = conn.execute("SELECT estimated_person_days FROM task_nodes WHERE task_id=?", (cid,)).fetchone()
                    self.assertLessEqual(float(epd["estimated_person_days"]), 10.0 + 1e-6)
                    c = conn.execute(
                        "SELECT COUNT(1) AS cnt FROM task_nodes WHERE plan_id=? AND node_type='CHECK' AND review_target_task_id=? AND active_branch=1",
                        (plan_id, cid),
                    ).fetchone()
                    self.assertEqual(int(c["cnt"]), 1)

                ok, findings = doctor_plan(conn, plan_id=plan_id, workflow_mode="v2")
                self.assertTrue(ok, [x.to_dict() for x in findings])
            finally:
                conn.close()

    def test_depth_limit_marks_risk_and_does_not_apply_split(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _set_runtime_v2(td, max_depth=1, threshold=10.0)
            conn = connect(Path(td) / "t.db")
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                plan_id = _insert_plan(conn, "p")
                # Make action depth=1 by adding root->a1 decompose.
                _insert_action_with_fields(conn, plan_id, "a1", epd=25.0)
                conn.execute(
                    "INSERT INTO task_edges(edge_id, plan_id, from_task_id, to_task_id, edge_type, metadata_json, created_at) VALUES('e1', ?, 'p_root', 'a1', 'DECOMPOSE', '{\"and_or\":\"AND\"}', datetime('now'))",
                    (plan_id,),
                )
                _insert_check(conn, plan_id, "c1", "a1")
                conn.commit()

                patch_plan = propose_rewrite(conn, plan_id, workflow_mode="v2", one_shot_threshold_person_days=10, max_depth=1)
                split = next((p for p in (patch_plan.get("patches") or []) if p.get("type") == "SPLIT_OVERSIZED_ACTION"), None)
                self.assertIsNotNone(split)
                self.assertFalse(bool(split["targets"][0]["apply_allowed"]))

                apply_rewrite(conn, patch_plan, dry_run=False)
                # Parent should remain ACTION because split not applied.
                parent = conn.execute("SELECT node_type FROM task_nodes WHERE task_id='a1'").fetchone()
                self.assertEqual(parent["node_type"], "ACTION")
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()

