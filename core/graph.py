from __future__ import annotations

import json
import sqlite3
import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import config
from core.util import utc_now_iso


@dataclass(frozen=True)
class GraphQueryResult:
    graph: Dict[str, Any]
    plan_id: str


def _parse_required_docs_md(path: Path) -> List[Dict[str, Any]]:
    """
    Parse `workspace/required_docs/<task_id>.md` written by run.py.

    Format:
      - name: description
        - accepted_types: [...]
        - suggested_path: workspace/inputs/...
    """
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []

    out: List[Dict[str, Any]] = []
    cur: Dict[str, Any] | None = None

    def _parse_accepted_types(raw: str) -> List[str]:
        s2 = (raw or "").strip()
        if not s2:
            return []
        # Prefer JSON list: ["md","txt"]
        for candidate in (s2, s2.replace("'", '"')):
            try:
                v = json.loads(candidate)
                if isinstance(v, list):
                    return [str(x).strip().strip("'").strip('"') for x in v if str(x).strip()]
            except Exception:
                pass
        # Fallback: Python literal list: ['md','txt']
        try:
            v = ast.literal_eval(s2)
            if isinstance(v, list):
                return [str(x).strip().strip("'").strip('"') for x in v if str(x).strip()]
        except Exception:
            pass
        # Last resort: comma split
        s3 = s2.strip("[](){} ")
        parts = [p.strip().strip("'").strip('"') for p in s3.split(",")]
        return [p for p in parts if p]

    def flush() -> None:
        nonlocal cur
        if cur and cur.get("name"):
            out.append(cur)
        cur = None

    for ln in lines:
        s = ln.rstrip()
        if s.startswith("- ") and not s.startswith("  - "):
            flush()
            body = s[2:].strip()
            name = body
            desc = ""
            if ":" in body:
                name, desc = body.split(":", 1)
            cur = {
                "name": name.strip(),
                "description": desc.strip(),
                "accepted_types": [],
                "suggested_path": "",
            }
            continue
        if cur is None:
            continue
        if s.strip().startswith("- accepted_types:"):
            v = s.split(":", 1)[1].strip()
            cur["accepted_types"] = _parse_accepted_types(v)
            continue
        if s.strip().startswith("- suggested_path:"):
            v = s.split(":", 1)[1].strip()
            cur["suggested_path"] = v
            continue

    flush()
    return out


def _missing_requirements(conn: sqlite3.Connection, *, task_id: str) -> List[Dict[str, Any]]:
    """
    Compute missing required inputs for a task based on input_requirements/evidences counts.
    """
    reqs = conn.execute(
        """
        SELECT requirement_id, name, required, min_count, allowed_types_json
        FROM input_requirements
        WHERE task_id = ?
        ORDER BY created_at ASC
        """,
        (task_id,),
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in reqs:
        if int(r["required"] or 0) != 1:
            continue
        have = conn.execute("SELECT COUNT(1) FROM evidences WHERE requirement_id=?", (r["requirement_id"],)).fetchone()[0]
        need = int(r["min_count"] or 1)
        if int(have) >= need:
            continue
        allowed = []
        raw = r["allowed_types_json"]
        if raw:
            try:
                allowed = json.loads(raw)
            except Exception:
                allowed = []
        out.append(
            {
                "name": r["name"],
                "have": int(have),
                "need": need,
                "accepted_types": allowed,
                "suggested_path": f"workspace/inputs/{r['name']}/",
            }
        )
    return out


def _last_error(conn: sqlite3.Connection, *, plan_id: str, task_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        """
        SELECT created_at, payload_json
        FROM task_events
        WHERE plan_id = ? AND task_id = ? AND event_type = 'ERROR'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (plan_id, task_id),
    ).fetchone()
    if not row:
        return None
    try:
        payload = json.loads(row["payload_json"] or "{}")
    except Exception:
        payload = {"raw": row["payload_json"]}
    code = payload.get("error_code") if isinstance(payload, dict) else None
    msg = payload.get("message") if isinstance(payload, dict) else None
    return {"created_at": row["created_at"], "error_code": code, "message": msg}


def _last_review(conn: sqlite3.Connection, *, task_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        """
        SELECT total_score, action_required, summary, created_at
        FROM reviews
        WHERE task_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (task_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "total_score": int(row["total_score"] or 0),
        "action_required": row["action_required"],
        "summary": row["summary"],
        "created_at": row["created_at"],
    }

def _infer_running_task(conn: sqlite3.Connection, *, plan_id: str) -> Tuple[Optional[str], Optional[str], str]:
    """
    Best-effort running task detection for UI highlight.

    Priority:
    1) Any task_nodes.status == IN_PROGRESS
    2) Latest llm_calls.task_id within last ~2 minutes for this plan
    """
    row = conn.execute(
        """
        SELECT task_id, updated_at
        FROM task_nodes
        WHERE plan_id = ? AND active_branch = 1 AND status = 'IN_PROGRESS'
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (plan_id,),
    ).fetchone()
    if row:
        return row["task_id"], row["updated_at"], "status"

    row = conn.execute(
        """
        SELECT task_id, created_at
        FROM llm_calls
        WHERE plan_id = ? AND task_id IS NOT NULL
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (plan_id,),
    ).fetchone()
    if row and row["task_id"]:
        return row["task_id"], row["created_at"], "llm_calls"

    return None, None, "none"


def build_plan_graph(conn: sqlite3.Connection, *, plan_id: Optional[str]) -> GraphQueryResult:
    if plan_id is None:
        row = conn.execute("SELECT plan_id FROM plans ORDER BY created_at DESC LIMIT 1").fetchone()
        if not row:
            raise RuntimeError("No plan found in DB.")
        plan_id = row["plan_id"]

    plan = conn.execute("SELECT plan_id, title, root_task_id, created_at FROM plans WHERE plan_id=?", (plan_id,)).fetchone()
    if not plan:
        raise RuntimeError(f"Plan not found: {plan_id}")

    nodes_rows = conn.execute(
        """
        SELECT
          n.task_id,
          n.title,
          n.node_type,
          n.status,
          n.owner_agent_id,
          n.priority,
          n.blocked_reason,
          n.attempt_count,
          n.tags_json,
          n.active_artifact_id,
          a.artifact_id,
          a.format AS artifact_format,
          a.path AS artifact_path
        FROM task_nodes n
        LEFT JOIN artifacts a ON a.artifact_id = n.active_artifact_id
        WHERE n.plan_id = ? AND n.active_branch = 1
        ORDER BY n.priority DESC, n.created_at ASC
        """,
        (plan_id,),
    ).fetchall()

    edges_rows = conn.execute(
        """
        SELECT edge_id, from_task_id, to_task_id, edge_type, metadata_json
        FROM task_edges
        WHERE plan_id = ?
        ORDER BY created_at ASC
        """,
        (plan_id,),
    ).fetchall()

    running_task_id, running_since, running_source = _infer_running_task(conn, plan_id=plan_id)

    nodes: List[Dict[str, Any]] = []
    for r in nodes_rows:
        req_path = config.REQUIRED_DOCS_DIR / f"{r['task_id']}.md"
        artifact_dir = config.ARTIFACTS_DIR / str(r["task_id"])
        review_dir = config.REVIEWS_DIR / str(r["task_id"])
        missing = _missing_requirements(conn, task_id=r["task_id"])
        # If required_docs exists, prefer its suggested_path and accepted_types.
        if req_path.exists():
            parsed = _parse_required_docs_md(req_path)
            if parsed:
                missing_docs: List[Dict[str, Any]] = []
                for d in parsed:
                    missing_docs.append(
                        {
                            "name": d.get("name") or "",
                            "description": d.get("description") or "",
                            "accepted_types": d.get("accepted_types") or [],
                            "suggested_path": d.get("suggested_path") or "",
                        }
                    )
                missing = missing_docs

        nodes.append(
            {
                "task_id": r["task_id"],
                "title": r["title"],
                "node_type": r["node_type"],
                "status": r["status"],
                "owner_agent_id": r["owner_agent_id"],
                "priority": int(r["priority"] or 0),
                "blocked_reason": r["blocked_reason"],
                "attempt_count": int(r["attempt_count"] or 0),
                "tags": json.loads(r["tags_json"] or "[]") if r["tags_json"] else [],
                "active_artifact": (
                    {
                        "artifact_id": r["artifact_id"],
                        "format": r["artifact_format"],
                        "path": r["artifact_path"],
                    }
                    if r["artifact_id"]
                    else None
                ),
                "missing_inputs": missing,
                "required_docs_path": str(req_path),
                "last_error": _last_error(conn, plan_id=plan_id, task_id=r["task_id"]),
                "last_review": _last_review(conn, task_id=r["task_id"]),
                "artifact_dir": str(artifact_dir),
                "review_dir": str(review_dir),
                "is_running": bool(r["task_id"] == running_task_id),
            }
        )

    edges: List[Dict[str, Any]] = []
    for r in edges_rows:
        meta = {}
        if r["metadata_json"]:
            try:
                meta = json.loads(r["metadata_json"])
            except Exception:
                meta = {"raw": r["metadata_json"]}
        edges.append(
            {
                "edge_id": r["edge_id"],
                "from_task_id": r["from_task_id"],
                "to_task_id": r["to_task_id"],
                "edge_type": r["edge_type"],
                "metadata": meta,
            }
        )

    graph = {
        "schema_version": "graph_v1",
        "plan": {"plan_id": plan["plan_id"], "title": plan["title"], "root_task_id": plan["root_task_id"], "created_at": plan["created_at"]},
        "running": {"task_id": running_task_id, "since": running_since, "source": running_source},
        "nodes": nodes,
        "edges": edges,
        "ts": utc_now_iso(),
        "paths": {
            "inputs_dir": str(config.INPUTS_DIR),
            "baseline_inputs_dir": str(config.BASELINE_INPUTS_DIR),
            "deliverables_dir": str(config.DELIVERABLES_DIR),
            "db_path": str(config.DB_PATH_DEFAULT),
        },
    }
    return GraphQueryResult(graph=graph, plan_id=plan_id)
