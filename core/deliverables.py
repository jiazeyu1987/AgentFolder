from __future__ import annotations

import json
import re
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.util import ensure_dir, stable_hash_text, utc_now_iso
from core.final_picker import FinalDeliverableError, pick_final_deliverable
from core.events import emit_event
from core.deliverables_paths import deliverables_root, rel_to_deliverables, task_slug
from core.ssot.types import DELIVERABLES_FINAL_SCHEMA_VERSION, DELIVERABLES_MANIFEST_SCHEMA_VERSION


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


def write_manifest_json(
    conn: sqlite3.Connection,
    *,
    plan_id: str,
    include_candidates: bool = True,
    out_dir: Optional[Path] = None,
    done_only: bool = False,
) -> Path:
    """
    Single writer for deliverables `manifest.json`.

    Used in two contexts:
    - Runtime (plan deliverables folder): done_only=False to expose current artifacts for UI/debug/cleanup.
    - Export: done_only=True to list only DONE ACTION deliverables.
    """
    out_dir = out_dir or deliverables_root(plan_id)
    ensure_dir(out_dir)

    plan = conn.execute("SELECT plan_id, title, root_task_id, created_at FROM plans WHERE plan_id=?", (plan_id,)).fetchone()
    plan_meta = {
        "plan_id": str(plan["plan_id"]) if plan else str(plan_id),
        "title": str(plan["title"] if plan else ""),
        "root_task_id": str(plan["root_task_id"] if plan else ""),
        "created_at": str(plan["created_at"] if plan else ""),
        "updated_at": utc_now_iso(),
    }

    status_filter = "AND n.status = 'DONE'" if bool(done_only) else ""
    rows = conn.execute(
        f"""
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
          {status_filter}
          AND a.artifact_id IS NOT NULL
        ORDER BY a.created_at ASC
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
                "approved": bool(r["approved_artifact_id"]) and str(r["approved_artifact_id"]) == artifact_id,
                "artifact": {
                    "artifact_id": artifact_id,
                    "name": str(r["artifact_name"] or ""),
                    "format": str(r["artifact_format"] or ""),
                    "sha256": str(r["artifact_sha256"] or ""),
                    "created_at": str(r["artifact_created_at"] or ""),
                    "source_path": str(p),
                    "dest_path": rel_to_deliverables(str(plan_id), p),
                },
            }
        )

    manifest: Dict[str, Any] = {
        "schema_version": DELIVERABLES_MANIFEST_SCHEMA_VERSION,
        "plan": plan_meta,
        "files": files,
        "bundle_mode": "SINGLE" if len(files) <= 1 else "MANIFEST",
        "entrypoint": "",
        "final_candidates": [],
    }
    out_path = Path(out_dir) / "manifest.json"
    out_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def export_deliverables(
    conn: sqlite3.Connection,
    *,
    plan_id: str,
    out_dir: Path,
    include_reviews: bool = False,
    include_candidates: bool = False,
    job_id: Optional[str] = None,
) -> ExportResult:
    """
    Collect "final deliverables" for a plan into a single folder for easy handoff.

    Default behavior:
    - v2: Copy approved artifacts from DONE ACTION tasks.
    - v1 compatibility: If include_candidates=True, falls back to active artifacts when approved is missing.
    - Write a manifest.json with metadata and file mapping.
    - Write plan_meta.json (title/root/created_at).
    - Write final.json pointing to a single entrypoint deliverable.
    """
    ensure_dir(out_dir)
    # All deliverables are flattened directly under `out_dir` (no subfolders).

    plan = conn.execute("SELECT plan_id, title, root_task_id, created_at FROM plans WHERE plan_id=?", (plan_id,)).fetchone()
    if not plan:
        raise RuntimeError(f"plan not found: {plan_id}")
    # Event-first: start export step (best-effort; never break export).
    try:
        from core.workflow_events import emit_workflow_event

        emit_workflow_event(
            conn,
            workflow="EXPORT",
            event_type="STEP_STARTED",
            severity="INFO",
            message="EXPORT started",
            job_id=job_id,
            plan_id=str(plan_id),
            payload={"step": "EXPORT", "out_dir": str(out_dir)},
        )
    except Exception:
        pass

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
          AND n.status = 'DONE'
          AND a.artifact_id IS NOT NULL
        ORDER BY a.created_at ASC
        """,
        (1 if include_candidates else 0, plan_id),
    ).fetchall()

    manifest: Dict[str, Any] = {
        "schema_version": DELIVERABLES_MANIFEST_SCHEMA_VERSION,
        "plan": plan_meta,
        "files": [],
        "bundle_mode": "MANIFEST",
        "entrypoint": "",
        "final_candidates": [],
    }
    files_copied = 0

    for t in tasks:
        src = Path(str(t["artifact_path"] or ""))
        if not src.exists():
            continue
        from core.deliverables_paths import artifact_output_filename

        task_title = str(t["title"] or "task")
        task_id = str(t["task_id"] or "")
        artifact_id = str(t["artifact_id"] or "")
        artifact_name = str(t["artifact_name"] or src.stem or "artifact")
        artifact_fmt = str(t["artifact_format"] or src.suffix.lstrip(".") or "txt")

        dest_name = artifact_output_filename(
            task_title=task_title,
            task_id=task_id or "task",
            name=artifact_name,
            fmt=artifact_fmt,
            artifact_id=artifact_id or None,
        )
        dest = out_dir / dest_name

        if src.resolve() != dest.resolve():
            if dest.exists():
                dest = out_dir / f"{Path(dest_name).stem}_{artifact_id[:8] or '00000000'}{Path(dest_name).suffix}"
            shutil.copy2(str(src), str(dest))
            files_copied += 1

        manifest["files"].append(
            {
                "task_id": t["task_id"],
                "task_title": t["title"],
                "node_type": t["node_type"],
                "status": t["status"],
                "owner_agent_id": t["owner_agent_id"],
                "tags_json": t["tags_json"],
                "approved": bool(t["approved_artifact_id"]) and str(t["approved_artifact_id"]) == str(t["artifact_id"]),
                "artifact": {
                    "artifact_id": t["artifact_id"],
                    "name": t["artifact_name"],
                    "format": t["artifact_format"],
                    "sha256": t["artifact_sha256"],
                    "created_at": t["artifact_created_at"],
                    "source_path": str(src),
                    "dest_path": rel_to_deliverables(str(plan_id), dest),
                },
            }
        )

        if include_reviews:
            # Copy any review files under workspace/reviews/<task_id>/ (if present).
            try:
                slug = task_slug(task_title, task_id=task_id)
                workspace_reviews = Path("workspace") / "reviews" / task_id
                if workspace_reviews.exists():
                    for f in sorted(workspace_reviews.glob("review_*.json")):
                        dst = out_dir / f"review__{slug}__{_safe_name(f.name, max_len=80)}"
                        if dst.exists():
                            dst = out_dir / f"review__{slug}__{_safe_name(f.stem, max_len=70)}_{stable_hash_text(str(f))[:6]}{f.suffix}"
                        shutil.copy2(str(f), str(dst))
            except Exception:
                pass

    # final.json: single-entrypoint deliverable pointer
    try:
        picked = pick_final_deliverable(conn, plan_id=plan_id, include_candidates=include_candidates)
    except FinalDeliverableError as exc:
        raise RuntimeError(str(exc)) from exc

    # Locate exported file path for the picked artifact_id
    final_entrypoint = ""
    final_task_title = picked.get("task_title") or ""
    final_artifact_id = picked.get("artifact_id") or ""
    for f in manifest["files"]:
        art = (f or {}).get("artifact") or {}
        if str(art.get("artifact_id") or "") == str(final_artifact_id):
            final_entrypoint = str(art.get("dest_path") or "")
            break
    if not final_entrypoint:
        # Should not happen if export copied it; fallback to source basename.
        final_entrypoint = str(Path(str(picked.get("source_path") or "")).name)
    manifest["entrypoint"] = final_entrypoint

    # root acceptance criteria (optional)
    acceptance: List[Dict[str, Any]] = []
    try:
        root_row = conn.execute("SELECT root_task_id FROM plans WHERE plan_id = ?", (plan_id,)).fetchone()
        if root_row:
            root = conn.execute(
                "SELECT root_acceptance_criteria_json FROM task_nodes WHERE task_id = ?",
                (str(root_row["root_task_id"]),),
            ).fetchone()
            if root and root["root_acceptance_criteria_json"]:
                arr = json.loads(root["root_acceptance_criteria_json"])
                if isinstance(arr, list):
                    acceptance = [x for x in arr if isinstance(x, dict)]
    except Exception:
        acceptance = []

    # Trace (best-effort): last review per exported action.
    trace: List[Dict[str, Any]] = []
    for f in manifest["files"]:
        tid = str(f.get("task_id") or "")
        if not tid:
            continue
        ptr = conn.execute("SELECT approved_artifact_id FROM task_nodes WHERE task_id = ?", (tid,)).fetchone()
        approved_id = str((ptr["approved_artifact_id"] if ptr else "") or "")
        latest = conn.execute(
            """
            SELECT reviewed_artifact_id, verdict, created_at
            FROM reviews
            WHERE review_target_task_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (tid,),
        ).fetchone()
        trace.append(
            {
                "task_title": str(f.get("task_title") or ""),
                "approved_artifact_id": approved_id,
                "reviewed_artifact_id": str(latest["reviewed_artifact_id"] or "") if latest else "",
                "latest_verdict": str(latest["verdict"] or "") if latest else "",
                "review_created_at": str(latest["created_at"] or "") if latest else "",
            }
        )

    how_to_run: List[str] = []
    fmt = str(picked.get("format") or "").lower()
    if fmt == "html":
        how_to_run = [f"Open `{final_entrypoint}` in a browser (double click)."]
    else:
        how_to_run = [f"Open `{final_entrypoint}` and follow its instructions."]

    final_json = {
        "schema_version": DELIVERABLES_FINAL_SCHEMA_VERSION,
        "final_entrypoint": final_entrypoint,
        "final_task_title": final_task_title,
        "final_artifact_id": final_artifact_id,
        "how_to_run": how_to_run,
        "acceptance_criteria": acceptance,
        "trace": trace,
        "reasoning": picked.get("reasoning") or [],
    }
    (out_dir / "final.json").write_text(json.dumps(final_json, ensure_ascii=False, indent=2), encoding="utf-8")

    # Update manifest with entrypoint/bundle_mode/final_candidates, then write again.
    manifest["bundle_mode"] = "SINGLE" if len(manifest["files"]) <= 1 else "MANIFEST"
    manifest["final_candidates"] = [
        {
            "task_title": str(f.get("task_title") or ""),
            "artifact_id": str(((f.get("artifact") or {}).get("artifact_id")) or ""),
            "format": str(((f.get("artifact") or {}).get("format")) or ""),
        }
        for f in (manifest["files"] or [])[:10]
    ]
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    # Observability: record export completion (best-effort; do not fail export on telemetry).
    try:
        emit_event(
            conn,
            plan_id=plan_id,
            task_id=None,
            event_type="EXPORT_DONE",
            payload={
                "out_dir": str(out_dir),
                "files_copied": int(files_copied),
                "final_entrypoint": str(final_json.get("final_entrypoint") or ""),
                "final_artifact_id": str(final_json.get("final_artifact_id") or ""),
            },
        )
    except Exception:
        pass
    try:
        from core.workflow_events import emit_workflow_event

        emit_workflow_event(
            conn,
            workflow="EXPORT",
            event_type="EXPORT_DONE",
            severity="INFO",
            message="export done",
            job_id=job_id,
            plan_id=str(plan_id),
            payload={
                "step": "EXPORT",
                "out_dir": str(out_dir),
                "files_copied": int(files_copied),
                "final_entrypoint": str(final_json.get("final_entrypoint") or ""),
                "final_artifact_id": str(final_json.get("final_artifact_id") or ""),
            },
        )
        emit_workflow_event(
            conn,
            workflow="EXPORT",
            event_type="STEP_FINISHED",
            severity="INFO",
            message="EXPORT finished",
            job_id=job_id,
            plan_id=str(plan_id),
            payload={"step": "EXPORT", "ok": True},
        )
    except Exception:
        pass

    return ExportResult(plan_id=plan_id, out_dir=out_dir, files_copied=files_copied)
