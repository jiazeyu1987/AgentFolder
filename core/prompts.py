from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.util import stable_hash_text
from core.prompt_store import get_or_create_prompt_version


@dataclass(frozen=True)
class PromptDoc:
    path: Path
    content: str
    version: str
    sha256: str


@dataclass(frozen=True)
class PromptBundle:
    shared: PromptDoc
    xiaobo: PromptDoc
    xiaojing: PromptDoc
    xiaoxie: PromptDoc


def _load_prompt(path: Path, *, default_version: str) -> PromptDoc:
    content = path.read_text(encoding="utf-8")
    return PromptDoc(path=path, content=content, version=default_version, sha256=stable_hash_text(content))


def load_prompts(shared_path: Path, agents_dir: Path) -> PromptBundle:
    shared = _load_prompt(shared_path, default_version="shared_v1")
    xiaobo = _load_prompt(agents_dir / "xiaobo_prompt.md", default_version="xiaobo_v1")
    xiaojing = _load_prompt(agents_dir / "xiaojing_prompt.md", default_version="xiaojing_v1")
    xiaoxie = _load_prompt(agents_dir / "xiaoxie_prompt.md", default_version="xiaoxie_v1")
    return PromptBundle(shared=shared, xiaobo=xiaobo, xiaojing=xiaojing, xiaoxie=xiaoxie)


def register_prompt_versions(conn: sqlite3.Connection, bundle: PromptBundle) -> PromptBundle:
    # Spec-aligned: one shared prompt + one prompt per agent.
    shared_id, shared_v = get_or_create_prompt_version(conn, kind="SHARED", name="shared", agent=None, path=bundle.shared.path, sha256=bundle.shared.sha256)
    xiaobo_id, xiaobo_v = get_or_create_prompt_version(conn, kind="AGENT", name="default", agent="xiaobo", path=bundle.xiaobo.path, sha256=bundle.xiaobo.sha256)
    xiaojing_id, xiaojing_v = get_or_create_prompt_version(conn, kind="AGENT", name="default", agent="xiaojing", path=bundle.xiaojing.path, sha256=bundle.xiaojing.sha256)
    xiaoxie_id, xiaoxie_v = get_or_create_prompt_version(conn, kind="AGENT", name="default", agent="xiaoxie", path=bundle.xiaoxie.path, sha256=bundle.xiaoxie.sha256)

    # Version strings follow spec: shared_prompt_vN / agent_{name}_prompt_vN
    return PromptBundle(
        shared=PromptDoc(path=bundle.shared.path, content=bundle.shared.content, version=f"shared_prompt_v{shared_v}", sha256=bundle.shared.sha256),
        xiaobo=PromptDoc(path=bundle.xiaobo.path, content=bundle.xiaobo.content, version=f"agent_xiaobo_prompt_v{xiaobo_v}", sha256=bundle.xiaobo.sha256),
        xiaojing=PromptDoc(path=bundle.xiaojing.path, content=bundle.xiaojing.content, version=f"agent_xiaojing_prompt_v{xiaojing_v}", sha256=bundle.xiaojing.sha256),
        xiaoxie=PromptDoc(path=bundle.xiaoxie.path, content=bundle.xiaoxie.content, version=f"agent_xiaoxie_prompt_v{xiaoxie_v}", sha256=bundle.xiaoxie.sha256),
    )


def _load_requirements_and_evidence(conn: sqlite3.Connection, task_id: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    reqs = conn.execute(
        """
        SELECT requirement_id, name, kind, required, min_count, allowed_types_json, source, validation_json
        FROM input_requirements
        WHERE task_id = ?
        """,
        (task_id,),
    ).fetchall()
    requirements: List[Dict[str, Any]] = []
    for r in reqs:
        requirements.append(
            {
                "requirement_id": r["requirement_id"],
                "name": r["name"],
                "kind": r["kind"],
                "required": int(r["required"]),
                "min_count": int(r["min_count"]),
                "allowed_types": json.loads(r["allowed_types_json"] or "[]"),
                "source": r["source"],
                "validation": json.loads(r["validation_json"] or "{}"),
            }
        )

    evidence = conn.execute(
        """
        SELECT e.evidence_id, e.requirement_id, e.ref_path, e.sha256, e.added_at, r.name AS requirement_name
        FROM evidences e
        JOIN input_requirements r ON r.requirement_id = e.requirement_id
        WHERE r.task_id = ?
        ORDER BY e.added_at DESC
        """,
        (task_id,),
    ).fetchall()
    evidences: List[Dict[str, Any]] = []
    for e in evidence:
        evidences.append(
            {
                "evidence_id": e["evidence_id"],
                "requirement_id": e["requirement_id"],
                "requirement_name": e["requirement_name"],
                "path": e["ref_path"],
                "sha256": e["sha256"],
                "added_at": e["added_at"],
            }
        )
    return requirements, evidences


def build_xiaobo_prompt(
    bundle: PromptBundle,
    *,
    conn: sqlite3.Connection,
    plan_id: str,
    task_id: str,
    artifact_text_snippets: Optional[List[str]] = None,
    suggestions_text: Optional[str] = None,
) -> str:
    plan = conn.execute("SELECT title, root_task_id FROM plans WHERE plan_id = ?", (plan_id,)).fetchone()
    root_goal = None
    if plan and plan["root_task_id"]:
        root_goal = conn.execute(
            "SELECT title, goal_statement FROM task_nodes WHERE task_id = ?",
            (plan["root_task_id"],),
        ).fetchone()
    task = conn.execute(
        "SELECT title, attempt_count, priority, status FROM task_nodes WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    requirements, evidences = _load_requirements_and_evidence(conn, task_id)

    upstream: List[Dict[str, Any]] = []
    upstream_paths_text = ""
    try:
        rows = conn.execute(
            """
            SELECT
              e.from_task_id AS upstream_task_id,
              n.title AS upstream_title,
              n.approved_artifact_id,
              n.active_artifact_id,
              aa.path AS approved_path,
              ab.path AS active_path
            FROM task_edges e
            JOIN task_nodes n ON n.task_id = e.from_task_id
            LEFT JOIN artifacts aa ON aa.artifact_id = n.approved_artifact_id
            LEFT JOIN artifacts ab ON ab.artifact_id = n.active_artifact_id
            WHERE e.plan_id = ?
              AND e.to_task_id = ?
              AND e.edge_type = 'DEPENDS_ON'
              AND n.node_type = 'ACTION'
              AND n.active_branch = 1
            ORDER BY e.created_at ASC
            """,
            (plan_id, task_id),
        ).fetchall()
        for r in rows:
            preferred = str(r["approved_path"] or "").strip() or str(r["active_path"] or "").strip()
            if not preferred:
                continue
            upstream.append(
                {
                    "upstream_task_title": str(r["upstream_title"] or ""),
                    "preferred_path": preferred,
                    "approved_artifact_id": str(r["approved_artifact_id"] or ""),
                    "active_artifact_id": str(r["active_artifact_id"] or ""),
                }
            )
        if upstream:
            lines = ["UPSTREAM_ARTIFACTS (local paths; read these files if needed):"]
            for item in upstream[:20]:
                title = str(item.get("upstream_task_title") or "").strip() or "upstream_task"
                p = str(item.get("preferred_path") or "").strip()
                if not p:
                    continue
                lines.append(f"- {title}:")
                lines.append(f"  - {p}")
            upstream_paths_text = "\n".join(lines).strip()
    except Exception:
        upstream = []
        upstream_paths_text = ""
    context = {
        "plan_id": plan_id,
        "plan": {
            "title": (plan["title"] if plan else None),
            "root_task_id": (plan["root_task_id"] if plan else None),
            "root_title": (root_goal["title"] if root_goal else None),
            "root_goal_statement": (root_goal["goal_statement"] if root_goal else None),
        },
        "task": {
            "task_id": task_id,
            "title": task["title"],
            "status": task["status"],
            "attempt_count": int(task["attempt_count"]),
            "priority": int(task["priority"]),
        },
        "requirements": requirements,
        "evidences": evidences,
        "upstream_artifacts": upstream,
        "upstream_artifacts_paths_text": upstream_paths_text,
        "suggestions": suggestions_text or "",
        "extracted_text_snippets": artifact_text_snippets or [],
    }
    return "\n\n".join(
        [
            bundle.shared.content.strip(),
            bundle.xiaobo.content.strip(),
            "RUNTIME_CONTEXT_JSON:",
            json.dumps(context, ensure_ascii=False, indent=2),
        ]
    ).strip() + "\n"


def build_xiaobo_plan_prompt(
    bundle: PromptBundle,
    *,
    top_task: str,
    constraints: Dict[str, Any],
    skills: List[str],
    review_notes: Optional[str] = None,
    gen_notes: Optional[str] = None,
    previous_plan_json: Optional[Dict[str, Any]] = None,
    previous_plan_gen_llm_call_id: Optional[str] = None,
    remediation_source_review_llm_call_id: Optional[str] = None,
) -> str:
    review_notes_text = (review_notes or "").strip()
    iterative = bool(previous_plan_json) and bool(review_notes_text)
    mode = "ITERATIVE_REMEDIATION" if iterative else "INITIAL"
    iteration_rules = ""
    if iterative:
        iteration_rules = "\n".join(
            [
                "ITERATIVE REMEDIATION RULES (must follow):",
                "1) You MUST modify PREVIOUS_PLAN_JSON in-place; do NOT generate an unrelated new plan.",
                "2) You MUST address every item in review_notes (problems/steps/acceptance_criteria).",
                "3) Output MUST be a single JSON object with schema_version=xiaobo_plan_v1 and a FULL plan_json (not a patch).",
                "4) Keep IDs stable: reuse existing task_id/edge_id when possible; only add/remove nodes when required by review_notes.",
            ]
        )
    context = {
        "top_task": top_task,
        "constraints": constraints,
        "available_skills": skills,
        "mode": mode,
        "iteration_rules": iteration_rules,
        "review_notes": review_notes_text,
        "generation_notes": (gen_notes or "").strip(),
        "previous_plan_json": previous_plan_json,
        "previous_plan_gen_llm_call_id": (previous_plan_gen_llm_call_id or "").strip(),
        "remediation_source_review_llm_call_id": (remediation_source_review_llm_call_id or "").strip(),
    }
    return "\n\n".join(
        [
            bundle.shared.content.strip(),
            bundle.xiaobo.content.strip(),
            "RUNTIME_CONTEXT_JSON:",
            json.dumps(context, ensure_ascii=False, indent=2),
        ]
    ).strip() + "\n"


def build_xiaojing_review_prompt(
    bundle: PromptBundle,
    *,
    conn: sqlite3.Connection,
    plan_id: str,
    task_id: str,
    rubric_json: Dict[str, Any],
    artifact_path: Path,
    artifact_text: str,
) -> str:
    task = conn.execute(
        "SELECT title, attempt_count, priority, status FROM task_nodes WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    context = {
        "plan_id": plan_id,
        "task": {
            "task_id": task_id,
            "title": task["title"],
            "status": task["status"],
            "attempt_count": int(task["attempt_count"]),
            "priority": int(task["priority"]),
        },
        "review_target": "NODE",
        "rubric": rubric_json,
        "artifact": {
            "path": str(artifact_path),
            "content": artifact_text,
        },
    }
    return "\n\n".join(
        [
            bundle.shared.content.strip(),
            bundle.xiaojing.content.strip(),
            "RUNTIME_CONTEXT_JSON:",
            json.dumps(context, ensure_ascii=False, indent=2),
            "OUTPUT_JSON_TEMPLATE (copy exactly, fill values; do not wrap inside another object):",
            _xiaojing_review_output_template(review_target="NODE", task_id=task_id),
        ]
    ).strip() + "\n"


def build_xiaojing_plan_review_prompt(
    bundle: PromptBundle,
    *,
    plan_id: str,
    rubric_json: Dict[str, Any],
    plan_json: Dict[str, Any],
    stage: Optional[str] = None,
    stage_checklist: Optional[List[str]] = None,
) -> str:
    from core.runtime_config import get_runtime_config

    cfg = get_runtime_config()
    context = {
        "plan_id": plan_id,
        "review_target": "PLAN",
        "stage": (str(stage or "").strip().upper() or "STRUCTURE"),
        "pass_score": int(cfg.plan_review_pass_score),
        "rubric": rubric_json,
        "stage_checklist": stage_checklist or [],
        "plan_json": plan_json,
    }
    return "\n\n".join(
        [
            bundle.shared.content.strip(),
            bundle.xiaojing.content.strip(),
            "RUNTIME_CONTEXT_JSON:",
            json.dumps(context, ensure_ascii=False, indent=2),
            "OUTPUT_JSON_TEMPLATE (copy exactly, fill values; do not wrap inside another object):",
            _xiaojing_review_output_template(review_target="PLAN", task_id=plan_id),
        ]
    ).strip() + "\n"


def build_xiaojing_plan_rubric_prompt(
    bundle: PromptBundle,
    *,
    top_task: str,
    top_task_hash: str,
    pass_score: int,
    base_rubric_json: Dict[str, Any],
) -> str:
    """
    Phase-1 rubric: freeze the scoring standard so later reviews are consistent.
    Output contract: xiaojing_plan_rubric_v1.
    """
    context = {
        "top_task": str(top_task),
        "top_task_hash": str(top_task_hash),
        "pass_score": int(pass_score),
        "base_rubric": base_rubric_json,
        "required_stages": ["STRUCTURE", "BINDINGS", "EXECUTION"],
    }
    tmpl = {
        "schema_version": "xiaojing_plan_rubric_v1",
        "top_task_hash": "<TOP_TASK_HASH>",
        "pass_score": int(pass_score),
        "dimensions": [
            {"dimension": "Completeness", "max_score": 40, "description": "What must exist in the plan", "scoring_guide": "How to score 0..40"},
            {"dimension": "Dependency Soundness", "max_score": 25, "description": "Correct DAG / deps", "scoring_guide": "How to score 0..25"},
            {"dimension": "Executability", "max_score": 20, "description": "Action nodes concrete", "scoring_guide": "How to score 0..20"},
            {"dimension": "Clarity", "max_score": 15, "description": "Naming unambiguous", "scoring_guide": "How to score 0..15"},
        ],
        "stage_checklists": {
            "STRUCTURE": ["Root GOAL defined", "No placeholder nodes", "All edges reference existing nodes"],
            "BINDINGS": ["Each ACTION has deliverable + acceptance criteria", "Inputs/outputs are bound"],
            "EXECUTION": ["Plan executable in serial", "Export final deliverable is specified"],
        },
    }
    return "\n\n".join(
        [
            bundle.shared.content.strip(),
            bundle.xiaojing.content.strip(),
            "You are defining the scoring rubric for later PLAN reviews. Freeze it so later scores are consistent.",
            "Hard rules:",
            "- Output MUST be a single JSON object only.",
            "- JSON must satisfy the schema_version exactly.",
            "- dimensions[*].max_score must sum to 100.",
            "RUNTIME_CONTEXT_JSON:",
            json.dumps(context, ensure_ascii=False, indent=2),
            "OUTPUT_JSON_TEMPLATE (copy exactly, fill values; do not wrap inside another object):",
            json.dumps(tmpl, ensure_ascii=False, indent=2),
        ]
    ).strip() + "\n"


def _xiaojing_review_output_template(*, review_target: str, task_id: str) -> str:
    """
    Minimal contract template to reduce reviewer output drift.
    Must match `xiaojing_review_v1` exactly (see core.contracts_v2 PLAN_REVIEW/TASK_CHECK).
    """
    rt = str(review_target or "").strip().upper() or "PLAN"
    tid = str(task_id or "").strip() or "<TASK_ID>"
    tmpl = {
        "schema_version": "xiaojing_review_v1",
        "task_id": tid,
        "review_target": rt,
        "total_score": 0,
        "action_required": "MODIFY",
        "summary": "Explain the main issues and why score does not meet pass_score.",
        "breakdown": [
            {
                "dimension": "overall",
                "score": 0,
                "max_score": 100,
                "issues": [
                    {
                        "problem": "What is wrong (concrete).",
                        "evidence": "Where in the plan/artifact this appears.",
                        "impact": "Why it matters / what breaks.",
                        "suggestion": "What to change (concrete).",
                        "acceptance_criteria": "How to verify the change.",
                    }
                ],
            }
        ],
        "suggestions": [
            {
                "priority": "MED",
                "change": "Summarize the most important fix.",
                "steps": ["Step 1", "Step 2"],
                "acceptance_criteria": "What must be true after the fix.",
            }
        ],
    }
    return json.dumps(tmpl, ensure_ascii=False, indent=2)


def build_xiaojing_check_prompt(
    bundle: PromptBundle,
    *,
    conn: sqlite3.Connection,
    plan_id: str,
    check_task_id: str,
    rubric_json: Dict[str, Any],
    target_task_id: str,
    target_artifacts: List[Dict[str, Any]],
    reviewer: str = "xiaojing",
) -> str:
    check_task = conn.execute(
        "SELECT title, attempt_count, priority, status, tags_json FROM task_nodes WHERE task_id = ?",
        (check_task_id,),
    ).fetchone()
    target_task = conn.execute(
        "SELECT title, attempt_count, priority, status, tags_json FROM task_nodes WHERE task_id = ?",
        (target_task_id,),
    ).fetchone()
    context = {
        "plan_id": plan_id,
        "check_task": {
            "task_id": check_task_id,
            "title": check_task["title"],
            "status": check_task["status"],
            "attempt_count": int(check_task["attempt_count"]),
            "priority": int(check_task["priority"]),
            "tags_json": check_task["tags_json"],
        },
        "review_target": "NODE",
        "rubric": rubric_json,
        "target_task": {
            "task_id": target_task_id,
            "title": target_task["title"],
            "status": target_task["status"],
            "attempt_count": int(target_task["attempt_count"]),
            "priority": int(target_task["priority"]),
            "tags_json": target_task["tags_json"],
        },
        "artifacts": target_artifacts,
        "instructions": "This is a CHECK task. If you request modifications, they should be applied to the target_task output.",
    }
    return "\n\n".join(
        [
            bundle.shared.content.strip(),
            (bundle.xiaojing.content.strip() if reviewer == "xiaojing" else bundle.xiaoxie.content.strip()),
            "RUNTIME_CONTEXT_JSON:",
            json.dumps(context, ensure_ascii=False, indent=2),
        ]
    ).strip() + "\n"
