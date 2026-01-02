import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import config
from core.cleanup import plan_cleanup
from core.db import apply_migrations, connect
from core.llm_calls import record_llm_call
from core.runtime_config import load_runtime_config, reset_runtime_config_cache


class GuardrailsM6Test(unittest.TestCase):
    def test_runtime_config_defaults_guardrails(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = load_runtime_config(Path(td) / "missing_runtime_config.json")
            self.assertGreater(cfg.guardrails.max_run_iterations, 0)
            self.assertGreater(cfg.guardrails.max_llm_calls_per_run, 0)
            self.assertGreater(cfg.guardrails.max_prompt_chars, 0)

    def test_llm_call_truncation_marks_flags(self) -> None:
        orig_path = config.RUNTIME_CONFIG_PATH
        try:
            with tempfile.TemporaryDirectory() as td:
                tmp_cfg = Path(td) / "runtime_config.json"
                tmp_cfg.write_text(
                    json.dumps(
                        {
                            "llm": {"provider": "llm_demo", "claude_code_bin": "claude_code", "timeout_s": 300},
                            "workflow_mode": "v1",
                            "guardrails": {"max_prompt_chars": 120, "max_response_chars": 160},
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                config.RUNTIME_CONFIG_PATH = tmp_cfg
                reset_runtime_config_cache()

                db_path = Path(td) / "t.db"
                conn = connect(db_path)
                try:
                    apply_migrations(conn, config.MIGRATIONS_DIR)
                    long_prompt = "P" * 500
                    long_resp = "R" * 800
                    record_llm_call(
                        conn,
                        plan_id=None,
                        task_id=None,
                        agent="t",
                        scope="TEST",
                        provider="x",
                        prompt_text=long_prompt,
                        response_text=long_resp,
                        meta={"k": "v"},
                    )
                    row = conn.execute(
                        "SELECT prompt_text, response_text, prompt_truncated, response_truncated, meta_json FROM llm_calls ORDER BY created_at DESC LIMIT 1"
                    ).fetchone()
                    self.assertIsNotNone(row)
                    self.assertLessEqual(len(row["prompt_text"] or ""), 120)
                    self.assertLessEqual(len(row["response_text"] or ""), 160)
                    self.assertEqual(int(row["prompt_truncated"]), 1)
                    self.assertEqual(int(row["response_truncated"]), 1)
                    meta = json.loads(row["meta_json"] or "{}")
                    self.assertTrue(isinstance(meta, dict))
                    self.assertTrue(meta.get("truncated", {}).get("prompt"))
                    self.assertTrue(meta.get("truncated", {}).get("response"))
                finally:
                    conn.close()
        finally:
            config.RUNTIME_CONFIG_PATH = orig_path
            reset_runtime_config_cache()

    def test_cleanup_keeps_approved_and_reviewed_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            conn = connect(db_path)
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                plan_id = "p"
                root_id = "p_root"
                conn.execute(
                    "INSERT INTO plans(plan_id, title, owner_agent_id, root_task_id, created_at, constraints_json) VALUES(?, 'Plan', 'xiaobo', ?, datetime('now'), '{}')",
                    (plan_id, root_id),
                )
                conn.execute(
                    "INSERT INTO task_nodes(task_id, plan_id, node_type, title, owner_agent_id, status, created_at, updated_at) VALUES(?, ?, 'GOAL', 'Root', 'xiaobo', 'PENDING', datetime('now'), datetime('now'))",
                    (root_id, plan_id),
                )
                task_id = "a1"
                conn.execute(
                    """
                    INSERT INTO task_nodes(
                      task_id, plan_id, node_type, title, owner_agent_id, status, created_at, updated_at,
                      approved_artifact_id, active_artifact_id
                    )
                    VALUES(?, ?, 'ACTION', 'A1', 'xiaobo', 'DONE', datetime('now'), datetime('now'), ?, ?)
                    """,
                    (task_id, plan_id, "art_approved", "art_active"),
                )
                # Insert 60 artifacts.
                for i in range(60):
                    conn.execute(
                        "INSERT INTO artifacts(artifact_id, task_id, name, path, format, version, sha256, created_at) VALUES(?, ?, 'x', ?, 'txt', ?, 's', datetime('now'))",
                        (f"art_{i}", task_id, str(Path(td) / f"a{i}.txt"), i),
                    )
                # Add keepers in artifacts table too.
                conn.execute(
                    "INSERT INTO artifacts(artifact_id, task_id, name, path, format, version, sha256, created_at) VALUES('art_approved', ?, 'x', ?, 'txt', 999, 's', datetime('now'))",
                    (task_id, str(Path(td) / "approved.txt")),
                )
                conn.execute(
                    "INSERT INTO artifacts(artifact_id, task_id, name, path, format, version, sha256, created_at) VALUES('art_active', ?, 'x', ?, 'txt', 998, 's', datetime('now'))",
                    (task_id, str(Path(td) / "active.txt")),
                )
                # Review references a historical artifact.
                conn.execute(
                    """
                    INSERT INTO reviews(
                      review_id, task_id, reviewer_agent_id, total_score, breakdown_json, suggestions_json, summary,
                      action_required, created_at, check_task_id, review_target_task_id, reviewed_artifact_id, verdict, acceptance_results_json
                    )
                    VALUES('r1', ?, 'xiaojing', 90, '[]', '[]', 'ok', 'APPROVE', datetime('now'), 'c1', ?, ?, 'APPROVED', '[]')
                    """,
                    (task_id, task_id, "art_7"),
                )
                conn.commit()

                plan_cleanup(
                    conn,
                    max_llm_calls_rows=5000,
                    max_task_events_rows=20000,
                    max_artifact_versions_per_task=10,
                    max_review_versions_per_check=50,
                    dry_run=False,
                    deliverables_dir=Path(td) / "deliverables",
                )
                conn.commit()

                remaining = {r["artifact_id"] for r in conn.execute("SELECT artifact_id FROM artifacts").fetchall()}
                self.assertIn("art_approved", remaining)
                self.assertIn("art_active", remaining)
                self.assertIn("art_7", remaining)  # referenced by review
                # Should be trimmed to <= 10 + keepers.
                rows = conn.execute("SELECT COUNT(1) AS cnt FROM artifacts WHERE task_id=?", (task_id,)).fetchone()
                self.assertLessEqual(int(rows["cnt"]), 20)
            finally:
                conn.close()

    def test_cleanup_trims_llm_calls_rows(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            conn = connect(db_path)
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                # Insert 6000 rows.
                for i in range(6000):
                    conn.execute(
                        """
                        INSERT INTO llm_calls(
                          llm_call_id, created_at, agent, scope, prompt_text, response_text,
                          prompt_truncated, response_truncated, meta_json
                        )
                        VALUES(?, datetime('now'), 't', 'S', 'p', 'r', 0, 0, '{}')
                        """,
                        (f"id_{i}",),
                    )
                conn.commit()
                plan_cleanup(
                    conn,
                    max_llm_calls_rows=5000,
                    max_task_events_rows=20000,
                    max_artifact_versions_per_task=50,
                    max_review_versions_per_check=50,
                    dry_run=False,
                    deliverables_dir=Path(td) / "deliverables",
                )
                conn.commit()
                cnt = conn.execute("SELECT COUNT(1) AS cnt FROM llm_calls").fetchone()["cnt"]
                self.assertLessEqual(int(cnt), 5000)
            finally:
                conn.close()

    def test_cli_cleanup_dry_run_is_readable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            conn = connect(db_path)
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
            finally:
                conn.close()
            proc = subprocess.run(
                [sys.executable, "agent_cli.py", "--db", str(db_path), "cleanup"],
                cwd=str(config.ROOT_DIR),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(proc.returncode, 0)
            out = (proc.stdout or "").lower()
            self.assertIn("would delete", out)
            self.assertIn("keep approved", out)


if __name__ == "__main__":
    unittest.main()

