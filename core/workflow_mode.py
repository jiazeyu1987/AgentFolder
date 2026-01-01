from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from core.runtime_config import get_runtime_config


@dataclass(frozen=True)
class WorkflowModeGuardError(RuntimeError):
    message: str

    def __str__(self) -> str:  # pragma: no cover
        return self.message


def get_workflow_mode() -> str:
    return get_runtime_config().workflow_mode


def ensure_mode_supported_for_action(*, action: str, detected_plan_version: Optional[str] = None) -> None:
    """
    Minimal guard required by P0.3:
    - workflow_mode can be toggled without crashing
    - v2 mode should not hard-crash on "old plans"; it must fail with a readable error

    This repo does not fully implement v2 workflow yet, so we treat all current plans as v1 unless
    they explicitly declare a `workflow_version` marker (future).
    """
    mode = get_workflow_mode()
    if mode == "v1":
        return
    if mode != "v2":
        raise WorkflowModeGuardError(f"Unknown workflow_mode: {mode!r}")

    # v2 requested: if caller passes detected_plan_version and it isn't v2, fail gracefully.
    if detected_plan_version and detected_plan_version != "v2":
        raise WorkflowModeGuardError(
            f"workflow_mode=v2 but {action} detected plan workflow_version={detected_plan_version!r}; "
            "please re-run create-plan with v2 workflow (or set workflow_mode=v1)."
        )

    # Default behavior for now: allow running in compatibility mode (no crash).
    # Later milestones will enforce real v2 plan requirements.
    return

