from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import config


@dataclass(frozen=True)
class CleanupPlan:
    llm_calls_delete: int
    task_events_delete: int
    audit_events_delete: int
    reviews_delete: int
    artifacts_delete: int
    artifact_files_delete: int
    kept_artifact_ids: List[str]


def _read_json_file(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _gather_deliverables_artifact_ids(deliverables_dir: Path, *, max_files: int = 2000) -> Set[str]:
    keep: Set[str] = set()
    if not deliverables_dir.exists():
        return keep
    # Only look for final.json/manifest.json under plan subfolders.
    n = 0
    for p in deliverables_dir.rglob("final.json"):
        n += 1
        if n > max_files:
            break
        obj = _read_json_file(p)
        if not obj:
            continue
        v = obj.get("final_artifact_id")
        if isinstance(v, str) and v.strip():
            keep.add(v.strip())
    for p in deliverables_dir.rglob("manifest.json"):
        n += 1
        if n > max_files:
            break
        obj = _read_json_file(p)
        if not obj:
            continue
        files = obj.get("files") or []
        if isinstance(files, list):
            for f in files:
                if not isinstance(f, dict):
                    continue
                art = f.get("artifact")
                if isinstance(art, dict):
                    aid = art.get("artifact_id")
                    if isinstance(aid, str) and aid.strip():
                        keep.add(aid.strip())
    return keep


def _artifact_ids_referenced_by_reviews(conn: sqlite3.Connection) -> Set[str]:
    keep: Set[str] = set()
    cols = [c[1] for c in conn.execute("PRAGMA table_info(reviews)").fetchall()]
    if "reviewed_artifact_id" in cols:
        rows = conn.execute("SELECT reviewed_artifact_id FROM reviews WHERE reviewed_artifact_id IS NOT NULL").fetchall()
        for r in rows:
            v = r["reviewed_artifact_id"]
            if isinstance(v, str) and v.strip():
                keep.add(v.strip())
    return keep


def _artifact_ids_from_task_nodes(conn: sqlite3.Connection) -> Set[str]:
    keep: Set[str] = set()
    rows = conn.execute(
        """
        SELECT approved_artifact_id, active_artifact_id
        FROM task_nodes
        WHERE approved_artifact_id IS NOT NULL OR active_artifact_id IS NOT NULL
        """
    ).fetchall()
    for r in rows:
        for k in ("approved_artifact_id", "active_artifact_id"):
            v = r[k]
            if isinstance(v, str) and v.strip():
                keep.add(v.strip())
    return keep


def compute_keeper_artifact_ids(
    conn: sqlite3.Connection,
    *,
    extra_keep: Optional[Iterable[str]] = None,
    deliverables_dir: Path = config.DELIVERABLES_DIR,
) -> Set[str]:
    keep = set(str(x).strip() for x in (extra_keep or []) if str(x).strip())
    keep |= _artifact_ids_from_task_nodes(conn)
    keep |= _artifact_ids_referenced_by_reviews(conn)
    keep |= _gather_deliverables_artifact_ids(deliverables_dir)
    return keep


def trim_llm_calls(conn: sqlite3.Connection, *, max_rows: int, dry_run: bool) -> int:
    cur = conn.execute("SELECT COUNT(1) AS cnt FROM llm_calls").fetchone()
    total = int(cur["cnt"] or 0) if cur else 0
    if total <= max_rows:
        return 0
    to_delete = total - max_rows
    if dry_run:
        return to_delete
    conn.execute(
        """
        DELETE FROM llm_calls
        WHERE rowid NOT IN (
          SELECT rowid FROM llm_calls ORDER BY created_at DESC LIMIT ?
        )
        """,
        (int(max_rows),),
    )
    return to_delete


def trim_task_events(conn: sqlite3.Connection, *, max_rows: int, dry_run: bool) -> int:
    cur = conn.execute("SELECT COUNT(1) AS cnt FROM task_events").fetchone()
    total = int(cur["cnt"] or 0) if cur else 0
    if total <= max_rows:
        return 0
    to_delete = total - max_rows
    if dry_run:
        return to_delete
    conn.execute(
        """
        DELETE FROM task_events
        WHERE rowid NOT IN (
          SELECT rowid FROM task_events ORDER BY created_at DESC LIMIT ?
        )
        """,
        (int(max_rows),),
    )
    return to_delete


def trim_audit_events(conn: sqlite3.Connection, *, max_rows: int, dry_run: bool) -> int:
    cols = [c[1] for c in conn.execute("PRAGMA table_info(audit_events)").fetchall()]
    if not cols:
        return 0
    cur = conn.execute("SELECT COUNT(1) AS cnt FROM audit_events").fetchone()
    total = int(cur["cnt"] or 0) if cur else 0
    if total <= max_rows:
        return 0
    to_delete = total - max_rows
    if dry_run:
        return to_delete
    conn.execute(
        """
        DELETE FROM audit_events
        WHERE rowid NOT IN (
          SELECT rowid FROM audit_events ORDER BY created_at DESC LIMIT ?
        )
        """,
        (int(max_rows),),
    )
    return to_delete


def trim_reviews(conn: sqlite3.Connection, *, max_versions_per_check: int, keep_latest_verdict: bool, dry_run: bool) -> int:
    cols = [c[1] for c in conn.execute("PRAGMA table_info(reviews)").fetchall()]
    group_col = "check_task_id" if "check_task_id" in cols else "task_id"
    verdict_col = "verdict" if "verdict" in cols else None

    groups = conn.execute(f"SELECT {group_col} AS gid, COUNT(1) AS cnt FROM reviews GROUP BY {group_col}").fetchall()
    delete_ids: List[str] = []

    for g in groups:
        gid = g["gid"]
        cnt = int(g["cnt"] or 0)
        if gid is None or cnt <= max_versions_per_check:
            continue
        rows = conn.execute(
            f"SELECT review_id, created_at{', verdict' if verdict_col else ''} FROM reviews WHERE {group_col}=? ORDER BY created_at DESC",
            (gid,),
        ).fetchall()
        keep_ids: Set[str] = set()
        for r in rows[:max_versions_per_check]:
            keep_ids.add(str(r["review_id"]))
        if keep_latest_verdict and verdict_col:
            # Also keep the latest APPROVED if it exists.
            for r in rows:
                if str(r.get("verdict") or "") == "APPROVED":
                    keep_ids.add(str(r["review_id"]))
                    break
        for r in rows:
            rid = str(r["review_id"])
            if rid not in keep_ids:
                delete_ids.append(rid)

    if not delete_ids:
        return 0
    if dry_run:
        return len(delete_ids)
    conn.executemany("DELETE FROM reviews WHERE review_id = ?", [(rid,) for rid in delete_ids])
    return len(delete_ids)


def trim_artifacts(
    conn: sqlite3.Connection,
    *,
    max_versions_per_task: int,
    keep_artifact_ids: Set[str],
    dry_run: bool,
) -> Tuple[int, int]:
    """
    Returns (artifacts_deleted, files_deleted).
    """
    groups = conn.execute("SELECT task_id, COUNT(1) AS cnt FROM artifacts GROUP BY task_id").fetchall()
    delete_artifact_ids: List[str] = []

    # Track how many artifacts share the same path to avoid deleting a shared file.
    path_counts: Dict[str, int] = {}
    for r in conn.execute("SELECT path, COUNT(1) AS cnt FROM artifacts GROUP BY path").fetchall():
        p = str(r["path"] or "")
        if p:
            path_counts[p] = int(r["cnt"] or 0)

    for g in groups:
        task_id = str(g["task_id"])
        cnt = int(g["cnt"] or 0)
        if cnt <= max_versions_per_task:
            continue
        rows = conn.execute(
            """
            SELECT artifact_id, created_at
            FROM artifacts
            WHERE task_id = ?
            ORDER BY created_at DESC
            """,
            (task_id,),
        ).fetchall()
        keep_ids: Set[str] = set()
        for r in rows[:max_versions_per_task]:
            keep_ids.add(str(r["artifact_id"]))
        keep_ids |= {aid for aid in keep_artifact_ids if aid}

        for r in rows:
            aid = str(r["artifact_id"])
            if aid not in keep_ids:
                delete_artifact_ids.append(aid)

    if not delete_artifact_ids:
        return 0, 0

    files_to_delete: List[Path] = []
    if not dry_run:
        rows = conn.execute(
            f"SELECT artifact_id, path FROM artifacts WHERE artifact_id IN ({','.join(['?']*len(delete_artifact_ids))})",
            tuple(delete_artifact_ids),
        ).fetchall()
        for r in rows:
            p = str(r["path"] or "")
            if not p:
                continue
            if path_counts.get(p, 0) > 1:
                continue
            files_to_delete.append(Path(p))

    if dry_run:
        return len(delete_artifact_ids), 0

    conn.executemany("DELETE FROM artifacts WHERE artifact_id = ?", [(aid,) for aid in delete_artifact_ids])

    deleted_files = 0
    for p in files_to_delete:
        try:
            if p.exists() and p.is_file():
                p.unlink()
                deleted_files += 1
        except Exception:
            # best-effort file cleanup
            pass
    return len(delete_artifact_ids), deleted_files


def plan_cleanup(
    conn: sqlite3.Connection,
    *,
    max_llm_calls_rows: int,
    max_task_events_rows: int,
    max_artifact_versions_per_task: int,
    max_review_versions_per_check: int,
    dry_run: bool,
    extra_keep_artifact_ids: Optional[Iterable[str]] = None,
    deliverables_dir: Path = config.DELIVERABLES_DIR,
) -> CleanupPlan:
    keep_ids = compute_keeper_artifact_ids(conn, extra_keep=extra_keep_artifact_ids, deliverables_dir=deliverables_dir)
    llm_del = trim_llm_calls(conn, max_rows=max_llm_calls_rows, dry_run=dry_run)
    evt_del = trim_task_events(conn, max_rows=max_task_events_rows, dry_run=dry_run)
    aud_del = trim_audit_events(conn, max_rows=max_task_events_rows, dry_run=dry_run)
    rev_del = trim_reviews(conn, max_versions_per_check=max_review_versions_per_check, keep_latest_verdict=True, dry_run=dry_run)
    art_del, file_del = trim_artifacts(
        conn,
        max_versions_per_task=max_artifact_versions_per_task,
        keep_artifact_ids=keep_ids,
        dry_run=dry_run,
    )
    return CleanupPlan(
        llm_calls_delete=int(llm_del),
        task_events_delete=int(evt_del),
        audit_events_delete=int(aud_del),
        reviews_delete=int(rev_del),
        artifacts_delete=int(art_del),
        artifact_files_delete=int(file_del),
        kept_artifact_ids=sorted(list(keep_ids))[:200],
    )
