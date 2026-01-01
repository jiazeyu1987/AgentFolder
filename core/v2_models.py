from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union


JsonObj = Dict[str, Any]
JsonArr = List[Any]


@dataclass(frozen=True)
class V2ModelError(ValueError):
    message: str
    json_path: str = "$"

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.json_path}: {self.message}" if self.json_path else self.message


def dumps_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def loads_json(text: Optional[str], *, expect: str, default: Any) -> Any:
    if text is None:
        return default
    s = str(text).strip()
    if not s:
        return default
    try:
        v = json.loads(s)
    except Exception as exc:  # noqa: BLE001
        raise V2ModelError(f"invalid JSON ({type(exc).__name__}: {exc})", json_path="$") from exc
    if expect == "object" and not isinstance(v, dict):
        raise V2ModelError("expected JSON object", json_path="$")
    if expect == "array" and not isinstance(v, list):
        raise V2ModelError("expected JSON array", json_path="$")
    return v


def validate_deliverable_spec(obj: Any) -> Tuple[bool, str, str]:
    """
    Minimal schema:
    - format, filename, single_file, bundle_mode, description
    """
    if not isinstance(obj, dict):
        return False, "deliverable_spec must be an object", "$"
    required = ["format", "filename", "single_file", "bundle_mode", "description"]
    for k in required:
        if k not in obj:
            return False, f"missing key: {k}", f"$.{k}"
    if not isinstance(obj.get("format"), str) or not obj["format"].strip():
        return False, "format must be non-empty string", "$.format"
    if not isinstance(obj.get("filename"), str) or not obj["filename"].strip():
        return False, "filename must be non-empty string", "$.filename"
    if not isinstance(obj.get("single_file"), bool):
        return False, "single_file must be boolean", "$.single_file"
    if not isinstance(obj.get("bundle_mode"), str) or not obj["bundle_mode"].strip():
        return False, "bundle_mode must be non-empty string", "$.bundle_mode"
    if not isinstance(obj.get("description"), str):
        return False, "description must be string", "$.description"
    return True, "", "$"


def validate_acceptance_criteria(arr: Any) -> Tuple[bool, str, str]:
    """
    Minimal schema for each item:
    - id/type/statement/check_method/severity
    """
    if not isinstance(arr, list) or not arr:
        return False, "acceptance_criteria must be a non-empty array", "$"
    required = ["id", "type", "statement", "check_method", "severity"]
    for idx, item in enumerate(arr):
        if not isinstance(item, dict):
            return False, "acceptance_criteria item must be object", f"$[{idx}]"
        for k in required:
            if k not in item:
                return False, f"missing key: {k}", f"$[{idx}].{k}"
            if not isinstance(item.get(k), str) or not str(item.get(k)).strip():
                return False, f"{k} must be non-empty string", f"$[{idx}].{k}"
    return True, "", "$"


def validate_review_output_spec(obj: Any) -> Tuple[bool, str, str]:
    """
    Minimal schema:
    - approved_filename, rejected_filename
    """
    if obj is None:
        return True, "", "$"
    if not isinstance(obj, dict):
        return False, "review_output_spec must be object", "$"
    for k in ("approved_filename", "rejected_filename"):
        if k in obj and (not isinstance(obj[k], str) or not obj[k].strip()):
            return False, f"{k} must be non-empty string", f"$.{k}"
    return True, "", "$"


def parse_deliverable_spec_json(text: Optional[str]) -> JsonObj:
    return loads_json(text, expect="object", default={})


def parse_acceptance_criteria_json(text: Optional[str]) -> JsonArr:
    return loads_json(text, expect="array", default=[])


def parse_review_output_spec_json(text: Optional[str]) -> JsonObj:
    return loads_json(text, expect="object", default={})

