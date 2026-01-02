import time
from pathlib import Path

import pytest

import config
from core.db import apply_migrations, connect
from core.llm_client import LLMCallResult
from core.plan_workflow import PlanWorkflowError, generate_and_review_plan
from core.prompts import load_prompts, register_prompt_versions


class FakeLLM:
    def __init__(self, seq):
        self._seq = list(seq)
        self.prompts = []

    def call_json(self, prompt: str, *, timeout_s: int = 300):  # noqa: ARG002
        self.prompts.append(prompt)
        if not self._seq:
            raise RuntimeError("no more fake llm responses")
        parsed = self._seq.pop(0)
        return LLMCallResult(
            started_at_ts=time.time(),
            finished_at_ts=time.time(),
            prompt=prompt,
            raw_response_text="{}",
            parsed_json=parsed,
            error_code=None,
            error=None,
            provider="fake",
        )


def _plan_gen_payload(plan_id: str):
    return {
        "schema_version": "xiaobo_plan_v1",
        "plan_json": {
            "plan": {
                "plan_id": plan_id,
                "title": "T",
                "root_task_id": "root",
                "created_at": "2026-01-01T00:00:00Z",
                "owner_agent_id": "xiaobo",
                "constraints": {"deadline": None, "priority": "HIGH"},
            },
            "nodes": [
                {
                    "task_id": "root",
                    "plan_id": plan_id,
                    "node_type": "GOAL",
                    "title": "Root Task",
                    "goal_statement": "X",
                    "rationale": "",
                    "owner_agent_id": "xiaobo",
                    "priority": 0,
                    "tags": [],
                }
            ],
            "edges": [],
            "requirements": [],
        },
    }


def _invalid_review_payload():
    # parseable JSON, but violates the required xiaojing_review_v1 contract
    return {"schema_version": "v1", "action_required": "APPROVE", "review_summary": {"total_score": 92}}


def test_invalid_plan_review_does_not_advance_to_next_plan_gen(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(config, "PLAN_PATH_DEFAULT", tmp_path / "plan.json")

    db_path = tmp_path / "state.db"
    conn = connect(db_path)
    apply_migrations(conn, config.MIGRATIONS_DIR)
    prompts = register_prompt_versions(conn, load_prompts(config.PROMPTS_SHARED_PATH, config.PROMPTS_AGENTS_DIR))

    fake = FakeLLM([_plan_gen_payload("p1"), _invalid_review_payload(), _invalid_review_payload()])

    with pytest.raises(PlanWorkflowError):
        generate_and_review_plan(
            conn,
            prompts=prompts,
            llm=fake,
            top_task="Top Task",
            constraints={"deadline": None, "priority": "HIGH"},
            available_skills=[],
            max_plan_attempts=1,
            keep_trying=False,
            max_total_attempts=1,
            max_review_attempts_per_plan=2,
            plan_output_path=tmp_path / "plan.json",
        )

    # 1 PLAN_GEN + 2 PLAN_REVIEW, but NOT the second PLAN_GEN.
    assert len(fake.prompts) == 3
