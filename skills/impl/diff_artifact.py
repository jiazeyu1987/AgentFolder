from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any, Dict, List

from core.util import ensure_dir, safe_read_text, sha256_file


def run(*, task_id: str, plan_id: str, inputs: List[Dict[str, Any]], params: Dict[str, Any]) -> Dict[str, Any]:
    old_path = Path(str(params.get("old_path") or ""))
    new_path = Path(str(params.get("new_path") or ""))
    if not old_path.is_file() or not new_path.is_file():
        return {"status": "FAILED", "artifacts": [], "evidences": [], "error": {"code": "SKILL_BAD_INPUT", "message": "old_path/new_path must exist"}}

    old = safe_read_text(old_path, max_chars=200_000).splitlines(keepends=True)
    new = safe_read_text(new_path, max_chars=200_000).splitlines(keepends=True)
    diff = difflib.unified_diff(old, new, fromfile=str(old_path), tofile=str(new_path))
    diff_text = "".join(diff)

    out_dir = Path("workspace") / "artifacts" / task_id
    ensure_dir(out_dir)
    out_path = out_dir / "diff_summary.md"
    out_path.write_text(diff_text, encoding="utf-8")
    return {"status": "SUCCEEDED", "artifacts": [{"name": "diff_summary", "path": str(out_path), "sha256": sha256_file(out_path), "format": "md"}], "evidences": [], "error": None}

