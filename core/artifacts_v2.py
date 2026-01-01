from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional

from core.util import utc_now_iso


@dataclass(frozen=True)
class ApprovedArtifactError(RuntimeError):
    message: str

    def __str__(self) -> str:  # pragma: no cover
        return self.message


def set_approved_artifact(conn: sqlite3.Connection, *, task_id: str, artifact_id: str) -> None:
    """
    P1.2: set the "latest approved" artifact pointer for a task.

    This is a small API so all writers use a single consistent update.
    """
    if not isinstance(task_id, str) or not task_id.strip():
        raise ApprovedArtifactError("task_id is required")
    if not isinstance(artifact_id, str) or not artifact_id.strip():
        raise ApprovedArtifactError("artifact_id is required")
    try:
        conn.execute(
            "UPDATE task_nodes SET approved_artifact_id = ?, updated_at = ? WHERE task_id = ?",
            (artifact_id, utc_now_iso(), task_id),
        )
    except sqlite3.OperationalError as exc:
        raise ApprovedArtifactError(f"approved_artifact_id column not found (migrations not applied?): {exc}") from exc


def get_preferred_artifact_id_row_sql() -> str:
    """
    Helper SQL fragment for "prefer approved, fallback to active".
    """
    return "COALESCE(n.approved_artifact_id, n.active_artifact_id)"

