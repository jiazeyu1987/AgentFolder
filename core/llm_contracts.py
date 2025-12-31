from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def _is_str(x: Any) -> bool:
    return isinstance(x, str)


def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _require_keys(obj: Dict[str, Any], keys: List[str]) -> Optional[str]:
    for k in keys:
        if k not in obj:
            return f"missing key: {k}"
    return None


def validate_xiaobo_action(obj: Dict[str, Any]) -> Tuple[bool, str]:
    err = _require_keys(obj, ["schema_version", "task_id", "result_type"])
    if err:
        return False, err
    if obj.get("schema_version") != "xiaobo_action_v1":
        return False, "schema_version mismatch"
    if not _is_str(obj.get("task_id")):
        return False, "task_id must be string"
    result_type = obj.get("result_type")
    if result_type not in {"NEEDS_INPUT", "ARTIFACT", "NOOP", "ERROR"}:
        return False, "invalid result_type"

    if result_type == "NEEDS_INPUT":
        needs = obj.get("needs_input")
        if not isinstance(needs, dict):
            return False, "needs_input must be object"
        docs = needs.get("required_docs")
        if not isinstance(docs, list) or not docs:
            return False, "needs_input.required_docs must be non-empty array"
        for d in docs:
            if not isinstance(d, dict):
                return False, "required_docs item must be object"
            if not _is_str(d.get("name")) or not _is_str(d.get("description")):
                return False, "required_docs.name/description must be string"
            accepted = d.get("accepted_types")
            if accepted is not None and (not isinstance(accepted, list) or any(not _is_str(x) for x in accepted)):
                return False, "required_docs.accepted_types must be string array"

    if result_type == "ARTIFACT":
        art = obj.get("artifact")
        if not isinstance(art, dict):
            return False, "artifact must be object"
        for k in ("name", "format", "content"):
            if not _is_str(art.get(k)) or not art.get(k):
                return False, f"artifact.{k} is required"
        fmt = art.get("format")
        if fmt not in {"md", "txt", "json"}:
            return False, "artifact.format must be md|txt|json"

    if result_type == "ERROR":
        err_obj = obj.get("error")
        if not isinstance(err_obj, dict):
            return False, "error must be object"
        if not _is_str(err_obj.get("code")) or not _is_str(err_obj.get("message")):
            return False, "error.code/error.message must be string"

    return True, ""


def validate_xiaojing_review(obj: Dict[str, Any], *, review_target: str) -> Tuple[bool, str]:
    err = _require_keys(obj, ["schema_version", "task_id", "review_target", "total_score", "breakdown", "summary", "action_required", "suggestions"])
    if err:
        return False, err
    if obj.get("schema_version") != "xiaojing_review_v1":
        return False, "schema_version mismatch"
    if obj.get("review_target") != review_target:
        return False, "review_target mismatch"
    if not _is_str(obj.get("task_id")):
        return False, "task_id must be string"
    total = obj.get("total_score")
    if not _is_int(total):
        return False, "total_score must be int"
    if int(total) < 0 or int(total) > 100:
        return False, "total_score out of range"
    action = obj.get("action_required")
    if action not in {"APPROVE", "MODIFY", "REQUEST_EXTERNAL_INPUT"}:
        return False, "invalid action_required"
    if int(total) >= 90 and action != "APPROVE":
        return False, "total_score>=90 requires action_required=APPROVE"
    if int(total) < 90 and action == "APPROVE":
        return False, "total_score<90 cannot be APPROVE"

    breakdown = obj.get("breakdown")
    if not isinstance(breakdown, list):
        return False, "breakdown must be array"
    for dim in breakdown:
        if not isinstance(dim, dict):
            return False, "breakdown item must be object"
        for k in ("dimension", "score", "max_score", "issues"):
            if k not in dim:
                return False, f"breakdown missing {k}"
        if not _is_str(dim.get("dimension")):
            return False, "breakdown.dimension must be string"
        if not _is_int(dim.get("score")) or not _is_int(dim.get("max_score")):
            return False, "breakdown.score/max_score must be int"
        issues = dim.get("issues")
        if not isinstance(issues, list):
            return False, "breakdown.issues must be array"
        for issue in issues:
            if not isinstance(issue, dict):
                return False, "issue must be object"
            for k in ("problem", "evidence", "impact", "suggestion", "acceptance_criteria"):
                if not _is_str(issue.get(k)):
                    return False, f"issue.{k} must be string"

    suggestions = obj.get("suggestions")
    if not isinstance(suggestions, list):
        return False, "suggestions must be array"
    for s in suggestions:
        if not isinstance(s, dict):
            return False, "suggestion must be object"
        if s.get("priority") not in {"HIGH", "MED", "LOW"}:
            return False, "suggestion.priority must be HIGH|MED|LOW"
        if not _is_str(s.get("change")):
            return False, "suggestion.change must be string"
        steps = s.get("steps")
        if not isinstance(steps, list) or any(not _is_str(x) for x in steps):
            return False, "suggestion.steps must be string array"
        if not _is_str(s.get("acceptance_criteria")):
            return False, "suggestion.acceptance_criteria must be string"

    return True, ""

