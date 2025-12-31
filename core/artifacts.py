from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Optional

from core.events import emit_event
from core.util import ensure_dir, sha256_file, utc_now_iso


def write_artifact_file(base_dir: Path, *, task_id: str, name: str, fmt: str, content: str) -> Path:
    task_dir = base_dir / task_id
    ensure_dir(task_dir)
    safe_fmt = fmt.lower().lstrip(".")
    safe_name = name.replace("/", "_").replace("\\", "_").strip() or "artifact"
    path = task_dir / f"{safe_name}.{safe_fmt}"
    path.write_text(content, encoding="utf-8")
    return path


def insert_artifact_and_activate(
    conn: sqlite3.Connection,
    *,
    plan_id: str,
    task_id: str,
    name: str,
    fmt: str,
    path: Path,
) -> str:
    artifact_id = str(uuid.uuid4())
    sha = sha256_file(path)
    now = utc_now_iso()
    conn.execute(
        """
        INSERT INTO artifacts(artifact_id, task_id, name, path, format, version, sha256, created_at)
        VALUES(?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (artifact_id, task_id, name, str(path), fmt, sha, now),
    )
    conn.execute(
        "UPDATE task_nodes SET active_artifact_id = ?, updated_at = ? WHERE task_id = ?",
        (artifact_id, now, task_id),
    )
    emit_event(
        conn,
        plan_id=plan_id,
        task_id=task_id,
        event_type="ARTIFACT_CREATED",
        payload={"artifact_id": artifact_id, "path": str(path), "sha256": sha, "name": name, "format": fmt},
    )
    return artifact_id


def insert_artifact(
    conn: sqlite3.Connection,
    *,
    plan_id: str,
    task_id: str,
    name: str,
    fmt: str,
    path: Path,
) -> str:
    artifact_id = str(uuid.uuid4())
    sha = sha256_file(path)
    now = utc_now_iso()
    conn.execute(
        """
        INSERT INTO artifacts(artifact_id, task_id, name, path, format, version, sha256, created_at)
        VALUES(?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (artifact_id, task_id, name, str(path), fmt, sha, now),
    )
    emit_event(
        conn,
        plan_id=plan_id,
        task_id=task_id,
        event_type="ARTIFACT_CREATED",
        payload={"artifact_id": artifact_id, "path": str(path), "sha256": sha, "name": name, "format": fmt, "source": "SKILL"},
    )
    return artifact_id


def load_active_artifact_path(conn: sqlite3.Connection, task_id: str) -> Optional[Path]:
    row = conn.execute(
        """
        SELECT a.path
        FROM task_nodes n
        JOIN artifacts a ON a.artifact_id = n.active_artifact_id
        WHERE n.task_id = ?
        """,
        (task_id,),
    ).fetchone()
    if not row:
        return None
    return Path(row["path"])
