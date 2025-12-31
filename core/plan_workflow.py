from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import config
from core.db import transaction
from core.events import emit_event
from core.llm_client import LLMClient
from core.llm_contracts import validate_xiaojing_review
from core.models import PlanValidationError, validate_plan_dict
from core.plan_loader import upsert_plan
from core.prompts import PromptBundle, build_xiaobo_plan_prompt, build_xiaojing_plan_review_prompt
from core.reviews import insert_review, write_review_json
from core.util import ensure_dir, stable_hash_text, utc_now_iso


class PlanWorkflowError(RuntimeError):
    pass


@dataclass(frozen=True)
class PlanWorkflowResult:
    plan_json: Dict[str, Any]
    review_json: Dict[str, Any]
    plan_path: Path


def _append_llm_run(path: Path, payload: Dict[str, Any]) -> None:
    ensure_dir(path.parent)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _is_iso8601(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except Exception:
        return False


def _coerce_uuid_fields(plan_json: Dict[str, Any]) -> None:
    # Allow the model to output non-UUID ids; coerce to UUIDs deterministically by replacing them.
    id_map: Dict[str, str] = {}

    def map_id(value: Any) -> str:
        if isinstance(value, str) and value:
            if value not in id_map:
                id_map[value] = _new_uuid()
            return id_map[value]
        return _new_uuid()

    plan = plan_json.get("plan")
    if not isinstance(plan, dict):
        plan = {}
        plan_json["plan"] = plan

    plan["plan_id"] = map_id(plan.get("plan_id"))
    plan["root_task_id"] = map_id(plan.get("root_task_id"))
    if not _is_iso8601(plan.get("created_at")):
        plan["created_at"] = utc_now_iso()
    if not plan.get("owner_agent_id"):
        plan["owner_agent_id"] = "xiaobo"
    if not plan.get("constraints") or not isinstance(plan.get("constraints"), dict):
        plan["constraints"] = {"deadline": None, "priority": "HIGH"}

    if not isinstance(plan_json.get("nodes"), list):
        plan_json["nodes"] = []
    if not isinstance(plan_json.get("edges"), list):
        plan_json["edges"] = []
    if not isinstance(plan_json.get("requirements"), list):
        plan_json["requirements"] = []

    for node in plan_json.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        node["task_id"] = map_id(node.get("task_id"))
        node["plan_id"] = plan.get("plan_id")

    for edge in plan_json.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        edge["edge_id"] = map_id(edge.get("edge_id"))
        edge["plan_id"] = plan.get("plan_id")
        edge["from_task_id"] = map_id(edge.get("from_task_id"))
        edge["to_task_id"] = map_id(edge.get("to_task_id"))

    for req in plan_json.get("requirements") or []:
        if not isinstance(req, dict):
            continue
        req["requirement_id"] = map_id(req.get("requirement_id"))
        req["task_id"] = map_id(req.get("task_id"))


def _coerce_required_plan_fields(plan_json: Dict[str, Any], *, top_task: str) -> None:
    plan = plan_json.get("plan")
    if not isinstance(plan, dict):
        plan = {}
        plan_json["plan"] = plan
    title = str(plan.get("title") or "").strip()
    if not title:
        title = top_task.strip().splitlines()[0].strip()[:120] or "Untitled Plan"
    plan["title"] = title


def _coerce_required_node_fields(plan_json: Dict[str, Any]) -> None:
    plan = plan_json.get("plan") or {}
    root_task_id = plan.get("root_task_id")

    nodes = plan_json.get("nodes")
    if not isinstance(nodes, list):
        return

    allowed_node_types = {"GOAL", "ACTION", "CHECK"}
    allowed_agents = {"xiaobo", "xiaojing", "xiaoxie"}

    for idx, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue

        # Required by Plan_Definition_Spec_and_Example.md / validate_plan_dict
        node_type = node.get("node_type")
        if not isinstance(node_type, str) or node_type.strip().upper() not in allowed_node_types:
            # Best-effort inference: root node is GOAL, everything else defaults to ACTION.
            if isinstance(root_task_id, str) and node.get("task_id") == root_task_id:
                node["node_type"] = "GOAL"
            else:
                node["node_type"] = "ACTION"
        else:
            node["node_type"] = node_type.strip().upper()

        if "title" not in node or not str(node.get("title") or "").strip():
            fallback = str(node.get("goal_statement") or node.get("rationale") or "").strip()
            node["title"] = (fallback[:120] if fallback else f"Task {idx + 1}")

        owner_agent_id = node.get("owner_agent_id")
        if not isinstance(owner_agent_id, str) or owner_agent_id.strip() not in allowed_agents:
            node["owner_agent_id"] = "xiaobo"

        priority = node.get("priority")
        try:
            node["priority"] = int(priority) if priority is not None else 0
        except Exception:
            node["priority"] = 0

        if "tags" not in node or not isinstance(node.get("tags"), list):
            node["tags"] = []


def _ensure_graph_referential_integrity(plan_json: Dict[str, Any]) -> None:
    """
    Ensure every referenced task_id exists in nodes so validate_plan_dict() can run.

    The model occasionally emits edges/requirements pointing at non-existent nodes. For MVP robustness we
    create placeholder ACTION nodes so the plan is structurally valid, leaving quality issues to review.
    """
    plan = plan_json.get("plan") or {}
    plan_id = plan.get("plan_id")
    root_task_id = plan.get("root_task_id")

    nodes = plan_json.get("nodes")
    if not isinstance(nodes, list):
        return
    edges = plan_json.get("edges")
    if not isinstance(edges, list):
        return
    requirements = plan_json.get("requirements")
    if not isinstance(requirements, list):
        return

    node_by_id: Dict[str, Dict[str, Any]] = {}
    for n in nodes:
        if isinstance(n, dict) and isinstance(n.get("task_id"), str):
            node_by_id[n["task_id"]] = n

    def ensure_node(task_id: Any, *, is_root: bool = False) -> None:
        if not isinstance(task_id, str) or not task_id:
            return
        if task_id in node_by_id:
            return
        title = "Root Task" if is_root else f"AUTO: missing node {task_id[:8]}"
        node: Dict[str, Any] = {
            "task_id": task_id,
            "plan_id": plan_id,
            "node_type": "GOAL" if is_root else "ACTION",
            "title": title,
            "goal_statement": None,
            "rationale": "Autocreated placeholder node for referential integrity.",
            "owner_agent_id": "xiaobo",
            "priority": 0,
            "tags": ["autofix", "placeholder"],
        }
        nodes.append(node)
        node_by_id[task_id] = node

    ensure_node(root_task_id, is_root=True)

    for e in edges:
        if not isinstance(e, dict):
            continue
        ensure_node(e.get("from_task_id"))
        ensure_node(e.get("to_task_id"))

    for r in requirements:
        if not isinstance(r, dict):
            continue
        ensure_node(r.get("task_id"))


def _coerce_required_requirement_fields(plan_json: Dict[str, Any]) -> None:
    plan = plan_json.get("plan") or {}
    plan_id = plan.get("plan_id")

    reqs = plan_json.get("requirements")
    if not isinstance(reqs, list):
        return

    allowed_kinds = {"FILE", "CONFIRMATION", "SKILL_OUTPUT"}
    allowed_sources = {"USER", "AGENT", "ANY"}
    kind_aliases = {
        "FILES": "FILE",
        "DOC": "FILE",
        "DOCS": "FILE",
        "DOCUMENT": "FILE",
        "DOCUMENTS": "FILE",
        "CONFIRM": "CONFIRMATION",
        "SKILL": "SKILL_OUTPUT",
        "SKILL_RESULT": "SKILL_OUTPUT",
        "SKILL_ARTIFACT": "SKILL_OUTPUT",
    }

    def normalize_kind(value: Any) -> str:
        if not isinstance(value, str):
            return "FILE"
        k = value.strip().upper()
        k = kind_aliases.get(k, k)
        return k if k in allowed_kinds else "FILE"

    def normalize_source(value: Any) -> str:
        if not isinstance(value, str):
            return "USER"
        s = value.strip().upper()
        return s if s in allowed_sources else "USER"

    def coerce_bool_int(value: Any, *, default: int) -> int:
        if isinstance(value, bool):
            return 1 if value else 0
        if isinstance(value, int):
            return 1 if value != 0 else 0
        if isinstance(value, str):
            t = value.strip().lower()
            if t in {"1", "true", "yes", "y"}:
                return 1
            if t in {"0", "false", "no", "n"}:
                return 0
        return default

    def coerce_min_count(value: Any) -> int:
        try:
            n = int(value)
        except Exception:
            return 1
        return 1 if n <= 0 else n

    for idx, r in enumerate(reqs):
        if not isinstance(r, dict):
            continue
        if "plan_id" not in r and plan_id is not None:
            r["plan_id"] = plan_id

        name = r.get("name")
        if not isinstance(name, str) or not name.strip():
            r["name"] = f"requirement_{idx + 1}"

        r["kind"] = normalize_kind(r.get("kind"))
        r["source"] = normalize_source(r.get("source"))
        r["required"] = coerce_bool_int(r.get("required"), default=1)
        r["min_count"] = coerce_min_count(r.get("min_count") if "min_count" in r else 1)

        allowed = r.get("allowed_types")
        if isinstance(allowed, str):
            r["allowed_types"] = [allowed]
        elif not isinstance(allowed, list) or any(not isinstance(x, str) for x in allowed):
            r["allowed_types"] = []


def _normalize_plan_review_json(review_json: Dict[str, Any], *, plan_id: str) -> None:
    """
    Best-effort normalization for xiaojing plan review JSON.

    The reviewer model may output slightly different casing/aliases (e.g. "plan") or wrong primitive
    types. Normalize the easy cases to reduce brittle failures.
    """
    review_target = review_json.get("review_target")
    if isinstance(review_target, str):
        t = review_target.strip().upper()
        aliases = {
            "PLAN_REVIEW": "PLAN",
            "PLAN_JSON": "PLAN",
            "TOP_TASK": "PLAN",
        }
        review_json["review_target"] = aliases.get(t, t)
    else:
        review_json["review_target"] = "PLAN"

    task_id = review_json.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        review_json["task_id"] = plan_id

    total_score = review_json.get("total_score")
    if isinstance(total_score, str):
        try:
            review_json["total_score"] = int(total_score.strip())
        except Exception:
            pass
    elif total_score is None:
        review_json["total_score"] = 0

    action_required = review_json.get("action_required")
    if isinstance(action_required, str):
        review_json["action_required"] = action_required.strip().upper()
    elif action_required is None:
        # Default policy: be conservative.
        review_json["action_required"] = "MODIFY"

    # Enforce score/action consistency expected by validate_xiaojing_review().
    try:
        total_int = int(review_json.get("total_score") or 0)
    except Exception:
        total_int = 0
        review_json["total_score"] = 0
    ar = str(review_json.get("action_required") or "").strip().upper()
    if total_int >= 90:
        review_json["action_required"] = "APPROVE"
    elif ar == "APPROVE":
        review_json["action_required"] = "MODIFY"

    summary = review_json.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        review_json["summary"] = "No summary provided."

    breakdown = review_json.get("breakdown")
    if not isinstance(breakdown, list) or not breakdown:
        review_json["breakdown"] = [{"dimension": "overall", "score": total_int, "max_score": 100, "issues": []}]

    suggestions = review_json.get("suggestions")
    if not isinstance(suggestions, list):
        review_json["suggestions"] = []


def _coerce_required_edge_fields(plan_json: Dict[str, Any]) -> None:
    plan = plan_json.get("plan") or {}
    plan_id = plan.get("plan_id")

    edges = plan_json.get("edges")
    if not isinstance(edges, list):
        return

    def normalize_edge_type(value: Any) -> str:
        if not isinstance(value, str):
            return "DEPENDS_ON"
        t = value.strip().upper()
        aliases = {
            "DEPEND": "DEPENDS_ON",
            "DEPENDS": "DEPENDS_ON",
            "DEPEND_ON": "DEPENDS_ON",
            "DEPENDS-ON": "DEPENDS_ON",
            "DEPENDS ON": "DEPENDS_ON",
            "REQUIRES": "DEPENDS_ON",
            "PREREQ": "DEPENDS_ON",
            "PREREQUISITE": "DEPENDS_ON",
            "DECOMPOSITION": "DECOMPOSE",
            "BREAKDOWN": "DECOMPOSE",
            "CHILD_OF": "DECOMPOSE",
            "PARENT_OF": "DECOMPOSE",
            "ALT": "ALTERNATIVE",
            "ALTERNATE": "ALTERNATIVE",
        }
        t = aliases.get(t, t)
        if t not in {"DECOMPOSE", "DEPENDS_ON", "ALTERNATIVE"}:
            return "DEPENDS_ON"
        return t

    for e in edges:
        if not isinstance(e, dict):
            continue
        if "plan_id" not in e and plan_id is not None:
            e["plan_id"] = plan_id

        e["edge_type"] = normalize_edge_type(e.get("edge_type"))
        metadata = e.get("metadata")
        if metadata is None or not isinstance(metadata, dict):
            metadata = {}
            e["metadata"] = metadata

        if e["edge_type"] == "DECOMPOSE":
            and_or = str(metadata.get("and_or") or "AND").strip().upper()
            if and_or not in {"AND", "OR"}:
                and_or = "AND"
            metadata["and_or"] = and_or
        if e["edge_type"] == "ALTERNATIVE":
            group_id = metadata.get("group_id")
            if not isinstance(group_id, str) or not group_id.strip():
                metadata["group_id"] = "AUTO_GROUP_1"


def _ensure_plan_stub(
    conn: sqlite3.Connection,
    *,
    plan_id: str,
    title: str,
    owner_agent_id: str,
    root_task_id: str,
    constraints: Dict[str, Any],
) -> None:
    """
    Ensure a minimal row exists in plans so task_events FK can be satisfied even if plan.json is invalid.
    """
    row = conn.execute("SELECT 1 FROM plans WHERE plan_id = ?", (plan_id,)).fetchone()
    if row:
        return
    conn.execute(
        """
        INSERT INTO plans(plan_id, title, owner_agent_id, root_task_id, created_at, constraints_json)
        VALUES(?, ?, ?, ?, ?, ?)
        """,
        (plan_id, title, owner_agent_id, root_task_id, utc_now_iso(), json.dumps(constraints or {}, ensure_ascii=False)),
    )


def _load_plan_rubric() -> Dict[str, Any]:
    data = json.loads(config.REVIEW_RUBRIC_PATH.read_text(encoding="utf-8"))
    return data.get("plan_review") or data


def generate_and_review_plan(
    conn: sqlite3.Connection,
    *,
    prompts: PromptBundle,
    llm: LLMClient,
    top_task: str,
    constraints: Optional[Dict[str, Any]] = None,
    available_skills: Optional[List[str]] = None,
    max_plan_attempts: int = 3,
    plan_output_path: Path = config.PLAN_PATH_DEFAULT,
) -> PlanWorkflowResult:
    constraints = constraints or {"deadline": None, "priority": "HIGH"}
    available_skills = available_skills or []
    rubric = _load_plan_rubric()

    last_review: Dict[str, Any] = {}
    for attempt in range(1, max_plan_attempts + 1):
        plan_prompt = build_xiaobo_plan_prompt(prompts, top_task=top_task, constraints=constraints, skills=available_skills)
        plan_res = llm.call_json(plan_prompt)
        _append_llm_run(
            config.LLM_RUNS_LOG_PATH,
            {
                "ts": utc_now_iso(),
                "plan_id": None,
                "task_id": None,
                "agent": "xiaobo",
                "shared_prompt_version": prompts.shared.version,
                "shared_prompt_hash": prompts.shared.sha256,
                "agent_prompt_version": prompts.xiaobo.version,
                "agent_prompt_hash": prompts.xiaobo.sha256,
                "runtime_context_hash": stable_hash_text(plan_prompt),
                "final_prompt": plan_prompt,
                "response": plan_res.parsed_json or plan_res.raw_response_text,
                "error": {"code": plan_res.error_code, "message": plan_res.error} if plan_res.error else None,
                "scope": "PLAN_GENERATION",
                "attempt": attempt,
            },
        )
        if plan_res.error or not plan_res.parsed_json:
            raise PlanWorkflowError(f"plan generation failed: {plan_res.error}")

        outer = plan_res.parsed_json
        if outer.get("schema_version") != "xiaobo_plan_v1" or not isinstance(outer.get("plan_json"), dict):
            msg = "plan generation output must be JSON with schema_version=xiaobo_plan_v1 and plan_json object"
            if attempt < max_plan_attempts:
                top_task = top_task + "\n\n" + msg
                continue
            raise PlanWorkflowError(msg)
        plan_json = outer.get("plan_json")  # type: ignore[assignment]

        _coerce_uuid_fields(plan_json)
        _coerce_required_plan_fields(plan_json, top_task=top_task)
        _coerce_required_node_fields(plan_json)
        _coerce_required_edge_fields(plan_json)
        _coerce_required_requirement_fields(plan_json)
        _ensure_graph_referential_integrity(plan_json)
        _coerce_required_node_fields(plan_json)
        _coerce_required_edge_fields(plan_json)
        _coerce_required_requirement_fields(plan_json)
        plan = plan_json.get("plan") or {}
        plan_id = plan.get("plan_id")
        if not isinstance(plan_id, str) or not plan_id:
            raise PlanWorkflowError("plan.plan_id missing after coercion")

        # Ensure FK safety for any subsequent emit_event calls (PLAN_REVIEWED, ERROR, etc.)
        with transaction(conn):
            _ensure_plan_stub(
                conn,
                plan_id=plan_id,
                title=str(plan.get("title") or "Untitled Plan"),
                owner_agent_id=str(plan.get("owner_agent_id") or "xiaobo"),
                root_task_id=str(plan.get("root_task_id") or plan_id),
                constraints=constraints,
            )
        try:
            validate_plan_dict(plan_json)
        except PlanValidationError as exc:
            with transaction(conn):
                emit_event(
                    conn,
                    plan_id=plan_id,
                    event_type="ERROR",
                    payload={"error_code": "PLAN_INVALID", "message": str(exc)},
                )
            if attempt < max_plan_attempts:
                top_task = top_task + "\n\nPlan JSON schema validation error (must fix):\n" + str(exc)
                continue
            raise PlanWorkflowError(f"PLAN_INVALID: {exc}") from exc

        review_prompt = build_xiaojing_plan_review_prompt(prompts, plan_id=plan_id, rubric_json=rubric, plan_json=plan_json)
        review_res = llm.call_json(review_prompt)
        _append_llm_run(
            config.LLM_RUNS_LOG_PATH,
            {
                "ts": utc_now_iso(),
                "plan_id": plan_id,
                "task_id": None,
                "agent": "xiaojing",
                "shared_prompt_version": prompts.shared.version,
                "shared_prompt_hash": prompts.shared.sha256,
                "agent_prompt_version": prompts.xiaojing.version,
                "agent_prompt_hash": prompts.xiaojing.sha256,
                "runtime_context_hash": stable_hash_text(review_prompt),
                "final_prompt": review_prompt,
                "response": review_res.parsed_json or review_res.raw_response_text,
                "error": {"code": review_res.error_code, "message": review_res.error} if review_res.error else None,
                "scope": "PLAN_REVIEW",
                "attempt": attempt,
            },
        )
        if review_res.error or not review_res.parsed_json:
            if attempt < max_plan_attempts:
                top_task = top_task + "\n\nPlan review failed (must return valid xiaojing_review_v1 JSON). Error:\n" + str(review_res.error or review_res.error_code)
                continue
            raise PlanWorkflowError(f"plan review failed: {review_res.error}")

        review_json = review_res.parsed_json
        if isinstance(review_json, dict):
            _normalize_plan_review_json(review_json, plan_id=plan_id)
        last_review = review_json
        if review_json.get("schema_version") != "xiaojing_review_v1":
            if attempt < max_plan_attempts:
                top_task = top_task + "\n\nPlan review schema_version mismatch (expected xiaojing_review_v1)."
                continue
            raise PlanWorkflowError("plan review schema_version mismatch (expected xiaojing_review_v1)")
        if review_json.get("review_target") != "PLAN":
            if attempt < max_plan_attempts:
                top_task = top_task + "\n\nPlan review_target mismatch (expected PLAN)."
                continue
            raise PlanWorkflowError("plan review_target mismatch (expected PLAN)")
        ok, reason = validate_xiaojing_review(review_json, review_target="PLAN")
        if not ok:
            if attempt < max_plan_attempts:
                top_task = top_task + "\n\nPlan review contract invalid (must fix):\n" + reason
                continue
            raise PlanWorkflowError(f"plan review contract invalid: {reason}")

        total_score = int(review_json.get("total_score") or 0)
        action_required = str(review_json.get("action_required") or "")

        if total_score >= 90 and action_required == "APPROVE":
            ensure_dir(plan_output_path.parent)
            plan_output_path.write_text(json.dumps(plan_json, ensure_ascii=False, indent=2), encoding="utf-8")
            with transaction(conn):
                upsert_plan(conn, plan_json)
                emit_event(conn, plan_id=plan_id, event_type="PLAN_APPROVED", payload={"total_score": total_score})
                # If the plan contains a dedicated CHECK node for plan review, mark it DONE and store the review there.
                for n in plan_json.get("nodes") or []:
                    if not isinstance(n, dict):
                        continue
                    if n.get("node_type") != "CHECK" or n.get("owner_agent_id") != "xiaojing":
                        continue
                    tags = n.get("tags") or []
                    if isinstance(tags, list) and "review" in tags and "plan" in tags:
                        check_task_id = n.get("task_id")
                        if isinstance(check_task_id, str):
                            conn.execute(
                                "UPDATE task_nodes SET status='DONE', blocked_reason=NULL, updated_at=? WHERE task_id=?",
                                (utc_now_iso(), check_task_id),
                            )
                            write_review_json(config.REVIEWS_DIR, task_id=check_task_id, review=review_json)
                            insert_review(conn, plan_id=plan_id, task_id=check_task_id, reviewer_agent_id="xiaojing", review=review_json)
            return PlanWorkflowResult(plan_json=plan_json, review_json=review_json, plan_path=plan_output_path)

        with transaction(conn):
            emit_event(conn, plan_id=plan_id, event_type="PLAN_REVIEWED", payload={"total_score": total_score, "action_required": action_required, "attempt": attempt})

        # Feed reviewer suggestions back into the next attempt by appending them to the top_task.
        suggestions = review_json.get("suggestions") or []
        top_task = top_task + "\n\nReviewer feedback (must address):\n" + json.dumps(suggestions, ensure_ascii=False, indent=2)

    raise PlanWorkflowError(f"plan not approved after {max_plan_attempts} attempts; last_review={json.dumps(last_review, ensure_ascii=False)}")
