import json
import time
from pathlib import Path

import config
from core.db import apply_migrations, connect
from core.llm_client import LLMCallResult
from core.plan_workflow import generate_and_review_plan
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
            "plan": {"plan_id": plan_id, "title": "T", "root_task_id": "root", "created_at": "2026-01-01T00:00:00Z", "owner_agent_id": "xiaobo", "constraints": {"deadline": None, "priority": "HIGH"}},
            "nodes": [{"task_id": "root", "plan_id": plan_id, "node_type": "GOAL", "title": "Root Task", "goal_statement": "X", "rationale": "", "owner_agent_id": "xiaobo", "priority": 0, "tags": []}],
            "edges": [],
            "requirements": [],
        },
    }


def _plan_review_modify():
    return {
        "schema_version": "xiaojing_review_v1",
        "review_target": "PLAN",
        "task_id": "any",
        "total_score": 10,
        "action_required": "MODIFY",
        "summary": "need improvements",
        "breakdown": [{"dimension": "overall", "score": 10, "max_score": 100, "issues": []}],
        "suggestions": [
            {
                "priority": "HIGH",
                "problem": "missing testing",
                "change": "add a testing node",
                "steps": ["add ACTION Testing & Verification", "connect deps"],
                "acceptance_criteria": "plan includes explicit test step",
            }
        ],
    }


def _plan_review_approve():
    return {
        "schema_version": "xiaojing_review_v1",
        "review_target": "PLAN",
        "task_id": "any",
        "total_score": 95,
        "action_required": "APPROVE",
        "summary": "ok",
        "breakdown": [{"dimension": "overall", "score": 95, "max_score": 100, "issues": []}],
        "suggestions": [],
    }


def test_plan_review_generates_bounded_notes_and_feeds_next_gen(tmp_path: Path, monkeypatch):
    # isolate workspace review notes
    monkeypatch.setattr(config, "REVIEW_NOTES_DIR", tmp_path / "review_notes")
    monkeypatch.setattr(config, "PLAN_PATH_DEFAULT", tmp_path / "plan.json")

    db_path = tmp_path / "state.db"
    conn = connect(db_path)
    apply_migrations(conn, config.MIGRATIONS_DIR)
    prompts = register_prompt_versions(conn, load_prompts(config.PROMPTS_SHARED_PATH, config.PROMPTS_AGENTS_DIR))

    fake = FakeLLM(
        [
            _plan_gen_payload("p1"),
            _plan_review_modify(),
            _plan_gen_payload("p2"),
            _plan_review_approve(),
        ]
    )

    generate_and_review_plan(
        conn,
        prompts=prompts,
        llm=fake,
        top_task="Top Task",
        constraints={"deadline": None, "priority": "HIGH"},
        available_skills=[],
        max_plan_attempts=3,
        keep_trying=False,
        max_total_attempts=3,
        plan_output_path=tmp_path / "plan.json",
    )

    # There should be a remediation note file for attempt 1 (plan_id may be coerced by contracts).
    note_paths = list((tmp_path / "review_notes").glob("*/plan_review_attempt_1.md"))
    assert len(note_paths) == 1
    note = note_paths[0].read_text(encoding="utf-8")
    assert len(note) <= 500

    # The second PLAN_GEN prompt must include this note in RUNTIME_CONTEXT_JSON.
    # fake.prompts[0] = PLAN_GEN attempt1, fake.prompts[1] = PLAN_REVIEW attempt1,
    # fake.prompts[2] = PLAN_GEN attempt2
    prompt2 = fake.prompts[2]
    assert "review_notes" in prompt2
    # Extract RUNTIME_CONTEXT_JSON and compare normalized value.
    marker = "RUNTIME_CONTEXT_JSON:"
    assert marker in prompt2
    ctx_text = prompt2.split(marker, 1)[1].strip()
    ctx = json.loads(ctx_text)
    assert ctx.get("review_notes") == note
