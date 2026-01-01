from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import config
from core.doctor import doctor_plan
from core.events import emit_event
from core.feasibility_v2 import feasibility_check
from core.rewriter_v2 import apply_rewrite, propose_rewrite
from core.util import ensure_dir, utc_now_iso


@dataclass(frozen=True)
class ConvergeResult:
    status: str  # "OK" | "REQUEST_EXTERNAL_INPUT"
    rounds: int
    plan_id: str
    required_docs_path: Optional[Path] = None
    required_docs: Optional[List[Dict[str, Any]]] = None


def _write_plan_required_docs(*, plan_id: str, required_docs: List[Dict[str, Any]]) -> Path:
    ensure_dir(config.REQUIRED_DOCS_DIR)
    path = config.REQUIRED_DOCS_DIR / f"plan_{plan_id}.md"
    lines = [
        f"# Required Docs for plan {plan_id}",
        "",
        f"> NOTE: System will auto-search `{config.BASELINE_INPUTS_DIR.as_posix()}/` first. If not found, place files under `{config.INPUTS_DIR.as_posix()}/` as suggested below.",
        "",
    ]
    for doc in required_docs:
        lines.append(f"- {doc.get('name','')}: {doc.get('description','')}")
        if doc.get("accepted_types"):
            lines.append(f"  - accepted_types: {doc['accepted_types']}")
        if doc.get("suggested_path"):
            lines.append(f"  - suggested_path: {doc['suggested_path']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def converge_v2_plan(
    conn: sqlite3.Connection,
    *,
    plan_id: str,
    max_rounds: int,
    threshold_person_days: float,
    max_depth: int,
) -> ConvergeResult:
    """
    Deterministic convergence loop (no real LLM):
    - If doctor(v2) fails or feasibility fails, apply structural rewrites (3B) up to max_rounds.
    - If unable to make progress (no applicable patches), request external input with a concrete list.
    """
    plan_row = conn.execute("SELECT plan_id FROM plans WHERE plan_id = ?", (plan_id,)).fetchone()
    if not plan_row:
        raise RuntimeError(f"plan not found: {plan_id}")

    last_required: Optional[List[Dict[str, Any]]] = None
    for round_idx in range(1, int(max_rounds) + 1):
        ok_doctor, findings = doctor_plan(conn, plan_id=plan_id, workflow_mode="v2")
        feas = feasibility_check(conn, plan_id=plan_id, threshold_person_days=threshold_person_days, max_depth=max_depth)
        if ok_doctor and bool(feas.get("ok")):
            return ConvergeResult(status="OK", rounds=round_idx, plan_id=plan_id)

        patch_plan = propose_rewrite(conn, plan_id, workflow_mode="v2", one_shot_threshold_person_days=threshold_person_days, max_depth=max_depth)
        patches = patch_plan.get("patches") or []

        # If feasibility fails only due to over-threshold but split is not applicable, request external input.
        blocked_by_depth = False
        for p in patches:
            if str(p.get("type") or "") != "SPLIT_OVERSIZED_ACTION":
                continue
            for t in p.get("targets") or []:
                if bool(t.get("apply_allowed")) is False:
                    blocked_by_depth = True
        if (not patches) or blocked_by_depth:
            required_docs = [
                {
                    "name": "effort_estimates",
                    "description": "Provide per-feature effort estimates or constraints to guide decomposition (person-days).",
                    "accepted_types": ["md", "txt", "json"],
                    "suggested_path": "workspace/inputs/plan/effort_estimates.md",
                },
                {
                    "name": "decomposition_guidance",
                    "description": "Provide decomposition rules or target module breakdown (what sub-systems, acceptance).",
                    "accepted_types": ["md", "txt"],
                    "suggested_path": "workspace/inputs/plan/decomposition_guidance.md",
                },
            ]
            last_required = required_docs
            path = _write_plan_required_docs(plan_id=plan_id, required_docs=required_docs)
            emit_event(conn, plan_id=plan_id, task_id=None, event_type="ERROR", payload={"error_code": "REQUEST_EXTERNAL_INPUT", "message": "Need additional decomposition guidance to converge.", "context": {"required_docs_path": str(path), "required_docs": required_docs}})
            return ConvergeResult(status="REQUEST_EXTERNAL_INPUT", rounds=round_idx, plan_id=plan_id, required_docs_path=path, required_docs=required_docs)

        # Apply patches.
        apply_rewrite(conn, patch_plan, dry_run=False)

    # Exhausted rounds => request external input.
    required_docs = last_required or [
        {
            "name": "decomposition_guidance",
            "description": "Provide decomposition rules or target module breakdown.",
            "accepted_types": ["md", "txt"],
            "suggested_path": "workspace/inputs/plan/decomposition_guidance.md",
        }
    ]
    path = _write_plan_required_docs(plan_id=plan_id, required_docs=required_docs)
    emit_event(conn, plan_id=plan_id, task_id=None, event_type="ERROR", payload={"error_code": "REQUEST_EXTERNAL_INPUT", "message": "Convergence rounds exceeded.", "context": {"required_docs_path": str(path), "required_docs": required_docs}})
    return ConvergeResult(status="REQUEST_EXTERNAL_INPUT", rounds=int(max_rounds), plan_id=plan_id, required_docs_path=path, required_docs=required_docs)

