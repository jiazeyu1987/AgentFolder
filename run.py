from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import config
from core.artifacts import insert_artifact_and_activate, load_active_artifact_path, write_artifact_file
from core.db import apply_migrations, connect, transaction
from core.events import emit_event
from core.error_counters import increment_counter, reset_counter
from core.errors import apply_error_outcome, map_error_to_outcome, maybe_reset_failed_to_ready, record_error
from core.llm_calls import record_llm_call
from core.llm_client import LLMClient
from core.contracts_v2 import format_contract_error_short, normalize_and_validate
from core.matcher import detect_removed_input_files_all, scan_inputs_and_bind_evidence_all
from core.plan_loader import load_plan_into_db_if_needed
from core.prompts import build_xiaobo_prompt, build_xiaojing_review_prompt, load_prompts, register_prompt_versions
from core.readiness import recompute_readiness_for_plan
from core.reviews import insert_review, write_review_json
from core.scheduler import pick_xiaobo_tasks, pick_xiaojing_tasks
from core.scheduler import pick_xiaojing_check_nodes
from core.util import ensure_dir, safe_read_text, stable_hash_text, utc_now_iso
from core.workflow_mode import WorkflowModeGuardError, ensure_mode_supported_for_action
from skills.registry import load_registry, run_skill


def _ensure_layout() -> None:
    for path in [
        config.TASKS_DIR,
        config.STATE_DIR,
        config.MIGRATIONS_DIR,
        config.WORKSPACE_DIR,
        config.INPUTS_DIR,
        config.BASELINE_INPUTS_DIR,
        config.ARTIFACTS_DIR,
        config.REVIEWS_DIR,
        config.REQUIRED_DOCS_DIR,
        config.DELIVERABLES_DIR,
        config.LOGS_DIR,
        config.PROMPTS_AGENTS_DIR,
        config.ROOT_DIR / "rubric",
        config.ROOT_DIR / "skills" / "impl",
    ]:
        ensure_dir(path)


def _append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    ensure_dir(path.parent)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _inc_attempt(conn, task_id: str) -> None:
    conn.execute("UPDATE task_nodes SET attempt_count = attempt_count + 1, updated_at = ? WHERE task_id = ?", (utc_now_iso(), task_id))


def _attempt_exceeded(conn, task_id: str) -> bool:
    row = conn.execute("SELECT attempt_count FROM task_nodes WHERE task_id = ?", (task_id,)).fetchone()
    if not row:
        return False
    return int(row["attempt_count"]) >= int(config.MAX_TASK_ATTEMPTS)


def _handle_error(conn, *, plan_id: str, task_id: Optional[str], error_code: str, message: str, context: Optional[Dict[str, Any]] = None) -> None:
    record_error(conn, plan_id=plan_id, task_id=task_id, error_code=error_code, message=message, context=context)
    if task_id:
        outcome = map_error_to_outcome(error_code)
        apply_error_outcome(conn, plan_id=plan_id, task_id=task_id, outcome=outcome)


def _set_status(conn, *, plan_id: str, task_id: str, status: str, blocked_reason: Optional[str] = None) -> None:
    conn.execute(
        "UPDATE task_nodes SET status = ?, blocked_reason = ?, updated_at = ? WHERE task_id = ?",
        (status, blocked_reason, utc_now_iso(), task_id),
    )
    emit_event(conn, plan_id=plan_id, task_id=task_id, event_type="STATUS_CHANGED", payload={"status": status, "blocked_reason": blocked_reason})


def _apply_modify_or_escalate(conn, *, plan_id: str, task_id: str, suggestions: Any) -> None:
    """
    Increment attempt_count for a task that needs modification; escalate to WAITING_EXTERNAL after MAX_TASK_ATTEMPTS.
    """
    _inc_attempt(conn, task_id)
    if _attempt_exceeded(conn, task_id):
        _handle_error(conn, plan_id=plan_id, task_id=task_id, error_code="MAX_ATTEMPTS_EXCEEDED", message="Max attempts exceeded")
        return

    _set_status(conn, plan_id=plan_id, task_id=task_id, status="TO_BE_MODIFY")
    suggestions_dir = config.REVIEWS_DIR / task_id
    ensure_dir(suggestions_dir)
    (suggestions_dir / "suggestions.md").write_text(
        json.dumps(suggestions or [], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _retry_review_or_escalate(conn, *, plan_id: str, task_id: str, reason: str, reviewer: str) -> None:
    """
    Review LLM returned invalid contract. Unlike executor failures, we keep the task in READY_TO_CHECK
    (so the reviewer can retry) until MAX_TASK_ATTEMPTS is exceeded.
    """
    record_error(conn, plan_id=plan_id, task_id=task_id, error_code="LLM_UNPARSEABLE", message=reason, context={"validator_error": reason, "reviewer": reviewer})
    _inc_attempt(conn, task_id)
    if _attempt_exceeded(conn, task_id):
        _handle_error(conn, plan_id=plan_id, task_id=task_id, error_code="MAX_ATTEMPTS_EXCEEDED", message="Max review attempts exceeded")
        return
    _set_status(conn, plan_id=plan_id, task_id=task_id, status="READY_TO_CHECK")


def _write_required_docs(task_id: str, required_docs: List[Dict[str, Any]]) -> Path:
    ensure_dir(config.REQUIRED_DOCS_DIR)
    path = config.REQUIRED_DOCS_DIR / f"{task_id}.md"
    lines = [
        f"# Required Docs for {task_id}",
        "",
        f"> NOTE: System will auto-search `{config.BASELINE_INPUTS_DIR.as_posix()}/` first. If not found, place files under `{config.INPUTS_DIR.as_posix()}/` as suggested below.",
        "",
    ]
    for doc in required_docs:
        lines.append(f"- {doc.get('name','')}: {doc.get('description','')}")
        if doc.get("accepted_types"):
            lines.append(f"  - accepted_types: {doc['accepted_types']}")
        if doc.get("suggested_path"):
            lines.append(f"  - suggested_path: {doc['suggested_path']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _list_task_input_files(conn, task_id: str) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT e.ref_path, e.sha256, r.name AS requirement_name, e.added_at
        FROM evidences e
        JOIN input_requirements r ON r.requirement_id = e.requirement_id
        WHERE r.task_id = ?
        ORDER BY e.added_at DESC
        """,
        (task_id,),
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        if not r["ref_path"]:
            continue
        out.append({"path": r["ref_path"], "sha256": r["sha256"], "requirement_name": r["requirement_name"], "added_at": r["added_at"]})
    return out


def _select_best_inputs_per_requirement(files: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Implements the MVP selection rule:
    - Prefer filename containing 'FINAL' (case-insensitive)
    - Else prefer latest modified time
    Also returns conflicts for visibility (does not block by default).
    """
    by_req: Dict[str, List[Dict[str, Any]]] = {}
    for f in files:
        by_req.setdefault(str(f.get("requirement_name") or ""), []).append(f)

    selected: List[Dict[str, Any]] = []
    conflicts: List[Dict[str, Any]] = []

    for req_name, items in by_req.items():
        enriched = []
        for it in items:
            p = Path(str(it.get("path") or ""))
            mtime = p.stat().st_mtime if p.exists() else 0
            name = p.name.lower()
            is_final = "final" in name
            enriched.append((is_final, mtime, it))
        enriched.sort(key=lambda x: (x[0], x[1]), reverse=True)
        if enriched:
            selected.append(enriched[0][2])
        if len(enriched) > 1 and not enriched[0][0]:
            conflicts.append(
                {
                    "requirement_name": req_name,
                    "chosen": str(Path(str(enriched[0][2].get("path") or "")).name),
                    "candidates": [str(Path(str(e[2].get("path") or "")).name) for e in enriched[:5]],
                }
            )
    return selected, conflicts


def xiaobo_round(*, conn, plan_id: str, prompts, llm: LLMClient, llm_calls: int, skills_registry: Dict[str, Any]) -> int:
    tasks = pick_xiaobo_tasks(conn, plan_id=plan_id, limit=10)
    for task in tasks:
        task_id = task["task_id"]
        suggestions_path = config.REVIEWS_DIR / task_id / "suggestions.md"
        suggestions_text = suggestions_path.read_text(encoding="utf-8") if suggestions_path.exists() else ""

        extracted_snippets: List[str] = []
        input_files = _list_task_input_files(conn, task_id)
        selected_files, conflicts = _select_best_inputs_per_requirement(input_files)
        if conflicts:
            emit_event(conn, plan_id=plan_id, task_id=task_id, event_type="INPUT_CONFLICT", payload={"conflicts": conflicts})
            _handle_error(
                conn,
                plan_id=plan_id,
                task_id=task_id,
                error_code="INPUT_CONFLICT",
                message="Multiple input versions detected; please confirm which to use.",
                context={"conflicts": conflicts},
            )
            continue
        if input_files:
            try:
                skill_res = run_skill(
                    conn,
                    plan_id=plan_id,
                    task_id=task_id,
                    registry=skills_registry,
                    skill_name="text_extract",
                    inputs=[{"path": f["path"], "sha256": f["sha256"]} for f in selected_files],
                    params={"max_chars": 50_000},
                    timeout_s=config.SKILL_TIMEOUT_SECONDS,
                )
                if skill_res.get("status") == "FAILED":
                    code = ((skill_res.get("error") or {}).get("code") if isinstance(skill_res.get("error"), dict) else "SKILL_FAILED") or "SKILL_FAILED"
                    msg = ((skill_res.get("error") or {}).get("message") if isinstance(skill_res.get("error"), dict) else "skill failed") or "skill failed"
                    _handle_error(conn, plan_id=plan_id, task_id=task_id, error_code=code, message=msg, context={"skill": "text_extract"})
                    if code in {"SKILL_FAILED", "SKILL_TIMEOUT"}:
                        c = increment_counter(conn, plan_id=plan_id, task_id=task_id, key="WAITING_SKILL")
                        if c >= config.MAX_SKILL_RETRIES:
                            _handle_error(conn, plan_id=plan_id, task_id=task_id, error_code="MAX_ATTEMPTS_EXCEEDED", message="Skill failed repeatedly; waiting external.")
                    continue
                reset_counter(conn, plan_id=plan_id, task_id=task_id, key="WAITING_SKILL")
                for art in (skill_res.get("artifacts") or [])[:3]:
                    p = Path(str(art.get("path") or ""))
                    if p.exists():
                        extracted_snippets.append(safe_read_text(p, max_chars=20_000))
            except Exception as exc:
                _handle_error(conn, plan_id=plan_id, task_id=task_id, error_code="SKILL_FAILED", message=str(exc), context={"skill": "text_extract"})
                c = increment_counter(conn, plan_id=plan_id, task_id=task_id, key="WAITING_SKILL")
                if c >= config.MAX_SKILL_RETRIES:
                    _handle_error(conn, plan_id=plan_id, task_id=task_id, error_code="MAX_ATTEMPTS_EXCEEDED", message="Skill failed repeatedly; waiting external.")
                continue

        prompt = build_xiaobo_prompt(
            prompts,
            conn=conn,
            plan_id=plan_id,
            task_id=task_id,
            suggestions_text=suggestions_text,
            artifact_text_snippets=extracted_snippets,
        )
        res = llm.call_json(prompt)
        llm_calls += 1 + int(getattr(res, "extra_calls", 0))

        _append_jsonl(
            config.LLM_RUNS_LOG_PATH,
            {
                "ts": utc_now_iso(),
                "plan_id": plan_id,
                "task_id": task_id,
                "agent": "xiaobo",
                "shared_prompt_version": prompts.shared.version,
                "shared_prompt_hash": prompts.shared.sha256,
                "agent_prompt_version": prompts.xiaobo.version,
                "agent_prompt_hash": prompts.xiaobo.sha256,
                "runtime_context_hash": stable_hash_text(prompt),
                "final_prompt": prompt,
                "response": res.parsed_json or res.raw_response_text,
                "error": {"code": res.error_code, "message": res.error} if res.error else None,
            },
        )

        normalized_obj: Optional[Dict[str, Any]] = None
        validator_error: Optional[str] = None
        if not res.error and res.parsed_json:
            normalized, err = normalize_and_validate("TASK_ACTION", res.parsed_json, {"task_id": task_id})
            if isinstance(normalized, dict):
                normalized_obj = normalized
            if err:
                validator_error = format_contract_error_short(err)
        record_llm_call(
            conn,
            plan_id=plan_id,
            task_id=task_id,
            agent="xiaobo",
            scope="TASK_ACTION",
            provider=res.provider,
            prompt_text=prompt,
            response_text=res.raw_response_text,
            started_at_ts=res.started_at_ts,
            finished_at_ts=res.finished_at_ts,
            runtime_context_hash=stable_hash_text(prompt),
            shared_prompt_version=prompts.shared.version,
            shared_prompt_hash=prompts.shared.sha256,
            agent_prompt_version=prompts.xiaobo.version,
            agent_prompt_hash=prompts.xiaobo.sha256,
            parsed_json=res.parsed_json,
            normalized_json=normalized_obj,
            validator_error=validator_error,
            error_code=res.error_code,
            error_message=res.error,
        )

        if res.error or not res.parsed_json:
            _handle_error(conn, plan_id=plan_id, task_id=task_id, error_code=res.error_code or "LLM_FAILED", message=res.error or "llm failed")
            if _attempt_exceeded(conn, task_id):
                _handle_error(conn, plan_id=plan_id, task_id=task_id, error_code="MAX_ATTEMPTS_EXCEEDED", message="Max attempts exceeded")
            continue

        obj = normalized_obj or res.parsed_json
        obj, err = normalize_and_validate("TASK_ACTION", obj, {"task_id": task_id})
        if err:
            reason = format_contract_error_short(err)
            _handle_error(conn, plan_id=plan_id, task_id=task_id, error_code="LLM_UNPARSEABLE", message=reason, context={"validator_error": reason, "validator_error_obj": err})
            if _attempt_exceeded(conn, task_id):
                _handle_error(conn, plan_id=plan_id, task_id=task_id, error_code="MAX_ATTEMPTS_EXCEEDED", message="Max attempts exceeded")
            continue

        result_type = obj.get("result_type")
        if result_type == "NEEDS_INPUT":
            required_docs = ((obj.get("needs_input") or {}).get("required_docs") or [])
            if isinstance(required_docs, list):
                _write_required_docs(task_id, required_docs)
            _handle_error(conn, plan_id=plan_id, task_id=task_id, error_code="INPUT_MISSING", message="Missing required input(s).", context={"required_docs": required_docs})
            continue

        if result_type == "ARTIFACT":
            artifact = obj.get("artifact") or {}
            name = str(artifact.get("name") or "artifact")
            fmt = str(artifact.get("format") or "md")
            content = str(artifact.get("content") or "")
            path = write_artifact_file(config.ARTIFACTS_DIR, task_id=task_id, name=name, fmt=fmt, content=content)
            insert_artifact_and_activate(conn, plan_id=plan_id, task_id=task_id, name=name, fmt=fmt, path=path)
            _set_status(conn, plan_id=plan_id, task_id=task_id, status="READY_TO_CHECK")
            continue

        if result_type == "NOOP":
            _set_status(conn, plan_id=plan_id, task_id=task_id, status="READY_TO_CHECK")
            continue

        if result_type == "ERROR":
            err_obj = obj.get("error") or {}
            code = str(err_obj.get("code") or "LLM_FAILED")
            msg = str(err_obj.get("message") or "model reported ERROR")
            _handle_error(conn, plan_id=plan_id, task_id=task_id, error_code="LLM_FAILED", message=f"model_error[{code}]: {msg}")
            if _attempt_exceeded(conn, task_id):
                _handle_error(conn, plan_id=plan_id, task_id=task_id, error_code="MAX_ATTEMPTS_EXCEEDED", message="Max attempts exceeded")
            continue

        _handle_error(conn, plan_id=plan_id, task_id=task_id, error_code="LLM_UNPARSEABLE", message=f"unknown result_type: {result_type}")
    return llm_calls


def xiaojing_round(
    *,
    conn,
    plan_id: str,
    prompts,
    llm: LLMClient,
    llm_calls: int,
    rubric_json: Dict[str, Any],
) -> int:
    tasks = pick_xiaojing_tasks(conn, plan_id=plan_id, limit=10)
    for task in tasks:
        task_id = task["task_id"]
        artifact_path = load_active_artifact_path(conn, task_id)
        if not artifact_path or not artifact_path.exists():
            _handle_error(conn, plan_id=plan_id, task_id=task_id, error_code="INPUT_MISSING", message="artifact missing for review")
            continue

        artifact_text = safe_read_text(artifact_path, max_chars=200_000)
        prompt = build_xiaojing_review_prompt(
            prompts,
            conn=conn,
            plan_id=plan_id,
            task_id=task_id,
            rubric_json=rubric_json,
            artifact_path=artifact_path,
            artifact_text=artifact_text,
        )
        res = llm.call_json(prompt)
        llm_calls += 1 + int(getattr(res, "extra_calls", 0))

        _append_jsonl(
            config.LLM_RUNS_LOG_PATH,
            {
                "ts": utc_now_iso(),
                "plan_id": plan_id,
                "task_id": task_id,
                "agent": "xiaojing",
                "shared_prompt_version": prompts.shared.version,
                "shared_prompt_hash": prompts.shared.sha256,
                "agent_prompt_version": prompts.xiaojing.version,
                "agent_prompt_hash": prompts.xiaojing.sha256,
                "runtime_context_hash": stable_hash_text(prompt),
                "final_prompt": prompt,
                "response": res.parsed_json or res.raw_response_text,
                "error": {"code": res.error_code, "message": res.error} if res.error else None,
            },
        )

        normalized_obj: Optional[Dict[str, Any]] = None
        validator_error: Optional[str] = None
        if not res.error and res.parsed_json:
            normalized, err = normalize_and_validate("TASK_CHECK", res.parsed_json, {"task_id": task_id})
            if isinstance(normalized, dict):
                normalized_obj = normalized
            if err:
                validator_error = format_contract_error_short(err)
        record_llm_call(
            conn,
            plan_id=plan_id,
            task_id=task_id,
            agent="xiaojing",
            scope="TASK_REVIEW",
            provider=res.provider,
            prompt_text=prompt,
            response_text=res.raw_response_text,
            started_at_ts=res.started_at_ts,
            finished_at_ts=res.finished_at_ts,
            runtime_context_hash=stable_hash_text(prompt),
            shared_prompt_version=prompts.shared.version,
            shared_prompt_hash=prompts.shared.sha256,
            agent_prompt_version=prompts.xiaojing.version,
            agent_prompt_hash=prompts.xiaojing.sha256,
            parsed_json=res.parsed_json,
            normalized_json=normalized_obj,
            validator_error=validator_error,
            error_code=res.error_code,
            error_message=res.error,
        )

        if res.error or not res.parsed_json:
            _handle_error(conn, plan_id=plan_id, task_id=task_id, error_code=res.error_code or "LLM_FAILED", message=res.error or "llm failed")
            continue

        obj = normalized_obj or res.parsed_json
        obj, err = normalize_and_validate("TASK_CHECK", obj, {"task_id": task_id})
        if err:
            _retry_review_or_escalate(conn, plan_id=plan_id, task_id=task_id, reason=format_contract_error_short(err), reviewer="xiaojing")
            continue

        write_review_json(config.REVIEWS_DIR, task_id=task_id, review=obj)
        insert_review(conn, plan_id=plan_id, task_id=task_id, reviewer_agent_id="xiaojing", review=obj)

        score = int(obj.get("total_score") or 0)
        if score >= 90:
            _set_status(conn, plan_id=plan_id, task_id=task_id, status="DONE")
        else:
            _apply_modify_or_escalate(conn, plan_id=plan_id, task_id=task_id, suggestions=obj.get("suggestions") or [])

    return llm_calls


def _get_tags(conn, task_id: str) -> List[str]:
    row = conn.execute("SELECT tags_json FROM task_nodes WHERE task_id = ?", (task_id,)).fetchone()
    if not row or not row["tags_json"]:
        return []
    try:
        tags = json.loads(row["tags_json"])
    except Exception:
        return []
    if not isinstance(tags, list):
        return []
    return [t for t in tags if isinstance(t, str)]


def _pick_check_target_task(conn, *, plan_id: str, check_task_id: str) -> Optional[str]:
    dep_rows = conn.execute(
        """
        SELECT from_task_id
        FROM task_edges
        WHERE plan_id = ? AND to_task_id = ? AND edge_type = 'DEPENDS_ON'
        ORDER BY created_at ASC
        """,
        (plan_id, check_task_id),
    ).fetchall()
    if not dep_rows:
        return None

    dep_ids = [r["from_task_id"] for r in dep_rows]
    placeholders = ",".join("?" for _ in dep_ids)
    check_tags = set(_get_tags(conn, check_task_id))
    rows = conn.execute(
        f"""
        SELECT task_id, priority, active_artifact_id, title, tags_json
        FROM task_nodes
        WHERE task_id IN ({placeholders})
        ORDER BY priority DESC
        """,
        tuple(dep_ids),
    ).fetchall()

    def tags_of(row) -> set[str]:
        try:
            t = json.loads(row["tags_json"] or "[]")
        except Exception:
            t = []
        return {x for x in t if isinstance(x, str)}

    candidates = list(rows)
    if "final" in check_tags and "check" in check_tags:
        # For final checks, prefer tasks tagged final/package, and prefer those with artifacts.
        prioritized = []
        for r in candidates:
            t = tags_of(r)
            score = 0
            if r["active_artifact_id"]:
                score += 100
            if "final" in t:
                score += 30
            if "package" in t or "final" in (str(r["title"] or "").lower()):
                score += 10
            prioritized.append((score, int(r["priority"]), r))
        prioritized.sort(key=lambda x: (x[0], x[1]), reverse=True)
        chosen = prioritized[0][2] if prioritized else None
    else:
        # Prefer dependencies that actually produced an artifact.
        with_artifact = [r for r in candidates if r["active_artifact_id"]]
        chosen = (with_artifact[0] if with_artifact else candidates[0]) if candidates else None

    return chosen["task_id"] if chosen else None


def xiaojing_check_round(
    *,
    conn,
    plan_id: str,
    prompts,
    llm: LLMClient,
    llm_calls: int,
    rubric_json: Dict[str, Any],
) -> int:
    checks = pick_xiaojing_check_nodes(conn, plan_id=plan_id, limit=10)
    for chk in checks:
        check_task_id = chk["task_id"]
        tags = _get_tags(conn, check_task_id)

        # Plan review CHECK nodes are handled by create-plan workflow and/or readiness (informational gate).
        if "review" in tags and "plan" in tags:
            _set_status(conn, plan_id=plan_id, task_id=check_task_id, status="DONE")
            continue

        target_task_id = _pick_check_target_task(conn, plan_id=plan_id, check_task_id=check_task_id)
        if not target_task_id:
            _set_status(conn, plan_id=plan_id, task_id=check_task_id, status="DONE")
            continue

        dep_rows = conn.execute(
            """
            SELECT from_task_id
            FROM task_edges
            WHERE plan_id = ? AND to_task_id = ? AND edge_type = 'DEPENDS_ON'
            ORDER BY created_at ASC
            """,
            (plan_id, check_task_id),
        ).fetchall()
        dep_ids = [r["from_task_id"] for r in dep_rows] if dep_rows else [target_task_id]

        artifacts_payload: List[Dict[str, Any]] = []
        for dep_id in dep_ids:
            p = load_active_artifact_path(conn, dep_id)
            if not p or not p.exists():
                continue
            artifacts_payload.append({"task_id": dep_id, "path": str(p), "content": safe_read_text(p, max_chars=120_000)})

        if not artifacts_payload:
            _handle_error(conn, plan_id=plan_id, task_id=check_task_id, error_code="INPUT_MISSING", message="no dependent artifacts found")
            continue

        from core.prompts import build_xiaojing_check_prompt

        prompt = build_xiaojing_check_prompt(
            prompts,
            conn=conn,
            plan_id=plan_id,
            check_task_id=check_task_id,
            rubric_json=rubric_json,
            target_task_id=target_task_id,
            target_artifacts=artifacts_payload,
            reviewer="xiaojing",
        )
        res = llm.call_json(prompt)
        llm_calls += 1 + int(getattr(res, "extra_calls", 0))

        _append_jsonl(
            config.LLM_RUNS_LOG_PATH,
            {
                "ts": utc_now_iso(),
                "plan_id": plan_id,
                "task_id": check_task_id,
                "agent": "xiaojing",
                "shared_prompt_version": prompts.shared.version,
                "shared_prompt_hash": prompts.shared.sha256,
                "agent_prompt_version": prompts.xiaojing.version,
                "agent_prompt_hash": prompts.xiaojing.sha256,
                "runtime_context_hash": stable_hash_text(prompt),
                "final_prompt": prompt,
                "response": res.parsed_json or res.raw_response_text,
                "error": {"code": res.error_code, "message": res.error} if res.error else None,
                "scope": "CHECK_NODE",
                "target_task_id": target_task_id,
            },
        )

        normalized_obj: Optional[Dict[str, Any]] = None
        validator_error: Optional[str] = None
        if not res.error and res.parsed_json:
            normalized, err = normalize_and_validate("TASK_CHECK", res.parsed_json, {"task_id": check_task_id})
            if isinstance(normalized, dict):
                normalized_obj = normalized
            if err:
                validator_error = format_contract_error_short(err)
        record_llm_call(
            conn,
            plan_id=plan_id,
            task_id=check_task_id,
            agent="xiaojing",
            scope="CHECK_NODE_REVIEW",
            provider=res.provider,
            prompt_text=prompt,
            response_text=res.raw_response_text,
            started_at_ts=res.started_at_ts,
            finished_at_ts=res.finished_at_ts,
            runtime_context_hash=stable_hash_text(prompt),
            shared_prompt_version=prompts.shared.version,
            shared_prompt_hash=prompts.shared.sha256,
            agent_prompt_version=prompts.xiaojing.version,
            agent_prompt_hash=prompts.xiaojing.sha256,
            parsed_json=res.parsed_json,
            normalized_json=normalized_obj,
            validator_error=validator_error,
            error_code=res.error_code,
            error_message=res.error,
            meta={"target_task_id": target_task_id},
        )

        if res.error or not res.parsed_json:
            _handle_error(conn, plan_id=plan_id, task_id=check_task_id, error_code=res.error_code or "LLM_FAILED", message=res.error or "llm failed")
            continue

        obj = normalized_obj or res.parsed_json
        obj, err = normalize_and_validate("TASK_CHECK", obj, {"task_id": check_task_id})
        if err:
            _retry_review_or_escalate(conn, plan_id=plan_id, task_id=check_task_id, reason=format_contract_error_short(err), reviewer="xiaojing")
            continue

        write_review_json(config.REVIEWS_DIR, task_id=check_task_id, review=obj)
        insert_review(conn, plan_id=plan_id, task_id=check_task_id, reviewer_agent_id="xiaojing", review=obj)

        score = int(obj.get("total_score") or 0)
        if score >= 90:
            _set_status(conn, plan_id=plan_id, task_id=check_task_id, status="DONE")
            continue

        # Failed check: push modifications onto the target task (xiaobo) and let deps block this CHECK node again.
        _apply_modify_or_escalate(conn, plan_id=plan_id, task_id=target_task_id, suggestions=obj.get("suggestions") or [])
        _set_status(conn, plan_id=plan_id, task_id=check_task_id, status="PENDING")

    return llm_calls


def xiaoxie_check_round(
    *,
    conn,
    plan_id: str,
    prompts,
    llm: LLMClient,
    llm_calls: int,
    rubric_json: Dict[str, Any],
) -> int:
    # Reuse the same CHECK-node execution logic, but for xiaoxie-owned CHECK nodes.
    checks = conn.execute(
        """
        SELECT task_id, title, node_type, owner_agent_id, priority, status, attempt_count
        FROM task_nodes
        WHERE plan_id = ?
          AND active_branch = 1
          AND node_type = 'CHECK'
          AND owner_agent_id = 'xiaoxie'
          AND status = 'READY'
        ORDER BY priority DESC, attempt_count ASC
        LIMIT 10
        """,
        (plan_id,),
    ).fetchall()

    for chk in checks:
        check_task_id = chk["task_id"]
        target_task_id = _pick_check_target_task(conn, plan_id=plan_id, check_task_id=check_task_id)
        if not target_task_id:
            _set_status(conn, plan_id=plan_id, task_id=check_task_id, status="DONE")
            continue

        dep_rows = conn.execute(
            """
            SELECT from_task_id
            FROM task_edges
            WHERE plan_id = ? AND to_task_id = ? AND edge_type = 'DEPENDS_ON'
            ORDER BY created_at ASC
            """,
            (plan_id, check_task_id),
        ).fetchall()
        dep_ids = [r["from_task_id"] for r in dep_rows] if dep_rows else [target_task_id]

        artifacts_payload: List[Dict[str, Any]] = []
        for dep_id in dep_ids:
            p = load_active_artifact_path(conn, dep_id)
            if not p or not p.exists():
                continue
            artifacts_payload.append({"task_id": dep_id, "path": str(p), "content": safe_read_text(p, max_chars=120_000)})
        if not artifacts_payload:
            _handle_error(conn, plan_id=plan_id, task_id=check_task_id, error_code="INPUT_MISSING", message="no dependent artifacts found")
            continue

        from core.prompts import build_xiaojing_check_prompt

        prompt = build_xiaojing_check_prompt(
            prompts,
            conn=conn,
            plan_id=plan_id,
            check_task_id=check_task_id,
            rubric_json=rubric_json,
            target_task_id=target_task_id,
            target_artifacts=artifacts_payload,
            reviewer="xiaoxie",
        )
        res = llm.call_json(prompt)
        llm_calls += 1 + int(getattr(res, "extra_calls", 0))

        _append_jsonl(
            config.LLM_RUNS_LOG_PATH,
            {
                "ts": utc_now_iso(),
                "plan_id": plan_id,
                "task_id": check_task_id,
                "agent": "xiaoxie",
                "shared_prompt_version": prompts.shared.version,
                "shared_prompt_hash": prompts.shared.sha256,
                "agent_prompt_version": prompts.xiaoxie.version,
                "agent_prompt_hash": prompts.xiaoxie.sha256,
                "runtime_context_hash": stable_hash_text(prompt),
                "final_prompt": prompt,
                "response": res.parsed_json or res.raw_response_text,
                "error": {"code": res.error_code, "message": res.error} if res.error else None,
                "scope": "CHECK_NODE",
                "target_task_id": target_task_id,
            },
        )

        normalized_obj: Optional[Dict[str, Any]] = None
        validator_error: Optional[str] = None
        if not res.error and res.parsed_json:
            normalized, err = normalize_and_validate("TASK_CHECK", res.parsed_json, {"task_id": check_task_id})
            if isinstance(normalized, dict):
                normalized_obj = normalized
            if err:
                validator_error = format_contract_error_short(err)
        record_llm_call(
            conn,
            plan_id=plan_id,
            task_id=check_task_id,
            agent="xiaoxie",
            scope="CHECK_NODE_REVIEW",
            provider=res.provider,
            prompt_text=prompt,
            response_text=res.raw_response_text,
            started_at_ts=res.started_at_ts,
            finished_at_ts=res.finished_at_ts,
            runtime_context_hash=stable_hash_text(prompt),
            shared_prompt_version=prompts.shared.version,
            shared_prompt_hash=prompts.shared.sha256,
            agent_prompt_version=prompts.xiaoxie.version,
            agent_prompt_hash=prompts.xiaoxie.sha256,
            parsed_json=res.parsed_json,
            normalized_json=normalized_obj,
            validator_error=validator_error,
            error_code=res.error_code,
            error_message=res.error,
            meta={"target_task_id": target_task_id},
        )

        if res.error or not res.parsed_json:
            _handle_error(conn, plan_id=plan_id, task_id=check_task_id, error_code=res.error_code or "LLM_FAILED", message=res.error or "llm failed")
            continue

        obj = normalized_obj or res.parsed_json
        obj, err = normalize_and_validate("TASK_CHECK", obj, {"task_id": check_task_id})
        if err:
            _retry_review_or_escalate(conn, plan_id=plan_id, task_id=check_task_id, reason=format_contract_error_short(err), reviewer="xiaoxie")
            continue

        write_review_json(config.REVIEWS_DIR, task_id=check_task_id, review=obj)
        insert_review(conn, plan_id=plan_id, task_id=check_task_id, reviewer_agent_id="xiaoxie", review=obj)

        score = int(obj.get("total_score") or 0)
        if score >= 90:
            _set_status(conn, plan_id=plan_id, task_id=check_task_id, status="DONE")
            continue

        _apply_modify_or_escalate(conn, plan_id=plan_id, task_id=target_task_id, suggestions=obj.get("suggestions") or [])
        _set_status(conn, plan_id=plan_id, task_id=check_task_id, status="PENDING")

    return llm_calls


def is_plan_done(conn, plan_id: str) -> bool:
    root = conn.execute("SELECT root_task_id FROM plans WHERE plan_id = ?", (plan_id,)).fetchone()
    if not root:
        return False
    status = conn.execute("SELECT status FROM task_nodes WHERE task_id = ?", (root["root_task_id"],)).fetchone()
    return bool(status and status["status"] == "DONE")


def is_plan_blocked_waiting_user(conn, plan_id: str) -> bool:
    runnable = conn.execute(
        """
        SELECT COUNT(1) FROM task_nodes
        WHERE plan_id = ? AND active_branch = 1 AND status IN ('READY', 'TO_BE_MODIFY', 'READY_TO_CHECK', 'IN_PROGRESS')
        """,
        (plan_id,),
    ).fetchone()[0]
    if int(runnable) > 0:
        return False
    blocked = conn.execute(
        """
        SELECT COUNT(1) FROM task_nodes
        WHERE plan_id = ? AND active_branch = 1 AND status = 'BLOCKED' AND blocked_reason IN ('WAITING_INPUT', 'WAITING_EXTERNAL')
        """,
        (plan_id,),
    ).fetchone()[0]
    return int(blocked) > 0


def write_blocked_summary(conn, plan_id: str) -> Path:
    def missing_requirements(task_id: str) -> List[str]:
        reqs = conn.execute(
            "SELECT requirement_id, name, required, min_count FROM input_requirements WHERE task_id = ?",
            (task_id,),
        ).fetchall()
        out: List[str] = []
        for r in reqs:
            if int(r["required"]) != 1:
                continue
            count = conn.execute("SELECT COUNT(1) FROM evidences WHERE requirement_id = ?", (r["requirement_id"],)).fetchone()[0]
            if int(count) < int(r["min_count"]):
                out.append(f"{r['name']} (need {int(r['min_count'])}, have {int(count)})")
        return out

    def last_error(task_id: str) -> Optional[Dict[str, Any]]:
        row = conn.execute(
            """
            SELECT created_at, payload_json
            FROM task_events
            WHERE plan_id = ? AND task_id = ? AND event_type = 'ERROR'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (plan_id, task_id),
        ).fetchone()
        if not row:
            return None
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except Exception:
            payload = {"raw": row["payload_json"]}
        return {"created_at": row["created_at"], "payload": payload}

    def error_counters(task_id: str) -> Dict[str, int]:
        try:
            rows = conn.execute(
                "SELECT key, count FROM task_error_counters WHERE plan_id = ? AND task_id = ?",
                (plan_id, task_id),
            ).fetchall()
            return {r["key"]: int(r["count"]) for r in rows}
        except Exception:
            return {}

    rows = conn.execute(
        """
        SELECT task_id, title, blocked_reason, attempt_count, owner_agent_id
        FROM task_nodes
        WHERE plan_id = ? AND active_branch = 1 AND status = 'BLOCKED'
        ORDER BY priority DESC
        """,
        (plan_id,),
    ).fetchall()
    lines = [f"# Blocked Summary ({plan_id})", "", f"- ts: {utc_now_iso()}", "- how_to_resume: add files under workspace/inputs/<requirement_name>/", ""]
    for r in rows:
        lines.append(f"- {r['task_id']} ({r['blocked_reason']}, attempts={r['attempt_count']}, owner={r['owner_agent_id']}): {r['title']}")
        req_path = config.REQUIRED_DOCS_DIR / f"{r['task_id']}.md"
        if req_path.exists():
            lines.append(f"  - required_docs: {req_path.as_posix()}")
        missing = missing_requirements(r["task_id"])
        if missing:
            lines.append("  - missing_requirements:")
            for m in missing[:20]:
                lines.append(f"    - {m}")

        counters = error_counters(r["task_id"])
        if counters:
            lines.append("  - error_counters:")
            for k, v in sorted(counters.items()):
                lines.append(f"    - {k}: {v}")

        le = last_error(r["task_id"])
        if le:
            payload = le["payload"]
            code = payload.get("error_code") if isinstance(payload, dict) else None
            msg = payload.get("message") if isinstance(payload, dict) else None
            lines.append(f"  - last_error_at: {le['created_at']}")
            lines.append(f"  - last_error_code: {code}")
            if msg:
                lines.append(f"  - last_error_message: {str(msg)[:200]}")
    path = config.REQUIRED_DOCS_DIR / "blocked_summary.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Single-machine serial CLI agent (xiaobo/xiaojing).")
    parser.add_argument("--plan", type=Path, default=config.PLAN_PATH_DEFAULT, help="Path to tasks/plan.json")
    parser.add_argument("--db", type=Path, default=config.DB_PATH_DEFAULT, help="Path to state/state.db")
    parser.add_argument("--max-iterations", type=int, default=10_000, help="Safety limit")
    parser.add_argument("--skip-doctor", action="store_true", help="Skip preflight doctor checks (debug only)")
    args = parser.parse_args(argv)

    _ensure_layout()
    try:
        ensure_mode_supported_for_action(action="run")
    except WorkflowModeGuardError as exc:
        print(f"workflow mode error: {exc}")
        return 2

    conn = connect(args.db)
    apply_migrations(conn, config.MIGRATIONS_DIR)

    plan_id = load_plan_into_db_if_needed(conn, args.plan)
    if not bool(getattr(args, "skip_doctor", False)):
        from core.doctor import format_findings_human, run_doctor
        from core.runtime_config import get_runtime_config

        cfg = get_runtime_config()
        findings = run_doctor(conn, plan_id=plan_id, workflow_mode=cfg.workflow_mode)
        if findings:
            print("doctor failed (preflight):")
            print(format_findings_human(findings))
            print("hint: fix the above issues, or re-run with --skip-doctor for debugging.")
            return 2
    prompts = register_prompt_versions(conn, load_prompts(config.PROMPTS_SHARED_PATH, config.PROMPTS_AGENTS_DIR))
    rubric_all = json.loads(config.REVIEW_RUBRIC_PATH.read_text(encoding="utf-8"))
    rubric = rubric_all.get("node_review") or rubric_all

    # Skills registry is loaded so it can be surfaced to the executor prompt later (MVP keeps it minimal).
    skills_registry = load_registry(config.SKILLS_REGISTRY_PATH)
    emit_event(conn, plan_id=plan_id, event_type="SKILLS_LOADED", payload={"count": len(skills_registry), "skills": sorted(skills_registry.keys())})

    llm = LLMClient()
    t0 = time.time()
    llm_calls = 0

    for _ in range(int(args.max_iterations)):
        if time.time() - t0 > config.MAX_PLAN_RUNTIME_SECONDS:
            record_error(conn, plan_id=plan_id, task_id=None, error_code="PLAN_TIMEOUT", message="Plan runtime exceeded")
            break

        with transaction(conn):
            scan_inputs_and_bind_evidence_all(conn, plan_id=plan_id, inputs_dirs=[config.INPUTS_DIR, config.BASELINE_INPUTS_DIR])
            detect_removed_input_files_all(conn, plan_id=plan_id, inputs_dirs=[config.INPUTS_DIR, config.BASELINE_INPUTS_DIR])
            maybe_reset_failed_to_ready(conn, plan_id=plan_id)
            recompute_readiness_for_plan(conn, plan_id=plan_id)
            llm_calls = xiaobo_round(conn=conn, plan_id=plan_id, prompts=prompts, llm=llm, llm_calls=llm_calls, skills_registry=skills_registry)
            llm_calls = xiaojing_round(conn=conn, plan_id=plan_id, prompts=prompts, llm=llm, llm_calls=llm_calls, rubric_json=rubric)
            llm_calls = xiaojing_check_round(conn=conn, plan_id=plan_id, prompts=prompts, llm=llm, llm_calls=llm_calls, rubric_json=rubric)
            llm_calls = xiaoxie_check_round(conn=conn, plan_id=plan_id, prompts=prompts, llm=llm, llm_calls=llm_calls, rubric_json=rubric)

        if llm_calls > config.MAX_LLM_CALLS:
            record_error(conn, plan_id=plan_id, task_id=None, error_code="MAX_LLM_CALLS_EXCEEDED", message="Max LLM calls exceeded")
            break
        if is_plan_done(conn, plan_id):
            break
        if is_plan_blocked_waiting_user(conn, plan_id):
            write_blocked_summary(conn, plan_id)
            break

        time.sleep(config.POLL_INTERVAL_SECONDS)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
