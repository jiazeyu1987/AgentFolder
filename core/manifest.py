from __future__ import annotations

"""
Backward-compatible wrapper for deliverables `manifest.json`.

Phase D rule: manifest schema + writing logic must live in ONE place only:
`core.deliverables.write_manifest_json`.

This module may only:
- call the single writer
- read the on-disk manifest for tooling/UI
It must not assemble SQL queries or define a second schema.
"""

import json
import sqlite3
from pathlib import Path
from typing import Dict, Optional

from core.deliverables import write_manifest_json as deliverables_write_manifest_json
from core.deliverables_paths import deliverables_root


def write_manifest_json(conn: sqlite3.Connection, *, plan_id: str, include_candidates: bool = True) -> Path:
    """
    Backward-compatible wrapper: manifest writing is centralized in `core.deliverables.write_manifest_json`.
    """
    # Runtime manifest: include all ACTION nodes (not just DONE) so UI/debug can show current artifacts.
    return deliverables_write_manifest_json(
        conn,
        plan_id=str(plan_id),
        include_candidates=bool(include_candidates),
        out_dir=deliverables_root(plan_id),
        done_only=False,
    )


def read_manifest_json(plan_dir: Path) -> Optional[Dict[str, Any]]:
    p = plan_dir / "manifest.json"
    if not p.exists():
        return None
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


__all__ = [
    "write_manifest_json",
    "read_manifest_json",
]
