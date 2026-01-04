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
    (v2) No-op: upstream deliverables are injected into downstream prompts at run-time.

    Why:
    - Generating UPSTREAM_ARTIFACT input requirements causes noisy "inputs/upstream:*" missing-input prompts.
    - This repo runs on Claude Code which can read local files; we can inject upstream artifact paths directly
      based on DEPENDS_ON and approved/active artifacts.
    """
    return plan_json
