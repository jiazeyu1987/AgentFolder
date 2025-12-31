from __future__ import annotations

import importlib
import json
import sqlite3
import uuid
import multiprocessing as mp
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.events import emit_event
from core.artifacts import insert_artifact
from core.util import canonical_json, stable_hash_parts, utc_now_iso


class SkillsRegistryError(RuntimeError):
    pass


@dataclass(frozen=True)
class SkillDef:
    name: str
    implementation: str
    idempotency_strategy: str
    cache: bool
    inputs: List[Dict[str, Any]]
    outputs: Dict[str, Any]
    params_schema: Dict[str, Any]


def load_registry(path: Path) -> Dict[str, SkillDef]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text)
        if not isinstance(data, dict) or "skills" not in data:
            raise SkillsRegistryError("registry.yaml must contain a top-level 'skills' list")
        skills_data = data.get("skills") or []
    except Exception:
        # If the file uses the full schema (nested inputs/outputs/params), require pyyaml.
        if "inputs:" in text or "outputs:" in text or "params:" in text:
            raise SkillsRegistryError("Missing dependency: pyyaml (pip install pyyaml) is required to parse the full skills registry schema.")

        # Minimal fallback parser for very small registry.yaml structure.
        skills_data = []
        current: Dict[str, Any] = {}

        def flush() -> None:
            nonlocal current
            if current.get("name") and current.get("implementation"):
                skills_data.append(current)
            current = {}

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("- name:"):
                flush()
                current["name"] = line.split(":", 1)[1].strip()
                continue
            if line.startswith("implementation:"):
                current["implementation"] = line.split(":", 1)[1].strip()
                continue
            if line.startswith("strategy:"):
                current.setdefault("idempotency", {})["strategy"] = line.split(":", 1)[1].strip()
                continue
            if line.startswith("cache:"):
                v = line.split(":", 1)[1].strip()
                current.setdefault("idempotency", {})["cache"] = v.lower() in {"true", "1", "yes"}
                continue
        flush()

    out: Dict[str, SkillDef] = {}
    for item in skills_data:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        impl = item.get("implementation")
        idem = ((item.get("idempotency") or {}).get("strategy") or "DISABLED").strip()
        cache = bool((item.get("idempotency") or {}).get("cache") is True)
        if not isinstance(name, str) or not isinstance(impl, str):
            continue
        if idem not in {"INPUT_HASHES", "INPUT_HASHES_PLUS_PARAMS", "DISABLED"}:
            raise SkillsRegistryError(f"invalid idempotency.strategy for {name}: {idem}")

        inputs = item.get("inputs") or []
        if not isinstance(inputs, list):
            raise SkillsRegistryError(f"{name}.inputs must be an array")
        for inp in inputs:
            if not isinstance(inp, dict):
                raise SkillsRegistryError(f"{name}.inputs item must be an object")
            if inp.get("kind") not in {"FILE", "CONFIRMATION", "ARTIFACT"}:
                raise SkillsRegistryError(f"{name}.inputs.kind invalid")
            if "required" not in inp or not isinstance(inp.get("required"), bool):
                raise SkillsRegistryError(f"{name}.inputs.required must be boolean")
            schema = inp.get("schema") or {}
            if not isinstance(schema, dict) or "fields" not in schema or not isinstance(schema.get("fields"), list):
                raise SkillsRegistryError(f"{name}.inputs.schema.fields must be array")
            if any(not isinstance(f, str) for f in schema.get("fields")):
                raise SkillsRegistryError(f"{name}.inputs.schema.fields must be string array")

        outputs = item.get("outputs") or {}
        if not isinstance(outputs, dict):
            raise SkillsRegistryError(f"{name}.outputs must be an object")
        if "artifacts" not in outputs or "evidences" not in outputs:
            raise SkillsRegistryError(f"{name}.outputs must include artifacts and evidences")

        params_schema = ((item.get("params") or {}).get("schema") or {})
        if not isinstance(params_schema, dict):
            raise SkillsRegistryError(f"{name}.params.schema must be an object")

        out[name] = SkillDef(
            name=name,
            implementation=impl,
            idempotency_strategy=idem,
            cache=cache,
            inputs=inputs,
            outputs=outputs,
            params_schema=params_schema,
        )
    return out


def validate_skill_call(skill: SkillDef, *, inputs: List[Dict[str, Any]], params: Dict[str, Any]) -> None:
    # Minimal deterministic validation per Skills_Registry_Spec.md.
    declared_inputs = skill.inputs or []
    required_inputs = [i for i in declared_inputs if i.get("required") is True]
    if required_inputs and not inputs:
        raise SkillsRegistryError(f"{skill.name} requires inputs")
    for inp in inputs:
        if not isinstance(inp, dict):
            raise SkillsRegistryError(f"{skill.name} input must be object")
        # For FILE kind we at least require path+sha256 in runtime calls.
        if "path" in (required_inputs[0].get("schema", {}).get("fields") if required_inputs else []):
            if not inp.get("path"):
                raise SkillsRegistryError(f"{skill.name} input missing path")
        if "sha256" in (required_inputs[0].get("schema", {}).get("fields") if required_inputs else []):
            if not inp.get("sha256"):
                raise SkillsRegistryError(f"{skill.name} input missing sha256")
    if not isinstance(params, dict):
        raise SkillsRegistryError(f"{skill.name} params must be object")


def _resolve_impl(implementation: str) -> Callable[..., Dict[str, Any]]:
    if ":" not in implementation:
        raise SkillsRegistryError(f"invalid implementation: {implementation}")
    mod, func = implementation.split(":", 1)
    module = importlib.import_module(mod)
    fn = getattr(module, func, None)
    if not callable(fn):
        raise SkillsRegistryError(f"implementation not callable: {implementation}")
    return fn  # type: ignore[return-value]


def _run_impl_in_child(implementation: str, kwargs: Dict[str, Any], queue: "mp.Queue[Dict[str, Any]]") -> None:
    try:
        fn = _resolve_impl(implementation)
        queue.put(fn(**kwargs))
    except Exception as exc:  # noqa: BLE001
        queue.put({"status": "FAILED", "artifacts": [], "evidences": [], "error": {"code": "SKILL_FAILED", "message": f"{type(exc).__name__}: {exc}"}})


def _execute_with_timeout(implementation: str, kwargs: Dict[str, Any], timeout_s: int) -> Tuple[Dict[str, Any], Optional[str]]:
    queue: "mp.Queue[Dict[str, Any]]" = mp.Queue()
    proc = mp.Process(target=_run_impl_in_child, args=(implementation, kwargs, queue))
    proc.start()
    proc.join(timeout=timeout_s)
    if proc.is_alive():
        proc.terminate()
        proc.join(timeout=5)
        return {"status": "FAILED", "artifacts": [], "evidences": [], "error": {"code": "SKILL_TIMEOUT", "message": f"skill timed out after {timeout_s}s"}}, "SKILL_TIMEOUT"
    try:
        return queue.get_nowait(), None
    except Exception:
        return {"status": "FAILED", "artifacts": [], "evidences": [], "error": {"code": "SKILL_FAILED", "message": "no result from skill process"}}, "SKILL_FAILED"


def _compute_idempotency_key(skill_name: str, *, input_hashes: List[str], params_json: str, strategy: str) -> Optional[str]:
    if strategy == "DISABLED":
        return None
    parts = [skill_name] + sorted(h for h in input_hashes if h)
    if strategy == "INPUT_HASHES_PLUS_PARAMS":
        parts.append(params_json)
    return stable_hash_parts(parts)


def run_skill(
    conn: sqlite3.Connection,
    *,
    plan_id: str,
    task_id: str,
    registry: Dict[str, SkillDef],
    skill_name: str,
    inputs: List[Dict[str, Any]],
    params: Optional[Dict[str, Any]] = None,
    timeout_s: int = 120,
) -> Dict[str, Any]:
    if skill_name not in registry:
        raise SkillsRegistryError(f"unknown skill: {skill_name}")
    skill = registry[skill_name]
    validate_skill_call(skill, inputs=inputs, params=params or {})

    params_json = canonical_json(params or {})
    input_hashes = [str(i.get("sha256") or "") for i in inputs if isinstance(i, dict)]
    idempotency_key = _compute_idempotency_key(skill_name, input_hashes=input_hashes, params_json=params_json, strategy=skill.idempotency_strategy)

    if skill.cache and idempotency_key:
        row = conn.execute(
            """
            SELECT output_artifacts_json, output_evidences_json
            FROM skill_runs
            WHERE idempotency_key = ? AND status = 'SUCCEEDED'
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (idempotency_key,),
        ).fetchone()
        if row:
            return {
                "status": "SUCCEEDED",
                "artifacts": json.loads(row["output_artifacts_json"] or "[]"),
                "evidences": json.loads(row["output_evidences_json"] or "[]"),
                "error": None,
                "cached": True,
            }

    skill_run_id = str(uuid.uuid4())
    started_at = utc_now_iso()
    conn.execute(
        """
        INSERT INTO skill_runs(
          skill_run_id, task_id, plan_id, skill_name, inputs_json, params_json, status,
          output_artifacts_json, output_evidences_json, error_code, error_message,
          started_at, finished_at, idempotency_key
        )
        VALUES(?, ?, ?, ?, ?, ?, 'RUNNING', NULL, NULL, NULL, NULL, ?, NULL, ?)
        """,
        (
            skill_run_id,
            task_id,
            plan_id,
            skill_name,
            canonical_json(inputs),
            params_json,
            started_at,
            idempotency_key,
        ),
    )
    emit_event(conn, plan_id=plan_id, task_id=task_id, event_type="SKILL_RUN", payload={"skill_run_id": skill_run_id, "skill_name": skill_name, "status": "RUNNING"})

    result, forced_error_code = _execute_with_timeout(
        skill.implementation,
        {"task_id": task_id, "plan_id": plan_id, "inputs": inputs, "params": params or {}},
        timeout_s=timeout_s,
    )
    finished_at = utc_now_iso()

    status = result.get("status")
    if status not in {"SUCCEEDED", "FAILED"}:
        status = "FAILED"

    artifacts = result.get("artifacts") or []
    evidences = result.get("evidences") or []
    error = result.get("error") or None

    if status == "SUCCEEDED":
        for art in artifacts:
            try:
                name = str(art.get("name") or skill_name)
                path = Path(str(art.get("path") or ""))
                fmt = str(art.get("format") or path.suffix.lstrip(".") or "txt")
                if path.exists():
                    insert_artifact(conn, plan_id=plan_id, task_id=task_id, name=name, fmt=fmt, path=path)
            except Exception:
                # Best-effort: do not fail the whole skill on artifact persistence.
                pass

    conn.execute(
        """
        UPDATE skill_runs
        SET status = ?, output_artifacts_json = ?, output_evidences_json = ?, error_code = ?, error_message = ?, finished_at = ?
        WHERE skill_run_id = ?
        """,
        (
            status,
            canonical_json(artifacts),
            canonical_json(evidences),
            forced_error_code or ((error or {}).get("code") if isinstance(error, dict) else None),
            (error or {}).get("message") if isinstance(error, dict) else None,
            finished_at,
            skill_run_id,
        ),
    )
    emit_event(conn, plan_id=plan_id, task_id=task_id, event_type="SKILL_RUN", payload={"skill_run_id": skill_run_id, "skill_name": skill_name, "status": status})

    result["cached"] = False
    result["skill_run_id"] = skill_run_id
    return result
