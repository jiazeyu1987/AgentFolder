from __future__ import annotations

from typing import Dict


def consume_per_task_budget(counts: Dict[str, int], *, task_id: str, limit: int, cost: int = 1) -> bool:
    """
    In-memory per-run budget. Returns True if budget is consumed, False if it would exceed the limit.
    """
    if limit <= 0:
        return True
    if cost <= 0:
        return True
    current = int(counts.get(task_id, 0) or 0)
    if current + int(cost) > int(limit):
        return False
    counts[task_id] = current + int(cost)
    return True

