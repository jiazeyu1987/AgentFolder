from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import config
from core.contracts import normalize_plan_json, normalize_xiaojing_review, validate_xiaojing_review
from core.db import transaction
from core.events import emit_event
from core.llm_calls import record_llm_call
from core.llm_client import LLMClient
from core.models import PlanValidationError, validate_plan_dict
from core.plan_loader import upsert_plan
from core.prompts import PromptBundle, build_xiaobo_plan_prompt, build_xiaojing_plan_review_prompt
from core.reviews import insert_review, write_review_json
from core.util import ensure_dir, stable_hash_text, utc_now_iso


class PlanWorkflowError(RuntimeError):
    pass


@dataclass(frozen=True)
class PlanWorkflowResult:
    plan_json: Dict[str, Any]
    review_json: Dict[str, Any]
    plan_path: Path


def _append_llm_run(path: Path, payload: Dict[str, Any]) -> None:
    ensure_dir(path.parent)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")



def _coerce_required_edge_fields(plan_json: Dict[str, Any]) -> None:
    plan = plan_json.get("plan") or {}
    plan_id = plan.get("plan_id")

    edges = plan_json.get("edges")
    if not isinstance(edges, list):
        return

    def normalize_edge_type(value: Any) -> str:
        if not isinstance(value, str):
            return "DEPENDS_ON"
        t = value.strip().upper()
        aliases = {
            "DEPEND": "DEPENDS_ON",
            "DEPENDS": "DEPENDS_ON",
            "DEPEND_ON": "DEPENDS_ON",
            "DEPENDS-ON": "DEPENDS_ON",
            "DEPENDS ON": "DEPENDS_ON",
            "REQUIRES": "DEPENDS_ON",
            "PREREQ": "DEPENDS_ON",
            "PREREQUISITE": "DEPENDS_ON",
            "DECOMPOSITION": "DECOMPOSE",
            "BREAKDOWN": "DECOMPOSE",
            "CHILD_OF": "DECOMPOSE",
            "PARENT_OF": "DECOMPOSE",
            "ALT": "ALTERNATIVE",
            "ALTERNATE": "ALTERNATIVE",
        }
        t = aliases.get(t, t)
        if t not in {"DECOMPOSE", "DEPENDS_ON", "ALTERNATIVE"}:
            return "DEPENDS_ON"
        return t

    for e in edges:
        if not isinstance(e, dict):
            continue
        if "plan_id" not in e and plan_id is not None:
            e["plan_id"] = plan_id

        e["edge_type"] = normalize_edge_type(e.get("edge_type"))
        metadata = e.get("metadata")
        if metadata is None or not isinstance(metadata, dict):
            metadata = {}
            e["metadata"] = metadata

        if e["edge_type"] == "DECOMPOSE":
            and_or = str(metadata.get("and_or") or "AND").strip().upper()
            if and_or not in {"AND", "OR"}:
                and_or = "AND"
            metadata["and_or"] = and_or
        if e["edge_type"] == "ALTERNATIVE":
            group_id = metadata.get("group_id")
            if not isinstance(group_id, str) or not group_id.strip():
                metadata["group_id"] = "AUTO_GROUP_1"


def _ensure_plan_stub(
    conn: sqlite3.Connection,
    *,
    plan_id: str,
    title: str,
    owner_agent_id: str,
    root_task_id: str,
    constraints: Dict[str, Any],
) -> None:
    """
    Ensure a minimal row exists in plans so task_events FK can be satisfied even if plan.json is invalid.
    """
    row = conn.execute("SELECT 1 FROM plans WHERE plan_id = ?", (plan_id,)).fetchone()
    if row:
        return
    conn.execute(
        """
        INSERT INTO plans(plan_id, title, owner_agent_id, root_task_id, created_at, constraints_json)
        VALUES(?, ?, ?, ?, ?, ?)
        """,
        (plan_id, title, owner_agent_id, root_task_id, utc_now_iso(), json.dumps(constraints or {}, ensure_ascii=False)),
    )
    # Keep DB internally consistent even when we only have a stub plan (e.g. before plan.json passes validation).
    # This avoids orphan plans that later make diagnosis confusing.
    row = conn.execute("SELECT 1 FROM task_nodes WHERE task_id = ?", (root_task_id,)).fetchone()
    if not row:
        conn.execute(
            """
            INSERT INTO task_nodes(
              task_id, plan_id, node_type, title,
              goal_statement, rationale, owner_agent_id,
              priority, status, blocked_reason,
              attempt_count, confidence, active_branch, active_artifact_id,
              created_at, updated_at, tags_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                root_task_id,
                plan_id,
                "GOAL",
                title or "Root Task",
                None,
                "AUTO_STUB (plan stub created for FK safety)",
                owner_agent_id or "xiaobo",
                0,
                "PENDING",
                None,
                0,
                0.5,
                1,
                None,
                utc_now_iso(),
                utc_now_iso(),
                json.dumps(["placeholder", "autofix"], ensure_ascii=False),
            ),
        )


def _load_plan_rubric() -> Dict[str, Any]:
    data = json.loads(config.REVIEW_RUBRIC_PATH.read_text(encoding="utf-8"))
    return data.get("plan_review") or data


def generate_and_review_plan(
    conn: sqlite3.Connection,
    *,
    prompts: PromptBundle,
    llm: LLMClient,
    top_task: str,
    constraints: Optional[Dict[str, Any]] = None,
    available_skills: Optional[List[str]] = None,
    max_plan_attempts: int = 3,
    plan_output_path: Path = config.PLAN_PATH_DEFAULT,
) -> PlanWorkflowResult:
    constraints = constraints or {"deadline": None, "priority": "HIGH"}
    available_skills = available_skills or []
    rubric = _load_plan_rubric()

    last_review: Dict[str, Any] = {}
    for attempt in range(1, max_plan_attempts + 1):
        plan_prompt = build_xiaobo_plan_prompt(prompts, top_task=top_task, constraints=constraints, skills=available_skills)
        plan_res = llm.call_json(plan_prompt)
        record_llm_call(
            conn,
            plan_id=None,
            task_id=None,
            agent="xiaobo",
            scope="PLAN_GEN",
            provider=plan_res.provider,
            prompt_text=plan_prompt,
            response_text=plan_res.raw_response_text,
            started_at_ts=plan_res.started_at_ts,
            finished_at_ts=plan_res.finished_at_ts,
            runtime_context_hash=stable_hash_text(plan_prompt),
            shared_prompt_version=prompts.shared.version,
            shared_prompt_hash=prompts.shared.sha256,
            agent_prompt_version=prompts.xiaobo.version,
            agent_prompt_hash=prompts.xiaobo.sha256,
            parsed_json=plan_res.parsed_json,
            normalized_json=None,
            validator_error=None,
            error_code=plan_res.error_code,
            error_message=plan_res.error,
            meta={"attempt": attempt},
        )
        _append_llm_run(
            config.LLM_RUNS_LOG_PATH,
            {
                "ts": utc_now_iso(),
                "plan_id": None,
                "task_id": None,
                "agent": "xiaobo",
                "shared_prompt_version": prompts.shared.version,
                "shared_prompt_hash": prompts.shared.sha256,
                "agent_prompt_version": prompts.xiaobo.version,
                "agent_prompt_hash": prompts.xiaobo.sha256,
                "runtime_context_hash": stable_hash_text(plan_prompt),
                "final_prompt": plan_prompt,
                "response": plan_res.parsed_json or plan_res.raw_response_text,
                "error": {"code": plan_res.error_code, "message": plan_res.error} if plan_res.error else None,
                "scope": "PLAN_GENERATION",
                "attempt": attempt,
            },
        )
        if plan_res.error or not plan_res.parsed_json:
            raise PlanWorkflowError(f"plan generation failed: {plan_res.error}")

        outer = plan_res.parsed_json
        if outer.get("schema_version") != "xiaobo_plan_v1" or not isinstance(outer.get("plan_json"), dict):
            msg = "plan generation output must be JSON with schema_version=xiaobo_plan_v1 and plan_json object"
            if attempt < max_plan_attempts:
                top_task = top_task + "\n\n" + msg
                continue
            raise PlanWorkflowError(msg)
        plan_json = outer.get("plan_json")  # type: ignore[assignment]

        plan_json = normalize_plan_json(plan_json, top_task=top_task, utc_now_iso=utc_now_iso)
        plan = plan_json.get("plan") or {}
        plan_id = plan.get("plan_id")
        if not isinstance(plan_id, str) or not plan_id:
            raise PlanWorkflowError("plan.plan_id missing after coercion")

        # Ensure FK safety for any subsequent emit_event calls (PLAN_REVIEWED, ERROR, etc.)
        with transaction(conn):
            _ensure_plan_stub(
                conn,
                plan_id=plan_id,
                title=str(plan.get("title") or "Untitled Plan"),
                owner_agent_id=str(plan.get("owner_agent_id") or "xiaobo"),
                root_task_id=str(plan.get("root_task_id") or plan_id),
                constraints=constraints,
            )
        try:
            validate_plan_dict(plan_json)
        except PlanValidationError as exc:
            with transaction(conn):
                emit_event(
                    conn,
                    plan_id=plan_id,
                    event_type="ERROR",
                    payload={"error_code": "PLAN_INVALID", "message": str(exc), "context": {"validator_error": str(exc)}},
                )
            if attempt < max_plan_attempts:
                top_task = top_task + "\n\nPlan JSON schema validation error (must fix):\n" + str(exc)
                continue
            raise PlanWorkflowError(f"PLAN_INVALID: {exc}") from exc
        else:
            # Update latest PLAN_GEN telemetry row with normalized_json (best-effort).
            try:
                conn.execute(
                    """
                    UPDATE llm_calls
                    SET normalized_json = ?
                    WHERE llm_call_id = (
                      SELECT llm_call_id FROM llm_calls
                      WHERE scope='PLAN_GEN'
                      ORDER BY created_at DESC
                      LIMIT 1
                    )
                    """,
                    (json.dumps(plan_json, ensure_ascii=False),),
                )
            except Exception:
                pass

        review_prompt = build_xiaojing_plan_review_prompt(prompts, plan_id=plan_id, rubric_json=rubric, plan_json=plan_json)
        review_res = llm.call_json(review_prompt)
        record_llm_call(
            conn,
            plan_id=plan_id,
            task_id=None,
            agent="xiaojing",
            scope="PLAN_REVIEW",
            provider=review_res.provider,
            prompt_text=review_prompt,
            response_text=review_res.raw_response_text,
            started_at_ts=review_res.started_at_ts,
            finished_at_ts=review_res.finished_at_ts,
            runtime_context_hash=stable_hash_text(review_prompt),
            shared_prompt_version=prompts.shared.version,
            shared_prompt_hash=prompts.shared.sha256,
            agent_prompt_version=prompts.xiaojing.version,
            agent_prompt_hash=prompts.xiaojing.sha256,
            parsed_json=review_res.parsed_json,
            normalized_json=None,
            validator_error=None,
            error_code=review_res.error_code,
            error_message=review_res.error,
            meta={"attempt": attempt, "scope": "PLAN_REVIEW"},
        )
        _append_llm_run(
            config.LLM_RUNS_LOG_PATH,
            {
                "ts": utc_now_iso(),
                "plan_id": plan_id,
                "task_id": None,
                "agent": "xiaojing",
                "shared_prompt_version": prompts.shared.version,
                "shared_prompt_hash": prompts.shared.sha256,
                "agent_prompt_version": prompts.xiaojing.version,
                "agent_prompt_hash": prompts.xiaojing.sha256,
                "runtime_context_hash": stable_hash_text(review_prompt),
                "final_prompt": review_prompt,
                "response": review_res.parsed_json or review_res.raw_response_text,
                "error": {"code": review_res.error_code, "message": review_res.error} if review_res.error else None,
                "scope": "PLAN_REVIEW",
                "attempt": attempt,
            },
        )
        if review_res.error or not review_res.parsed_json:
            if attempt < max_plan_attempts:
                top_task = top_task + "\n\nPlan review failed (must return valid xiaojing_review_v1 JSON). Error:\n" + str(review_res.error or review_res.error_code)
                continue
            raise PlanWorkflowError(f"plan review failed: {review_res.error}")

        review_json = review_res.parsed_json
        if isinstance(review_json, dict):
            review_json = normalize_xiaojing_review(review_json, task_id=plan_id, review_target="PLAN")
        last_review = review_json
        if review_json.get("schema_version") != "xiaojing_review_v1":
            if attempt < max_plan_attempts:
                top_task = top_task + "\n\nPlan review schema_version mismatch (expected xiaojing_review_v1)."
                continue
            raise PlanWorkflowError("plan review schema_version mismatch (expected xiaojing_review_v1)")
        if review_json.get("review_target") != "PLAN":
            if attempt < max_plan_attempts:
                top_task = top_task + "\n\nPlan review_target mismatch (expected PLAN)."
                continue
            raise PlanWorkflowError("plan review_target mismatch (expected PLAN)")
        ok, reason = validate_xiaojing_review(review_json, review_target="PLAN")
        if not ok:
            if attempt < max_plan_attempts:
                top_task = top_task + "\n\nPlan review contract invalid (must fix):\n" + reason
                continue
            raise PlanWorkflowError(f"plan review contract invalid: {reason}")
        else:
            # Update latest PLAN_REVIEW telemetry row with normalized_json and validator_error (best-effort).
            try:
                conn.execute(
                    """
                    UPDATE llm_calls
                    SET normalized_json = ?, validator_error = NULL
                    WHERE llm_call_id = (
                      SELECT llm_call_id FROM llm_calls
                      WHERE scope='PLAN_REVIEW' AND plan_id = ?
                      ORDER BY created_at DESC
                      LIMIT 1
                    )
                    """,
                    (json.dumps(review_json, ensure_ascii=False), plan_id),
                )
            except Exception:
                pass

        total_score = int(review_json.get("total_score") or 0)
        action_required = str(review_json.get("action_required") or "")

        if total_score >= 90 and action_required == "APPROVE":
            ensure_dir(plan_output_path.parent)
            plan_output_path.write_text(json.dumps(plan_json, ensure_ascii=False, indent=2), encoding="utf-8")
            with transaction(conn):
                upsert_plan(conn, plan_json)
                emit_event(conn, plan_id=plan_id, event_type="PLAN_APPROVED", payload={"total_score": total_score})
                # If the plan contains a dedicated CHECK node for plan review, mark it DONE and store the review there.
                for n in plan_json.get("nodes") or []:
                    if not isinstance(n, dict):
                        continue
                    if n.get("node_type") != "CHECK" or n.get("owner_agent_id") != "xiaojing":
                        continue
                    tags = n.get("tags") or []
                    if isinstance(tags, list) and "review" in tags and "plan" in tags:
                        check_task_id = n.get("task_id")
                        if isinstance(check_task_id, str):
                            conn.execute(
                                "UPDATE task_nodes SET status='DONE', blocked_reason=NULL, updated_at=? WHERE task_id=?",
                                (utc_now_iso(), check_task_id),
                            )
                            write_review_json(config.REVIEWS_DIR, task_id=check_task_id, review=review_json)
                            insert_review(conn, plan_id=plan_id, task_id=check_task_id, reviewer_agent_id="xiaojing", review=review_json)
            return PlanWorkflowResult(plan_json=plan_json, review_json=review_json, plan_path=plan_output_path)

        with transaction(conn):
            emit_event(conn, plan_id=plan_id, event_type="PLAN_REVIEWED", payload={"total_score": total_score, "action_required": action_required, "attempt": attempt})

        # Feed reviewer suggestions back into the next attempt by appending them to the top_task.
        suggestions = review_json.get("suggestions") or []
        top_task = top_task + "\n\nReviewer feedback (must address):\n" + json.dumps(suggestions, ensure_ascii=False, indent=2)

    raise PlanWorkflowError(f"plan not approved after {max_plan_attempts} attempts; last_review={json.dumps(last_review, ensure_ascii=False)}")
