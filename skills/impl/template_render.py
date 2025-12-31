from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from core.util import ensure_dir, sha256_file


def run(*, task_id: str, plan_id: str, inputs: List[Dict[str, Any]], params: Dict[str, Any]) -> Dict[str, Any]:
    template_path = Path(str(params.get("template_path") or ""))
    data = params.get("data_json") or {}
    if not template_path.is_file():
        return {"status": "FAILED", "artifacts": [], "evidences": [], "error": {"code": "SKILL_BAD_INPUT", "message": f"template not found: {template_path}"}}
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            data = {}
    if not isinstance(data, dict):
        data = {}

    template = template_path.read_text(encoding="utf-8")
    content = template.format(**data)

    out_dir = Path("workspace") / "artifacts" / task_id
    ensure_dir(out_dir)
    out_path = out_dir / "rendered.md"
    out_path.write_text(content, encoding="utf-8")
    return {"status": "SUCCEEDED", "artifacts": [{"name": "rendered", "path": str(out_path), "sha256": sha256_file(out_path), "format": "md"}], "evidences": [], "error": None}

