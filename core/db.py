from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from core.util import ensure_dir


def connect(db_path: Path) -> sqlite3.Connection:
    ensure_dir(db_path.parent)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn


def apply_migrations(conn: sqlite3.Connection, migrations_dir: Path) -> None:
    ensure_dir(migrations_dir)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          filename TEXT PRIMARY KEY,
          applied_at TEXT NOT NULL
        )
        """
    )
    conn.commit()

    migration_files = sorted(p for p in migrations_dir.iterdir() if p.is_file() and p.suffix.lower() == ".sql")
    for path in migration_files:
        filename = path.name
        row = conn.execute("SELECT 1 FROM schema_migrations WHERE filename = ?", (filename,)).fetchone()
        if row:
            continue
        sql = path.read_text(encoding="utf-8")
        try:
            conn.executescript(sql)
        except sqlite3.OperationalError as exc:
            # Some migrations use ALTER TABLE ADD COLUMN without IF NOT EXISTS. If a user's DB already
            # contains the column (e.g., from a previous manual run or a renamed migration), treat
            # known "already applied" errors as success and record the migration as applied.
            msg = str(exc).lower()
            safe_idempotent = (
                "duplicate column name" in msg
                or "duplicate index" in msg
                or "already exists" in msg
            )
            if not safe_idempotent:
                raise
        conn.execute("INSERT OR IGNORE INTO schema_migrations(filename, applied_at) VALUES(?, datetime('now'))", (filename,))
        conn.commit()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def scalar(conn: sqlite3.Connection, query: str, params: tuple = ()) -> Optional[object]:
    cur = conn.execute(query, params)
    row = cur.fetchone()
    if not row:
        return None
    return row[0]
