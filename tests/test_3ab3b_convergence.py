import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import config
from core.db import apply_migrations, connect
from core.doctor import doctor_plan
from core.feasibility_v2 import feasibility_check
from core.runtime_config import reset_runtime_config_cache
from core.v2_converge import converge_v2_plan


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


def _add_decompose(conn: sqlite3.Connection, *, plan_id: str, parent_id: str, child_id: str, edge_id: str) -> None:
    conn.execute(
        "INSERT INTO task_edges(edge_id, plan_id, from_task_id, to_task_id, edge_type, metadata_json, created_at) VALUES(?, ?, ?, ?, 'DECOMPOSE', '{\"and_or\":\"AND\"}', datetime('now'))",
        (edge_id, plan_id, parent_id, child_id),
    )


def _insert_action(conn: sqlite3.Connection, plan_id: str, task_id: str, *, epd: float) -> None:
    deliverable = {"format": "md", "filename": "deliverable.md", "single_file": True, "bundle_mode": "MANIFEST", "description": "d"}
    acceptance = [{"id": "ac1", "type": "manual", "statement": "ok", "check_method": "manual_review", "severity": "MED"}]
    conn.execute(
        """
        INSERT INTO task_nodes(
          task_id, plan_id, node_type, title, owner_agent_id, status, created_at, updated_at,
          estimated_person_days, deliverable_spec_json, acceptance_criteria_json
        )
        VALUES(?, ?, 'ACTION', ?, 'xiaobo', 'PENDING', datetime('now'), datetime('now'), ?, ?, ?)
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


class Convergence3AB3BTest(unittest.TestCase):
    def setUp(self) -> None:
        self._old_runtime = config.RUNTIME_CONFIG_PATH

    def tearDown(self) -> None:
        config.RUNTIME_CONFIG_PATH = self._old_runtime
        reset_runtime_config_cache()

    def test_v2_plan_with_required_fields_passes_doctor(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _set_runtime_v2(td)
            conn = connect(Path(td) / "t.db")
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                plan_id = _insert_plan(conn, "p")
                _insert_action(conn, plan_id, "a1", epd=3.0)
                _insert_action(conn, plan_id, "a2", epd=5.0)
                _insert_check(conn, plan_id, "c1", "a1")
                _insert_check(conn, plan_id, "c2", "a2")
                _add_decompose(conn, plan_id=plan_id, parent_id="p_root", child_id="a1", edge_id="e1")
                _add_decompose(conn, plan_id=plan_id, parent_id="p_root", child_id="a2", edge_id="e2")
                conn.commit()

                ok, findings = doctor_plan(conn, plan_id=plan_id, workflow_mode="v2")
                self.assertTrue(ok, [x.to_dict() for x in findings])
            finally:
                conn.close()

    def test_feasibility_check_lists_over_threshold_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _set_runtime_v2(td, threshold=10.0)
            conn = connect(Path(td) / "t.db")
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                plan_id = _insert_plan(conn, "p")
                _insert_action(conn, plan_id, "a1", epd=25.0)
                _insert_check(conn, plan_id, "c1", "a1")
                _add_decompose(conn, plan_id=plan_id, parent_id="p_root", child_id="a1", edge_id="e1")
                conn.commit()

                feas = feasibility_check(conn, plan_id=plan_id, threshold_person_days=10.0, max_depth=5)
                self.assertFalse(bool(feas["ok"]))
                self.assertEqual(len(feas["over_threshold"]), 1)
                self.assertIn("over_threshold", feas["over_threshold"][0]["reason"])
            finally:
                conn.close()

    def test_converge_huge_task_splits_until_leaf_within_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _set_runtime_v2(td, max_depth=5, threshold=10.0)
            conn = connect(Path(td) / "t.db")
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                plan_id = _insert_plan(conn, "p")
                _insert_action(conn, plan_id, "a1", epd=25.0)
                _insert_check(conn, plan_id, "c1", "a1")
                _add_decompose(conn, plan_id=plan_id, parent_id="p_root", child_id="a1", edge_id="e1")
                conn.commit()

                res = converge_v2_plan(conn, plan_id=plan_id, max_rounds=5, threshold_person_days=10.0, max_depth=5)
                self.assertEqual(res.status, "OK")

                ok, findings = doctor_plan(conn, plan_id=plan_id, workflow_mode="v2")
                self.assertTrue(ok, [x.to_dict() for x in findings])
                feas = feasibility_check(conn, plan_id=plan_id, threshold_person_days=10.0, max_depth=5)
                self.assertTrue(bool(feas["ok"]))
                self.assertGreaterEqual(int(feas["leaf_action_count"]), 3)
            finally:
                conn.close()

    def test_converge_huge_task_hits_depth_limit_and_requests_external(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _set_runtime_v2(td, max_depth=0, threshold=10.0)
            conn = connect(Path(td) / "t.db")
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                plan_id = _insert_plan(conn, "p")
                _insert_action(conn, plan_id, "a1", epd=25.0)
                _insert_check(conn, plan_id, "c1", "a1")
                _add_decompose(conn, plan_id=plan_id, parent_id="p_root", child_id="a1", edge_id="e1")
                conn.commit()

                res = converge_v2_plan(conn, plan_id=plan_id, max_rounds=2, threshold_person_days=10.0, max_depth=0)
                self.assertEqual(res.status, "REQUEST_EXTERNAL_INPUT")
                self.assertIsNotNone(res.required_docs_path)
                self.assertTrue(Path(str(res.required_docs_path)).exists())
                self.assertTrue(res.required_docs and len(res.required_docs) >= 1)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
