from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class AuditRow:
    scope: str
    agent: str
    total: int
    with_error_code: int
    with_validator_error: int


def _safe_json_loads(text: Optional[str]) -> Optional[object]:
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _keys(obj: object) -> List[str]:
    if isinstance(obj, dict):
        return [str(k) for k in obj.keys()]
    return []


def audit_llm_calls(
    conn: sqlite3.Connection,
    *,
    plan_id: Optional[str] = None,
    limit: int = 200,
) -> Tuple[List[AuditRow], Dict[str, Dict[str, int]]]:
    """
    Returns:
      - aggregated rows by (scope, agent)
      - observed key frequency by scope: {"PLAN_REVIEW": {"review_result": 12, ...}, ...}
    """
    params: List[Any] = []
    where = ""
    if plan_id:
        where = "WHERE plan_id = ?"
        params.append(plan_id)
    rows = conn.execute(
        f"""
        SELECT created_at, scope, agent, error_code, validator_error, parsed_json, normalized_json
        FROM llm_calls
        {where}
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (*params, int(limit)),
    ).fetchall()

    agg: Dict[Tuple[str, str], Dict[str, int]] = {}
    key_freq: Dict[str, Dict[str, int]] = {}

    for r in rows:
        scope = str(r["scope"] or "")
        agent = str(r["agent"] or "")
        k = (scope, agent)
        a = agg.setdefault(k, {"total": 0, "with_error_code": 0, "with_validator_error": 0})
        a["total"] += 1
        if r["error_code"]:
            a["with_error_code"] += 1
        if r["validator_error"]:
            a["with_validator_error"] += 1

        parsed = _safe_json_loads(r["parsed_json"])
        norm = _safe_json_loads(r["normalized_json"])
        for name, obj in (("parsed", parsed), ("normalized", norm)):
            if not isinstance(obj, dict):
                continue
            freq = key_freq.setdefault(scope, {})
            for kk in _keys(obj):
                freq[kk] = int(freq.get(kk, 0)) + 1
            # Also record common wrapper key presence explicitly.
            if "review_result" in obj:
                freq["__has_review_result__"] = int(freq.get("__has_review_result__", 0)) + 1
            if name == "parsed" and ("plan_json" in obj or "schema_version" in obj):
                freq["__looks_like_outer__"] = int(freq.get("__looks_like_outer__", 0)) + 1

    out_rows: List[AuditRow] = []
    for (scope, agent), v in sorted(agg.items(), key=lambda x: (x[0][0], x[0][1])):
        out_rows.append(
            AuditRow(
                scope=scope,
                agent=agent,
                total=int(v["total"]),
                with_error_code=int(v["with_error_code"]),
                with_validator_error=int(v["with_validator_error"]),
            )
        )

    return out_rows, key_freq

