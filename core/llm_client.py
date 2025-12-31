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
    extra_calls: int = 0
    repair_used: bool = False
    repair_original_response: Optional[str] = None


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


_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


def _remove_trailing_commas(json_text: str) -> str:
    """
    Best-effort repair for a common model mistake:
      { "a": 1, }
      [1,2,]
    """
    return _TRAILING_COMMA_RE.sub(r"\\1", json_text)


def _build_json_repair_prompt(raw_response: str) -> str:
    # Keep it simple: ask for a single valid JSON object, no code fences, no extra text.
    return (
        "You previously responded with INVALID JSON.\n"
        "Please rewrite it as a single VALID JSON object only (no markdown, no code fences, no commentary).\n"
        "Rules:\n"
        "- Output must be a JSON object starting with '{' and ending with '}'.\n"
        "- Remove trailing commas.\n"
        "- Escape all newlines inside strings as \\n.\n"
        "- Preserve the original fields/values as much as possible.\n"
        "\n"
        "INVALID_JSON_START\n"
        f"{raw_response}\n"
        "INVALID_JSON_END\n"
    )


def _escape_control_chars_in_json_strings(json_text: str) -> str:
    """
    Repair invalid JSON caused by raw control characters inside string literals.

    Some models emit multi-line code inside JSON strings with literal newlines/tabs.
    JSON forbids control characters in strings; they must be escaped (\\n, \\t, ...).

    This function walks the JSON text and, when inside a quoted string, replaces any
    character with codepoint < 0x20 with an escaped representation.
    """
    out: list[str] = []
    in_string = False
    escape = False

    for ch in json_text:
        if not in_string:
            out.append(ch)
            if ch == '"':
                in_string = True
            continue

        # inside string
        if escape:
            out.append(ch)
            escape = False
            continue

        if ch == "\\":
            out.append(ch)
            escape = True
            continue

        if ch == '"':
            out.append(ch)
            in_string = False
            continue

        code = ord(ch)
        if code < 0x20:
            if ch == "\n":
                out.append("\\n")
            elif ch == "\r":
                out.append("\\r")
            elif ch == "\t":
                out.append("\\t")
            elif ch == "\b":
                out.append("\\b")
            elif ch == "\f":
                out.append("\\f")
            else:
                out.append(f"\\u{code:04x}")
            continue

        out.append(ch)

    return "".join(out)


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
            try:
                parsed = json.loads(json_text)
            except json.JSONDecodeError as exc:
                # Common repair: escape raw control chars inside strings (e.g. multi-line code).
                repaired = _escape_control_chars_in_json_strings(json_text)
                try:
                    parsed = json.loads(repaired)
                except json.JSONDecodeError:
                    # Common repair: remove trailing commas.
                    parsed = json.loads(_remove_trailing_commas(repaired))
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
            # Last resort: ask the model to rewrite into valid JSON (1 extra call, no recursion).
            try:
                repair_prompt = _build_json_repair_prompt(res.raw_response_text)
                repair_res = self.call_text(repair_prompt, timeout_s=timeout_s)
                if not repair_res.error:
                    json_text2 = _extract_json_object(repair_res.raw_response_text)
                    try:
                        parsed2 = json.loads(json_text2)
                    except json.JSONDecodeError:
                        repaired2 = _escape_control_chars_in_json_strings(json_text2)
                        parsed2 = json.loads(_remove_trailing_commas(repaired2))
                    if isinstance(parsed2, dict):
                        return LLMCallResult(
                            started_at_ts=res.started_at_ts,
                            finished_at_ts=repair_res.finished_at_ts,
                            prompt=res.prompt,
                            raw_response_text=repair_res.raw_response_text,
                            parsed_json=parsed2,
                            error_code=None,
                            error=None,
                            provider=res.provider,
                            extra_calls=1,
                            repair_used=True,
                            repair_original_response=res.raw_response_text,
                        )
            except Exception:
                pass
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
