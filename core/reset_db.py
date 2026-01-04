from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List


def _remove_file_best_effort(path: Path) -> bool:
    try:
        if path.exists():
            os.remove(str(path))
        return True
    except FileNotFoundError:
        return True
    except Exception:
        return False


def _wipe_db_tables(db_path: Path, *, retries: int = 6, sleep_s: float = 0.15) -> Dict[str, Any]:
    """
    Wipe all user data tables in-place (keeps schema + schema_migrations).

    This is a fallback for Windows when the DB file cannot be deleted due to an open handle
    (e.g., another process has a connection).
    """
    last_err: str | None = None
    for _ in range(max(1, int(retries))):
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("PRAGMA busy_timeout = 2000")
            conn.execute("PRAGMA foreign_keys = OFF")
            # Acquire a write lock early so we fail fast on contention.
            conn.execute("BEGIN IMMEDIATE")
            tables = [
                r[0]
                for r in conn.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type='table'
                      AND name NOT LIKE 'sqlite_%'
                      AND name != 'schema_migrations'
                    ORDER BY name
                    """
                ).fetchall()
            ]
            deleted_rows: Dict[str, int] = {}
            for t in tables:
                cur = conn.execute(f"DELETE FROM {t}")
                deleted_rows[t] = int(cur.rowcount or 0)
            conn.commit()
            return {"mode": "wiped", "tables": tables, "deleted_rows": deleted_rows}
        except sqlite3.OperationalError as exc:
            msg = str(exc)
            last_err = f"{type(exc).__name__}: {msg}"
            try:
                conn.rollback()
            except Exception:
                pass
            if "locked" in msg.lower() or "busy" in msg.lower():
                time.sleep(float(sleep_s))
                continue
            return {"mode": "wipe_failed", "error": last_err}
        except Exception as exc:  # noqa: BLE001
            last_err = f"{type(exc).__name__}: {exc}"
            try:
                conn.rollback()
            except Exception:
                pass
            return {"mode": "wipe_failed", "error": last_err}
        finally:
            try:
                conn.close()
            except Exception:
                pass
    return {"mode": "wipe_failed", "error": last_err or "database is locked"}


def reset_db_file_or_wipe(db_path: Path, *, retries: int = 12, sleep_s: float = 0.15) -> Dict[str, Any]:
    """
    Try to delete the sqlite DB file (+ WAL/SHM). If deletion fails (common on Windows when another
    process holds an open connection), fall back to wiping all user tables in-place.
    """
    db_path = Path(db_path)
    wal_path = Path(str(db_path) + "-wal")
    shm_path = Path(str(db_path) + "-shm")

    deleted_files: List[str] = []
    last_err: str | None = None

    for i in range(max(1, int(retries))):
        ok = True
        for p in (wal_path, shm_path, db_path):
            try:
                if p.exists():
                    os.remove(str(p))
                    deleted_files.append(str(p))
            except Exception as exc:  # noqa: BLE001
                ok = False
                last_err = f"{type(exc).__name__}: {exc}"
        if ok:
            return {"mode": "deleted", "deleted_files": deleted_files}
        time.sleep(float(sleep_s))

    if not db_path.exists():
        # Another process may have deleted it between retries.
        return {"mode": "deleted", "deleted_files": deleted_files}

    wipe = _wipe_db_tables(db_path, retries=max(2, int(retries)), sleep_s=float(sleep_s))
    # After wiping, best-effort remove WAL/SHM (they may appear again).
    for p in (wal_path, shm_path):
        if _remove_file_best_effort(p):
            if p.exists() is False:
                deleted_files.append(str(p))
    wipe["deleted_files"] = deleted_files
    if last_err:
        wipe["delete_error"] = last_err
    return wipe
