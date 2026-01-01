from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional


def _compute_depths(conn: sqlite3.Connection, *, plan_id: str, root_task_id: str) -> Dict[str, int]:
    edges = conn.execute(
        """
        SELECT from_task_id, to_task_id
        FROM task_edges
        WHERE plan_id = ? AND edge_type = 'DECOMPOSE'
        """,
        (plan_id,),
    ).fetchall()
    children: Dict[str, List[str]] = {}
    for e in edges:
        children.setdefault(str(e["from_task_id"]), []).append(str(e["to_task_id"]))
    depths: Dict[str, int] = {str(root_task_id): 0}
    stack = [str(root_task_id)]
    while stack:
        cur = stack.pop()
        d = depths.get(cur, 0)
        for ch in children.get(cur, []):
            if ch not in depths or depths[ch] > d + 1:
                depths[ch] = d + 1
                stack.append(ch)
    return depths


def _leaf_actions(conn: sqlite3.Connection, *, plan_id: str) -> List[sqlite3.Row]:
    """
    Leaf ACTION = ACTION with no active DECOMPOSE children.
    """
    return conn.execute(
        """
        SELECT n.task_id, n.title, n.estimated_person_days
        FROM task_nodes n
        WHERE n.plan_id = ?
          AND n.active_branch = 1
          AND n.node_type = 'ACTION'
          AND NOT EXISTS (
            SELECT 1
            FROM task_edges e
            JOIN task_nodes c ON c.task_id = e.to_task_id
            WHERE e.plan_id = n.plan_id
              AND e.edge_type = 'DECOMPOSE'
              AND e.from_task_id = n.task_id
              AND c.active_branch = 1
          )
        ORDER BY n.priority DESC, n.updated_at DESC
        """,
        (plan_id,),
    ).fetchall()


def feasibility_check(
    conn: sqlite3.Connection,
    *,
    plan_id: str,
    threshold_person_days: float,
    max_depth: int,
) -> Dict[str, Any]:
    """
    Feasibility focuses on v2 leaf ACTION nodes only:
    - estimated_person_days must exist
    - must be <= threshold_person_days
    Also reports depth-based ability to split further.
    """
    plan = conn.execute("SELECT plan_id, title, root_task_id FROM plans WHERE plan_id = ?", (plan_id,)).fetchone()
    if not plan:
        raise RuntimeError(f"plan not found: {plan_id}")
    depths = _compute_depths(conn, plan_id=plan_id, root_task_id=str(plan["root_task_id"]))
    leaves = _leaf_actions(conn, plan_id=plan_id)

    over: List[Dict[str, Any]] = []
    missing: List[Dict[str, Any]] = []
    for r in leaves:
        tid = str(r["task_id"])
        title = str(r["title"] or "")
        epd_raw = r["estimated_person_days"]
        depth = int(depths.get(tid, 0))
        can_split = depth < int(max_depth)
        if epd_raw is None:
            missing.append(
                {
                    "task_title": title,
                    "estimated_person_days": None,
                    "reason": "missing_estimate",
                    "depth": depth,
                    "max_depth": int(max_depth),
                    "can_split": bool(can_split),
                }
            )
            continue
        try:
            epd = float(epd_raw)
        except Exception:
            missing.append(
                {
                    "task_title": title,
                    "estimated_person_days": str(epd_raw),
                    "reason": "bad_estimate_type",
                    "depth": depth,
                    "max_depth": int(max_depth),
                    "can_split": bool(can_split),
                }
            )
            continue
        if epd > float(threshold_person_days) + 1e-9:
            over.append(
                {
                    "task_title": title,
                    "estimated_person_days": epd,
                    "reason": f"over_threshold>{float(threshold_person_days)}",
                    "depth": depth,
                    "max_depth": int(max_depth),
                    "can_split": bool(can_split),
                }
            )

    ok = (len(over) == 0) and (len(missing) == 0)
    return {
        "plan": {"plan_id": str(plan["plan_id"]), "title": str(plan["title"])},
        "threshold_person_days": float(threshold_person_days),
        "max_depth": int(max_depth),
        "leaf_action_count": len(leaves),
        "over_threshold": over,
        "missing_estimate": missing,
        "ok": bool(ok),
    }

