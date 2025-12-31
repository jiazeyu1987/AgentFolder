from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from core.util import sha256_file


def run(*, task_id: str, plan_id: str, inputs: List[Dict[str, Any]], params: Dict[str, Any]) -> Dict[str, Any]:
    artifacts = []
    evidences = []
    for inp in inputs:
        path = Path(str(inp.get("path") or ""))
        if not path.is_file():
            return {"status": "FAILED", "artifacts": [], "evidences": [], "error": {"code": "SKILL_BAD_INPUT", "message": f"not a file: {path}"}}
        sha = sha256_file(path)
        evidences.append({"kind": "FILE_HASH", "data": {"path": str(path), "sha256": sha, "size_bytes": path.stat().st_size}})
    return {"status": "SUCCEEDED", "artifacts": artifacts, "evidences": evidences, "error": None}

