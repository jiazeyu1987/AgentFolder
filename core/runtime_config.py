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
class RuntimeConfig:
    llm: LLMRuntimeConfig
    workflow_mode: str  # "v1" | "v2"


_CACHE: Optional[RuntimeConfig] = None


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        # Default config if file is missing.
        return {"llm": {"provider": "llm_demo", "claude_code_bin": "claude_code", "timeout_s": 300}, "workflow_mode": "v1"}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeConfigError(f"Invalid JSON in {path}: {exc}") from exc


def load_runtime_config(path: Path = config.RUNTIME_CONFIG_PATH) -> RuntimeConfig:
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

    return RuntimeConfig(
        llm=LLMRuntimeConfig(
            provider=provider,
            claude_code_bin=claude_code_bin,
            timeout_s=timeout_s,
        ),
        workflow_mode=workflow_mode,
    )


def get_runtime_config(path: Path = config.RUNTIME_CONFIG_PATH) -> RuntimeConfig:
    global _CACHE
    if _CACHE is None:
        _CACHE = load_runtime_config(path)
    return _CACHE
