import json
import time
import uuid
from pathlib import Path

import pytest

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
            raw_response_text=json.dumps(parsed, ensure_ascii=False),
            parsed_json=parsed,
            error_code=None,
            error=None,
            provider="fake",
        )


def _plan_rubric(top_task_hash: str, pass_score: int = 90):
    return {
        "schema_version": "xiaojing_plan_rubric_v1",
        "top_task_hash": top_task_hash,
        "pass_score": int(pass_score),
        "dimensions": [
            {"dimension": "Completeness", "max_score": 40, "description": "d", "scoring_guide": "g"},
            {"dimension": "Dependency Soundness", "max_score": 25, "description": "d", "scoring_guide": "g"},
            {"dimension": "Executability", "max_score": 20, "description": "d", "scoring_guide": "g"},
            {"dimension": "Clarity", "max_score": 15, "description": "d", "scoring_guide": "g"},
        ],
        "stage_checklists": {"STRUCTURE": ["a"], "BINDINGS": ["b"], "EXECUTION": ["c"]},
    }


def _plan_review(total_score: int, action_required: str = "MODIFY", summary: str = "x"):
    return {
        "schema_version": "xiaojing_review_v1",
        "review_target": "PLAN",
        "task_id": "any",
        "total_score": int(total_score),
        "action_required": str(action_required),
        "summary": str(summary),
        "breakdown": [{"dimension": "overall", "score": int(total_score), "max_score": 100, "issues": []}],
        "suggestions": [],
    }


def _valid_plan_gen_payload(plan_id: str):
    root_id = str(uuid.uuid4())
    return {
        "schema_version": "xiaobo_plan_v1",
        "plan_json": {
            "plan": {
                "plan_id": str(plan_id),
                "title": "T",
                "root_task_id": root_id,
                "created_at": "2026-01-01T00:00:00Z",
                "owner_agent_id": "xiaobo",
                "constraints": {"deadline": None, "priority": "HIGH"},
            },
            "nodes": [
                {
                    "task_id": root_id,
                    "plan_id": str(plan_id),
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


def _cyclic_plan_gen_payload(plan_id: str):
    plan_id = str(plan_id)
    root_id = str(uuid.uuid4())
    a = str(uuid.uuid4())
    b = str(uuid.uuid4())
    e1 = str(uuid.uuid4())
    e2 = str(uuid.uuid4())
    return {
        "schema_version": "xiaobo_plan_v1",
        "plan_json": {
            "plan": {
                "plan_id": plan_id,
                "title": "Cycle",
                "root_task_id": root_id,
                "created_at": "2026-01-01T00:00:00Z",
                "owner_agent_id": "xiaobo",
                "constraints": {"deadline": None, "priority": "HIGH"},
            },
            "nodes": [
                {
                    "task_id": root_id,
                    "plan_id": plan_id,
                    "node_type": "GOAL",
                    "title": "Root Task",
                    "goal_statement": "X",
                    "rationale": "",
                    "owner_agent_id": "xiaobo",
                    "priority": 0,
                    "tags": [],
                },
                {
                    "task_id": a,
                    "plan_id": plan_id,
                    "node_type": "ACTION",
                    "title": "A",
                    "owner_agent_id": "xiaobo",
                    "priority": 0,
                    "tags": [],
                },
                {
                    "task_id": b,
                    "plan_id": plan_id,
                    "node_type": "ACTION",
                    "title": "B",
                    "owner_agent_id": "xiaobo",
                    "priority": 0,
                    "tags": [],
                },
            ],
            "edges": [
                {"edge_id": e1, "plan_id": plan_id, "from_task_id": a, "to_task_id": b, "edge_type": "DEPENDS_ON", "metadata": {}},
                {"edge_id": e2, "plan_id": plan_id, "from_task_id": b, "to_task_id": a, "edge_type": "DEPENDS_ON", "metadata": {}},
            ],
            "requirements": [],
        },
    }


def _fetch_events(conn, job_id: str):
    rows = conn.execute(
        "SELECT event_type, payload_json FROM workflow_events WHERE job_id=? ORDER BY created_at ASC",
        (str(job_id),),
    ).fetchall()
    out = []
    for r in rows:
        payload = {}
        if r["payload_json"]:
            try:
                payload = json.loads(r["payload_json"])
            except Exception:
                payload = {}
        out.append((str(r["event_type"]), payload))
    return out


def test_create_plan_emits_step_and_decision_events(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(config, "PLAN_PATH_DEFAULT", tmp_path / "plan.json")
    monkeypatch.setattr(config, "REVIEW_NOTES_DIR", tmp_path / "review_notes")
    rc_path = tmp_path / "runtime_config.json"
    rc_path.write_text(json.dumps({"plan_review_pass_score": 90}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(config, "RUNTIME_CONFIG_PATH", rc_path)
    from core.runtime_config import reset_runtime_config_cache

    reset_runtime_config_cache()

    db_path = tmp_path / "state.db"
    conn = connect(db_path)
    apply_migrations(conn, config.MIGRATIONS_DIR)
    prompts = register_prompt_versions(conn, load_prompts(config.PROMPTS_SHARED_PATH, config.PROMPTS_AGENTS_DIR))

    from core.util import normalize_title, stable_hash_text

    top_task_hash = stable_hash_text(normalize_title("Top Task"))
    job_id = str(uuid.uuid4())

    fake = FakeLLM(
        [
            _plan_rubric(top_task_hash, pass_score=90),
            _valid_plan_gen_payload(str(uuid.uuid4())),
            _plan_review(95, action_required="APPROVE", summary="ok"),  # STRUCTURE pass
            _plan_review(80, action_required="MODIFY", summary="bindings low"),  # BINDINGS fail => retry attempt
            _valid_plan_gen_payload(str(uuid.uuid4())),
            _plan_review(95, action_required="APPROVE", summary="ok"),
            _plan_review(95, action_required="APPROVE", summary="ok"),  # BINDINGS pass
            _plan_review(95, action_required="APPROVE", summary="ok"),  # EXECUTION pass
        ]
    )

    res = generate_and_review_plan(
        conn,
        prompts=prompts,
        llm=fake,
        top_task="Top Task",
        constraints={"deadline": None, "priority": "HIGH"},
        available_skills=[],
        max_plan_attempts=3,
        keep_trying=False,
        max_total_attempts=3,
        max_review_attempts_per_plan=2,
        job_id=job_id,
        plan_output_path=tmp_path / "plan.json",
    )
    assert res.plan_json["plan"]["plan_id"]

    events = _fetch_events(conn, job_id)
    steps = [p.get("step") for (t, p) in events if t == "STEP_STARTED"]
    assert "PLAN_RUBRIC" in steps
    assert "STRUCTURE_GEN" in steps
    assert "STRUCTURE_REVIEW" in steps
    assert "BINDINGS_APPLY" in steps
    assert "BINDINGS_REVIEW" in steps
    assert "EXECUTION_SNAPSHOT" in steps
    assert "EXECUTION_REVIEW" in steps
    assert "FINALIZE" in steps

    decisions = [(t, p) for (t, p) in events if t == "DECISION_MADE"]
    assert any(p.get("stage") == "BINDINGS" and p.get("why") == "SCORE_BELOW_THRESHOLD" for _, p in decisions)


def test_invalid_plan_gen_cycle_emits_error_and_decision(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(config, "PLAN_PATH_DEFAULT", tmp_path / "plan.json")
    rc_path = tmp_path / "runtime_config.json"
    rc_path.write_text(json.dumps({"plan_review_pass_score": 90}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(config, "RUNTIME_CONFIG_PATH", rc_path)
    from core.runtime_config import reset_runtime_config_cache

    reset_runtime_config_cache()

    db_path = tmp_path / "state.db"
    conn = connect(db_path)
    apply_migrations(conn, config.MIGRATIONS_DIR)
    prompts = register_prompt_versions(conn, load_prompts(config.PROMPTS_SHARED_PATH, config.PROMPTS_AGENTS_DIR))

    from core.util import normalize_title, stable_hash_text

    top_task_hash = stable_hash_text(normalize_title("Top Task"))
    job_id = str(uuid.uuid4())

    fake = FakeLLM(
        [
            _plan_rubric(top_task_hash, pass_score=90),
            _cyclic_plan_gen_payload(str(uuid.uuid4())),  # invalid => retry attempt
            _valid_plan_gen_payload(str(uuid.uuid4())),
            _plan_review(95, action_required="APPROVE", summary="ok"),
            _plan_review(95, action_required="APPROVE", summary="ok"),
            _plan_review(95, action_required="APPROVE", summary="ok"),
        ]
    )

    res = generate_and_review_plan(
        conn,
        prompts=prompts,
        llm=fake,
        top_task="Top Task",
        constraints={"deadline": None, "priority": "HIGH"},
        available_skills=[],
        max_plan_attempts=3,
        keep_trying=False,
        max_total_attempts=3,
        max_review_attempts_per_plan=2,
        job_id=job_id,
        plan_output_path=tmp_path / "plan.json",
    )
    assert res.plan_json["plan"]["plan_id"]

    events = _fetch_events(conn, job_id)
    # Expect a schema validation error decision caused by cycle detection.
    assert any(t == "ERROR_RAISED" and "cycle" in str(p.get("validator_error", "")).lower() for t, p in events)
    assert any(t == "DECISION_MADE" and p.get("why") == "PLAN_INVALID" for t, p in events)

