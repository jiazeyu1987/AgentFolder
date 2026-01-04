from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.deliverables_paths import deliverables_root, rel_to_deliverables
from core.util import ensure_dir, utc_now_iso


def write_manifest_json(conn: sqlite3.Connection, *, plan_id: str, include_candidates: bool = True) -> Path:
    """
    Write `workspace/deliverables/<plan_id>/manifest.json`.

    Schema aligns with `core.deliverables.export_deliverables` (kept stable for cleanup/UI):
    { plan: {...}, files: [{task_id,...,artifact:{artifact_id,...,dest_path}}], bundle_mode, entrypoint }
    """
    root = deliverables_root(plan_id)
    ensure_dir(root)

    plan = conn.execute("SELECT plan_id, title, root_task_id, created_at FROM plans WHERE plan_id=?", (plan_id,)).fetchone()
    plan_meta = {
        "plan_id": str(plan["plan_id"]) if plan else str(plan_id),
        "title": str(plan["title"] if plan else ""),
        "root_task_id": str(plan["root_task_id"] if plan else ""),
        "created_at": str(plan["created_at"] if plan else ""),
        "updated_at": utc_now_iso(),
    }

    rows = conn.execute(
        """
        SELECT
          n.task_id,
          n.title,
          n.node_type,
          n.status,
          n.owner_agent_id,
          n.tags_json,
          n.approved_artifact_id,
          n.active_artifact_id,
          a.artifact_id,
          a.name AS artifact_name,
          a.format AS artifact_format,
          a.path AS artifact_path,
          a.sha256 AS artifact_sha256,
          a.created_at AS artifact_created_at
        FROM task_nodes n
        LEFT JOIN artifacts a ON a.artifact_id = (
          CASE
            WHEN n.approved_artifact_id IS NOT NULL THEN n.approved_artifact_id
            WHEN ? THEN n.active_artifact_id
            ELSE NULL
          END
        )
        WHERE n.plan_id = ?
          AND n.active_branch = 1
          AND n.node_type = 'ACTION'
        ORDER BY n.created_at ASC
        """,
        (1 if include_candidates else 0, plan_id),
    ).fetchall()

    files: List[Dict[str, Any]] = []
    for r in rows:
        artifact_id = str(r["artifact_id"] or "")
        artifact_path = str(r["artifact_path"] or "")
        if not artifact_id or not artifact_path:
            continue
        p = Path(artifact_path)
        files.append(
            {
                "task_id": str(r["task_id"] or ""),
                "task_title": str(r["title"] or ""),
                "node_type": str(r["node_type"] or ""),
                "status": str(r["status"] or ""),
                "owner_agent_id": str(r["owner_agent_id"] or ""),
                "tags_json": str(r["tags_json"] or "[]"),
                "artifact": {
                    "artifact_id": artifact_id,
                    "name": str(r["artifact_name"] or ""),
                    "format": str(r["artifact_format"] or ""),
                    "sha256": str(r["artifact_sha256"] or ""),
                    "created_at": str(r["artifact_created_at"] or ""),
                    "source_path": str(p),
                    "dest_path": rel_to_deliverables(plan_id, p),
                },
            }
        )

    manifest: Dict[str, Any] = {
        "schema_version": "deliverables_manifest_v1",
        "plan": plan_meta,
        "files": files,
        "bundle_mode": "SINGLE" if len(files) <= 1 else "MANIFEST",
        "entrypoint": "",
        "final_candidates": [],
    }
    out_path = root / "manifest.json"
    out_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def read_manifest_json(plan_dir: Path) -> Optional[Dict[str, Any]]:
    p = plan_dir / "manifest.json"
    if not p.exists():
        return None
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None
