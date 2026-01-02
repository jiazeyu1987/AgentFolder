from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import config
from core.contracts_v2 import format_contract_error_short, normalize_and_validate
from core.db import transaction
from core.events import emit_event
from core.llm_calls import record_llm_call
from core.llm_client import LLMClient
from core.models import validate_plan_dict
from core.plan_loader import upsert_plan
from core.prompts import PromptBundle, build_xiaobo_plan_prompt, build_xiaojing_plan_review_prompt
from core.runtime_config import get_runtime_config
from core.reviews import insert_review, write_review_json
from core.util import ensure_dir, stable_hash_text, utc_now_iso


class PlanWorkflowError(RuntimeError):
    pass


class PlanNotApprovedError(PlanWorkflowError):
    def __init__(self, *, plan_id: Optional[str], max_attempts: int, last_review: Dict[str, Any]) -> None:
        super().__init__("plan not approved")
        self.plan_id = plan_id
        self.max_attempts = int(max_attempts)
        self.last_review = last_review


def _limit_chars(text: str, *, max_chars: int) -> str:
    s = (text or "").strip()
    if max_chars <= 0 or len(s) <= max_chars:
        return s
    if max_chars < 16:
        return s[:max_chars]
    return s[: max_chars - 12] + "…[TRUNCATED]"


def _build_plan_remediation_note(review: Dict[str, Any], *, max_chars: int = 500) -> str:
    """
    Build a concise remediation note for the generator (xiaobo) based on a non-approved plan review.
    Hard cap: <= max_chars characters.
    """
    score = int(review.get("total_score") or 0)
    action = str(review.get("action_required") or "MODIFY")
    summary = str(review.get("summary") or "").strip()

    lines: List[str] = []
    lines.append(f"PLAN_REVIEW 未通过：score={score} action_required={action}")
    if summary:
        lines.append(f"问题：{summary}")

    sugs = review.get("suggestions")
    if isinstance(sugs, list) and sugs:
        lines.append("整改要求：")
        for s in sugs:
            if not isinstance(s, dict):
                continue
            pr = str(s.get("priority") or "").strip() or "MED"
            problem = str(s.get("problem") or "").strip()
            change = str(s.get("change") or "").strip()
            steps = s.get("steps")
            ac = str(s.get("acceptance_criteria") or "").strip()

            lines.append(f"- {pr}: {change or problem or '修改计划以满足评审要求'}")
            if isinstance(steps, list) and steps:
                for st in steps[:6]:
                    st2 = str(st or "").strip()
                    if st2:
                        lines.append(f"  * {st2}")
            if ac:
                lines.append(f"  验收：{ac}")

    note = "\n".join(lines).strip()
    return _limit_chars(note, max_chars=max_chars)


def _summarize_plan_review(review: Dict[str, Any]) -> str:
    cfg = get_runtime_config()
    score = int(review.get("total_score") or 0)
    action = str(review.get("action_required") or "")
    summary = str(review.get("summary") or "").strip()

    # Prefer richer fields if provided by certain reviewer variants.
    eval_obj = review.get("evaluation")
    if isinstance(eval_obj, dict):
        s2 = str(eval_obj.get("summary") or "").strip()
        if s2:
            summary = s2

    dims: list[str] = []
    dim_scores = review.get("dimension_scores")
    if isinstance(dim_scores, list):
        for d in dim_scores:
            if not isinstance(d, dict):
                continue
            dim = str(d.get("dimension") or "").strip()
            sc = d.get("score")
            try:
                sc_i = int(sc)
            except Exception:
                sc_i = None
            if dim and sc_i is not None:
                dims.append(f"{dim}:{sc_i}")
    if not dims:
        scores = review.get("scores")
        if isinstance(scores, dict):
            for k, v in list(scores.items())[:6]:
                try:
                    dims.append(f"{k}:{int(v)}")
                except Exception:
                    continue

    sug_lines: list[str] = []
    sugs = review.get("suggestions")
    if isinstance(sugs, list):
        for s in sugs[:5]:
            if not isinstance(s, dict):
                continue
            pr = str(s.get("priority") or "").strip()
            change = str(s.get("change") or "").strip()
            if change:
                sug_lines.append(f"- {pr or 'MED'}: {change}")

    parts: list[str] = []
    parts.append(f"Plan 审核未通过：score={score}（门槛>={cfg.plan_review_pass_score}），action_required={action or 'MODIFY'}")
    if summary:
        parts.append(f"主要原因：{summary}")
    if dims:
        parts.append("维度分： " + ", ".join(dims))
    if sug_lines:
        parts.append("修改建议（前几条）：")
        parts.extend(sug_lines)
    parts.append("说明：这是“计划质量/可执行性”问题，不是数据结构解析错误。")
    parts.append("查看完整输入输出：UI -> LLM Explorer，或 `agent_cli.py llm-calls --plan-id <PLAN_ID>`。")
    return "\n".join(parts)


def _review_invalid_reason(*, review_json: Dict[str, Any], expected_target: str) -> str:
    """
    Build a concrete, user-readable reason for why a review JSON is invalid.
    """
    sv = review_json.get("schema_version")
    rt = review_json.get("review_target")
    keys = sorted([k for k in review_json.keys() if isinstance(k, str)])
    head = f"invalid review JSON: schema_version={sv!r}, review_target={rt!r}, keys={keys[:25]}"
    contract = "PLAN_REVIEW" if expected_target == "PLAN" else "TASK_CHECK"
    _, err = normalize_and_validate(contract, review_json, {"task_id": review_json.get("task_id")})
    if not err:
        return head
    return head + f"; validator_error={format_contract_error_short(err)}"


def _build_review_retry_prompt(*, original_prompt: str, invalid_response: str, reason: str) -> str:
    """
    Ask the reviewer to re-emit a valid `xiaojing_review_v1` JSON object.
    """
    return (
        "Your previous output could NOT be accepted by the automation system.\n"
        f"Reason: {reason}\n"
        "\n"
        "Please output a single VALID JSON object only, matching schema `xiaojing_review_v1` exactly.\n"
        "Do not use markdown fences. Do not include extra text.\n"
        "\n"
        "ORIGINAL_REVIEW_PROMPT_START\n"
        f"{original_prompt}\n"
        "ORIGINAL_REVIEW_PROMPT_END\n"
        "\n"
        "YOUR_INVALID_OUTPUT_START\n"
        f"{invalid_response}\n"
        "YOUR_INVALID_OUTPUT_END\n"
    )


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
    keep_trying: bool = False,
    max_total_attempts: Optional[int] = None,
    max_review_attempts_per_plan: int = 4,
    plan_output_path: Path = config.PLAN_PATH_DEFAULT,
) -> PlanWorkflowResult:
    constraints = constraints or {"deadline": None, "priority": "HIGH"}
    available_skills = available_skills or []
    rubric = _load_plan_rubric()
    started_at_ts = time.time()

    # IMPORTANT: keep user intent stable. Retry feedback should not be appended into the "top_task" that
    # becomes plan title/root goal_statement, otherwise the plan can include embedded JSON feedback.
    user_top_task = top_task
    gen_notes = ""
    review_notes = ""

    last_review: Dict[str, Any] = {}
    if max_total_attempts is None:
        max_total_attempts = int(max_plan_attempts)
    max_total_attempts = max(1, int(max_total_attempts))

    attempt = 0
    while True:
        attempt += 1
        if attempt > max_total_attempts:
            raise PlanNotApprovedError(plan_id=locals().get("plan_id"), max_attempts=max_total_attempts, last_review=last_review)

        plan_prompt = build_xiaobo_plan_prompt(
            prompts,
            top_task=user_top_task,
            constraints=constraints,
            skills=available_skills,
            review_notes=review_notes,
            gen_notes=gen_notes,
        )
        plan_res = llm.call_json(plan_prompt)
        # NOTE: plan_workflow doesn't track llm_calls budget, but we still record extra_calls in telemetry/logs via meta.
        plan_gen_call_id = record_llm_call(
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
            meta={"attempt": attempt, "extra_calls": int(getattr(plan_res, "extra_calls", 0)), "repair_used": bool(getattr(plan_res, "repair_used", False))},
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
            gen_notes = _limit_chars(
                "Plan generation failed (must return valid xiaobo_plan_v1 JSON). Error:\n" + str(plan_res.error or plan_res.error_code),
                max_chars=500,
            )
            if keep_trying or attempt < max_plan_attempts:
                continue
            raise PlanWorkflowError(f"plan generation failed: {plan_res.error}")

        outer = plan_res.parsed_json
        if outer.get("schema_version") != "xiaobo_plan_v1" or not isinstance(outer.get("plan_json"), dict):
            msg = "plan generation output must be JSON with schema_version=xiaobo_plan_v1 and plan_json object"
            gen_notes = msg
            if keep_trying or attempt < max_plan_attempts:
                continue
            raise PlanWorkflowError(msg)
        plan_json = outer.get("plan_json")  # type: ignore[assignment]

        # Normalize+validate with the original user top task, not the retry feedback.
        plan_json, plan_err = normalize_and_validate("PLAN_GEN", plan_json, {"top_task": user_top_task, "utc_now_iso": utc_now_iso})
        if plan_err:
            msg = format_contract_error_short(plan_err)
            with transaction(conn):
                emit_event(
                    conn,
                    plan_id=str(plan_id or "UNKNOWN"),
                    event_type="ERROR",
                    payload={"error_code": "PLAN_INVALID", "message": msg, "context": {"validator_error": msg, "validator_error_obj": plan_err}},
                )
            gen_notes = _limit_chars("Plan JSON schema validation error (must fix):\n" + msg, max_chars=500)
            if keep_trying or attempt < max_plan_attempts:
                continue
            raise PlanWorkflowError(f"PLAN_INVALID: {msg}")
        plan = plan_json.get("plan") or {}
        plan_id = plan.get("plan_id")
        if not isinstance(plan_id, str) or not plan_id:
            raise PlanWorkflowError("plan.plan_id missing after coercion")

        # Back-fill PLAN_GEN telemetry row with resolved plan_id (so UI can show plan_title/plan_id).
        if plan_gen_call_id and plan_gen_call_id != "UNKNOWN":
            try:
                conn.execute("UPDATE llm_calls SET plan_id=? WHERE llm_call_id=?", (plan_id, plan_gen_call_id))
            except Exception:
                pass

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
        # Keep audit_events consistent: PLAN_GEN was logged before plan_id/title existed.
        if plan_gen_call_id and plan_gen_call_id != "UNKNOWN":
            try:
                from core.audit_log import backfill_audit_llm_call_plan_id

                backfill_audit_llm_call_plan_id(conn, llm_call_id=str(plan_gen_call_id), plan_id=str(plan_id))
            except Exception:
                pass
        # Update latest PLAN_GEN telemetry row with normalized_json (best-effort).
        try:
            conn.execute(
                """
                UPDATE llm_calls
                SET normalized_json = ?, validator_error = NULL
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
        review_prompt_to_use = review_prompt
        review_json: Dict[str, Any] = {}

        # Review output can be unstable; retry reviewer until we get a valid `xiaojing_review_v1` JSON.
        # IMPORTANT: if review is still invalid after retries, do NOT proceed to next PLAN_GEN attempt,
        # otherwise we lose remediation info and just "self-soothe" by re-reviewing.
        review_attempt = 0
        while True:
            review_attempt += 1
            if time.time() - started_at_ts > float(config.MAX_PLAN_RUNTIME_SECONDS):
                with transaction(conn):
                    emit_event(
                        conn,
                        plan_id=plan_id,
                        event_type="ERROR",
                        payload={
                            "error_code": "PLAN_REVIEW_TIMEOUT",
                            "message": "PLAN_REVIEW timed out before producing a valid review JSON.",
                            "context": {
                                "attempt": attempt,
                                "review_attempt": review_attempt,
                                "hint": "Open UI -> LLM Workflow to inspect the last PLAN_GEN/PLAN_REVIEW calls; consider increasing MAX_PLAN_RUNTIME_SECONDS if LLM is slow.",
                            },
                        },
                    )
                raise PlanWorkflowError("plan review still invalid (timeout); see llm_calls/LLM Workflow for details")
            review_res = llm.call_json(review_prompt_to_use)
            review_call_id = record_llm_call(
                conn,
                plan_id=plan_id,
                task_id=None,
                agent="xiaojing",
                scope="PLAN_REVIEW",
                provider=review_res.provider,
                prompt_text=review_prompt_to_use,
                response_text=review_res.raw_response_text,
                started_at_ts=review_res.started_at_ts,
                finished_at_ts=review_res.finished_at_ts,
                runtime_context_hash=stable_hash_text(review_prompt_to_use),
                shared_prompt_version=prompts.shared.version,
                shared_prompt_hash=prompts.shared.sha256,
                agent_prompt_version=prompts.xiaojing.version,
                agent_prompt_hash=prompts.xiaojing.sha256,
                parsed_json=review_res.parsed_json,
                normalized_json=None,
                validator_error=None,
                error_code=review_res.error_code,
                error_message=review_res.error,
                meta={
                    "attempt": attempt,
                    "review_attempt": review_attempt,
                    "scope": "PLAN_REVIEW",
                    "extra_calls": int(getattr(review_res, "extra_calls", 0)),
                    "repair_used": bool(getattr(review_res, "repair_used", False)),
                },
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
                    "runtime_context_hash": stable_hash_text(review_prompt_to_use),
                    "final_prompt": review_prompt_to_use,
                    "response": review_res.parsed_json or review_res.raw_response_text,
                    "error": {"code": review_res.error_code, "message": review_res.error} if review_res.error else None,
                    "scope": "PLAN_REVIEW",
                    "attempt": attempt,
                    "review_attempt": review_attempt,
                },
            )

            if review_res.error or not isinstance(review_res.parsed_json, dict):
                reason = str(review_res.error or review_res.error_code or "review_unparseable")
                # Persist why we are retrying in telemetry for observability.
                try:
                    conn.execute("UPDATE llm_calls SET validator_error=? WHERE llm_call_id=?", (_limit_chars(reason, max_chars=500), review_call_id))
                    from core.audit_log import annotate_llm_output_for_retry

                    annotate_llm_output_for_retry(conn, llm_call_id=review_call_id, retry_kind="PARSE_ERROR", retry_reason=reason)
                except Exception:
                    pass
                review_prompt_to_use = _build_review_retry_prompt(original_prompt=review_prompt, invalid_response=review_res.raw_response_text, reason=reason)
                if (not keep_trying) and review_attempt >= max(1, int(max_review_attempts_per_plan)):
                    break
                continue

            review_json, review_err = normalize_and_validate("PLAN_REVIEW", review_res.parsed_json, {"plan_id": plan_id, "task_id": plan_id})
            if not isinstance(review_json, dict):
                review_json = {}
            last_review = review_json
            if not review_err:
                # Update latest PLAN_REVIEW telemetry row with normalized_json (best-effort).
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
                break

            detailed = _review_invalid_reason(review_json=review_json, expected_target="PLAN")
            try:
                # Attach concrete contract mismatch detail to the llm_calls + audit timeline.
                conn.execute("UPDATE llm_calls SET validator_error=? WHERE llm_call_id=?", (_limit_chars(detailed, max_chars=500), review_call_id))
                from core.audit_log import annotate_llm_output_for_retry

                annotate_llm_output_for_retry(conn, llm_call_id=review_call_id, retry_kind="CONTRACT_MISMATCH", retry_reason=detailed)
            except Exception:
                pass
            review_prompt_to_use = _build_review_retry_prompt(original_prompt=review_prompt, invalid_response=review_res.raw_response_text, reason=detailed)
            if (not keep_trying) and review_attempt >= max(1, int(max_review_attempts_per_plan)):
                break

        # Still invalid after reviewer retries => stop early; do NOT proceed to next PLAN_GEN.
        _, final_review_err = normalize_and_validate("PLAN_REVIEW", review_json, {"plan_id": plan_id, "task_id": plan_id})
        if final_review_err:
            with transaction(conn):
                emit_event(
                    conn,
                    plan_id=plan_id,
                    event_type="ERROR",
                    payload={
                        "error_code": "PLAN_REVIEW_INVALID",
                        "message": "PLAN_REVIEW output remained contract-invalid after retries.",
                        "context": {
                            "attempt": attempt,
                            "hint": "Open UI -> LLM Workflow and click the latest PLAN_REVIEW node to see validator_error and raw output.",
                            "validator_error": format_contract_error_short(final_review_err),
                        },
                    },
                )
            raise PlanWorkflowError("plan review invalid after retries (see llm_calls/LLM Workflow for details)")

        total_score = int(review_json.get("total_score") or 0)
        action_required = str(review_json.get("action_required") or "")

        cfg = get_runtime_config()
        if total_score >= int(cfg.plan_review_pass_score) and action_required == "APPROVE":
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

        # Feed reviewer conclusions back into the next PLAN_GEN attempt as a bounded remediation note.
        review_notes = _build_plan_remediation_note(review_json, max_chars=500)
        try:
            ensure_dir(config.REVIEW_NOTES_DIR / plan_id)
            (config.REVIEW_NOTES_DIR / plan_id / f"plan_review_attempt_{attempt}.md").write_text(review_notes, encoding="utf-8")
        except Exception:
            pass
        gen_notes = ""
        if not keep_trying and attempt >= int(max_plan_attempts):
            raise PlanNotApprovedError(plan_id=locals().get("plan_id"), max_attempts=max_plan_attempts, last_review=last_review)
