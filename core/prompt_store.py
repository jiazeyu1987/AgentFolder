from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Optional, Tuple

from core.util import utc_now_iso


def get_or_create_prompt_version(
    conn: sqlite3.Connection,
    *,
    kind: str,
    name: str,
    agent: Optional[str],
    path: Path,
    sha256: str,
) -> Tuple[str, int]:
    row = conn.execute(
        """
        SELECT prompt_id, version
        FROM prompts
        WHERE kind = ? AND name = ? AND agent IS ? AND sha256 = ?
        """,
        (kind, name, agent, sha256),
    ).fetchone()
    if row:
        return row["prompt_id"], int(row["version"])

    max_row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) AS v FROM prompts WHERE kind = ? AND name = ? AND agent IS ?",
        (kind, name, agent),
    ).fetchone()
    next_version = int(max_row["v"] or 0) + 1
    prompt_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO prompts(prompt_id, kind, name, agent, version, path, sha256, created_at)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (prompt_id, kind, name, agent, next_version, str(path), sha256, utc_now_iso()),
    )
    return prompt_id, next_version
