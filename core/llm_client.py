from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from core.runtime_config import get_runtime_config


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMCallResult:
    started_at_ts: float
    finished_at_ts: float
    prompt: str
    raw_response_text: str
    parsed_json: Optional[Dict[str, Any]]
    error_code: Optional[str]
    error: Optional[str]
    provider: str


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
_REFUSAL_HINTS = [
    "i can't help",
    "i can’t help",
    "i can't comply",
    "i can’t comply",
    "i'm sorry",
    "i’m sorry",
    "cannot comply",
    "i can't do that",
    "i can’t do that",
    "refuse",
    "cannot assist",
    "i can't assist",
    "i can’t assist",
]


def _extract_json_object(text: str) -> str:
    text = text.strip()
    if text.startswith("{") and text.endswith("}"):
        return text
    match = _JSON_OBJECT_RE.search(text)
    if not match:
        raise ValueError("response does not contain a JSON object")
    return match.group(0)


def _looks_like_refusal(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    return any(h in t for h in _REFUSAL_HINTS)


class LLMClient:
    """
    LLM client adapter.

    Always routes through `src.llm_demo.llm_communication` (which itself can call `claude_code`),
    to match the project's integration design.
    """

    def __init__(self) -> None:
        cfg = get_runtime_config().llm
        self._timeout_s = cfg.timeout_s

    def call_text(self, prompt: str, *, timeout_s: int = 300) -> LLMCallResult:
        # If caller doesn't pass explicit timeout, use runtime_config default.
        if timeout_s == 300:
            timeout_s = int(self._timeout_s)
        started_at = time.time()
        raw: str = ""
        cfg = get_runtime_config().llm
        provider = f"src.llm_demo/{cfg.provider}"
        error_code: Optional[str] = None
        error: Optional[str] = None

        try:
            from src.llm_demo.llm_communication import simple_llm_service  # type: ignore

            raw = (simple_llm_service.llm_call(prompt) or "").strip()
            return LLMCallResult(
                started_at_ts=started_at,
                finished_at_ts=time.time(),
                prompt=prompt,
                raw_response_text=raw,
                parsed_json=None,
                error_code=None,
                error=None,
                provider=provider,
            )
        except Exception as exc:  # noqa: BLE001 - treat anything as an LLM failure
            error = f"{type(exc).__name__}: {exc}"
            if error_code is None:
                error_code = "LLM_FAILED"
            return LLMCallResult(
                started_at_ts=started_at,
                finished_at_ts=time.time(),
                prompt=prompt,
                raw_response_text=raw,
                parsed_json=None,
                error_code=error_code,
                error=error,
                provider=provider,
            )

    def call_json(self, prompt: str, *, timeout_s: int = 300) -> LLMCallResult:
        res = self.call_text(prompt, timeout_s=timeout_s)
        if res.error:
            return res

        try:
            json_text = _extract_json_object(res.raw_response_text)
            parsed = json.loads(json_text)
            if not isinstance(parsed, dict):
                raise ValueError("parsed JSON is not an object")
            return LLMCallResult(
                started_at_ts=res.started_at_ts,
                finished_at_ts=res.finished_at_ts,
                prompt=res.prompt,
                raw_response_text=res.raw_response_text,
                parsed_json=parsed,
                error_code=None,
                error=None,
                provider=res.provider,
            )
        except Exception as exc:  # noqa: BLE001
            if _looks_like_refusal(res.raw_response_text):
                return LLMCallResult(
                    started_at_ts=res.started_at_ts,
                    finished_at_ts=res.finished_at_ts,
                    prompt=res.prompt,
                    raw_response_text=res.raw_response_text,
                    parsed_json=None,
                    error_code="LLM_REFUSAL",
                    error="LLM refusal",
                    provider=res.provider,
                )
            return LLMCallResult(
                started_at_ts=res.started_at_ts,
                finished_at_ts=res.finished_at_ts,
                prompt=res.prompt,
                raw_response_text=res.raw_response_text,
                parsed_json=None,
                error_code="LLM_UNPARSEABLE",
                error=f"UNPARSEABLE_JSON: {type(exc).__name__}: {exc}",
                provider=res.provider,
            )
