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
    root = ensure_deliverables_root(plan_id)
    out = root / "tasks" / task_slug(task_title, task_id=task_id)
    ensure_dir(out)
    return out


def rel_to_deliverables(plan_id: str, abs_path: Path) -> str:
    root = deliverables_root(plan_id)
    try:
        return str(abs_path.resolve().relative_to(root.resolve()))
    except Exception:
        return str(abs_path)
