from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


class FinalDeliverableError(RuntimeError):
    pass


def _parse_json_obj(text: str) -> Optional[Dict[str, Any]]:
    s = (text or "").strip()
    if not s:
        return None
    try:
        obj = json.loads(s)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _tags_list(tags_json: str) -> List[str]:
    try:
        t = json.loads(tags_json or "[]")
    except Exception:
        return []
    if not isinstance(t, list):
        return []
    return [x for x in t if isinstance(x, str)]


def _is_finalish(title: str, tags: List[str]) -> bool:
    t = (title or "").lower()
    tagset = {x.lower() for x in (tags or [])}
    return ("final" in tagset) or ("package" in tagset) or ("final" in t) or ("package" in t)


def pick_final_deliverable(
    conn: sqlite3.Connection,
    *,
    plan_id: str,
    include_candidates: bool = False,
) -> Dict[str, Any]:
    """
    Pick a single "final deliverable" for a plan.

    Priority:
    1) root GOAL.final_deliverable_spec_json (if present) to match a DONE ACTION artifact filename/format.
    2) DONE ACTION with tags final/package (or title contains).
    3) Most recently created DONE ACTION approved artifact.

    By default, only considers approved artifacts. If include_candidates=True, it may fall back to active artifacts.
    """
    plan = conn.execute("SELECT root_task_id FROM plans WHERE plan_id = ?", (plan_id,)).fetchone()
    if not plan:
        raise FinalDeliverableError(f"plan not found: {plan_id}")
    root_task_id = str(plan["root_task_id"])
    root = conn.execute(
        """
        SELECT final_deliverable_spec_json
        FROM task_nodes
        WHERE task_id = ? AND plan_id = ? AND node_type = 'GOAL'
        """,
        (root_task_id, plan_id),
    ).fetchone()
    spec = _parse_json_obj(str(root["final_deliverable_spec_json"] or "")) if root else None
    desired_filename = str((spec or {}).get("filename") or "").strip()
    desired_format = str((spec or {}).get("format") or "").strip().lower()

    # Candidate rows: only DONE ACTION.
    rows = conn.execute(
        """
        SELECT
          n.task_id,
          n.title,
          n.tags_json,
          n.approved_artifact_id,
          n.active_artifact_id,
          a.artifact_id,
          a.format AS artifact_format,
          a.path AS artifact_path,
          a.created_at AS artifact_created_at
        FROM task_nodes n
        LEFT JOIN artifacts a ON a.artifact_id = (
          CASE
            WHEN n.approved_artifact_id IS NOT NULL THEN n.approved_artifact_id
            WHEN ? THEN n.active_artifact_id
            ELSE NULL
          END
        )
        WHERE n.plan_id = ?
          AND n.active_branch = 1
          AND n.node_type = 'ACTION'
          AND n.status = 'DONE'
          AND a.artifact_id IS NOT NULL
        ORDER BY a.created_at DESC
        """,
        (1 if include_candidates else 0, plan_id),
    ).fetchall()
    if not rows:
        raise FinalDeliverableError("No approved deliverables found. Next: run CHECK reviews so ACTION nodes get approved_artifact_id, then re-run export.")

    def score(r: sqlite3.Row) -> tuple:
        tags = _tags_list(str(r["tags_json"] or "[]"))
        title = str(r["title"] or "")
        p = Path(str(r["artifact_path"] or ""))
        name_match = 1 if desired_filename and p.name.lower() == desired_filename.lower() else 0
        fmt_match = 1 if desired_format and str(r["artifact_format"] or "").lower() == desired_format else 0
        spec_match = 10 if (name_match and (not desired_format or fmt_match)) else (5 if name_match else (3 if fmt_match else 0))
        finalish = 2 if _is_finalish(title, tags) else 0
        return (spec_match, finalish, str(r["artifact_created_at"] or ""), title)

    best = sorted(rows, key=score, reverse=True)[0]
    src_path = Path(str(best["artifact_path"] or ""))
    fmt = str(best["artifact_format"] or "").lower()

    reasoning = []
    if desired_filename or desired_format:
        reasoning.append("matched_root_final_deliverable_spec" if score(best)[0] >= 5 else "root_spec_present_but_not_matched")
    if _is_finalish(str(best["title"] or ""), _tags_list(str(best["tags_json"] or "[]"))):
        reasoning.append("final_tag_or_title")
    reasoning.append("latest_approved_artifact_fallback")

    entrypoint_filename = src_path.name if src_path.name else (desired_filename or "deliverable")
    if fmt == "html" and not entrypoint_filename.lower().endswith(".html"):
        entrypoint_filename = entrypoint_filename + ".html"

    return {
        "task_id": str(best["task_id"]),
        "task_title": str(best["title"] or ""),
        "artifact_id": str(best["artifact_id"] or ""),
        "source_path": str(src_path),
        "format": fmt,
        "entrypoint_filename": entrypoint_filename,
        "reasoning": reasoning,
    }

