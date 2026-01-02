from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import config
from core.doctor import run_doctor
from core.feasibility_v2 import feasibility_check
from core.reporting import generate_plan_report, render_plan_report_md
from core.runtime_config import get_runtime_config
from core.util import utc_now_iso


ReasonCode = str


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _summarize_reasons(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Produce a stable reason list for UI/CLI.
    """
    nodes = report.get("nodes") or {}
    blocked = nodes.get("blocked") or []
    failed = nodes.get("failed") or []
    waiting_review = nodes.get("waiting_review") or []
    ready = nodes.get("ready") or []

    reasons: List[Dict[str, Any]] = []
    if waiting_review:
        reasons.append({"code": "WAITING_REVIEW", "count": len(waiting_review), "example": (waiting_review[0].get("task_title") if waiting_review else "")})
    if blocked:
        # Split WAITING_INPUT / WAITING_EXTERNAL (based on blocked_reason)
        waiting_input = [b for b in blocked if str(b.get("blocked_reason") or "") == "WAITING_INPUT"]
        waiting_external = [b for b in blocked if str(b.get("blocked_reason") or "") == "WAITING_EXTERNAL"]
        other = [b for b in blocked if b not in waiting_input and b not in waiting_external]
        if waiting_input:
            reasons.append({"code": "WAITING_INPUT", "count": len(waiting_input), "example": waiting_input[0].get("task_title")})
        if waiting_external:
            reasons.append({"code": "WAITING_EXTERNAL", "count": len(waiting_external), "example": waiting_external[0].get("task_title")})
        if other:
            reasons.append({"code": "BLOCKED", "count": len(other), "example": other[0].get("task_title")})
    if failed:
        reasons.append({"code": "FAILED", "count": len(failed), "example": failed[0].get("task_title")})
    if ready:
        reasons.append({"code": "RUNNABLE", "count": len(ready), "example": ready[0].get("task_title")})

    # If no reasons and plan is done, mark DONE.
    summary = report.get("summary") or {}
    if not reasons and bool(summary.get("is_done")):
        reasons.append({"code": "DONE", "count": 1, "example": str((report.get("plan") or {}).get("title") or "")})
    return reasons


def get_plan_snapshot(conn: sqlite3.Connection, plan_id: str, *, workflow_mode: str) -> Dict[str, Any]:
    """
    Single Source of Truth snapshot used by:
    - CLI status --brief
    - CLI report (derived)
    - UI backend /api/plan_snapshot
    """
    cfg = get_runtime_config()
    report = generate_plan_report(conn, plan_id, workflow_mode=workflow_mode)
    doctor_findings = [f.to_dict() for f in run_doctor(conn, plan_id=plan_id, workflow_mode=workflow_mode)]
    feas: Optional[Dict[str, Any]] = None
    if str(workflow_mode) == "v2":
        feas = feasibility_check(
            conn,
            plan_id=plan_id,
            threshold_person_days=float(cfg.one_shot_threshold_person_days),
            max_depth=int(cfg.max_decomposition_depth),
        )

    deliver_dir = config.DELIVERABLES_DIR / str(plan_id)
    final_obj = _read_json(deliver_dir / "final.json")
    manifest_obj = _read_json(deliver_dir / "manifest.json")
    final_deliverable = None
    if final_obj:
        final_deliverable = {
            "deliverables_dir": str(deliver_dir),
            "final_entrypoint": str(final_obj.get("final_entrypoint") or ""),
            "how_to_run": final_obj.get("how_to_run") if isinstance(final_obj.get("how_to_run"), list) else [],
            "final_task_title": str(final_obj.get("final_task_title") or ""),
            "final_artifact_id": str(final_obj.get("final_artifact_id") or ""),
        }

    plan_meta = dict(report.get("plan") or {})
    plan_meta["workflow_mode"] = str(workflow_mode)

    snapshot = {
        "ts": utc_now_iso(),
        "plan": plan_meta,
        "summary": report.get("summary"),
        "reasons": _summarize_reasons(report),
        "inputs_needed": report.get("inputs_needed") or [],
        "waiting_review": (report.get("nodes") or {}).get("waiting_review") or [],
        "recent_errors": report.get("recent_errors") or [],
        "final_deliverable": final_deliverable,
        "doctor": {"ok": len(doctor_findings) == 0, "findings": doctor_findings},
        "feasibility": feas,
        "report": report,  # keep full report JSON for UI drilldown
        "manifest": manifest_obj,
    }
    return snapshot


def render_snapshot_brief(snapshot: Dict[str, Any]) -> str:
    plan = snapshot.get("plan") or {}
    summary = snapshot.get("summary") or {}
    reasons = snapshot.get("reasons") or []
    final_deliverable = snapshot.get("final_deliverable") or {}

    lines: List[str] = []
    lines.append(f"plan: {plan.get('title','')}")
    lines.append(f"plan_id: {plan.get('plan_id','')}")
    lines.append(f"workflow_mode: {plan.get('workflow_mode','')}")
    lines.append("")

    if bool(summary.get("is_done")):
        lines.append("status: DONE")
    else:
        lines.append("status: NOT_DONE")
    lines.append("")

    if reasons:
        lines.append("reasons:")
        for r in reasons[:8]:
            lines.append(f"- {r.get('code')}: {r.get('count')}")
        lines.append("")

    if final_deliverable and final_deliverable.get("final_entrypoint"):
        lines.append(f"final_entrypoint: {final_deliverable.get('final_entrypoint')}")
        how = final_deliverable.get("how_to_run") if isinstance(final_deliverable.get("how_to_run"), list) else []
        if how:
            lines.append("how_to_run:")
            for s in how[:6]:
                lines.append(f"- {s}")
        lines.append("")

    # Next steps: derived from report.next_steps
    report = snapshot.get("report") or {}
    next_steps = report.get("next_steps") or []
    if next_steps:
        lines.append("next_steps:")
        for s in next_steps[:8]:
            cmd = str(s.get("cmd") or "").strip()
            if cmd:
                lines.append(f"- {cmd}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_snapshot_md(snapshot: Dict[str, Any]) -> str:
    """
    A single markdown that combines report + doctor + feasibility + final pointer.
    """
    report = snapshot.get("report") or {}
    md = render_plan_report_md(report)
    lines = [md.rstrip(), ""]

    doctor = snapshot.get("doctor") or {}
    lines.append("## Doctor")
    if doctor.get("ok"):
        lines.append("- OK")
    else:
        for f in (doctor.get("findings") or [])[:30]:
            if not isinstance(f, dict):
                continue
            head = f"- {f.get('code')}: {f.get('message')}"
            title = f.get("task_title")
            if title:
                head += f" (task={title})"
            lines.append(head)
            hint = f.get("hint")
            if hint:
                lines.append(f"  - hint: {hint}")
    lines.append("")

    feas = snapshot.get("feasibility")
    if isinstance(feas, dict):
        lines.append("## Feasibility (v2)")
        lines.append(f"- ok: {bool(feas.get('ok'))}")
        lines.append(f"- threshold_person_days: {feas.get('threshold_person_days')}")
        over = feas.get("over_threshold") or []
        if over:
            lines.append("- over_threshold:")
            for it in over[:20]:
                lines.append(f"  - {it.get('task_title','')}: {it.get('estimated_person_days')}d ({it.get('reason','')}) can_split={it.get('can_split')}")
        miss = feas.get("missing_estimate") or []
        if miss:
            lines.append("- missing_estimate:")
            for it in miss[:20]:
                lines.append(f"  - {it.get('task_title','')}: {it.get('reason','')}")
        lines.append("")

    final_deliverable = snapshot.get("final_deliverable") or {}
    lines.append("## Final Deliverable")
    if final_deliverable and final_deliverable.get("final_entrypoint"):
        lines.append(f"- deliverables_dir: {final_deliverable.get('deliverables_dir')}")
        lines.append(f"- final_entrypoint: {final_deliverable.get('final_entrypoint')}")
    else:
        lines.append("- (not exported yet)")
    lines.append("")

    return "\n".join(lines).rstrip() + "\n"
