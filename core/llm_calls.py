from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any, Dict, Optional, Tuple

from core.runtime_config import get_runtime_config
from core.util import utc_now_iso


def _truncate_text(s: str, *, max_chars: int) -> Tuple[str, bool]:
    if max_chars <= 0:
        return s, False
    if len(s) <= max_chars:
        return s, False
    if max_chars < 40:
        return s[:max_chars], True
    marker = "\n...[TRUNCATED]...\n"
    head_len = max_chars // 2
    tail_len = max_chars - head_len - len(marker)
    if tail_len < 0:
        return s[:max_chars], True
    return s[:head_len] + marker + s[-tail_len:], True


def record_llm_call(
    conn: sqlite3.Connection,
    *,
    plan_id: Optional[str],
    task_id: Optional[str],
    agent: str,
    scope: str,
    provider: Optional[str],
    prompt_text: str,
    response_text: str,
    started_at_ts: Optional[float] = None,
    finished_at_ts: Optional[float] = None,
    runtime_context_hash: Optional[str] = None,
    shared_prompt_version: Optional[str] = None,
    shared_prompt_hash: Optional[str] = None,
    agent_prompt_version: Optional[str] = None,
    agent_prompt_hash: Optional[str] = None,
    parsed_json: Optional[Dict[str, Any]] = None,
    normalized_json: Optional[Dict[str, Any]] = None,
    validator_error: Optional[str] = None,
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Best-effort telemetry: never raise (LLM call logging must not break workflows).
    Returns llm_call_id (or "UNKNOWN" if insertion fails).
    """
    llm_call_id = str(uuid.uuid4())
    try:
        cfg = get_runtime_config()
        prompt_text2, prompt_truncated = _truncate_text(prompt_text or "", max_chars=int(cfg.guardrails.max_prompt_chars))
        response_text2, response_truncated = _truncate_text(response_text or "", max_chars=int(cfg.guardrails.max_response_chars))
    except Exception:
        prompt_text2, prompt_truncated = prompt_text, False
        response_text2, response_truncated = response_text, False

    meta2 = dict(meta or {})
    if prompt_truncated or response_truncated:
        meta2.setdefault("truncated", {})
        if isinstance(meta2.get("truncated"), dict):
            meta2["truncated"].update({"prompt": bool(prompt_truncated), "response": bool(response_truncated)})

    try:
        conn.execute(
            """
            INSERT INTO llm_calls(
              llm_call_id, created_at,
              started_at_ts, finished_at_ts,
              plan_id, task_id, agent, scope, provider,
              runtime_context_hash,
              shared_prompt_version, shared_prompt_hash,
              agent_prompt_version, agent_prompt_hash,
              prompt_text, response_text,
              prompt_truncated, response_truncated,
              parsed_json, normalized_json,
              validator_error, error_code, error_message,
              meta_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                llm_call_id,
                utc_now_iso(),
                started_at_ts,
                finished_at_ts,
                plan_id,
                task_id,
                agent,
                scope,
                provider,
                runtime_context_hash,
                shared_prompt_version,
                shared_prompt_hash,
                agent_prompt_version,
                agent_prompt_hash,
                prompt_text2,
                response_text2,
                1 if prompt_truncated else 0,
                1 if response_truncated else 0,
                json.dumps(parsed_json, ensure_ascii=False) if parsed_json is not None else None,
                json.dumps(normalized_json, ensure_ascii=False) if normalized_json is not None else None,
                validator_error,
                error_code,
                error_message,
                json.dumps(meta2, ensure_ascii=False) if meta2 is not None else None,
            ),
        )
    except Exception:
        # Backward compatibility: if DB has not applied the truncation columns yet, retry old schema insert.
        try:
            conn.execute(
                """
                INSERT INTO llm_calls(
                  llm_call_id, created_at,
                  started_at_ts, finished_at_ts,
                  plan_id, task_id, agent, scope, provider,
                  runtime_context_hash,
                  shared_prompt_version, shared_prompt_hash,
                  agent_prompt_version, agent_prompt_hash,
                  prompt_text, response_text,
                  parsed_json, normalized_json,
                  validator_error, error_code, error_message,
                  meta_json
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    llm_call_id,
                    utc_now_iso(),
                    started_at_ts,
                    finished_at_ts,
                    plan_id,
                    task_id,
                    agent,
                    scope,
                    provider,
                    runtime_context_hash,
                    shared_prompt_version,
                    shared_prompt_hash,
                    agent_prompt_version,
                    agent_prompt_hash,
                    prompt_text2,
                    response_text2,
                    json.dumps(parsed_json, ensure_ascii=False) if parsed_json is not None else None,
                    json.dumps(normalized_json, ensure_ascii=False) if normalized_json is not None else None,
                    validator_error,
                    error_code,
                    error_message,
                    json.dumps(meta2, ensure_ascii=False) if meta2 is not None else None,
                ),
            )
        except Exception:
            return "UNKNOWN"
    return llm_call_id
