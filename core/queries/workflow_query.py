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
    started_at: Optional[str] = None
    top_task_hash: Optional[str] = None
    scopes: Optional[Sequence[str]] = None
    agent: Optional[str] = None
    only_errors: bool = False
    limit: int = 200


def _parse_meta(meta_json: Optional[str]) -> Dict[str, Any]:
    if not meta_json:
        return {}
    try:
        obj = json.loads(meta_json)
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


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


def _parse_stage(meta_json: Optional[str], *, scope: str) -> Tuple[str, int]:
    meta = _parse_meta(meta_json)
    stage = str(meta.get("stage") or "").strip().upper()
    if not stage:
        # Backward compatibility: older data may not include stage.
        if scope == "PLAN_GEN":
            stage = "STRUCTURE"
        elif scope == "PLAN_REVIEW":
            stage = "STRUCTURE"
        elif scope == "PLAN_RUBRIC":
            # Rubric is a pre-step for structure; keep it in the STRUCTURE lane to avoid an "UNKNOWN" lane.
            stage = "STRUCTURE"
        else:
            stage = "UNKNOWN"
    try:
        stage_attempt = int(meta.get("stage_attempt") or 1)
    except Exception:
        stage_attempt = 1
    if stage_attempt <= 0:
        stage_attempt = 1
    return stage, stage_attempt


def _safe_parse_json(s: Optional[str]) -> Optional[Dict[str, Any]]:
    if not s:
        return None
    try:
        obj = json.loads(s)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _extract_review_fields(obj: Optional[Dict[str, Any]]) -> Tuple[Optional[int], Optional[str]]:
    if not obj:
        return None, None
    # Common shapes:
    # - { total_score, action_required, ... }
    # - { review_result: { total_score, action_required, ... } }
    root = obj
    if isinstance(obj.get("review_result"), dict):
        root = obj["review_result"]  # type: ignore[assignment]
    score = root.get("total_score")
    action = root.get("action_required")
    try:
        score_i = int(score) if score is not None else None
    except Exception:
        score_i = None
    action_s = str(action).strip() if isinstance(action, str) else None
    return score_i, action_s


def build_workflow(conn: sqlite3.Connection, q: WorkflowQuery) -> Dict[str, Any]:
    """
    Build an LLM workflow graph from llm_calls, returning nodes/edges/groups.
    This is intended as SSOT for UI and is safe to call repeatedly (read-only).
    """
    cfg = get_runtime_config()

    where: List[str] = []
    params: List[Any] = []

    if q.started_at and str(q.started_at).strip():
        where.append("c.created_at >= ?")
        params.append(str(q.started_at).strip())
    if q.top_task_hash and str(q.top_task_hash).strip():
        # Support both:
        # - New data: llm_calls.top_task_hash is populated at write time.
        # - Legacy data: llm_calls.top_task_hash may be NULL; in that case, we group by
        #   stable_hash(normalized(plans.title)) and include calls whose plan_id belongs to that group.
        h = str(q.top_task_hash).strip()
        plan_ids_for_title_hash: List[str] = []
        try:
            import re
            import hashlib

            def _normalize_title_local(t: str) -> str:
                s = (t or "").strip()
                s = re.sub(r"\s+", " ", s)
                s = re.sub(r"\s*\([0-9a-fA-F-]{6,}\)\s*$", "", s).strip()
                return s

            def _hash_text_local(t: str) -> str:
                return hashlib.sha256(t.encode("utf-8")).hexdigest()

            plans = conn.execute("SELECT plan_id, title FROM plans").fetchall()
            for p in plans:
                title = _normalize_title_local(str(p["title"] or ""))
                if not title:
                    continue
                if _hash_text_local(title) == h:
                    plan_ids_for_title_hash.append(str(p["plan_id"]))
        except Exception:
            plan_ids_for_title_hash = []

        if plan_ids_for_title_hash:
            where.append(
                "("
                + "c.top_task_hash = ?"
                + " OR (c.top_task_hash IS NULL AND c.plan_id IN (" + ",".join(["?"] * len(plan_ids_for_title_hash)) + "))"
                + ")"
            )
            params.append(h)
            params.extend(plan_ids_for_title_hash)
        else:
            where.append("c.top_task_hash = ?")
            params.append(h)
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
        c.provider,
        c.meta_json,
        c.error_code,
        c.validator_error,
        c.normalized_json,
        c.parsed_json
      FROM llm_calls c
      LEFT JOIN task_nodes tn ON tn.task_id = c.task_id
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    # Prefer "latest N" to keep UI responsive while showing the most relevant recent history.
    # We then reverse in-memory to restore chronological order for rendering.
    sql += " ORDER BY c.created_at DESC LIMIT ?"
    params.append(int(q.limit))

    rows = list(conn.execute(sql, tuple(params)).fetchall())
    rows.reverse()

    # Total count for UI (showing N of M).
    total_sql = "SELECT COUNT(1) FROM llm_calls c"
    if where:
        total_sql += " WHERE " + " AND ".join(where)
    total_rows = int(conn.execute(total_sql, tuple(params[:-1] if params else [])).fetchone()[0]) if True else 0

    nodes: List[Dict[str, Any]] = []
    for r in rows:
        attempt, review_attempt = _parse_attempts(r["meta_json"])
        stage, stage_attempt = _parse_stage(r["meta_json"], scope=str(r["scope"] or ""))
        meta = _parse_meta(r["meta_json"])
        node_kind = "LLM_CALL"
        if str(r["scope"] or "") == "PLAN_GEN":
            if str(r["provider"] or "").strip().upper() == "SYSTEM" or str(meta.get("kind") or "").strip().upper() == "AUTO_STAGE_GEN":
                node_kind = "GEN_CLONE"
        total_score = None
        action_required = None
        if str(r["scope"] or "") == "PLAN_REVIEW":
            # Prefer normalized_json; fallback to parsed_json.
            obj = _safe_parse_json(r["normalized_json"]) or _safe_parse_json(r["parsed_json"])
            total_score, action_required = _extract_review_fields(obj)
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
                "stage": stage,
                "stage_attempt": stage_attempt,
                "node_kind": node_kind,
                "error_code": r["error_code"],
                "validator_error": r["validator_error"],
                "total_score": total_score,
                "action_required": action_required,
            }
        )

    edges: List[Dict[str, Any]] = []
    # Global time chain across raw LLM calls.
    for i in range(1, len(nodes)):
        edges.append({"from": nodes[i - 1]["llm_call_id"], "to": nodes[i]["llm_call_id"], "edge_type": "NEXT"})

    # Pairing: connect the most recent PLAN_GEN within the same stage to the next PLAN_REVIEW.
    last_gen_by_stage: Dict[str, str] = {}
    for n in nodes:
        scope = str(n.get("scope") or "")
        st = str(n.get("stage") or "STRUCTURE").upper()
        if scope == "PLAN_GEN":
            # Ignore "GEN_CLONE" (SYSTEM snapshot nodes) for pairing so reviews attach to the real generator.
            if str(n.get("node_kind") or "") != "GEN_CLONE":
                last_gen_by_stage[st] = str(n["llm_call_id"])
            continue
        if scope == "PLAN_REVIEW":
            src = last_gen_by_stage.get(st)
            if src:
                edges.append({"from": src, "to": str(n["llm_call_id"]), "edge_type": "PAIR", "stage": st})

    stage_order = ["STRUCTURE", "BINDINGS", "EXECUTION"]
    if not q.only_errors:
        for st in stage_order:
            lane = [n for n in nodes if str(n.get("stage") or "").upper() == st and str(n.get("scope") or "") in ("PLAN_GEN", "PLAN_REVIEW")]
            lane.sort(key=lambda x: (str(x.get("created_at") or ""), str(x.get("llm_call_id") or "")))
            for i in range(1, len(lane)):
                edges.append({"from": str(lane[i - 1]["llm_call_id"]), "to": str(lane[i]["llm_call_id"]), "edge_type": "STAGE_NEXT", "stage": st})

    nodes2 = nodes

    groups: List[Dict[str, Any]] = []
    by_attempt: Dict[int, List[str]] = {}
    for n in nodes2:
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
        "nodes": nodes2,
        "edges": edges,
        "groups": groups,
        "total_rows": total_rows,
        "returned_rows": len(rows),
        "ts": utc_now_iso(),
    }

