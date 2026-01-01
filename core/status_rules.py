from __future__ import annotations

from dataclasses import dataclass
from typing import AbstractSet, Dict, FrozenSet


@dataclass(frozen=True)
class StatusRuleError(ValueError):
    message: str

    def __str__(self) -> str:  # pragma: no cover
        return self.message


ALL_STATUSES: FrozenSet[str] = frozenset(
    {
        "PENDING",
        "READY",
        "IN_PROGRESS",
        "BLOCKED",
        "READY_TO_CHECK",
        "TO_BE_MODIFY",
        "DONE",
        "FAILED",
        "ABANDONED",
    }
)


NODE_TYPES: FrozenSet[str] = frozenset({"GOAL", "ACTION", "CHECK"})


NODE_TYPE_ALLOWED_STATUSES: Dict[str, FrozenSet[str]] = {
    # GOAL is an aggregate node; it should never require READY_TO_CHECK.
    "GOAL": frozenset({"PENDING", "READY", "IN_PROGRESS", "BLOCKED", "DONE", "FAILED", "ABANDONED"}),
    # ACTION is executed by xiaobo and then gated for review.
    "ACTION": frozenset({"PENDING", "READY", "IN_PROGRESS", "BLOCKED", "READY_TO_CHECK", "TO_BE_MODIFY", "DONE", "FAILED", "ABANDONED"}),
    # CHECK is a reviewer-executed node; it must not use READY_TO_CHECK.
    "CHECK": frozenset({"PENDING", "READY", "IN_PROGRESS", "BLOCKED", "DONE", "FAILED", "ABANDONED"}),
}


def validate_status_for_node_type(*, node_type: str, status: str) -> None:
    nt = str(node_type or "").strip().upper()
    st = str(status or "").strip().upper()
    if nt not in NODE_TYPES:
        raise StatusRuleError(f"unknown node_type: {node_type!r}")
    if st not in ALL_STATUSES:
        raise StatusRuleError(f"unknown status: {status!r}")
    allowed = NODE_TYPE_ALLOWED_STATUSES.get(nt, frozenset())
    if st not in allowed:
        raise StatusRuleError(f"status {st!r} is not allowed for node_type {nt!r}")


def allowed_statuses_for_node_type(node_type: str) -> AbstractSet[str]:
    nt = str(node_type or "").strip().upper()
    return NODE_TYPE_ALLOWED_STATUSES.get(nt, frozenset())

