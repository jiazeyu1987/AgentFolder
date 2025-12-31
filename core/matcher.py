from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

from core.events import emit_event
from core.util import sha256_file, utc_now_iso


@dataclass(frozen=True)
class Requirement:
    requirement_id: str
    task_id: str
    name: str
    required: int
    min_count: int
    allowed_types: List[str]
    source: str
    validation: Dict[str, Any]


def _load_requirements(conn: sqlite3.Connection, plan_id: str) -> List[Requirement]:
    rows = conn.execute(
        """
        SELECT r.requirement_id, r.task_id, r.name, r.required, r.min_count, r.allowed_types_json, r.source, r.validation_json
        FROM input_requirements r
        JOIN task_nodes n ON n.task_id = r.task_id
        WHERE n.plan_id = ?
        """,
        (plan_id,),
    ).fetchall()
    out: List[Requirement] = []
    for row in rows:
        allowed_types = []
        if row["allowed_types_json"]:
            try:
                allowed_types = json.loads(row["allowed_types_json"])
            except Exception:
                allowed_types = []
        validation = {}
        if row["validation_json"]:
            try:
                validation = json.loads(row["validation_json"])
            except Exception:
                validation = {}
        out.append(
            Requirement(
                requirement_id=row["requirement_id"],
                task_id=row["task_id"],
                name=row["name"],
                required=int(row["required"]),
                min_count=int(row["min_count"]),
                allowed_types=[str(x).lower() for x in (allowed_types or [])],
                source=row["source"],
                validation=validation or {},
            )
        )
    return out


def _score_match(req: Requirement, file_path: Path, inputs_dir: Path) -> Tuple[int, List[str]]:
    score = 0
    reasons: List[str] = []

    try:
        rel = file_path.resolve().relative_to(inputs_dir.resolve())
        parts = list(rel.parts)
    except Exception:
        parts = []

    if parts and parts[0].lower() == req.name.lower():
        score += 100
        reasons.append("dir_map:+100")

    filename = file_path.name.lower()
    keywords = req.validation.get("filename_keywords") or []
    if isinstance(keywords, list):
        hit = 0
        for kw in keywords:
            if not isinstance(kw, str) or not kw:
                continue
            if kw.lower() in filename:
                hit += 1
                score += 40
        if hit:
            score = min(score, 100 + 80 + 10 + 10)
            reasons.append(f"filename_keywords:{hit}:+{min(80, hit * 40)}")

    ext = file_path.suffix.lower().lstrip(".")
    if ext and ext in req.allowed_types:
        score += 10
        reasons.append("type:+10")

    if req.source == "USER":
        score += 10
        reasons.append("source_user:+10")

    return score, reasons


def scan_inputs_and_bind_evidence(conn: sqlite3.Connection, *, plan_id: str, inputs_dir: Path) -> int:
    requirements = _load_requirements(conn, plan_id)
    if not requirements:
        return 0

    bound = 0
    if not inputs_dir.exists():
        return 0
    files = [p for p in inputs_dir.rglob("*") if p.is_file()]
    seen_keys: set[tuple[str, str]] = set()
    for file_path in files:
        sha = sha256_file(file_path)
        seen_keys.add((str(file_path), sha))

        # Track observed inputs for FILE_REMOVED detection.
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO input_files(
                  input_file_id, plan_id, path, sha256, size_bytes, mtime_utc, first_seen_at, last_seen_at, removed_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    str(uuid.uuid4()),
                    plan_id,
                    str(file_path),
                    sha,
                    int(file_path.stat().st_size),
                    utc_now_iso(),
                    utc_now_iso(),
                    utc_now_iso(),
                ),
            )
            conn.execute(
                """
                UPDATE input_files
                SET last_seen_at = ?, removed_at = NULL
                WHERE plan_id = ? AND path = ? AND sha256 = ?
                """,
                (utc_now_iso(), plan_id, str(file_path), sha),
            )
        except sqlite3.OperationalError:
            # input_files table may not exist if migrations haven't run yet.
            pass

        candidates: List[Tuple[int, Requirement, List[str]]] = []
        for req in requirements:
            score, reasons = _score_match(req, file_path, inputs_dir)
            if score >= 60:
                candidates.append((score, req, reasons))

        if not candidates:
            continue

        candidates.sort(key=lambda x: x[0], reverse=True)
        top_score = candidates[0][0]
        tied = [c for c in candidates if c[0] == top_score]
        if len(tied) > 1:
            emit_event(
                conn,
                plan_id=plan_id,
                event_type="EVIDENCE_CONFLICT",
                task_id=tied[0][1].task_id,
                payload={
                    "file": str(file_path),
                    "sha256": sha,
                    "score": top_score,
                    "tied_requirements": [{"requirement_id": t[1].requirement_id, "name": t[1].name} for t in tied],
                    "suggestion": "Place the file under workspace/inputs/<requirement_name>/ to disambiguate.",
                },
            )
            continue

        for score, req, reasons in candidates[:2]:
            try:
                conn.execute(
                    """
                    INSERT INTO evidences(evidence_id, requirement_id, evidence_type, ref_id, ref_path, sha256, added_at)
                    VALUES(?, ?, 'FILE', ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        req.requirement_id,
                        sha,
                        str(file_path),
                        sha,
                        utc_now_iso(),
                    ),
                )
                emit_event(
                    conn,
                    plan_id=plan_id,
                    task_id=req.task_id,
                    event_type="EVIDENCE_ADDED",
                    payload={
                        "requirement_id": req.requirement_id,
                        "requirement_name": req.name,
                        "file": str(file_path),
                        "sha256": sha,
                        "match_score": score,
                        "match_reasons": reasons,
                    },
                )
                bound += 1
            except sqlite3.IntegrityError:
                continue
    return bound


def detect_removed_input_files(conn: sqlite3.Connection, *, plan_id: str, inputs_dir: Path) -> int:
    """
    Best-effort FILE_REMOVED detection based on input_files table.
    """
    if not inputs_dir.exists():
        return 0
    try:
        rows = conn.execute(
            """
            SELECT path, sha256
            FROM input_files
            WHERE plan_id = ? AND removed_at IS NULL
            """,
            (plan_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return 0

    removed = 0
    for r in rows:
        path = Path(r["path"])
        if path.exists():
            continue
        removed += 1
        conn.execute(
            "UPDATE input_files SET removed_at = ? WHERE plan_id = ? AND path = ? AND sha256 = ?",
            (utc_now_iso(), plan_id, r["path"], r["sha256"]),
        )
        emit_event(
            conn,
            plan_id=plan_id,
            event_type="FILE_REMOVED",
            task_id=None,
            payload={"path": r["path"], "sha256": r["sha256"]},
        )
    return removed
