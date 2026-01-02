from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

from core.contracts import normalize_plan_json, normalize_xiaobo_action, normalize_xiaojing_review
from core.contracts import validate_xiaobo_action, validate_xiaojing_review
from core.models import PlanValidationError, validate_plan_dict


@dataclass(frozen=True)
class ContractError(ValueError):
    error_code: str
    schema: str
    schema_version: str
    json_path: str
    expected: str
    actual: str
    example_fix: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "error_code": self.error_code,
            "schema": self.schema,
            "schema_version": self.schema_version,
            "json_path": self.json_path,
            "expected": self.expected,
            "actual": self.actual,
            "example_fix": self.example_fix,
        }


def _infer_error_from_reason(*, schema: str, schema_version: str, reason: str, obj: Any) -> ContractError:
    r = str(reason or "").strip()
    actual_sv = ""
    if isinstance(obj, dict):
        actual_sv = str(obj.get("schema_version") or "")

    # Default fallback.
    json_path = "$"
    expected = "valid contract"
    actual = r or "invalid contract"
    example_fix = json.dumps({"schema_version": schema_version}, ensure_ascii=False)

    # schema_version mismatch.
    if "schema_version mismatch" in r:
        json_path = "$.schema_version"
        expected = schema_version
        actual = actual_sv or r
        example_fix = json.dumps({"schema_version": schema_version}, ensure_ascii=False)

    # missing key: X
    m = re.search(r"missing (required )?key: ([a-zA-Z0-9_]+)", r)
    if m:
        key = m.group(2)
        json_path = f"$.{key}"
        expected = f"object with key '{key}'"
        actual = "missing"
        example_fix = json.dumps({key: "<REQUIRED>"}, ensure_ascii=False)

    # artifact.format must be ...
    if "artifact.format must be" in r:
        json_path = "$.artifact.format"
        expected = "one of: md|txt|json|html|css|js"
        actual = str(((obj or {}).get("artifact") or {}).get("format") if isinstance(obj, dict) else "") or r
        example_fix = json.dumps({"artifact": {"format": "md"}}, ensure_ascii=False)

    # suggestion.priority must be ...
    if "suggestion.priority must be" in r:
        json_path = "$.suggestions[*].priority"
        expected = "one of: HIGH|MED|LOW"
        actual = r
        example_fix = json.dumps({"suggestions": [{"priority": "MED"}]}, ensure_ascii=False)

    # node missing key: node_type (plan schema)
    if r.startswith("node missing key:"):
        key = r.split(":", 1)[1].strip()
        json_path = f"$.nodes[*].{key}"
        expected = f"each node has '{key}'"
        actual = "missing"
        example_fix = json.dumps({"nodes": [{"node_type": "ACTION"}]}, ensure_ascii=False)

    # edge.edge_type must be ...
    if "edge.edge_type must be" in r:
        json_path = "$.edges[*].edge_type"
        expected = "one of: DECOMPOSE|DEPENDS_ON|ALTERNATIVE"
        actual = r
        example_fix = json.dumps({"edges": [{"edge_type": "DEPENDS_ON"}]}, ensure_ascii=False)

    return ContractError(
        error_code="SCHEMA_MISMATCH",
        schema=schema,
        schema_version=schema_version,
        json_path=json_path,
        expected=expected,
        actual=actual,
        example_fix=example_fix,
    )


def format_contract_error_short(err: Dict[str, str]) -> str:
    return (
        f"{err.get('error_code')} {err.get('schema')}@{err.get('schema_version')} "
        f"path={err.get('json_path')} expected={err.get('expected')} actual={err.get('actual')}"
    ).strip()


Normalizer = Callable[[Any, Dict[str, Any]], Any]
Validator = Callable[[Any, Dict[str, Any]], Tuple[bool, str]]


@dataclass(frozen=True)
class ContractSpec:
    name: str
    schema_version: str
    normalize: Normalizer
    validate: Validator
    summary: Dict[str, Any]


def _norm_task_action(raw: Any, ctx: Dict[str, Any]) -> Any:
    if not isinstance(raw, dict):
        return raw
    return normalize_xiaobo_action(raw, task_id=str(ctx.get("task_id") or ""))


def _val_task_action(obj: Any, ctx: Dict[str, Any]) -> Tuple[bool, str]:
    if not isinstance(obj, dict):
        return False, "expected object"
    return validate_xiaobo_action(obj)


def _norm_review_plan(raw: Any, ctx: Dict[str, Any]) -> Any:
    if not isinstance(raw, dict):
        return raw
    # Do not silently coerce a wrong schema_version into a valid one.
    # We want the reviewer to re-emit a correct `xiaojing_review_v1` JSON instead of losing information.
    sv = raw.get("schema_version")
    if isinstance(sv, str) and sv.strip() and sv.strip() != "xiaojing_review_v1":
        return raw
    return normalize_xiaojing_review(raw, task_id=str(ctx.get("plan_id") or ctx.get("task_id") or ""), review_target="PLAN")


def _val_review_plan(obj: Any, ctx: Dict[str, Any]) -> Tuple[bool, str]:
    if not isinstance(obj, dict):
        return False, "expected object"
    return validate_xiaojing_review(obj, review_target="PLAN")


def _norm_review_node(raw: Any, ctx: Dict[str, Any]) -> Any:
    if not isinstance(raw, dict):
        return raw
    sv = raw.get("schema_version")
    if isinstance(sv, str) and sv.strip() and sv.strip() != "xiaojing_review_v1":
        return raw
    return normalize_xiaojing_review(raw, task_id=str(ctx.get("task_id") or ""), review_target="NODE")


def _val_review_node(obj: Any, ctx: Dict[str, Any]) -> Tuple[bool, str]:
    if not isinstance(obj, dict):
        return False, "expected object"
    return validate_xiaojing_review(obj, review_target="NODE")


def _norm_plan_gen(raw: Any, ctx: Dict[str, Any]) -> Any:
    if not isinstance(raw, dict):
        return raw
    top_task = str(ctx.get("top_task") or "")
    utc_now_iso = ctx.get("utc_now_iso")
    return normalize_plan_json(raw, top_task=top_task, utc_now_iso=utc_now_iso)


def _val_plan_gen(obj: Any, ctx: Dict[str, Any]) -> Tuple[bool, str]:
    if not isinstance(obj, dict):
        return False, "expected object"
    try:
        validate_plan_dict(obj)
        return True, ""
    except PlanValidationError as exc:
        return False, str(exc)


CONTRACTS: Dict[str, ContractSpec] = {
    "TASK_ACTION": ContractSpec(
        name="TASK_ACTION",
        schema_version="xiaobo_action_v1",
        normalize=_norm_task_action,
        validate=_val_task_action,
        summary={
            "schema_version": "xiaobo_action_v1",
            "required_keys": ["schema_version", "task_id", "result_type"],
            "enums": {
                "result_type": ["ARTIFACT", "NEEDS_INPUT", "NOOP", "ERROR"],
                "artifact.format": ["md", "txt", "json", "html", "css", "js"],
            },
        },
    ),
    "PLAN_REVIEW": ContractSpec(
        name="PLAN_REVIEW",
        schema_version="xiaojing_review_v1",
        normalize=_norm_review_plan,
        validate=_val_review_plan,
        summary={
            "schema_version": "xiaojing_review_v1",
            "required_keys": ["schema_version", "task_id", "review_target", "total_score", "action_required", "summary", "breakdown", "suggestions"],
            "enums": {"review_target": ["PLAN"], "action_required": ["APPROVE", "MODIFY", "REQUEST_EXTERNAL_INPUT"], "suggestions[*].priority": ["HIGH", "MED", "LOW"]},
        },
    ),
    "TASK_CHECK": ContractSpec(
        name="TASK_CHECK",
        schema_version="xiaojing_review_v1",
        normalize=_norm_review_node,
        validate=_val_review_node,
        summary={
            "schema_version": "xiaojing_review_v1",
            "required_keys": ["schema_version", "task_id", "review_target", "total_score", "action_required", "summary", "breakdown", "suggestions"],
            "enums": {"review_target": ["NODE"], "action_required": ["APPROVE", "MODIFY", "REQUEST_EXTERNAL_INPUT"], "suggestions[*].priority": ["HIGH", "MED", "LOW"]},
        },
    ),
    "PLAN_GEN": ContractSpec(
        name="PLAN_GEN",
        schema_version="plan_json_v1",
        normalize=_norm_plan_gen,
        validate=_val_plan_gen,
        summary={
            "schema_version": "plan_json_v1",
            "required_keys": ["plan", "nodes", "edges"],
            "enums": {"nodes[*].node_type": ["GOAL", "ACTION", "CHECK"], "edges[*].edge_type": ["DECOMPOSE", "DEPENDS_ON", "ALTERNATIVE"]},
        },
    ),
}


CONTRACT_SUMMARY: Dict[str, Any] = {k: v.summary for k, v in CONTRACTS.items()}


def normalize_and_validate(contract_name: str, raw_obj: Any, context: Optional[Dict[str, Any]] = None) -> Tuple[Any, Optional[Dict[str, str]]]:
    ctx = dict(context or {})
    spec = CONTRACTS.get(str(contract_name or "").strip().upper())
    if not spec:
        err = ContractError(
            error_code="UNKNOWN_CONTRACT",
            schema=str(contract_name),
            schema_version="",
            json_path="$",
            expected="known contract_name",
            actual=str(contract_name),
            example_fix=json.dumps({"contract_name": "TASK_ACTION"}, ensure_ascii=False),
        )
        return raw_obj, err.to_dict()

    normalized = spec.normalize(raw_obj, ctx)
    ok, reason = spec.validate(normalized, ctx)
    if ok:
        return normalized, None

    err = _infer_error_from_reason(schema=spec.name, schema_version=spec.schema_version, reason=reason, obj=normalized)
    return normalized, err.to_dict()
