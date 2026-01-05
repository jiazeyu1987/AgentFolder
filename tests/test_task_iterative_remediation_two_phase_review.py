import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import config
from core.db import apply_migrations, connect
from core.prompts import build_xiaobo_prompt, load_prompts
from core.readiness import recompute_readiness_for_plan
from core.runtime_config import reset_runtime_config_cache
from run import v2_check_round


def _insert_plan(conn, plan_id: str = "p") -> str:
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
    conn.commit()
    return plan_id


def _insert_v2_action(conn, plan_id: str, task_id: str, title: str = "Do") -> None:
    deliverable = {"format": "html", "filename": "index.html", "single_file": True, "bundle_mode": "MANIFEST", "description": "deliver"}
    acceptance = [{"id": "a1", "type": "manual", "statement": "works", "check_method": "manual_review", "severity": "MED"}]
    conn.execute(
        """
        INSERT INTO task_nodes(
          task_id, plan_id, node_type, title, owner_agent_id, status, created_at, updated_at,
          estimated_person_days, deliverable_spec_json, acceptance_criteria_json
        )
        VALUES(?, ?, 'ACTION', ?, 'xiaobo', 'READY_TO_CHECK', datetime('now'), datetime('now'), ?, ?, ?)
        """,
        (task_id, plan_id, title, 1.0, json.dumps(deliverable, ensure_ascii=False), json.dumps(acceptance, ensure_ascii=False)),
    )


def _insert_v2_check(conn, plan_id: str, check_id: str, target_id: str) -> None:
    conn.execute(
        """
        INSERT INTO task_nodes(
          task_id, plan_id, node_type, title, owner_agent_id, status, created_at, updated_at,
          review_target_task_id
        )
        VALUES(?, ?, 'CHECK', 'Check', 'xiaojing', 'READY', datetime('now'), datetime('now'), ?)
        """,
        (check_id, plan_id, target_id),
    )


def _insert_artifact_and_activate(conn, *, task_id: str, artifact_id: str, path: Path) -> None:
    conn.execute(
        """
        INSERT INTO artifacts(artifact_id, task_id, name, path, format, version, sha256, created_at)
        VALUES(?, ?, 'x', ?, 'html', 1, 's', datetime('now'))
        """,
        (artifact_id, task_id, str(path)),
    )
    conn.execute("UPDATE task_nodes SET active_artifact_id = ? WHERE task_id = ?", (artifact_id, task_id))


@dataclass
class _FakeResp:
    parsed_json: Optional[Dict[str, Any]]
    raw_response_text: str
    provider: str = "fake"
    error: Optional[str] = None
    error_code: Optional[str] = None
    started_at_ts: float = 0.0
    finished_at_ts: float = 0.0
    extra_calls: int = 0


class _FakeLLM:
    def __init__(self, payloads: List[Dict[str, Any]]) -> None:
        self._payloads = list(payloads)
        self.calls: int = 0

    def call_json(self, _prompt: str) -> _FakeResp:
        self.calls += 1
        if not self._payloads:
            return _FakeResp(parsed_json=None, raw_response_text="", error="no more payloads")
        obj = self._payloads.pop(0)
        return _FakeResp(parsed_json=obj, raw_response_text=json.dumps(obj, ensure_ascii=False))


class TaskIterativeRemediationTwoPhaseReviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self._old_runtime_path = config.RUNTIME_CONFIG_PATH

    def tearDown(self) -> None:
        config.RUNTIME_CONFIG_PATH = self._old_runtime_path
        reset_runtime_config_cache()

    def _set_runtime_v2(self, td: str) -> None:
        p = Path(td) / "runtime_config.json"
        p.write_text(
            json.dumps(
                {
                    "workflow_mode": "v2",
                    "llm": {"provider": "llm_demo"},
                    "task_review_pass_score": 90,
                    "task_review_notes_max_chars": 50,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        config.RUNTIME_CONFIG_PATH = p
        reset_runtime_config_cache()

    def test_two_phase_review_writes_suggestions_and_reuses_rubric(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self._set_runtime_v2(td)
            db_path = Path(td) / "t.db"
            conn = connect(db_path)
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                plan_id = _insert_plan(conn, "p")
                action_id = "p_a1"
                check_id = "p_c1"
                _insert_v2_action(conn, plan_id, action_id, title="Core")
                _insert_v2_check(conn, plan_id, check_id, action_id)
                art1 = Path(td) / "a1.html"
                art1.write_text("<html>v1</html>", encoding="utf-8")
                _insert_artifact_and_activate(conn, task_id=action_id, artifact_id="art1", path=art1)
                conn.commit()

                rubric_payload = {
                    "schema_version": "xiaojing_task_rubric_v1",
                    "task_id": action_id,
                    "dimensions": [
                        {"dimension": "A", "max_score": 50, "description": "d", "scoring_guide": "g"},
                        {"dimension": "B", "max_score": 50, "description": "d", "scoring_guide": "g"},
                    ],
                }
                reject_payload = {
                    "schema_version": "xiaojing_review_v1",
                    "task_id": check_id,
                    "review_target": "NODE",
                    "total_score": 10,
                    "action_required": "MODIFY",
                    "summary": "bad",
                    "breakdown": [{"dimension": "overall", "score": 10, "max_score": 100, "issues": []}],
                    "suggestions": [{"priority": "MED", "change": "fix", "steps": ["s1"], "acceptance_criteria": "ac"}],
                    "remediation_text": "问题点：X\n修改步骤：Y\n验收标准：Z",
                }
                llm = _FakeLLM([rubric_payload, reject_payload])
                prompts = load_prompts(config.PROMPTS_SHARED_PATH, config.PROMPTS_AGENTS_DIR)

                v2_check_round(conn=conn, plan_id=plan_id, prompts=prompts, llm=llm, llm_calls=0)
                self.assertEqual(llm.calls, 2)  # rubric build + score

                # rubric stored
                r = conn.execute("SELECT rubric_json FROM task_review_rubrics WHERE task_id = ?", (action_id,)).fetchone()
                self.assertIsNotNone(r)

                # suggestions.md written for ACTION (truncated)
                sugg = config.REVIEWS_DIR / action_id / "suggestions.md"
                self.assertTrue(sugg.exists())
                txt = sugg.read_text(encoding="utf-8")
                self.assertTrue(len(txt) <= 50)

                a = conn.execute("SELECT status, attempt_count FROM task_nodes WHERE task_id = ?", (action_id,)).fetchone()
                self.assertEqual(a["status"], "TO_BE_MODIFY")

                # ACTION prompt should include previous artifact path and suggestions text (iterative)
                prompt_text = build_xiaobo_prompt(prompts, conn=conn, plan_id=plan_id, task_id=action_id, suggestions_text=txt)
                self.assertIn("previous_output_path", prompt_text)
                self.assertIn("a1.html", prompt_text)
                self.assertIn("问题点：X", prompt_text)
                self.assertIn("验收标准：Z", prompt_text)

                # Next round: new artifact + reset check via readiness, approve; rubric should NOT rebuild.
                art2 = Path(td) / "a2.html"
                art2.write_text("<html>v2</html>", encoding="utf-8")
                _insert_artifact_and_activate(conn, task_id=action_id, artifact_id="art2", path=art2)
                conn.execute("UPDATE task_nodes SET status = 'READY_TO_CHECK' WHERE task_id = ?", (action_id,))
                conn.execute("UPDATE task_nodes SET status = 'DONE' WHERE task_id = ?", (check_id,))
                conn.commit()
                recompute_readiness_for_plan(conn, plan_id=plan_id)

                approve_payload = dict(reject_payload)
                approve_payload["total_score"] = 95
                approve_payload["action_required"] = "APPROVE"
                approve_payload["summary"] = "ok"
                approve_payload["remediation_text"] = ""
                llm2 = _FakeLLM([approve_payload])
                v2_check_round(conn=conn, plan_id=plan_id, prompts=prompts, llm=llm2, llm_calls=0)
                self.assertEqual(llm2.calls, 1)  # score only

                a2 = conn.execute("SELECT status, approved_artifact_id FROM task_nodes WHERE task_id = ?", (action_id,)).fetchone()
                self.assertEqual(a2["status"], "DONE")
                self.assertEqual(a2["approved_artifact_id"], "art2")
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
