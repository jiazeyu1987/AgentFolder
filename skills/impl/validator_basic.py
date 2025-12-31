from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List


def run(*, task_id: str, plan_id: str, inputs: List[Dict[str, Any]], params: Dict[str, Any]) -> Dict[str, Any]:
    artifact_path = Path(str(params.get("artifact_path") or ""))
    required_sections = params.get("required_sections") or []
    if not artifact_path.is_file():
        return {"status": "FAILED", "artifacts": [], "evidences": [], "error": {"code": "SKILL_BAD_INPUT", "message": f"artifact not found: {artifact_path}"}}
    text = artifact_path.read_text(encoding="utf-8", errors="replace")
    missing = []
    if isinstance(required_sections, list):
        for sec in required_sections:
            if isinstance(sec, str) and sec and sec not in text:
                missing.append(sec)
    passed = len(missing) == 0
    return {"status": "SUCCEEDED", "artifacts": [], "evidences": [{"kind": "VALIDATION", "data": {"passed": passed, "missing": missing}}], "error": None}

