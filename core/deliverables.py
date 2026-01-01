from __future__ import annotations

import json
import re
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.util import ensure_dir, utc_now_iso


_BAD_CHARS_RE = re.compile(r"[^a-zA-Z0-9\u4e00-\u9fff._ -]+")


def _safe_name(text: str, *, max_len: int = 60) -> str:
    t = (text or "").strip()
    t = _BAD_CHARS_RE.sub("_", t)
    t = t.replace(" ", "_")
    t = t.strip("._-")
    if not t:
        return "item"
    return t[:max_len]


@dataclass(frozen=True)
class ExportResult:
    plan_id: str
    out_dir: Path
    files_copied: int


def export_deliverables(
    conn: sqlite3.Connection,
    *,
    plan_id: str,
    out_dir: Path,
    include_reviews: bool = False,
) -> ExportResult:
    """
    Collect "final deliverables" for a plan into a single folder for easy handoff.

    Default behavior:
    - Copy active artifacts from DONE ACTION tasks.
    - Write a manifest.json with metadata and file mapping.
    - Write plan_meta.json (title/root/created_at).
    """
    ensure_dir(out_dir)
    artifacts_dir = out_dir / "artifacts"
    ensure_dir(artifacts_dir)
    if include_reviews:
        ensure_dir(out_dir / "reviews")

    plan = conn.execute("SELECT plan_id, title, root_task_id, created_at FROM plans WHERE plan_id=?", (plan_id,)).fetchone()
    if not plan:
        raise RuntimeError(f"plan not found: {plan_id}")

    plan_meta = {"plan_id": plan["plan_id"], "title": plan["title"], "root_task_id": plan["root_task_id"], "created_at": plan["created_at"], "exported_at": utc_now_iso()}
    (out_dir / "plan_meta.json").write_text(json.dumps(plan_meta, ensure_ascii=False, indent=2), encoding="utf-8")

    tasks = conn.execute(
        """
        SELECT
          n.task_id,
          n.title,
          n.node_type,
          n.status,
          n.owner_agent_id,
          a.artifact_id,
          a.name AS artifact_name,
          a.format AS artifact_format,
          a.path AS artifact_path,
          a.sha256 AS artifact_sha256,
          a.created_at AS artifact_created_at
        FROM task_nodes n
        LEFT JOIN artifacts a ON a.artifact_id = COALESCE(n.approved_artifact_id, n.active_artifact_id)
        WHERE n.plan_id = ?
          AND n.active_branch = 1
          AND n.node_type = 'ACTION'
          AND n.status = 'DONE'
          AND COALESCE(n.approved_artifact_id, n.active_artifact_id) IS NOT NULL
        ORDER BY a.created_at ASC
        """,
        (plan_id,),
    ).fetchall()

    manifest: Dict[str, Any] = {"plan": plan_meta, "files": []}
    files_copied = 0

    for t in tasks:
        src = Path(str(t["artifact_path"] or ""))
        if not src.exists():
            continue
        task_slug = f"{_safe_name(str(t['title'] or 'task'))}_{str(t['task_id'])[:8]}"
        dest_dir = artifacts_dir / task_slug
        ensure_dir(dest_dir)

        dest = dest_dir / src.name
        if dest.exists():
            dest = dest_dir / f"{src.stem}_{str(t['artifact_id'])[:8]}{src.suffix}"

        shutil.copy2(str(src), str(dest))
        files_copied += 1

        manifest["files"].append(
            {
                "task_id": t["task_id"],
                "task_title": t["title"],
                "node_type": t["node_type"],
                "status": t["status"],
                "owner_agent_id": t["owner_agent_id"],
                "artifact": {
                    "artifact_id": t["artifact_id"],
                    "name": t["artifact_name"],
                    "format": t["artifact_format"],
                    "sha256": t["artifact_sha256"],
                    "created_at": t["artifact_created_at"],
                    "source_path": str(src),
                    "dest_path": str(dest.relative_to(out_dir)),
                },
            }
        )

        if include_reviews:
            # Copy any review files under workspace/reviews/<task_id>/ (if present).
            review_src_dir = out_dir.parent / "reviews"  # workspace/reviews if out_dir is workspace/deliverables/...
            # If caller provides out_dir elsewhere, fallback to sibling "reviews" won't exist; try DB path instead.
            # We'll still attempt a best-effort copy from workspace/reviews based on the repository layout.
            # (No hard failure.)
            try:
                workspace_reviews = Path("workspace") / "reviews" / str(t["task_id"])
                if workspace_reviews.exists():
                    dest_reviews_dir = out_dir / "reviews" / task_slug
                    ensure_dir(dest_reviews_dir)
                    for f in sorted(workspace_reviews.glob("review_*.json")):
                        shutil.copy2(str(f), str(dest_reviews_dir / f.name))
            except Exception:
                pass

    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return ExportResult(plan_id=plan_id, out_dir=out_dir, files_copied=files_copied)
