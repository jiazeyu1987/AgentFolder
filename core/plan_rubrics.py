from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from core.audit_log import annotate_llm_output_for_retry
from core.contracts_v2 import format_contract_error_short, normalize_and_validate
from core.llm_calls import record_llm_call
from core.llm_client import LLMClient
from core.prompts import PromptBundle, build_xiaojing_plan_rubric_prompt
from core.util import utc_now_iso


@dataclass(frozen=True)
class PlanRubric:
    rubric_id: str
    top_task_hash: str
    top_task_title: str
    plan_id: Optional[str]
    schema_version: str
    pass_score: int
    rubric: Dict[str, Any]
    created_at: str


def get_latest_rubric(conn: sqlite3.Connection, *, top_task_hash: str) -> Optional[PlanRubric]:
    row = conn.execute(
        """
        SELECT rubric_id, created_at, top_task_hash, top_task_title, plan_id, schema_version, pass_score, rubric_json
        FROM plan_review_rubrics
        WHERE top_task_hash = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (str(top_task_hash),),
    ).fetchone()
    if not row:
        return None
    try:
        obj = json.loads(row["rubric_json"] or "{}")
        if not isinstance(obj, dict):
            obj = {}
    except Exception:
        obj = {}
    return PlanRubric(
        rubric_id=str(row["rubric_id"]),
        created_at=str(row["created_at"]),
        top_task_hash=str(row["top_task_hash"]),
        top_task_title=str(row["top_task_title"]),
        plan_id=str(row["plan_id"]) if row["plan_id"] else None,
        schema_version=str(row["schema_version"]),
        pass_score=int(row["pass_score"] or 0),
        rubric=obj,
    )


def save_rubric(
    conn: sqlite3.Connection,
    *,
    top_task_hash: str,
    top_task_title: str,
    pass_score: int,
    rubric_obj: Dict[str, Any],
    plan_id: Optional[str] = None,
) -> PlanRubric:
    rubric_id = str(uuid.uuid4())
    created_at = utc_now_iso()
    schema_version = str(rubric_obj.get("schema_version") or "xiaojing_plan_rubric_v1")
    conn.execute(
        """
        INSERT INTO plan_review_rubrics(
          rubric_id, created_at, top_task_hash, top_task_title, plan_id, schema_version, pass_score, rubric_json
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            rubric_id,
            created_at,
            str(top_task_hash),
            str(top_task_title),
            str(plan_id) if plan_id else None,
            schema_version,
            int(pass_score),
            json.dumps(rubric_obj, ensure_ascii=False),
        ),
    )
    return PlanRubric(
        rubric_id=rubric_id,
        created_at=created_at,
        top_task_hash=str(top_task_hash),
        top_task_title=str(top_task_title),
        plan_id=str(plan_id) if plan_id else None,
        schema_version=schema_version,
        pass_score=int(pass_score),
        rubric=rubric_obj,
    )


def attach_rubric_to_plan(conn: sqlite3.Connection, *, rubric_id: str, plan_id: str) -> None:
    conn.execute("UPDATE plan_review_rubrics SET plan_id=? WHERE rubric_id=? AND (plan_id IS NULL OR plan_id='')", (str(plan_id), str(rubric_id)))


def _build_rubric_retry_prompt(*, original_prompt: str, invalid_response: str, reason: str) -> str:
    return (
        original_prompt.strip()
        + "\n\n"
        + "[RETRY_NOTES]\n"
        + "Your previous output did not satisfy the contract. Fix the JSON and re-send ONLY the JSON.\n"
        + f"Reason: {reason}\n"
        + "Previous invalid response (do not repeat; fix it):\n"
        + invalid_response.strip()
        + "\n"
    )


def ensure_plan_rubric(
    conn: sqlite3.Connection,
    *,
    prompts: PromptBundle,
    llm: LLMClient,
    top_task: str,
    top_task_hash: str,
    pass_score: int,
    base_rubric_json: Dict[str, Any],
    max_attempts: int = 4,
    keep_trying: bool = False,
    job_id: Optional[str] = None,
) -> Tuple[PlanRubric, str]:
    """
    Phase-1 rubric: define the scoring standard first, then reuse it for all PLAN_REVIEW stages.
    Returns: (rubric, llm_call_id_that_created_or_reused)
    """
    existing = get_latest_rubric(conn, top_task_hash=top_task_hash)
    if existing:
        return existing, "REUSED"

    prompt = build_xiaojing_plan_rubric_prompt(
        prompts,
        top_task=top_task,
        top_task_hash=top_task_hash,
        pass_score=int(pass_score),
        base_rubric_json=base_rubric_json,
    )
    prompt_to_use = prompt
    attempt = 0
    last_call_id = "UNKNOWN"
    rubric_obj: Dict[str, Any] = {}
    while True:
        attempt += 1
        try:
            from core.workflow_events import emit_workflow_event

            emit_workflow_event(
                conn,
                workflow="CREATE_PLAN",
                event_type="LLM_CALL_REQUESTED",
                severity="INFO",
                message="PLAN_RUBRIC: request",
                job_id=job_id,
                top_task_hash=top_task_hash,
                payload={
                    "step": "PLAN_RUBRIC",
                    "agent": "xiaojing",
                    "scope": "PLAN_RUBRIC",
                    "rubric_attempt": int(attempt),
                },
            )
        except Exception:
            pass
        res = llm.call_json(prompt_to_use)
        last_call_id = record_llm_call(
            conn,
            plan_id=None,
            task_id=None,
            top_task_hash=top_task_hash,
            agent="xiaojing",
            scope="PLAN_RUBRIC",
            provider=res.provider,
            prompt_text=prompt_to_use,
            response_text=res.raw_response_text or "",
            started_at_ts=getattr(res, "started_at_ts", None),
            finished_at_ts=getattr(res, "finished_at_ts", None),
            parsed_json=res.parsed_json if isinstance(res.parsed_json, dict) else None,
            meta={"rubric_attempt": int(attempt), "stage": "STRUCTURE", "stage_attempt": 1},
        )
        try:
            from core.workflow_events import emit_workflow_event

            emit_workflow_event(
                conn,
                workflow="CREATE_PLAN",
                event_type="LLM_CALL_RECORDED",
                severity="INFO",
                message="PLAN_RUBRIC: recorded",
                job_id=job_id,
                top_task_hash=top_task_hash,
                llm_call_id=str(last_call_id),
                payload={
                    "step": "PLAN_RUBRIC",
                    "agent": "xiaojing",
                    "scope": "PLAN_RUBRIC",
                    "rubric_attempt": int(attempt),
                    "llm_call_id": str(last_call_id),
                },
            )
        except Exception:
            pass

        rubric_obj, err = normalize_and_validate("PLAN_RUBRIC", res.parsed_json, {"top_task_hash": top_task_hash, "pass_score": int(pass_score)})
        if isinstance(rubric_obj, dict) and not err:
            try:
                conn.execute(
                    "UPDATE llm_calls SET normalized_json=?, validator_error=NULL WHERE llm_call_id=?",
                    (json.dumps(rubric_obj, ensure_ascii=False), str(last_call_id)),
                )
            except Exception:
                pass
            saved = save_rubric(
                conn,
                top_task_hash=top_task_hash,
                top_task_title=top_task,
                pass_score=int(pass_score),
                rubric_obj=rubric_obj,
                plan_id=None,
            )
            return saved, str(last_call_id)

        reason = format_contract_error_short(err or {}) if err else "invalid rubric"
        try:
            conn.execute("UPDATE llm_calls SET validator_error=? WHERE llm_call_id=?", (reason, str(last_call_id)))
            annotate_llm_output_for_retry(conn, llm_call_id=str(last_call_id), retry_kind="CONTRACT_MISMATCH", retry_reason=reason)
        except Exception:
            pass
        try:
            from core.workflow_events import emit_workflow_event

            emit_workflow_event(
                conn,
                workflow="CREATE_PLAN",
                event_type="DECISION_MADE",
                severity="WARN",
                message="PLAN_RUBRIC invalid; retry",
                job_id=job_id,
                top_task_hash=top_task_hash,
                llm_call_id=str(last_call_id),
                payload={
                    "step": "PLAN_RUBRIC",
                    "rubric_attempt": int(attempt),
                    "why": "CONTRACT_MISMATCH",
                    "retry_reason": str(reason)[:300],
                    "next": "RETRY_RUBRIC",
                },
            )
        except Exception:
            pass

        prompt_to_use = _build_rubric_retry_prompt(original_prompt=prompt, invalid_response=res.raw_response_text or "", reason=reason)
        if (not keep_trying) and attempt >= max(1, int(max_attempts)):
            raise RuntimeError("PLAN_RUBRIC invalid after retries")
