from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Iterable


_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

ALLOWED_ARTIFACT_FORMATS = {"md", "txt", "json", "html", "css", "js"}
ALLOWED_NODE_TYPES = {"GOAL", "ACTION", "CHECK"}
ALLOWED_EDGE_TYPES = {"DECOMPOSE", "DEPENDS_ON", "ALTERNATIVE"}
ALLOWED_AGENTS = {"xiaobo", "xiaojing", "xiaoxie"}
ALLOWED_REQUIREMENT_KINDS = {"FILE", "CONFIRMATION", "SKILL_OUTPUT"}
ALLOWED_REQUIREMENT_SOURCES = {"USER", "AGENT", "ANY"}
ALLOWED_REVIEW_ACTIONS = {"APPROVE", "MODIFY", "REQUEST_EXTERNAL_INPUT"}
ALLOWED_SUGGESTION_PRIORITIES = {"HIGH", "MED", "LOW"}


def is_uuid(value: Any) -> bool:
    return isinstance(value, str) and bool(_UUID_RE.match(value))


def is_iso8601(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except Exception:
        return False


def new_uuid() -> str:
    return str(uuid.uuid4())


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


def coerce_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _first_present(mapping: Dict[str, Any], keys: Iterable[str]) -> Any:
    for k in keys:
        if k in mapping and mapping.get(k) is not None:
            return mapping.get(k)
    return None


def _normalize_key_aliases(obj: Dict[str, Any], *, aliases: Dict[str, List[str]], overwrite: bool = False) -> None:
    """
    Move/alias keys in-place. For each canonical key, look for alternative keys and copy the first present value.

    Example:
      aliases={"task_id": ["id"], "node_type": ["type"]}
    """
    for canonical, alts in aliases.items():
        if not overwrite and canonical in obj and obj.get(canonical) is not None:
            continue
        v = _first_present(obj, alts)
        if v is not None:
            obj[canonical] = v


def _ensure_list_container(plan_json: Dict[str, Any], *, dst_key: str, src_keys: List[str]) -> List[Dict[str, Any]]:
    """
    Ensure `plan_json[dst_key]` is a list[dict]. If missing, try to take the first list found in `src_keys`.
    """
    raw = plan_json.get(dst_key)
    if not isinstance(raw, list):
        raw = _first_present(plan_json, src_keys)
    if not isinstance(raw, list):
        plan_json[dst_key] = []
        return plan_json[dst_key]
    plan_json[dst_key] = [x for x in raw if isinstance(x, dict)]
    return plan_json[dst_key]


def _clean_top_task_for_goal(top_task: str) -> str:
    # Keep only the first non-empty line so retry feedback doesn't pollute the goal statement/title.
    for line in (top_task or "").splitlines():
        s = line.strip()
        if s:
            return s[:200]
    return "Untitled Task"


def normalize_xiaobo_action(obj: Dict[str, Any], *, task_id: str) -> Dict[str, Any]:
    """
    Normalize xiaobo outputs to `xiaobo_action_v1` shape.

    This function is intentionally tolerant: it tries to repair common omissions/variants so that
    downstream validation can be strict and deterministic.
    """
    if not isinstance(obj, dict):
        return obj

    # Some models wrap the action payload under `action`/`result`/`data`.
    # Try to unwrap if the top-level does not look like a xiaobo action.
    if "result_type" not in obj:
        for k in ("action", "result", "output", "data", "payload", "response"):
            v = obj.get(k)
            if isinstance(v, dict) and ("result_type" in v or "artifact" in v or "needs_input" in v or "error" in v):
                obj = v
                break

    _normalize_key_aliases(obj, aliases={"schema_version": ["schema", "version"], "task_id": ["id", "taskId"]})

    # schema_version: default + aliases
    sv = obj.get("schema_version")
    if isinstance(sv, str):
        t = sv.strip()
        if t.lower() in {"xiaobo_action", "xiaobo_action_v0", "action_v1", "xiaobo_action_v1.0"}:
            t = "xiaobo_action_v1"
        if t.lower().startswith("xiaobo_action"):
            t = "xiaobo_action_v1"
        obj["schema_version"] = t
    else:
        obj["schema_version"] = "xiaobo_action_v1"

    if not isinstance(obj.get("task_id"), str) or not obj.get("task_id"):
        obj["task_id"] = task_id

    rt = obj.get("result_type")
    if isinstance(rt, str):
        obj["result_type"] = rt.strip().upper()

    if obj.get("result_type") == "NEEDS_INPUT":
        needs = obj.get("needs_input")
        if not isinstance(needs, dict):
            needs = {}
            obj["needs_input"] = needs

        docs = needs.get("required_docs")
        if not (isinstance(docs, list) and docs):
            normalized_docs: List[Dict[str, Any]] = []

            # Accept alternative shapes.
            missing_inputs = obj.get("missing_inputs")
            if isinstance(missing_inputs, list):
                for item in missing_inputs:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name") or "").strip()
                    desc = str(item.get("description") or item.get("reason") or "").strip()
                    accepted = item.get("accepted_types") or item.get("type")
                    accepted_types: List[str] = []
                    if isinstance(accepted, list) and all(isinstance(x, str) for x in accepted):
                        accepted_types = list(accepted)
                    elif isinstance(accepted, str) and accepted.strip():
                        accepted_types = [accepted.strip()]
                    if name:
                        normalized_docs.append({"name": name, "description": desc or name, "accepted_types": accepted_types})

            required_context = needs.get("required_context") if isinstance(needs, dict) else None
            if required_context is None:
                required_context = obj.get("required_context")
            if isinstance(required_context, list):
                for item in required_context:
                    if isinstance(item, str) and item.strip():
                        normalized_docs.append({"name": item.strip(), "description": item.strip(), "accepted_types": []})

            if not normalized_docs:
                reason = str(needs.get("reason") or obj.get("justification") or "").strip()
                normalized_docs = [{"name": "clarification", "description": reason or "Please provide missing inputs.", "accepted_types": []}]

            needs["required_docs"] = normalized_docs

    if obj.get("result_type") == "ARTIFACT":
        art = obj.get("artifact")
        if isinstance(art, dict):
            fmt = art.get("format")
            if isinstance(fmt, str):
                art["format"] = fmt.strip().lower().lstrip(".")

    return obj


def validate_xiaobo_action(obj: Dict[str, Any]) -> Tuple[bool, str]:
    def is_str(x: Any) -> bool:
        return isinstance(x, str)

    def require_keys(o: Dict[str, Any], keys: List[str]) -> Optional[str]:
        for k in keys:
            if k not in o:
                return f"missing key: {k}"
        return None

    err = require_keys(obj, ["schema_version", "task_id", "result_type"])
    if err:
        return False, err
    if obj.get("schema_version") != "xiaobo_action_v1":
        return False, f"schema_version mismatch (got {obj.get('schema_version')})"
    if not is_str(obj.get("task_id")):
        return False, "task_id must be string"
    result_type = obj.get("result_type")
    if result_type not in {"NEEDS_INPUT", "ARTIFACT", "NOOP", "ERROR"}:
        return False, "invalid result_type"

    if result_type == "NEEDS_INPUT":
        needs = obj.get("needs_input")
        if not isinstance(needs, dict):
            return False, "needs_input must be object"
        docs = needs.get("required_docs")
        if not isinstance(docs, list) or not docs:
            return False, "needs_input.required_docs must be non-empty array"
        for d in docs:
            if not isinstance(d, dict):
                return False, "required_docs item must be object"
            if not is_str(d.get("name")) or not is_str(d.get("description")):
                return False, "required_docs.name/description must be string"
            accepted = d.get("accepted_types")
            if accepted is not None and (not isinstance(accepted, list) or any(not is_str(x) for x in accepted)):
                return False, "required_docs.accepted_types must be string array"

    if result_type == "ARTIFACT":
        art = obj.get("artifact")
        if not isinstance(art, dict):
            return False, "artifact must be object"
        for k in ("name", "format", "content"):
            if not is_str(art.get(k)) or not art.get(k):
                return False, f"artifact.{k} is required"
        fmt = art.get("format")
        if fmt not in ALLOWED_ARTIFACT_FORMATS:
            return False, "artifact.format must be md|txt|json|html|css|js"

    if result_type == "ERROR":
        err_obj = obj.get("error")
        if not isinstance(err_obj, dict):
            return False, "error must be object"
        if not is_str(err_obj.get("code")) or not is_str(err_obj.get("message")):
            return False, "error.code/error.message must be string"

    return True, ""


def normalize_xiaojing_review(obj: Dict[str, Any], *, task_id: str, review_target: str) -> Dict[str, Any]:
    if not isinstance(obj, dict):
        return obj

    _normalize_key_aliases(obj, aliases={"schema_version": ["schema", "version"], "task_id": ["id", "taskId"]})

    # Some models wrap the review payload under `review_result`.
    rr = obj.get("review_result")
    if isinstance(rr, dict):
        # Copy score/action if missing or clearly defaulted.
        rr_score = rr.get("total_score")
        if isinstance(rr_score, str):
            rr_score = coerce_int(rr_score, default=0)
        if isinstance(rr_score, int) and not isinstance(rr_score, bool):
            if not isinstance(obj.get("total_score"), int) or int(obj.get("total_score") or 0) == 0:
                obj["total_score"] = int(rr_score)

        rr_action = rr.get("action_required")
        if isinstance(rr_action, str) and (not isinstance(obj.get("action_required"), str) or not obj.get("action_required")):
            obj["action_required"] = rr_action

        # Dimension scores -> breakdown (if breakdown missing/empty).
        if not isinstance(obj.get("breakdown"), list) or not obj.get("breakdown"):
            dims = rr.get("dimension_scores")
            if not isinstance(dims, list):
                dims = rr.get("scores")
            if isinstance(dims, list) and all(isinstance(x, dict) for x in dims):
                breakdown: List[Dict[str, Any]] = []
                for d in dims:
                    dim = str(d.get("dimension") or "overall")
                    sc = d.get("score")
                    if isinstance(sc, str):
                        sc = coerce_int(sc, default=0)
                    if not isinstance(sc, int) or isinstance(sc, bool):
                        sc = 0
                    comment = str(d.get("comment") or "").strip()
                    issues: List[Dict[str, Any]] = []
                    if comment:
                        issues = [
                            {
                                "problem": comment,
                                "evidence": comment,
                                "impact": "May block execution or reduce quality.",
                                "suggestion": "Follow the reviewer guidance to fix this issue.",
                                "acceptance_criteria": "Meets rubric requirements.",
                            }
                        ]
                    breakdown.append({"dimension": dim, "score": int(sc), "max_score": 100, "issues": issues})
                if breakdown:
                    obj["breakdown"] = breakdown

        # Wrapped suggestions -> suggestions (if missing/empty).
        if not isinstance(obj.get("suggestions"), list) or not obj.get("suggestions"):
            rr_sugs = rr.get("suggestions")
            if not isinstance(rr_sugs, list):
                rr_sugs = rr.get("recommendations")
            if isinstance(rr_sugs, list) and all(isinstance(x, dict) for x in rr_sugs):
                normalized: List[Dict[str, Any]] = []
                for s in rr_sugs:
                    change = s.get("change")
                    if not isinstance(change, str) or not change.strip():
                        prob = str(s.get("problem") or "").strip()
                        dim = str(s.get("dimension") or "").strip()
                        change = (prob + (f" ({dim})" if dim else "")).strip() or "Clarify and adjust output as requested."
                    steps = s.get("steps")
                    if not isinstance(steps, list) or any(not isinstance(x, str) for x in steps):
                        steps = []
                    acceptance = s.get("acceptance_criteria")
                    if not isinstance(acceptance, str) or not acceptance.strip():
                        acceptance = "Meets rubric requirements."
                    normalized.append(
                        {
                            "priority": "MED",
                            "change": change.strip(),
                            "steps": [x.strip() for x in steps if isinstance(x, str) and x.strip()],
                            "acceptance_criteria": acceptance.strip(),
                        }
                    )
                if normalized:
                    obj["suggestions"] = normalized

    # schema_version aliases
    schema_version = obj.get("schema_version")
    if isinstance(schema_version, str):
        sv = schema_version.strip()
        if sv.lower() in {"xiaojing_review", "xiaojing_review_v0", "review_v1", "xiaojing_review_v1.0"}:
            sv = "xiaojing_review_v1"
        # Very common short forms
        if sv.lower() in {"v1", "v01", "1", "review1", "review_v01"}:
            sv = "xiaojing_review_v1"
        if sv.lower().startswith("xiaojing_review"):
            sv = "xiaojing_review_v1"
        obj["schema_version"] = sv
    else:
        obj["schema_version"] = "xiaojing_review_v1"

    if not isinstance(obj.get("task_id"), str) or not obj.get("task_id"):
        obj["task_id"] = task_id

    rt = obj.get("review_target")
    if isinstance(rt, str):
        t = rt.strip().upper()
        if t in {"PLAN_REVIEW", "PLAN_JSON", "TOP_TASK"}:
            t = "PLAN"
        obj["review_target"] = t
    else:
        obj["review_target"] = review_target

    total_score = obj.get("total_score")
    if isinstance(total_score, str):
        obj["total_score"] = coerce_int(total_score, default=0)
    if not isinstance(obj.get("total_score"), int) or isinstance(obj.get("total_score"), bool):
        obj["total_score"] = 0
    score = int(obj.get("total_score") or 0)

    action_required = obj.get("action_required")
    if isinstance(action_required, str):
        obj["action_required"] = action_required.strip().upper()
    if obj.get("action_required") not in ALLOWED_REVIEW_ACTIONS:
        obj["action_required"] = "MODIFY"
    if score >= 90:
        obj["action_required"] = "APPROVE"
    elif obj.get("action_required") == "APPROVE":
        obj["action_required"] = "MODIFY"

    summary = obj.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        feedback = obj.get("feedback")
        if isinstance(feedback, str) and feedback.strip():
            obj["summary"] = feedback.strip()
        else:
            obj["summary"] = "No summary provided."

    breakdown = obj.get("breakdown")
    if not isinstance(breakdown, list) or not breakdown:
        obj["breakdown"] = [{"dimension": "overall", "score": score, "max_score": 100, "issues": []}]

    suggestions = obj.get("suggestions")
    if not isinstance(suggestions, list):
        suggestions = []
    priority_aliases = {
        "H": "HIGH",
        "HI": "HIGH",
        "URGENT": "HIGH",
        "CRITICAL": "HIGH",
        "M": "MED",
        "MID": "MED",
        "MEDIUM": "MED",
        "NORMAL": "MED",
        "L": "LOW",
        "MINOR": "LOW",
        "TRIVIAL": "LOW",
    }
    normalized_suggestions: List[Dict[str, Any]] = []
    for s in suggestions:
        if not isinstance(s, dict):
            continue
        pr = s.get("priority")
        pr_norm = "MED"
        if isinstance(pr, str):
            pr_norm = priority_aliases.get(pr.strip().upper(), pr.strip().upper())
        if pr_norm not in ALLOWED_SUGGESTION_PRIORITIES:
            pr_norm = "MED"
        change = s.get("change")
        if not isinstance(change, str) or not change.strip():
            change = "Clarify and adjust output as requested."
        steps = s.get("steps")
        if not isinstance(steps, list) or any(not isinstance(x, str) for x in steps):
            steps = []
        acceptance = s.get("acceptance_criteria")
        if not isinstance(acceptance, str) or not acceptance.strip():
            acceptance = "Meets rubric requirements."
        normalized_suggestions.append(
            {
                "priority": pr_norm,
                "change": change.strip(),
                "steps": [x.strip() for x in steps if isinstance(x, str) and x.strip()],
                "acceptance_criteria": acceptance.strip(),
            }
        )
    obj["suggestions"] = normalized_suggestions

    return obj


def validate_xiaojing_review(obj: Dict[str, Any], *, review_target: str) -> Tuple[bool, str]:
    def is_str(x: Any) -> bool:
        return isinstance(x, str)

    def is_int(x: Any) -> bool:
        return isinstance(x, int) and not isinstance(x, bool)

    def require_keys(o: Dict[str, Any], keys: List[str]) -> Optional[str]:
        for k in keys:
            if k not in o:
                return f"missing key: {k}"
        return None

    err = require_keys(obj, ["schema_version", "task_id", "review_target", "total_score", "breakdown", "summary", "action_required", "suggestions"])
    if err:
        return False, err
    if obj.get("schema_version") != "xiaojing_review_v1":
        return False, f"schema_version mismatch (got {obj.get('schema_version')})"
    if obj.get("review_target") != review_target:
        return False, f"review_target mismatch (got {obj.get('review_target')}, expected {review_target})"
    if not is_str(obj.get("task_id")):
        return False, "task_id must be string"
    total = obj.get("total_score")
    if not is_int(total):
        return False, "total_score must be int"
    if int(total) < 0 or int(total) > 100:
        return False, "total_score out of range"
    action = obj.get("action_required")
    if action not in ALLOWED_REVIEW_ACTIONS:
        return False, "invalid action_required"
    if int(total) >= 90 and action != "APPROVE":
        return False, "total_score>=90 requires action_required=APPROVE"
    if int(total) < 90 and action == "APPROVE":
        return False, "total_score<90 cannot be APPROVE"

    breakdown = obj.get("breakdown")
    if not isinstance(breakdown, list):
        return False, "breakdown must be array"
    for dim in breakdown:
        if not isinstance(dim, dict):
            return False, "breakdown item must be object"
        for k in ("dimension", "score", "max_score", "issues"):
            if k not in dim:
                return False, f"breakdown missing {k}"
        if not is_str(dim.get("dimension")):
            return False, "breakdown.dimension must be string"
        if not is_int(dim.get("score")) or not is_int(dim.get("max_score")):
            return False, "breakdown.score/max_score must be int"
        issues = dim.get("issues")
        if not isinstance(issues, list):
            return False, "breakdown.issues must be array"
        for issue in issues:
            if not isinstance(issue, dict):
                return False, "issue must be object"
            for k in ("problem", "evidence", "impact", "suggestion", "acceptance_criteria"):
                if not is_str(issue.get(k)):
                    return False, f"issue.{k} must be string"

    suggestions = obj.get("suggestions")
    if not isinstance(suggestions, list):
        return False, "suggestions must be array"
    for s in suggestions:
        if not isinstance(s, dict):
            return False, "suggestion must be object"
        if s.get("priority") not in ALLOWED_SUGGESTION_PRIORITIES:
            return False, "suggestion.priority must be HIGH|MED|LOW"
        if not is_str(s.get("change")):
            return False, "suggestion.change must be string"
        steps = s.get("steps")
        if not isinstance(steps, list) or any(not is_str(x) for x in steps):
            return False, "suggestion.steps must be string array"
        if not is_str(s.get("acceptance_criteria")):
            return False, "suggestion.acceptance_criteria must be string"

    return True, ""


def normalize_plan_json(plan_json: Dict[str, Any], *, top_task: str, utc_now_iso: Any) -> Dict[str, Any]:
    """
    Normalize a raw plan_json into the strict plan.json schema expected by validate_plan_dict().

    `utc_now_iso` is injected (function) to avoid circular imports.
    """
    if not isinstance(plan_json, dict):
        return {"plan": {}, "nodes": [], "edges": [], "requirements": []}

    plan = plan_json.get("plan")
    if not isinstance(plan, dict):
        # Accept flat plan fields at the top-level (common in other formats).
        if isinstance(plan_json, dict):
            plan = {}
            _normalize_key_aliases(
                plan,
                aliases={
                    "plan_id": ["id"],
                    "title": ["name"],
                    "owner_agent_id": ["owner", "agent"],
                    "root_task_id": ["root", "root_id"],
                    "created_at": ["ts", "created", "createdAt"],
                    "constraints": ["constraints_json", "constraint"],
                },
                overwrite=True,
            )
            # Also pull same keys from top-level if present.
            _normalize_key_aliases(
                plan,
                aliases={
                    "plan_id": ["plan_id", "planId"],
                    "title": ["title"],
                    "owner_agent_id": ["owner_agent_id"],
                    "root_task_id": ["root_task_id"],
                    "created_at": ["created_at"],
                    "constraints": ["constraints"],
                },
                overwrite=True,
            )
        plan_json["plan"] = plan

    title = str(plan.get("title") or "").strip()
    if not title:
        title = _clean_top_task_for_goal(top_task)[:120] or "Untitled Plan"
    plan["title"] = title

    if not is_uuid(plan.get("plan_id")):
        plan["plan_id"] = new_uuid()
    if not is_uuid(plan.get("root_task_id")):
        plan["root_task_id"] = new_uuid()
    if not is_iso8601(plan.get("created_at")):
        plan["created_at"] = utc_now_iso()
    if str(plan.get("owner_agent_id") or "").strip() not in ALLOWED_AGENTS:
        plan["owner_agent_id"] = "xiaobo"
    if not isinstance(plan.get("constraints"), dict):
        plan["constraints"] = {"deadline": None, "priority": "HIGH"}

    # Normalize containers and drop invalid items (accept common alternate names).
    nodes: List[Dict[str, Any]] = _ensure_list_container(plan_json, dst_key="nodes", src_keys=["nodes", "tasks", "task_nodes", "items"])
    edges: List[Dict[str, Any]] = _ensure_list_container(plan_json, dst_key="edges", src_keys=["edges", "links", "deps", "dependencies", "task_edges"])
    reqs: List[Dict[str, Any]] = _ensure_list_container(plan_json, dst_key="requirements", src_keys=["requirements", "inputs", "input_requirements", "requirements_list"])

    # UUID mapping for non-UUID ids; keep UUIDs stable.
    id_map: Dict[str, str] = {}

    def map_id(value: Any) -> str:
        if isinstance(value, str) and value:
            if is_uuid(value):
                return value
            if value not in id_map:
                id_map[value] = new_uuid()
            return id_map[value]
        return new_uuid()

    plan_id = plan["plan_id"]
    root_task_id = plan["root_task_id"]

    # Accept common alternate key names from other plan formats.
    for n in nodes:
        _normalize_key_aliases(
            n,
            aliases={
                "task_id": ["id", "taskId", "node_id", "nodeId"],
                "title": ["name", "label"],
                "node_type": ["type", "kind"],
                "owner_agent_id": ["owner", "agent"],
                "priority": ["prio"],
                "goal_statement": ["goal", "objective"],
                "rationale": ["reason", "why"],
                "tags": ["labels"],
            },
        )

    def _edge_endpoint(e: Dict[str, Any], keys: List[str]) -> Any:
        for k in keys:
            if k in e and e.get(k) is not None:
                return e.get(k)
        return None

    for e in edges:
        _normalize_key_aliases(
            e,
            aliases={
                "edge_id": ["id"],
                "from_task_id": ["from", "from_id", "source", "src", "parent_id"],
                "to_task_id": ["to", "to_id", "target", "tgt", "child_id"],
                "edge_type": ["type", "relation", "relation_type", "kind"],
                "metadata": ["meta"],
            },
        )

    for n in nodes:
        n["task_id"] = map_id(n.get("task_id"))
        n["plan_id"] = plan_id

    for e in edges:
        e["edge_id"] = map_id(e.get("edge_id"))
        e["plan_id"] = plan_id
        e["from_task_id"] = map_id(e.get("from_task_id"))
        e["to_task_id"] = map_id(e.get("to_task_id"))

    for r in reqs:
        r["requirement_id"] = map_id(r.get("requirement_id"))
        r["task_id"] = map_id(r.get("task_id"))

    # Normalize common synthetic START/END nodes:
    # - Rewrite edges from START -> X into root_task_id -> X (DECOMPOSE)
    # - Drop edges to END
    # This prevents autocreated placeholder nodes from polluting the plan.
    start_ids = {v for k, v in id_map.items() if isinstance(k, str) and k.strip().upper() in {"START", "BEGIN"}}
    end_ids = {v for k, v in id_map.items() if isinstance(k, str) and k.strip().upper() in {"END", "FINISH", "STOP"}}
    if start_ids or end_ids:
        new_edges: List[Dict[str, Any]] = []
        for e in edges:
            if e.get("to_task_id") in end_ids:
                continue
            if e.get("from_task_id") in start_ids:
                e["from_task_id"] = root_task_id
                e["edge_type"] = "DECOMPOSE"
                meta = e.get("metadata")
                if not isinstance(meta, dict):
                    meta = {}
                    e["metadata"] = meta
                meta["and_or"] = "AND"
            new_edges.append(e)
        edges[:] = new_edges

    # Ensure referenced nodes exist.
    node_by_id: Dict[str, Dict[str, Any]] = {n["task_id"]: n for n in nodes if isinstance(n.get("task_id"), str)}

    def ensure_node(task_id: Any, *, is_root: bool = False) -> None:
        if not isinstance(task_id, str) or not task_id:
            return
        if task_id in node_by_id:
            return
        nodes.append(
            {
                "task_id": task_id,
                "plan_id": plan_id,
                "node_type": "GOAL" if is_root else "ACTION",
                "title": "Root Task" if is_root else f"AUTO: missing node {task_id[:8]}",
                "goal_statement": _clean_top_task_for_goal(top_task) if is_root else None,
                "rationale": "Autocreated placeholder node for referential integrity.",
                "owner_agent_id": "xiaobo",
                "priority": 0,
                "tags": ["autofix", "placeholder"],
            }
        )
        node_by_id[task_id] = nodes[-1]

    ensure_node(root_task_id, is_root=True)
    for e in edges:
        ensure_node(e.get("from_task_id"))
        ensure_node(e.get("to_task_id"))
    for r in reqs:
        ensure_node(r.get("task_id"))

    # Drop placeholder nodes that correspond to START/END after edge rewrite.
    if start_ids or end_ids:
        drop_ids = set(start_ids) | set(end_ids)
        nodes[:] = [n for n in nodes if n.get("task_id") not in drop_ids]
        node_by_id = {n["task_id"]: n for n in nodes if isinstance(n.get("task_id"), str)}

    # Coerce required node fields.
    for idx, n in enumerate(nodes):
        node_type = n.get("node_type")
        if not isinstance(node_type, str) or node_type.strip().upper() not in ALLOWED_NODE_TYPES:
            n["node_type"] = "GOAL" if n.get("task_id") == root_task_id else "ACTION"
        else:
            n["node_type"] = node_type.strip().upper()
        if not str(n.get("title") or "").strip():
            n["title"] = f"Task {idx + 1}"
        # Ensure root GOAL has a non-empty goal_statement to reduce reviewer false negatives.
        if n.get("task_id") == root_task_id and n.get("node_type") == "GOAL":
            gs = n.get("goal_statement")
            if not isinstance(gs, str) or not gs.strip():
                n["goal_statement"] = _clean_top_task_for_goal(top_task)
        owner_agent_id = n.get("owner_agent_id")
        if not isinstance(owner_agent_id, str) or owner_agent_id.strip() not in ALLOWED_AGENTS:
            n["owner_agent_id"] = "xiaobo"
        n["priority"] = coerce_int(n.get("priority"), default=0)
        tags = n.get("tags")
        if not isinstance(tags, list) or any(not isinstance(x, str) for x in tags):
            n["tags"] = []

    # Coerce required edge fields + enums.
    edge_type_aliases = {
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
        "ALT": "ALTERNATIVE",
        "ALTERNATE": "ALTERNATIVE",
    }
    for e in edges:
        et = e.get("edge_type")
        et_norm = "DEPENDS_ON"
        if isinstance(et, str):
            et_norm = edge_type_aliases.get(et.strip().upper(), et.strip().upper())
        if et_norm not in ALLOWED_EDGE_TYPES:
            et_norm = "DEPENDS_ON"
        e["edge_type"] = et_norm
        meta = e.get("metadata")
        if meta is None or not isinstance(meta, dict):
            meta = {}
            e["metadata"] = meta
        if et_norm == "DECOMPOSE":
            ao = str(meta.get("and_or") or "AND").strip().upper()
            meta["and_or"] = ao if ao in {"AND", "OR"} else "AND"
        if et_norm == "ALTERNATIVE":
            gid = meta.get("group_id")
            if not isinstance(gid, str) or not gid.strip():
                meta["group_id"] = "AUTO_GROUP_1"

    # Coerce required requirement fields + enums.
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
    for idx, r in enumerate(reqs):
        if not str(r.get("name") or "").strip():
            r["name"] = f"requirement_{idx + 1}"
        kind = r.get("kind")
        kind_norm = "FILE"
        if isinstance(kind, str):
            kind_norm = kind_aliases.get(kind.strip().upper(), kind.strip().upper())
        if kind_norm not in ALLOWED_REQUIREMENT_KINDS:
            kind_norm = "FILE"
        r["kind"] = kind_norm
        source = r.get("source")
        src_norm = "USER"
        if isinstance(source, str):
            src_norm = source.strip().upper()
        if src_norm not in ALLOWED_REQUIREMENT_SOURCES:
            src_norm = "USER"
        r["source"] = src_norm
        r["required"] = coerce_bool_int(r.get("required"), default=1)
        r["min_count"] = max(1, coerce_int(r.get("min_count"), default=1))
        allowed_types = r.get("allowed_types")
        if isinstance(allowed_types, str):
            r["allowed_types"] = [allowed_types]
        elif not isinstance(allowed_types, list) or any(not isinstance(x, str) for x in allowed_types):
            r["allowed_types"] = []

    # If the model omitted edges entirely, synthesize a minimal DECOMPOSE tree from root -> all other nodes.
    # This makes plan completion computable (GOAL can become DONE when all children are DONE).
    if not edges and len(nodes) > 1:
        for n in nodes:
            if n.get("task_id") == root_task_id:
                continue
            edges.append(
                {
                    "edge_id": new_uuid(),
                    "plan_id": plan_id,
                    "from_task_id": root_task_id,
                    "to_task_id": n.get("task_id"),
                    "edge_type": "DECOMPOSE",
                    "metadata": {"and_or": "AND"},
                }
            )
    # If edges exist but there is no DECOMPOSE from root, add minimal root->children DECOMPOSE edges.
    # Many external planners encode only DEPENDS_ON chains (START->...->END) which prevents GOAL aggregation.
    has_root_decompose = any(e.get("edge_type") == "DECOMPOSE" and e.get("from_task_id") == root_task_id for e in edges)
    if (not has_root_decompose) and len(nodes) > 1:
        existing_pairs = {(e.get("from_task_id"), e.get("to_task_id"), e.get("edge_type")) for e in edges}
        for n in nodes:
            tid = n.get("task_id")
            if tid == root_task_id:
                continue
            key = (root_task_id, tid, "DECOMPOSE")
            if key in existing_pairs:
                continue
            edges.append(
                {
                    "edge_id": new_uuid(),
                    "plan_id": plan_id,
                    "from_task_id": root_task_id,
                    "to_task_id": tid,
                    "edge_type": "DECOMPOSE",
                    "metadata": {"and_or": "AND"},
                }
            )

    return plan_json
