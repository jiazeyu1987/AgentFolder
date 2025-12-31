from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any, Dict, Optional

from core.util import utc_now_iso


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
                prompt_text,
                response_text,
                json.dumps(parsed_json, ensure_ascii=False) if parsed_json is not None else None,
                json.dumps(normalized_json, ensure_ascii=False) if normalized_json is not None else None,
                validator_error,
                error_code,
                error_message,
                json.dumps(meta or {}, ensure_ascii=False) if meta is not None else None,
            ),
        )
    except Exception:
        return "UNKNOWN"
    return llm_call_id

