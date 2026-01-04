from __future__ import annotations

import uuid
from typing import Any, Dict, List, Tuple


def _node_type(node: Dict[str, Any]) -> str:
    nt = node.get("node_type") or node.get("type") or ""
    return str(nt or "").strip().upper()


def _node_title(node: Dict[str, Any]) -> str:
    for k in ("title", "name", "id"):
        v = node.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return "Upstream Task"


def _collect_existing_bindings(requirements: List[Dict[str, Any]]) -> set[Tuple[str, str]]:
    """
    Return a set of (task_id, source_task_id) pairs already bound via UPSTREAM_ARTIFACT.
    """
    out: set[Tuple[str, str]] = set()
    for r in requirements:
        if not isinstance(r, dict):
            continue
        if str(r.get("kind") or "").strip().upper() != "UPSTREAM_ARTIFACT":
            continue
        tid = str(r.get("task_id") or "").strip()
        src = str(r.get("source") or "").strip()
        if src.startswith("artifact:"):
            src = src.split("artifact:", 1)[1].strip()
        if tid and src:
            out.add((tid, src))
    return out


def add_default_upstream_bindings(plan_json: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deterministically add minimal UPSTREAM_ARTIFACT requirements based on DEPENDS_ON edges.

    Rationale (per Plan_Stages_And_Artifact_Bindings.md):
    - DEPENDS_ON is scheduling/gating only; bindings define which upstream deliverables a downstream ACTION should read.
    - We keep it minimal and deterministic: for every ACTION<-ACTION DEPENDS_ON edge, create 1 required binding.
    """
    if not isinstance(plan_json, dict):
        return plan_json

    nodes = plan_json.get("nodes")
    edges = plan_json.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return plan_json

    requirements = plan_json.get("requirements")
    if not isinstance(requirements, list):
        requirements = []
        plan_json["requirements"] = requirements

    node_by_task: Dict[str, Dict[str, Any]] = {}
    for n in nodes:
        if not isinstance(n, dict):
            continue
        tid = n.get("task_id")
        if isinstance(tid, str) and tid.strip():
            node_by_task[tid.strip()] = n

    existing = _collect_existing_bindings(requirements)

    for e in edges:
        if not isinstance(e, dict):
            continue
        if str(e.get("edge_type") or "").strip().upper() != "DEPENDS_ON":
            continue
        from_task_id = str(e.get("from_task_id") or "").strip()
        to_task_id = str(e.get("to_task_id") or "").strip()
        if not from_task_id or not to_task_id:
            continue
        from_node = node_by_task.get(from_task_id)
        to_node = node_by_task.get(to_task_id)
        if not from_node or not to_node:
            continue
        if _node_type(from_node) != "ACTION" or _node_type(to_node) != "ACTION":
            continue
        if (to_task_id, from_task_id) in existing:
            continue

        from_title = _node_title(from_node)
        from_spec = from_node.get("deliverable_spec_json") or from_node.get("deliverable_spec") or None
        desc = f"Read and follow the upstream deliverable from '{from_title}'."
        if isinstance(from_spec, dict):
            fmt = from_spec.get("format")
            fname = from_spec.get("filename")
            parts: list[str] = []
            if fmt:
                parts.append(f"format={fmt}")
            if fname:
                parts.append(f"filename={fname}")
            if parts:
                desc += " Upstream spec: " + ", ".join(parts) + "."

        requirements.append(
            {
                "requirement_id": str(uuid.uuid4()),
                "task_id": to_task_id,
                "name": f"upstream:{from_title}",
                "kind": "UPSTREAM_ARTIFACT",
                "required": True,
                "min_count": 1,
                # Keep permissive: actual file path is determined at run-time (approved/active artifact).
                "allowed_types": ["md", "txt", "json", "html", "css", "js"],
                "source": from_task_id,
                "validation": {
                    "description": desc,
                    "why": "Prevent interface drift by grounding this task on upstream deliverables.",
                },
            }
        )
        existing.add((to_task_id, from_task_id))

    return plan_json

