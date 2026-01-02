from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import config


class RuntimeConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMRuntimeConfig:
    provider: str  # "llm_demo" | "claude_code"
    claude_code_bin: str
    timeout_s: int


@dataclass(frozen=True)
class GuardrailsConfig:
    max_run_iterations: int
    max_llm_calls_per_run: int
    max_llm_calls_per_task: int
    max_prompt_chars: int
    max_response_chars: int
    max_task_events_per_task: int
    max_llm_calls_rows: int
    max_task_events_rows: int


@dataclass(frozen=True)
class RuntimeConfig:
    llm: LLMRuntimeConfig
    workflow_mode: str  # "v1" | "v2"
    python_executable: str
    max_decomposition_depth: int
    one_shot_threshold_person_days: float
    plan_review_pass_score: int
    export_include_candidates: bool
    max_artifact_versions_per_task: int
    max_review_versions_per_check: int
    max_check_attempts_v2: int
    guardrails: GuardrailsConfig


_CACHE: Optional[RuntimeConfig] = None


def reset_runtime_config_cache() -> None:
    global _CACHE
    _CACHE = None


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        # Default config if file is missing.
        return {
            "llm": {"provider": "llm_demo", "claude_code_bin": "claude_code", "timeout_s": 300},
            "workflow_mode": "v1",
            "python_executable": "",
            "max_decomposition_depth": 5,
            "one_shot_threshold_person_days": 10,
            "plan_review_pass_score": 90,
            "export_include_candidates": False,
            "max_artifact_versions_per_task": 50,
            "max_review_versions_per_check": 50,
            "max_check_attempts_v2": 3,
            "guardrails": {
                "max_run_iterations": 200,
                "max_llm_calls_per_run": 50,
                "max_llm_calls_per_task": 10,
                "max_prompt_chars": 120_000,
                "max_response_chars": 200_000,
                "max_task_events_per_task": 200,
                "max_llm_calls_rows": 5_000,
                "max_task_events_rows": 20_000,
            },
        }
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeConfigError(f"Invalid JSON in {path}: {exc}") from exc


def load_runtime_config(path: Optional[Path] = None) -> RuntimeConfig:
    path = path or config.RUNTIME_CONFIG_PATH
    data = _load_json(path)
    llm = data.get("llm") or {}
    if not isinstance(llm, dict):
        raise RuntimeConfigError("runtime_config.llm must be an object")
    provider = str(llm.get("provider") or "llm_demo")
    if provider not in {"llm_demo", "claude_code"}:
        raise RuntimeConfigError("llm.provider must be llm_demo|claude_code")
    claude_code_bin = str(llm.get("claude_code_bin") or "claude_code")
    timeout_s = int(llm.get("timeout_s") or 300)
    if timeout_s <= 0:
        raise RuntimeConfigError("llm.timeout_s must be > 0")

    workflow_mode = str(data.get("workflow_mode") or "v1").strip().lower()
    if workflow_mode not in {"v1", "v2"}:
        raise RuntimeConfigError("workflow_mode must be v1|v2")

    python_executable = str(data.get("python_executable") or "").strip()

    max_decomposition_depth = int(data.get("max_decomposition_depth") or 5)
    if max_decomposition_depth <= 0:
        raise RuntimeConfigError("max_decomposition_depth must be > 0")

    one_shot_threshold_person_days = float(data.get("one_shot_threshold_person_days") or 10)
    if one_shot_threshold_person_days <= 0:
        raise RuntimeConfigError("one_shot_threshold_person_days must be > 0")

    plan_review_pass_score = int(data.get("plan_review_pass_score") or 90)
    if plan_review_pass_score <= 0 or plan_review_pass_score > 100:
        raise RuntimeConfigError("plan_review_pass_score must be 1..100")

    export_include_candidates = bool(data.get("export_include_candidates") or False)

    guardrails_raw = data.get("guardrails") or {}
    if guardrails_raw is None:
        guardrails_raw = {}
    if not isinstance(guardrails_raw, dict):
        raise RuntimeConfigError("guardrails must be an object")

    max_artifact_versions_per_task = int(guardrails_raw.get("max_artifact_versions_per_task") or data.get("max_artifact_versions_per_task") or 50)
    if max_artifact_versions_per_task <= 0:
        raise RuntimeConfigError("max_artifact_versions_per_task must be > 0")

    max_review_versions_per_check = int(guardrails_raw.get("max_review_versions_per_check") or data.get("max_review_versions_per_check") or 50)
    if max_review_versions_per_check <= 0:
        raise RuntimeConfigError("max_review_versions_per_check must be > 0")

    max_check_attempts_v2 = int(data.get("max_check_attempts_v2") or 3)
    if max_check_attempts_v2 <= 0:
        raise RuntimeConfigError("max_check_attempts_v2 must be > 0")

    max_run_iterations = int(guardrails_raw.get("max_run_iterations") or 200)
    if max_run_iterations <= 0:
        raise RuntimeConfigError("guardrails.max_run_iterations must be > 0")

    max_llm_calls_per_run = int(guardrails_raw.get("max_llm_calls_per_run") or 50)
    if max_llm_calls_per_run <= 0:
        raise RuntimeConfigError("guardrails.max_llm_calls_per_run must be > 0")

    max_llm_calls_per_task = int(guardrails_raw.get("max_llm_calls_per_task") or 10)
    if max_llm_calls_per_task <= 0:
        raise RuntimeConfigError("guardrails.max_llm_calls_per_task must be > 0")

    max_prompt_chars = int(guardrails_raw.get("max_prompt_chars") or 120_000)
    if max_prompt_chars <= 0:
        raise RuntimeConfigError("guardrails.max_prompt_chars must be > 0")

    max_response_chars = int(guardrails_raw.get("max_response_chars") or 200_000)
    if max_response_chars <= 0:
        raise RuntimeConfigError("guardrails.max_response_chars must be > 0")

    max_task_events_per_task = int(guardrails_raw.get("max_task_events_per_task") or 200)
    if max_task_events_per_task <= 0:
        raise RuntimeConfigError("guardrails.max_task_events_per_task must be > 0")

    max_llm_calls_rows = int(guardrails_raw.get("max_llm_calls_rows") or 5_000)
    if max_llm_calls_rows <= 0:
        raise RuntimeConfigError("guardrails.max_llm_calls_rows must be > 0")

    max_task_events_rows = int(guardrails_raw.get("max_task_events_rows") or 20_000)
    if max_task_events_rows <= 0:
        raise RuntimeConfigError("guardrails.max_task_events_rows must be > 0")

    return RuntimeConfig(
        llm=LLMRuntimeConfig(
            provider=provider,
            claude_code_bin=claude_code_bin,
            timeout_s=timeout_s,
        ),
        workflow_mode=workflow_mode,
        python_executable=python_executable,
        max_decomposition_depth=max_decomposition_depth,
        one_shot_threshold_person_days=one_shot_threshold_person_days,
        plan_review_pass_score=plan_review_pass_score,
        export_include_candidates=export_include_candidates,
        max_artifact_versions_per_task=max_artifact_versions_per_task,
        max_review_versions_per_check=max_review_versions_per_check,
        max_check_attempts_v2=max_check_attempts_v2,
        guardrails=GuardrailsConfig(
            max_run_iterations=max_run_iterations,
            max_llm_calls_per_run=max_llm_calls_per_run,
            max_llm_calls_per_task=max_llm_calls_per_task,
            max_prompt_chars=max_prompt_chars,
            max_response_chars=max_response_chars,
            max_task_events_per_task=max_task_events_per_task,
            max_llm_calls_rows=max_llm_calls_rows,
            max_task_events_rows=max_task_events_rows,
        ),
    )


def get_runtime_config(path: Optional[Path] = None) -> RuntimeConfig:
    global _CACHE
    if _CACHE is None:
        _CACHE = load_runtime_config(path or config.RUNTIME_CONFIG_PATH)
    return _CACHE
