from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any, Dict, List, Optional


class PlanValidationError(ValueError):
    pass


_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _is_uuid(s: str) -> bool:
    return bool(_UUID_RE.match(s))


def _is_iso8601(s: str) -> bool:
    # Accept "Z" suffix.
    try:
        datetime.fromisoformat(s.replace("Z", "+00:00"))
        return True
    except Exception:
        return False


@dataclass(frozen=True)
class PlanMeta:
    plan_id: str
    title: str
    owner_agent_id: str
    root_task_id: str
    created_at: str
    constraints: Dict[str, Any]


def _require(d: Dict[str, Any], key: str, t: type) -> Any:
    if key not in d:
        raise PlanValidationError(f"missing required key: {key}")
    value = d[key]
    if not isinstance(value, t):
        raise PlanValidationError(f"invalid type for {key}: expected {t.__name__}")
    return value


def validate_plan_dict(plan_dict: Dict[str, Any]) -> None:
    if not isinstance(plan_dict, dict):
        raise PlanValidationError("plan.json root must be an object")
    for top_key in ("plan", "nodes", "edges", "requirements"):
        if top_key not in plan_dict:
            raise PlanValidationError(f"plan.json missing top-level field: {top_key}")

    plan = _require(plan_dict, "plan", dict)
    plan_id = _require(plan, "plan_id", str)
    _require(plan, "title", str)
    _require(plan, "owner_agent_id", str)
    root_task_id = _require(plan, "root_task_id", str)
    created_at = _require(plan, "created_at", str)

    if not _is_uuid(plan_id):
        raise PlanValidationError("plan.plan_id must be a UUID string")
    if not _is_uuid(root_task_id):
        raise PlanValidationError("plan.root_task_id must be a UUID string")
    if not _is_iso8601(created_at):
        raise PlanValidationError("plan.created_at must be ISO8601")

    constraints = plan.get("constraints", {})
    if constraints is None:
        constraints = {}
    if not isinstance(constraints, dict):
        raise PlanValidationError("plan.constraints must be an object")

    nodes = _require(plan_dict, "nodes", list)
    if not nodes:
        raise PlanValidationError("nodes must not be empty")
    seen_task_ids: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            raise PlanValidationError("each node must be an object")
        for k in ("task_id", "plan_id", "node_type", "title", "owner_agent_id", "priority", "tags"):
            if k not in node:
                raise PlanValidationError(f"node missing key: {k}")
        if node.get("plan_id") != plan_id:
            raise PlanValidationError("node.plan_id must equal plan.plan_id")
        if not isinstance(node.get("task_id"), str) or not _is_uuid(node["task_id"]):
            raise PlanValidationError("node.task_id must be a UUID string")
        node_type = node.get("node_type")
        if node_type not in {"GOAL", "ACTION", "CHECK"}:
            raise PlanValidationError("node.node_type must be GOAL|ACTION|CHECK")
        owner_agent_id = node.get("owner_agent_id")
        if owner_agent_id not in {"xiaobo", "xiaojing", "xiaoxie"}:
            raise PlanValidationError("node.owner_agent_id must be xiaobo|xiaojing|xiaoxie")
        tags = node.get("tags")
        if not isinstance(tags, list):
            raise PlanValidationError("node.tags must be an array")
        if node["task_id"] in seen_task_ids:
            raise PlanValidationError("duplicate node.task_id")
        seen_task_ids.add(node["task_id"])

    edges = _require(plan_dict, "edges", list)
    adjacency: Dict[str, List[str]] = {tid: [] for tid in seen_task_ids}
    decompose_mode_by_parent: Dict[str, str] = {}
    for edge in edges:
        if not isinstance(edge, dict):
            raise PlanValidationError("each edge must be an object")
        for k in ("edge_id", "plan_id", "from_task_id", "to_task_id", "edge_type"):
            if k not in edge:
                raise PlanValidationError(f"edge missing key: {k}")
        if edge.get("plan_id") != plan_id:
            raise PlanValidationError("edge.plan_id must equal plan.plan_id")
        if not isinstance(edge.get("edge_id"), str) or not _is_uuid(edge["edge_id"]):
            raise PlanValidationError("edge.edge_id must be a UUID string")
        for k in ("from_task_id", "to_task_id"):
            if not isinstance(edge.get(k), str) or not _is_uuid(edge[k]):
                raise PlanValidationError(f"edge.{k} must be a UUID string")
        edge_type = edge.get("edge_type")
        if edge_type not in {"DECOMPOSE", "DEPENDS_ON", "ALTERNATIVE"}:
            raise PlanValidationError("edge.edge_type must be DECOMPOSE|DEPENDS_ON|ALTERNATIVE")
        if edge["from_task_id"] not in seen_task_ids or edge["to_task_id"] not in seen_task_ids:
            raise PlanValidationError("edge endpoints must reference existing nodes.task_id")
        metadata = edge.get("metadata", {})
        if metadata is not None and not isinstance(metadata, dict):
            raise PlanValidationError("edge.metadata must be an object")
        if edge_type == "DECOMPOSE":
            and_or = (metadata or {}).get("and_or", "AND")
            if and_or not in {"AND", "OR"}:
                raise PlanValidationError("DECOMPOSE.metadata.and_or must be AND|OR")
            parent = edge["from_task_id"]
            prev = decompose_mode_by_parent.get(parent)
            if prev is None:
                decompose_mode_by_parent[parent] = and_or
            elif prev != and_or:
                raise PlanValidationError("DECOMPOSE.metadata.and_or must be consistent for the same parent")
        if edge_type == "ALTERNATIVE":
            group_id = (metadata or {}).get("group_id")
            if not isinstance(group_id, str) or not group_id:
                raise PlanValidationError("ALTERNATIVE.metadata.group_id is required")
        adjacency[edge["from_task_id"]].append(edge["to_task_id"])

    requirements = _require(plan_dict, "requirements", list)
    for req in requirements:
        if not isinstance(req, dict):
            raise PlanValidationError("each requirement must be an object")
        for k in ("requirement_id", "task_id", "name", "kind", "required", "min_count", "allowed_types", "source"):
            if k not in req:
                raise PlanValidationError(f"requirement missing key: {k}")
        if not isinstance(req.get("requirement_id"), str) or not _is_uuid(req["requirement_id"]):
            raise PlanValidationError("requirement.requirement_id must be a UUID string")
        if not isinstance(req.get("task_id"), str) or not _is_uuid(req["task_id"]):
            raise PlanValidationError("requirement.task_id must be a UUID string")
        if req["task_id"] not in seen_task_ids:
            raise PlanValidationError("requirement.task_id must reference an existing node.task_id")
        if req.get("kind") not in {"FILE", "CONFIRMATION", "SKILL_OUTPUT"}:
            raise PlanValidationError("requirement.kind must be FILE|CONFIRMATION|SKILL_OUTPUT")
        if req.get("source") not in {"USER", "AGENT", "ANY"}:
            raise PlanValidationError("requirement.source must be USER|AGENT|ANY")
        allowed_types = req.get("allowed_types")
        if not isinstance(allowed_types, list) or any(not isinstance(x, str) for x in allowed_types):
            raise PlanValidationError("requirement.allowed_types must be a string array")

    # Cycle detection across declared edges (DECOMPOSE/DEPENDS_ON/ALTERNATIVE).
    visiting: set[str] = set()
    visited: set[str] = set()

    def dfs(u: str) -> None:
        if u in visited:
            return
        if u in visiting:
            raise PlanValidationError("cycle detected in task graph")
        visiting.add(u)
        for v in adjacency.get(u, []):
            dfs(v)
        visiting.remove(u)
        visited.add(u)

    for node_id in list(seen_task_ids):
        dfs(node_id)


def parse_plan_meta(plan_dict: Dict[str, Any]) -> PlanMeta:
    plan = plan_dict["plan"]
    constraints = plan.get("constraints") or {}
    return PlanMeta(
        plan_id=plan["plan_id"],
        title=plan["title"],
        owner_agent_id=plan["owner_agent_id"],
        root_task_id=plan["root_task_id"],
        created_at=plan["created_at"],
        constraints=constraints,
    )


def as_list_of_str(value: Any) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for item in value:
        if isinstance(item, str):
            out.append(item)
    return out


def as_optional_str(value: Any) -> Optional[str]:
    if isinstance(value, str):
        return value
    return None
