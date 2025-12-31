from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def sha256_bytes(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_hash_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def stable_hash_parts(parts: Iterable[str]) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def safe_read_text(path: Path, *, max_chars: int = 200_000) -> str:
    data = path.read_text(encoding="utf-8", errors="replace")
    if len(data) <= max_chars:
        return data
    return data[:max_chars] + "\n\n[TRUNCATED]\n"


def to_optional_json(obj: Optional[Dict[str, Any]]) -> Optional[str]:
    if obj is None:
        return None
    return canonical_json(obj)
