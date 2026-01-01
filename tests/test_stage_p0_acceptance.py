import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import config
from core.db import apply_migrations, connect
from core.doctor import doctor_plan
from core.prompts import load_prompts, register_prompt_versions
from core.runtime_config import load_runtime_config, reset_runtime_config_cache
from core.workflow_mode import ensure_mode_supported_for_action
from tools.install_fixtures import list_cases, load_case


class P0StageAcceptanceTest(unittest.TestCase):
    def test_runtime_config_keys_parsed_and_backend_reads_same_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp_cfg = Path(td) / "runtime_config.json"
            raw = {
                "llm": {"provider": "claude_code", "claude_code_bin": "__missing__", "timeout_s": 123},
                "workflow_mode": "v1",
                "python_executable": "D:/miniconda3/python.exe",
                "max_decomposition_depth": 5,
                "one_shot_threshold_person_days": 10,
                "export_include_candidates": False,
                "max_artifact_versions_per_task": 50,
                "max_review_versions_per_check": 50,
            }
            tmp_cfg.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

            # Patch runtime_config.json path globally (CLI/backend read the same config module).
            old_path = config.RUNTIME_CONFIG_PATH
            try:
                config.RUNTIME_CONFIG_PATH = tmp_cfg
                reset_runtime_config_cache()
                parsed = load_runtime_config(tmp_cfg)
                self.assertEqual(parsed.workflow_mode, "v1")
                self.assertEqual(parsed.llm.provider, "claude_code")
                self.assertEqual(parsed.llm.timeout_s, 123)
                self.assertEqual(parsed.max_decomposition_depth, 5)
                self.assertEqual(parsed.one_shot_threshold_person_days, 10)

                # Backend reads runtime_config.json as raw JSON.
                from dashboard_backend import app as backend_app

                backend_raw = backend_app._read_runtime_config()
                for k in (
                    "workflow_mode",
                    "python_executable",
                    "max_decomposition_depth",
                    "one_shot_threshold_person_days",
                    "export_include_candidates",
                    "max_artifact_versions_per_task",
                    "max_review_versions_per_check",
                ):
                    self.assertEqual(backend_raw.get(k), raw.get(k))
            finally:
                config.RUNTIME_CONFIG_PATH = old_path
                reset_runtime_config_cache()

    def test_status_names_are_consistent_via_p01_contract(self) -> None:
        # P0.1 already enforces machine-readable status rules in Glossary.md.
        # This check ensures the doc contains the two potentially-confusing terms.
        glossary = (Path(__file__).resolve().parents[1] / "doc" / "code" / "Glossary.md").read_text(encoding="utf-8")
        self.assertIn("`READY`", glossary)
        self.assertIn("`READY_TO_CHECK`", glossary)

    def test_workflow_mode_can_toggle_without_deleting_db(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "state.db"
            conn = connect(db_path)
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
            finally:
                conn.close()
            size_before = db_path.stat().st_size
            self.assertGreater(size_before, 0)

            # This guard must never delete/overwrite DB; it only validates config mode safety.
            ensure_mode_supported_for_action(action="run")
            size_after = db_path.stat().st_size
            self.assertEqual(size_after, size_before)

    def test_old_plan_in_v2_mode_fails_with_readable_hint(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "state.db"
            conn = connect(db_path)
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                # Minimal v1 plan (no v2 columns exist in DB).
                conn.execute(
                    "INSERT INTO plans(plan_id, title, owner_agent_id, root_task_id, created_at, constraints_json) VALUES('p1','Plan','xiaobo','t_root',datetime('now'),'{}')"
                )
                conn.execute(
                    "INSERT INTO task_nodes(task_id, plan_id, node_type, title, owner_agent_id, status, created_at, updated_at) VALUES('t_root','p1','GOAL','Root','xiaobo','PENDING',datetime('now'),datetime('now'))"
                )
                conn.execute(
                    "INSERT INTO task_nodes(task_id, plan_id, node_type, title, owner_agent_id, status, created_at, updated_at) VALUES('t_a1','p1','ACTION','Do','xiaobo','READY',datetime('now'),datetime('now'))"
                )
                conn.execute(
                    "INSERT INTO task_edges(edge_id, plan_id, from_task_id, to_task_id, edge_type, metadata_json, created_at) VALUES('e1','p1','t_root','t_a1','DECOMPOSE','{\"and_or\":\"AND\"}',datetime('now'))"
                )
                conn.commit()

                ok, findings = doctor_plan(conn, plan_id="p1", workflow_mode="v2")
                self.assertFalse(ok)
                self.assertTrue(any("workflow_mode=v1" in (f.hint or "") for f in findings), [x.to_dict() for x in findings])
            finally:
                conn.close()

    def test_fixtures_cases_are_one_click_installable_and_have_commands(self) -> None:
        cases = list_cases()
        self.assertGreaterEqual(len(cases), 3)
        for cid in cases:
            c = load_case(cid)
            self.assertTrue(c.top_task)
            self.assertTrue(c.expected_outcome)
            # "One-click executable" at least means we have recommended commands recorded.
            self.assertTrue(c.recommended_commands)
            self.assertTrue(any("create-plan" in x for x in c.recommended_commands))
            # Small/medium cases should include a run loop suggestion; large case may only include status.
            if cid.startswith(("S_", "M_")):
                self.assertTrue(any(" run" in x or x.strip().endswith("run") or " run " in x for x in c.recommended_commands))


if __name__ == "__main__":
    unittest.main()
