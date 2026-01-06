import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

import pytest

import config
from core.db import apply_migrations
from core.llm_client import LLMCallResult
from core.plan_workflow import generate_and_review_plan
from core.prompts import PromptBundle, PromptDoc
from core.util import normalize_title, stable_hash_text


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_migrations(conn, config.MIGRATIONS_DIR)
    return conn


@dataclass
class _Queued:
    payload: Dict[str, Any]


class FakeLLM:
    def __init__(self, queue: List[_Queued]) -> None:
        self._q = list(queue)

    def call_json(self, prompt: str, *, timeout_s: int = 300) -> LLMCallResult:  # noqa: ARG002
        if not self._q:
            raise AssertionError("FakeLLM queue exhausted")
        payload = self._q.pop(0).payload
        now = time.time()
        return LLMCallResult(
            started_at_ts=now,
            finished_at_ts=now,
            prompt=prompt,
            raw_response_text=json.dumps(payload, ensure_ascii=False),
            parsed_json=payload,
            error_code=None,
            error=None,
            provider="fake",
        )


def _prompt_bundle(tmp_path: Path) -> PromptBundle:
    def doc(name: str) -> PromptDoc:
        p = tmp_path / f"{name}.md"
        p.write_text(name, encoding="utf-8")
        return PromptDoc(path=p, content=name, version=f"{name}_vtest", sha256=name)

    return PromptBundle(shared=doc("shared"), xiaobo=doc("xiaobo"), xiaojing=doc("xiaojing"), xiaoxie=doc("xiaoxie"), variants={})


def _rubric(*, top_task_hash: str, pass_score: int = 90) -> Dict[str, Any]:
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
        "stage_checklists": {"STRUCTURE": [], "BINDINGS": [], "EXECUTION": []},
    }


def _plan_wrapper(*, plan_id: str, root_task_id: str) -> Dict[str, Any]:
    goal_id = root_task_id
    action_id = str(uuid.uuid4())
    requirement_id = str(uuid.uuid4())
    now = "2026-01-01T00:00:00Z"
    return {
        "schema_version": "xiaobo_plan_v1",
        "plan_json": {
            "plan": {
                "plan_id": plan_id,
                "title": "t",
                "owner_agent_id": "xiaobo",
                "root_task_id": goal_id,
                "created_at": now,
                "constraints": {},
            },
            "nodes": [
                {"task_id": goal_id, "plan_id": plan_id, "node_type": "GOAL", "title": "g", "owner_agent_id": "xiaobo", "priority": 1, "tags": []},
                {"task_id": action_id, "plan_id": plan_id, "node_type": "ACTION", "title": "a", "owner_agent_id": "xiaobo", "priority": 1, "tags": []},
            ],
            "edges": [],
            "requirements": [
                {
                    "requirement_id": requirement_id,
                    "task_id": action_id,
                    "name": "input",
                    "kind": "FILE",
                    "required": 1,
                    "min_count": 1,
                    "allowed_types": ["txt"],
                    "source": "USER",
                    "validation": {},
                }
            ],
        },
    }


def _review(*, plan_id: str, target: str, score: int) -> Dict[str, Any]:
    action = "APPROVE" if score >= 90 else "MODIFY"
    return {
        "schema_version": "xiaojing_review_v1",
        "task_id": plan_id,
        "review_target": target,
        "total_score": int(score),
        "action_required": action,
        "summary": "s",
        "breakdown": [{"dimension": "d", "score": int(score), "max_score": 100, "issues": []}],
        "suggestions": [{"priority": "LOW", "change": "c", "steps": [], "acceptance_criteria": "a"}],
    }


@pytest.fixture()
def patched_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, "LLM_RUNS_LOG_PATH", tmp_path / "llm_runs.jsonl")
    monkeypatch.setattr(config, "REVIEW_NOTES_DIR", tmp_path / "review_notes")
    monkeypatch.setattr(config, "REVIEWS_DIR", tmp_path / "reviews")
    monkeypatch.setattr(config, "ARTIFACTS_DIR", tmp_path / "artifacts")
    monkeypatch.setattr(config, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(config, "INPUTS_DIR", tmp_path / "inputs")
    return tmp_path


def test_auto_seed_entry_user_file_inputs_creates_evidence(patched_paths: Path) -> None:
    conn = _make_conn()
    prompts = _prompt_bundle(patched_paths)

    plan_id = str(uuid.uuid4())
    root_task_id = str(uuid.uuid4())
    top_task = "my top task title"
    top_task_hash = stable_hash_text(normalize_title(top_task))

    llm = FakeLLM(
        [
            _Queued(_rubric(top_task_hash=top_task_hash)),
            _Queued(_plan_wrapper(plan_id=plan_id, root_task_id=root_task_id)),
            _Queued(_review(plan_id=plan_id, target="PLAN", score=95)),
            _Queued(_review(plan_id=plan_id, target="PLAN", score=95)),
            _Queued(_review(plan_id=plan_id, target="PLAN", score=95)),
        ]
    )

    generate_and_review_plan(
        conn,
        prompts=prompts,
        llm=llm,  # type: ignore[arg-type]
        top_task=top_task,
        max_plan_attempts=1,
        keep_trying=False,
        plan_output_path=patched_paths / "tasks" / "plan.json",
    )

    row = conn.execute(
        """
        SELECT r.requirement_id, r.name
        FROM input_requirements r
        JOIN task_nodes tn ON tn.task_id = r.task_id
        WHERE tn.plan_id = ? AND r.kind='FILE' AND r.source='USER' AND r.required=1
        LIMIT 1
        """,
        (plan_id,),
    ).fetchone()
    assert row, "expected at least one USER FILE requirement"
    rid = row["requirement_id"]
    cnt = conn.execute("SELECT COUNT(1) FROM evidences WHERE requirement_id=?", (rid,)).fetchone()[0]
    assert int(cnt) >= 1
    assert (patched_paths / "inputs").exists()
