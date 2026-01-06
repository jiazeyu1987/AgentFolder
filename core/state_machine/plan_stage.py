from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Literal, Optional


Stage = Literal["STRUCTURE", "BINDINGS", "EXECUTION"]

STAGE_ORDER: tuple[Stage, ...] = ("STRUCTURE", "BINDINGS", "EXECUTION")


def normalize_stage(stage: str) -> Stage:
    s = str(stage or "").strip().upper()
    if s in STAGE_ORDER:
        return s  # type: ignore[return-value]
    return "STRUCTURE"


def next_stage(stage: Stage) -> Optional[Stage]:
    if stage == "STRUCTURE":
        return "BINDINGS"
    if stage == "BINDINGS":
        return "EXECUTION"
    return None


@dataclass(frozen=True)
class StageDecision:
    decision: Literal["RETRY", "ADVANCE", "DONE"]
    stage: Stage


def decide_next_stage(*, current_stage: Stage, score: int, pass_score: int) -> StageDecision:
    """
    Centralized plan-stage gating rule:
    - if score < pass_score: retry current stage
    - else: advance to next stage
    - if EXECUTION passed: DONE
    """
    if int(score) < int(pass_score):
        return StageDecision(decision="RETRY", stage=current_stage)
    nxt = next_stage(current_stage)
    if nxt is None:
        return StageDecision(decision="DONE", stage=current_stage)
    return StageDecision(decision="ADVANCE", stage=nxt)


def clamp_resume_stage(*, resume_stage: Stage, passed: Dict[str, bool]) -> Stage:
    """
    Ensure resume_stage never skips a not-yet-passed previous stage.
    """
    r = normalize_stage(resume_stage)
    if r == "EXECUTION" and not bool(passed.get("BINDINGS")):
        return "BINDINGS"
    if r in ("BINDINGS", "EXECUTION") and not bool(passed.get("STRUCTURE")):
        return "STRUCTURE"
    return r

