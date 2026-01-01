from __future__ import annotations

import json
import math
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import config
from core.doctor import run_doctor
from core.reporting import generate_plan_report
from core.runtime_config import get_runtime_config
from core.util import ensure_dir, utc_now_iso


@dataclass(frozen=True)
class RewriteResult:
    patch_plan: Dict[str, Any]
    snapshot_path: Optional[Path] = None


def _now() -> str:
    return utc_now_iso()


def _safe_json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _to_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def _plan_meta(conn: sqlite3.Connection, plan_id: str) -> Dict[str, Any]:
    row = conn.execute("SELECT plan_id, title, root_task_id FROM plans WHERE plan_id = ?", (plan_id,)).fetchone()
    if not row:
        raise RuntimeError(f"plan not found: {plan_id}")
    return {"plan_id": str(row["plan_id"]), "title": str(row["title"]), "root_task_id": str(row["root_task_id"])}


def _row_by_title(conn: sqlite3.Connection, *, plan_id: str, node_type: str, title: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        """
        SELECT *
        FROM task_nodes
        WHERE plan_id = ? AND active_branch = 1 AND node_type = ? AND title = ?
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (plan_id, node_type, title),
    ).fetchone()


def _action_rows(conn: sqlite3.Connection, *, plan_id: str) -> List[sqlite3.Row]:
    return conn.execute(
        """
        SELECT task_id, title, owner_agent_id, status, estimated_person_days, deliverable_spec_json, acceptance_criteria_json
        FROM task_nodes
        WHERE plan_id = ? AND active_branch = 1 AND node_type = 'ACTION'
        ORDER BY priority DESC, updated_at DESC
        """,
        (plan_id,),
    ).fetchall()


def _check_rows(conn: sqlite3.Connection, *, plan_id: str) -> List[sqlite3.Row]:
    return conn.execute(
        """
        SELECT task_id, title, status, review_target_task_id
        FROM task_nodes
        WHERE plan_id = ? AND active_branch = 1 AND node_type = 'CHECK'
        """,
        (plan_id,),
    ).fetchall()


def _default_deliverable_spec(title: str) -> Dict[str, Any]:
    return {
        "format": "md",
        "filename": "deliverable.md",
        "single_file": True,
        "bundle_mode": "MANIFEST",
        "description": f"Deliverable for: {title}",
    }


def _default_acceptance_criteria() -> List[Dict[str, Any]]:
    return [
        {
            "id": "ac1",
            "type": "manual",
            "statement": "Meets the task requirements and is readable.",
            "check_method": "manual_review",
            "severity": "MED",
        }
    ]


def _compute_depths(conn: sqlite3.Connection, *, plan_id: str, root_task_id: str) -> Dict[str, int]:
    """
    Compute DECOMPOSE depth from root (root depth=0).
    """
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


def propose_rewrite(
    conn: sqlite3.Connection,
    plan_id: str,
    *,
    workflow_mode: str,
    one_shot_threshold_person_days: float,
    max_depth: int,
) -> Dict[str, Any]:
    """
    Produce a structured patch plan. Default is dry-run; no DB changes.
    Inputs are derived from:
    - report JSON (3A)
    - doctor findings (P0.5/P1.x)
    """
    plan = _plan_meta(conn, plan_id)
    report = generate_plan_report(conn, plan_id, workflow_mode=workflow_mode)
    doctor_findings = []
    try:
        doctor_findings = [f.to_dict() for f in run_doctor(conn, plan_id=plan_id, workflow_mode=workflow_mode)]
    except Exception:
        doctor_findings = []

    issues: List[Dict[str, Any]] = []
    for f in doctor_findings:
        if isinstance(f, dict):
            issues.append(
                {
                    "code": str(f.get("code") or ""),
                    "message": str(f.get("message") or ""),
                    "hint": str(f.get("hint") or ""),
                    "task_title": str(f.get("task_title") or ""),
                }
            )

    patches: List[Dict[str, Any]] = []
    risk_level = "LOW"
    risk_notes: List[str] = []

    if str(workflow_mode) != "v2":
        return {
            "plan": {"plan_id": plan["plan_id"], "title": plan["title"]},
            "issues": issues,
            "patches": [],
            "risk": {"level": "MED", "notes": ["workflow_mode is not v2; 3B only applies to v2."]},
            "next_steps": [{"cmd": "Set workflow_mode=v2 in runtime_config.json", "why": "Enable v2 rewrite tooling."}],
        }

    # Build working sets
    actions = _action_rows(conn, plan_id=plan_id)
    checks = _check_rows(conn, plan_id=plan_id)
    check_targets = {}
    for c in checks:
        t = str(c["review_target_task_id"] or "").strip()
        if t:
            check_targets.setdefault(t, []).append(str(c["task_id"]))

    # Depth map for split decisions
    depths = _compute_depths(conn, plan_id=plan_id, root_task_id=plan["root_task_id"])

    # 1) ADD_MISSING_V2_FIELDS
    missing_field_actions: List[Dict[str, Any]] = []
    for a in actions:
        missing = []
        if a["estimated_person_days"] is None:
            missing.append("estimated_person_days")
        if not str(a["deliverable_spec_json"] or "").strip():
            missing.append("deliverable_spec_json")
        if not str(a["acceptance_criteria_json"] or "").strip():
            missing.append("acceptance_criteria_json")
        if missing:
            missing_field_actions.append({"task_id": str(a["task_id"]), "title": str(a["title"]), "missing": missing})
    if missing_field_actions:
        patches.append(
            {
                "type": "ADD_MISSING_V2_FIELDS",
                "targets": missing_field_actions,
                "preview": {
                    "set_estimated_person_days": max(1.0, float(one_shot_threshold_person_days) * 0.5),
                    "deliverable_spec_default": _default_deliverable_spec("ACTION"),
                    "acceptance_criteria_default": _default_acceptance_criteria(),
                },
            }
        )

    # 2) ADD_CHECK_BINDING (for actions with zero checks)
    missing_check_actions: List[Dict[str, Any]] = []
    for a in actions:
        tid = str(a["task_id"])
        if tid not in check_targets:
            missing_check_actions.append({"task_id": tid, "title": str(a["title"])})
    if missing_check_actions:
        patches.append({"type": "ADD_CHECK_BINDING", "targets": missing_check_actions, "preview": {"new_check_status": "READY"}})

    # Multi-check risk (do not auto-delete)
    for aid, cids in check_targets.items():
        if len(cids) > 1:
            risk_level = "MED"
            ar = next((a for a in actions if str(a["task_id"]) == aid), None)
            risk_notes.append(f"Multiple CHECK nodes bound to one ACTION (will not auto-delete): action_title={str(ar['title'] if ar else aid)} count={len(cids)}")

    # 3) SPLIT_OVERSIZED_ACTION
    oversized: List[Dict[str, Any]] = []
    for a in actions:
        epd = _to_float(a["estimated_person_days"])
        if epd is None:
            continue
        if epd <= float(one_shot_threshold_person_days):
            continue
        aid = str(a["task_id"])
        depth = int(depths.get(aid, 0))
        apply_allowed = depth < int(max_depth)
        if not apply_allowed:
            risk_level = "MED"
            risk_notes.append(f"Split suggested but depth limit reached (will not apply): action_title={str(a['title'])} depth={depth} max_depth={int(max_depth)}")
        parts = int(math.ceil(epd / float(one_shot_threshold_person_days)))
        oversized.append(
            {
                "task_id": aid,
                "title": str(a["title"]),
                "estimated_person_days": epd,
                "parts": max(2, parts),
                "threshold": float(one_shot_threshold_person_days),
                "apply_allowed": apply_allowed,
            }
        )
    if oversized:
        patches.append({"type": "SPLIT_OVERSIZED_ACTION", "targets": oversized, "preview": {"child_node_type": "ACTION", "parent_node_type": "GOAL"}})

    if not patches:
        next_steps = [{"cmd": f"python agent_cli.py report --plan-id {plan_id}", "why": "No structural rewrite needed."}]
    else:
        next_steps = [{"cmd": f"python agent_cli.py rewrite --plan-id {plan_id} --apply", "why": "Apply the proposed patches (writes snapshot + DB changes)."}]

    return {
        "plan": {"plan_id": plan["plan_id"], "title": plan["title"]},
        "issues": issues,
        "patches": patches,
        "risk": {"level": risk_level, "notes": risk_notes},
        "next_steps": next_steps,
        "meta": {
            "workflow_mode": workflow_mode,
            "threshold_person_days": float(one_shot_threshold_person_days),
            "max_depth": int(max_depth),
        },
    }


def _snapshot_plan(conn: sqlite3.Connection, *, plan_id: str, snapshot_dir: Path, patch_plan: Dict[str, Any]) -> Path:
    ensure_dir(snapshot_dir)
    ts = _now().replace(":", "").replace("-", "")
    path = snapshot_dir / f"snapshot_{ts}.json"
    plan = conn.execute("SELECT * FROM plans WHERE plan_id = ?", (plan_id,)).fetchone()
    nodes = conn.execute("SELECT * FROM task_nodes WHERE plan_id = ?", (plan_id,)).fetchall()
    edges = conn.execute("SELECT * FROM task_edges WHERE plan_id = ?", (plan_id,)).fetchall()
    data = {
        "snapshot_at": _now(),
        "plan_id": plan_id,
        "patch_plan": patch_plan,
        "tables": {
            "plans": dict(plan) if plan else None,
            "task_nodes": [dict(r) for r in nodes],
            "task_edges": [dict(r) for r in edges],
        },
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _emit_rewrite_event(conn: sqlite3.Connection, *, plan_id: str, event_type: str, patch_plan: Dict[str, Any], snapshot_path: Optional[Path] = None) -> None:
    payload = {
        "event_type": event_type,
        "patch_types": [p.get("type") for p in (patch_plan.get("patches") or [])],
        "risk": patch_plan.get("risk"),
        "snapshot_path": str(snapshot_path) if snapshot_path else None,
    }
    conn.execute(
        "INSERT INTO task_events(event_id, plan_id, task_id, event_type, payload_json, created_at) VALUES(?, ?, NULL, ?, ?, ?)",
        (str(uuid.uuid4()), plan_id, event_type, json.dumps(payload, ensure_ascii=False), _now()),
    )


def apply_rewrite(conn: sqlite3.Connection, patch_plan: Dict[str, Any], *, dry_run: bool = True) -> RewriteResult:
    """
    Apply a patch plan to the DB. For MVP:
    - snapshot is always written when dry_run=False
    - changes are applied in a single transaction
    - rollback is not implemented; snapshot enables manual restore
    """
    plan_id = str(((patch_plan.get("plan") or {}).get("plan_id")) or "").strip()
    if not plan_id:
        raise RuntimeError("patch_plan.plan.plan_id missing")

    snapshot_path: Optional[Path] = None
    if dry_run:
        return RewriteResult(patch_plan=patch_plan, snapshot_path=None)

    snapshot_dir = config.WORKSPACE_DIR / "rewrites" / plan_id
    snapshot_path = _snapshot_plan(conn, plan_id=plan_id, snapshot_dir=snapshot_dir, patch_plan=patch_plan)
    from core.db import transaction

    # Apply patches in a single transaction.
    with transaction(conn):
        _emit_rewrite_event(conn, plan_id=plan_id, event_type="REWRITE_PROPOSED", patch_plan=patch_plan, snapshot_path=snapshot_path)
        for p in patch_plan.get("patches") or []:
            ptype = str(p.get("type") or "")
            if ptype == "ADD_MISSING_V2_FIELDS":
                for t in p.get("targets") or []:
                    tid = str(t.get("task_id") or "")
                    if not tid:
                        continue
                    row = conn.execute(
                        "SELECT title, estimated_person_days, deliverable_spec_json, acceptance_criteria_json FROM task_nodes WHERE task_id = ?",
                        (tid,),
                    ).fetchone()
                    if not row:
                        continue
                    title = str(row["title"] or "")
                    epd = row["estimated_person_days"]
                    deliverable_json = str(row["deliverable_spec_json"] or "").strip()
                    acceptance_json = str(row["acceptance_criteria_json"] or "").strip()
                    updates: Dict[str, Any] = {}
                    if epd is None:
                        meta = patch_plan.get("meta") or {}
                        threshold = float(meta.get("threshold_person_days") or 10)
                        updates["estimated_person_days"] = max(1.0, threshold * 0.5)
                    if not deliverable_json:
                        updates["deliverable_spec_json"] = _safe_json_dumps(_default_deliverable_spec(title))
                    if not acceptance_json:
                        updates["acceptance_criteria_json"] = _safe_json_dumps(_default_acceptance_criteria())
                    if updates:
                        sets = ", ".join(f"{k} = ?" for k in updates.keys())
                        params = list(updates.values()) + [_now(), tid]
                        conn.execute(f"UPDATE task_nodes SET {sets}, updated_at = ? WHERE task_id = ?", params)

            elif ptype == "ADD_CHECK_BINDING":
                for t in p.get("targets") or []:
                    target_id = str(t.get("task_id") or "")
                    if not target_id:
                        continue
                    # Do not create if already exists.
                    exists = conn.execute(
                        "SELECT COUNT(1) AS cnt FROM task_nodes WHERE plan_id = ? AND node_type='CHECK' AND review_target_task_id = ? AND active_branch = 1",
                        (plan_id, target_id),
                    ).fetchone()
                    if exists and int(exists["cnt"]) > 0:
                        continue
                    check_id = str(uuid.uuid4())
                    title = f"Review: {str(t.get('title') or 'ACTION')}"
                    now = _now()
                    conn.execute(
                        """
                        INSERT INTO task_nodes(
                          task_id, plan_id, node_type, title, owner_agent_id, priority, status, blocked_reason, attempt_count,
                          confidence, active_branch, active_artifact_id, created_at, updated_at,
                          review_target_task_id
                        )
                        VALUES(?, ?, 'CHECK', ?, 'xiaojing', 0, 'READY', NULL, 0, 0.5, 1, NULL, ?, ?, ?)
                        """,
                        (check_id, plan_id, title, now, now, target_id),
                    )

            elif ptype == "SPLIT_OVERSIZED_ACTION":
                for t in p.get("targets") or []:
                    parent_id = str(t.get("task_id") or "")
                    if not parent_id:
                        continue
                    if bool(t.get("apply_allowed")) is False:
                        continue
                    row = conn.execute(
                        """
                        SELECT task_id, title, owner_agent_id, priority, estimated_person_days, deliverable_spec_json, acceptance_criteria_json
                        FROM task_nodes
                        WHERE task_id = ? AND plan_id = ? AND node_type='ACTION' AND active_branch = 1
                        """,
                        (parent_id, plan_id),
                    ).fetchone()
                    if not row:
                        continue
                    epd = _to_float(row["estimated_person_days"]) or 0.0
                    meta = patch_plan.get("meta") or {}
                    threshold = float(meta.get("threshold_person_days") or 10)
                    parts = int(math.ceil(epd / threshold)) if threshold > 0 else 2
                    parts = max(2, parts)

                    # Deactivate any CHECKs bound to this ACTION to avoid invalid bindings after conversion.
                    conn.execute(
                        """
                        UPDATE task_nodes
                        SET status='ABANDONED', blocked_reason=NULL, review_target_task_id=NULL, updated_at=?
                        WHERE plan_id=? AND node_type='CHECK' AND review_target_task_id = ?
                        """,
                        (_now(), plan_id, parent_id),
                    )

                    # Convert parent ACTION -> GOAL (keeps task_id so existing edges remain).
                    conn.execute(
                        "UPDATE task_nodes SET node_type='GOAL', status='PENDING', blocked_reason=NULL, updated_at=? WHERE task_id=?",
                        (_now(), parent_id),
                    )

                    # Create child ACTIONs + their CHECKs + DECOMPOSE edges.
                    remaining = epd
                    child_ids: List[str] = []
                    for i in range(parts):
                        child_epd = min(threshold, remaining) if i < parts - 1 else max(0.1, remaining)
                        remaining = max(0.0, remaining - child_epd)
                        child_id = str(uuid.uuid4())
                        child_ids.append(child_id)
                        child_title = f"{str(row['title'] or 'Task')} (Part {i+1}/{parts})"
                        now = _now()
                        conn.execute(
                            """
                            INSERT INTO task_nodes(
                              task_id, plan_id, node_type, title, goal_statement, rationale,
                              owner_agent_id, priority, status, blocked_reason, attempt_count, confidence, active_branch,
                              active_artifact_id, created_at, updated_at,
                              estimated_person_days, deliverable_spec_json, acceptance_criteria_json
                            )
                            VALUES(?, ?, 'ACTION', ?, NULL, NULL, ?, ?, 'PENDING', NULL, 0, 0.5, 1, NULL, ?, ?, ?, ?, ?)
                            """,
                            (
                                child_id,
                                plan_id,
                                child_title,
                                str(row["owner_agent_id"] or "xiaobo"),
                                int(row["priority"] or 0),
                                now,
                                now,
                                float(child_epd),
                                str(row["deliverable_spec_json"] or "") or _safe_json_dumps(_default_deliverable_spec(child_title)),
                                str(row["acceptance_criteria_json"] or "") or _safe_json_dumps(_default_acceptance_criteria()),
                            ),
                        )
                        # 1:1 CHECK for child
                        check_id = str(uuid.uuid4())
                        conn.execute(
                            """
                            INSERT INTO task_nodes(
                              task_id, plan_id, node_type, title, owner_agent_id, priority, status, blocked_reason,
                              attempt_count, confidence, active_branch, active_artifact_id, created_at, updated_at,
                              review_target_task_id
                            )
                            VALUES(?, ?, 'CHECK', ?, 'xiaojing', 0, 'READY', NULL, 0, 0.5, 1, NULL, ?, ?, ?)
                            """,
                            (check_id, plan_id, f"Review: {child_title}", now, now, child_id),
                        )
                        # DECOMPOSE edge parent->child
                        conn.execute(
                            """
                            INSERT INTO task_edges(edge_id, plan_id, from_task_id, to_task_id, edge_type, metadata_json, created_at)
                            VALUES(?, ?, ?, ?, 'DECOMPOSE', '{"and_or":"AND"}', ?)
                            """,
                            (str(uuid.uuid4()), plan_id, parent_id, child_id, now),
                        )

            else:
                # Unknown patch type: ignore (forward-compat).
                continue
        _emit_rewrite_event(conn, plan_id=plan_id, event_type="REWRITE_APPLIED", patch_plan=patch_plan, snapshot_path=snapshot_path)

    return RewriteResult(patch_plan=patch_plan, snapshot_path=snapshot_path)


def render_patch_plan_md(patch_plan: Dict[str, Any]) -> str:
    plan = patch_plan.get("plan") or {}
    risk = patch_plan.get("risk") or {}
    patches = patch_plan.get("patches") or []
    issues = patch_plan.get("issues") or []
    lines: List[str] = []
    lines.append(f"# Rewrite Proposal: {plan.get('title','')}")
    lines.append("")
    lines.append(f"- plan_id: {plan.get('plan_id','')}")
    lines.append(f"- patch_count: {len(patches)}")
    lines.append(f"- risk: {risk.get('level','')}")
    for rn in (risk.get("notes") or [])[:10]:
        lines.append(f"  - {rn}")
    lines.append("")

    lines.append("## Issues")
    if not issues:
        lines.append("- (none)")
    else:
        for it in issues[:20]:
            code = str(it.get("code") or "")
            msg = str(it.get("message") or "")
            title = str(it.get("task_title") or "")
            suffix = f" (task={title})" if title else ""
            lines.append(f"- {code}: {msg}{suffix}".strip())
    lines.append("")

    lines.append("## Patches")
    if not patches:
        lines.append("- (none)")
    else:
        for p in patches:
            t = str(p.get("type") or "")
            lines.append(f"- {t}")
            preview = p.get("preview")
            if preview:
                lines.append(f"  - preview: {json.dumps(preview, ensure_ascii=False)[:220]}")
            targets = p.get("targets") or []
            if targets:
                lines.append("  - targets:")
                for tg in targets[:12]:
                    title = str(tg.get("title") or "")
                    missing = tg.get("missing")
                    extra = f" missing={missing}" if missing else ""
                    apply_allowed = tg.get("apply_allowed")
                    if apply_allowed is False:
                        extra += " apply_allowed=false"
                    lines.append(f"    - {title}{extra}".strip())
    lines.append("")

    lines.append("## Next Steps")
    for s in (patch_plan.get("next_steps") or [])[:10]:
        lines.append(f"- {s.get('cmd','')}")
        why = str(s.get("why") or "").strip()
        if why:
            lines.append(f"  - why: {why}")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def propose_rewrite_from_runtime(conn: sqlite3.Connection, plan_id: str, *, workflow_mode: str) -> Dict[str, Any]:
    cfg = get_runtime_config()
    return propose_rewrite(
        conn,
        plan_id,
        workflow_mode=workflow_mode,
        one_shot_threshold_person_days=float(cfg.one_shot_threshold_person_days),
        max_depth=int(cfg.max_decomposition_depth),
    )
