from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import config
from core.util import ensure_dir, stable_hash_text


_BAD_CHARS_RE = re.compile(r"[^a-zA-Z0-9\u4e00-\u9fff._ -]+")


def deliverables_root(plan_id: str) -> Path:
    return config.DELIVERABLES_DIR / str(plan_id)


def ensure_deliverables_root(plan_id: str) -> Path:
    root = deliverables_root(plan_id)
    ensure_dir(root)
    return root


def task_slug(task_title: str, *, task_id: Optional[str] = None, max_len: int = 60) -> str:
    t = (task_title or "").strip()
    t = _BAD_CHARS_RE.sub("_", t)
    t = t.replace(" ", "_").strip("._-")
    if not t:
        t = "task"
    t = t[:max_len]
    if task_id:
        return f"{t}_{str(task_id)[:8]}"
    # Deterministic fallback to avoid collisions when titles repeat.
    return f"{t}_{stable_hash_text(task_title)[:8]}"


def task_output_dir(plan_id: str, *, task_title: str, task_id: Optional[str] = None) -> Path:
    # All artifacts for a plan live in a single folder (no per-task subfolders).
    return ensure_deliverables_root(plan_id)


def artifact_output_filename(
    *,
    task_title: str,
    task_id: str,
    name: str,
    fmt: str,
    artifact_id: Optional[str] = None,
    max_slug_len: int = 50,
    max_name_len: int = 50,
) -> str:
    """
    Build a collision-safe filename for a task artifact stored in the plan deliverables folder.

    We keep task context in the filename to avoid collisions when multiple tasks output the same `name`.
    We also include an artifact_id prefix to preserve previous versions on retries.
    """

    def _safe_component(text: str, *, max_len: int) -> str:
        t = (text or "").strip()
        t = _BAD_CHARS_RE.sub("_", t)
        t = t.replace(" ", "_").strip("._-")
        return (t or "item")[:max_len]

    safe_fmt = (fmt or "md").lower().lstrip(".") or "md"
    task_part = _safe_component(task_slug(task_title or "task", task_id=task_id, max_len=max_slug_len), max_len=max_slug_len)
    name_part = _safe_component(name or "artifact", max_len=max_name_len)
    id_part = (str(artifact_id or task_id)[:8] or "00000000")
    return f"{task_part}__{name_part}_{id_part}.{safe_fmt}"


def rel_to_deliverables(plan_id: str, abs_path: Path) -> str:
    root = deliverables_root(plan_id)
    try:
        return str(abs_path.resolve().relative_to(root.resolve()))
    except Exception:
        return str(abs_path)
