from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.runtime_config import get_runtime_config
from core.util import utc_now_iso


@dataclass(frozen=True)
class WorkflowQuery:
    plan_id: Optional[str] = None
    plan_id_missing: bool = False
    scopes: Optional[Sequence[str]] = None
    agent: Optional[str] = None
    only_errors: bool = False
    limit: int = 200


def _parse_attempts(meta_json: Optional[str]) -> Tuple[int, int]:
    if not meta_json:
        return 1, 1
    try:
        obj = json.loads(meta_json)
    except Exception:
        return 1, 1
    if not isinstance(obj, dict):
        return 1, 1
    a = obj.get("attempt", 1)
    ra = obj.get("review_attempt", 1)
    try:
        a2 = int(a)
    except Exception:
        a2 = 1
    try:
        ra2 = int(ra)
    except Exception:
        ra2 = 1
    if a2 <= 0:
        a2 = 1
    if ra2 <= 0:
        ra2 = 1
    return a2, ra2


def build_workflow(conn: sqlite3.Connection, q: WorkflowQuery) -> Dict[str, Any]:
    """
    Build an LLM workflow graph from llm_calls, returning nodes/edges/groups.
    This is intended as SSOT for UI and is safe to call repeatedly (read-only).
    """
    cfg = get_runtime_config()

    where: List[str] = []
    params: List[Any] = []

    if q.plan_id_missing:
        where.append("c.plan_id IS NULL")
    elif q.plan_id and str(q.plan_id).strip():
        where.append("c.plan_id = ?")
        params.append(str(q.plan_id).strip())

    if q.agent and str(q.agent).strip():
        where.append("c.agent = ?")
        params.append(str(q.agent).strip())

    scopes = [s.strip() for s in (q.scopes or []) if isinstance(s, str) and s.strip()]
    if scopes:
        where.append("c.scope IN (" + ",".join(["?"] * len(scopes)) + ")")
        params.extend(scopes)

    if q.only_errors:
        where.append("((c.error_code IS NOT NULL AND c.error_code != '') OR (c.validator_error IS NOT NULL AND c.validator_error != ''))")

    sql = """
      SELECT
        c.llm_call_id,
        c.created_at,
        c.plan_id,
        c.task_id,
        tn.title AS task_title,
        c.agent,
        c.scope,
        c.meta_json,
        c.error_code,
        c.validator_error
      FROM llm_calls c
      LEFT JOIN task_nodes tn ON tn.task_id = c.task_id
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY c.created_at ASC LIMIT ?"
    params.append(int(q.limit))

    rows = conn.execute(sql, tuple(params)).fetchall()

    nodes: List[Dict[str, Any]] = []
    for r in rows:
        attempt, review_attempt = _parse_attempts(r["meta_json"])
        nodes.append(
            {
                "llm_call_id": r["llm_call_id"],
                "created_at": r["created_at"],
                "plan_id": r["plan_id"],
                "task_id": r["task_id"],
                "task_title": r["task_title"],
                "agent": r["agent"],
                "scope": r["scope"],
                "attempt": attempt,
                "review_attempt": review_attempt,
                "error_code": r["error_code"],
                "validator_error": r["validator_error"],
            }
        )

    edges: List[Dict[str, Any]] = []
    for i in range(1, len(nodes)):
        edges.append({"from": nodes[i - 1]["llm_call_id"], "to": nodes[i]["llm_call_id"], "edge_type": "NEXT"})

    # MVP pairing: within an attempt, connect the most recent PLAN_GEN to the next PLAN_REVIEW.
    last_gen_by_attempt: Dict[int, str] = {}
    for n in nodes:
        attempt = int(n.get("attempt") or 1)
        scope = str(n.get("scope") or "")
        if scope == "PLAN_GEN":
            last_gen_by_attempt[attempt] = str(n["llm_call_id"])
        elif scope == "PLAN_REVIEW":
            gen = last_gen_by_attempt.get(attempt)
            if gen:
                edges.append({"from": gen, "to": str(n["llm_call_id"]), "edge_type": "PAIR"})
                last_gen_by_attempt.pop(attempt, None)

    groups: List[Dict[str, Any]] = []
    by_attempt: Dict[int, List[str]] = {}
    for n in nodes:
        a = int(n.get("attempt") or 1)
        by_attempt.setdefault(a, []).append(str(n["llm_call_id"]))
    for a in sorted(by_attempt.keys()):
        groups.append({"group_type": "ATTEMPT", "id": f"attempt_{a}", "attempt": a, "node_ids": by_attempt[a]})

    plan_meta: Dict[str, Any] = {"plan_id": q.plan_id if q.plan_id else None, "title": None, "workflow_mode": str(cfg.workflow_mode)}
    if q.plan_id and str(q.plan_id).strip():
        p = conn.execute("SELECT plan_id, title FROM plans WHERE plan_id=?", (str(q.plan_id).strip(),)).fetchone()
        if p:
            plan_meta["plan_id"] = p["plan_id"]
            plan_meta["title"] = p["title"]

    return {
        "schema_version": "workflow_v1",
        "plan": plan_meta,
        "nodes": nodes,
        "edges": edges,
        "groups": groups,
        "ts": utc_now_iso(),
    }

