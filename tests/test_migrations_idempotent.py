import sqlite3
from pathlib import Path

import config
from core.db import apply_migrations, connect


def test_apply_migrations_tolerates_duplicate_column_when_schema_migrations_missing(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    conn = connect(db_path)
    apply_migrations(conn, config.MIGRATIONS_DIR)

    # Simulate a "drift" scenario: column exists but schema_migrations row is missing
    # (e.g., file renamed or schema_migrations table was edited).
    conn.execute("DELETE FROM schema_migrations WHERE filename = '011_m6_llm_calls_truncation.sql'")
    conn.commit()

    # Ensure the column exists already.
    cols = [c[1] for c in conn.execute("PRAGMA table_info(llm_calls)").fetchall()]
    assert "prompt_truncated" in cols

    # Re-applying migrations should NOT crash with "duplicate column name".
    apply_migrations(conn, config.MIGRATIONS_DIR)

    row = conn.execute("SELECT 1 FROM schema_migrations WHERE filename = '011_m6_llm_calls_truncation.sql'").fetchone()
    assert row is not None

    conn.close()

