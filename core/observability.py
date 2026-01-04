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
from core.workflow_events import WorkflowEvent, fetch_workflow_events


ReasonCode = str

SNAPSHOT_SCHEMA_SUMMARY: Dict[str, Any] = {
    "schema_version": "plan_snapshot_v1",
    "required_top_level_keys": [
        "schema_version",
        "ts",
        "plan",
        "job",
        "summary",
        "reasons",
        "inputs_needed",
        "waiting_review",
        "recent_errors",
        "final_deliverable",
        "doctor",
        "feasibility",
        "report",
        "manifest",
    ],
    "reason_codes": ["WAITING_REVIEW", "WAITING_INPUT", "WAITING_EXTERNAL", "BLOCKED", "FAILED", "RUNNABLE", "DONE"],
    "notes": "SSOT snapshot for CLI/backend/UI; adapters must not re-infer reasons.",
}


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


def _job_from_events(events: List[WorkflowEvent]) -> Dict[str, Any]:
    """
    Infer the latest active job (if any) from workflow_events for this plan.
    """
    finished: set[str] = set()
    active_job: Optional[WorkflowEvent] = None
    for e in events:
        jid = str(e.job_id or "").strip()
        if not jid:
            continue
        if e.event_type == "JOB_FINISHED":
            finished.add(jid)
            continue
        if e.event_type == "JOB_STARTED" and jid not in finished:
            active_job = e
            break

    def _brief(e: WorkflowEvent) -> Dict[str, Any]:
        return {
            "created_at": e.created_at,
            "workflow": e.workflow,
            "event_type": e.event_type,
            "severity": e.severity,
            "message": e.message,
            "payload": e.payload,
        }

    if not active_job:
        last = events[0] if events else None
        return {
            "active": False,
            "workflow": str(last.workflow) if last else None,
            "job_id": str(last.job_id) if (last and last.job_id) else None,
            "current_step": None,
            "step_started_at": None,
            "last_event": _brief(last) if last else None,
            "last_decision": next((_brief(e) for e in events if e.event_type == "DECISION_MADE"), None),
            "last_error": next((_brief(e) for e in events if e.event_type == "ERROR_RAISED"), None),
        }

    jid = str(active_job.job_id)
    step_finished: set[str] = set()
    current_step: Optional[str] = None
    step_started_at: Optional[str] = None
    last_event: Optional[Dict[str, Any]] = None
    last_decision: Optional[Dict[str, Any]] = None
    last_error: Optional[Dict[str, Any]] = None

    for e in events:
        if str(e.job_id or "") != jid:
            continue
        if last_event is None:
            last_event = _brief(e)
        if last_decision is None and e.event_type == "DECISION_MADE":
            last_decision = _brief(e)
        if last_error is None and e.event_type == "ERROR_RAISED":
            last_error = _brief(e)
        if e.event_type == "STEP_FINISHED":
            step = str(e.payload.get("step") or "").strip()
            if step:
                step_finished.add(step)
        if e.event_type == "STEP_STARTED" and current_step is None:
            step = str(e.payload.get("step") or "").strip()
            if step and step not in step_finished:
                current_step = step
                step_started_at = e.created_at
                break

    return {
        "active": True,
        "workflow": active_job.workflow,
        "job_id": jid,
        "current_step": current_step,
        "step_started_at": step_started_at,
        "last_event": last_event,
        "last_decision": last_decision,
        "last_error": last_error,
    }


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
        "schema_version": SNAPSHOT_SCHEMA_SUMMARY["schema_version"],
        "ts": utc_now_iso(),
        "plan": plan_meta,
        "job": _job_from_events(fetch_workflow_events(conn, plan_id=str(plan_id), limit=500)),
        "summary": report.get("summary") or {},
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
    job = snapshot.get("job") or {}
    summary = snapshot.get("summary") or {}
    reasons = snapshot.get("reasons") or []
    final_deliverable = snapshot.get("final_deliverable") or {}

    lines: List[str] = []
    lines.append(f"plan: {plan.get('title','')}")
    lines.append(f"plan_id: {plan.get('plan_id','')}")
    lines.append(f"workflow_mode: {plan.get('workflow_mode','')}")
    if bool(job.get("active")):
        wf = str(job.get("workflow") or "")
        step = str(job.get("current_step") or "")
        if wf or step:
            lines.append(f"job: {wf} step={step}".rstrip())
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
