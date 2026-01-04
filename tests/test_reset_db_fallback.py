import tempfile
from pathlib import Path

import sqlite3

import config
from core.db import apply_migrations, connect
from core.reset_db import reset_db_file_or_wipe


def test_reset_db_falls_back_to_wipe_when_db_locked(monkeypatch):
    # Use a temp db path and create some rows.
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        db_path = td_path / "state.db"
        conn = connect(db_path)
        apply_migrations(conn, config.MIGRATIONS_DIR)
        conn.execute(
            "INSERT INTO plans(plan_id, title, owner_agent_id, root_task_id, created_at, constraints_json) VALUES(?, ?, ?, ?, datetime('now'), '{}')",
            ("p1", "T", "xiaobo", "r1"),
        )
        conn.commit()

        # Hold a separate connection open to simulate Windows file lock during deletion.
        lock_conn = sqlite3.connect(str(db_path))
        try:
            res = reset_db_file_or_wipe(db_path, retries=2, sleep_s=0.01)
        finally:
            lock_conn.close()
            conn.close()

        assert res.get("mode") in {"deleted", "wiped"}

        # After reset, the DB should have no plans.
        conn2 = connect(db_path)
        apply_migrations(conn2, config.MIGRATIONS_DIR)
        try:
            n = conn2.execute("SELECT COUNT(1) FROM plans").fetchone()[0]
            assert int(n) == 0
        finally:
            conn2.close()

