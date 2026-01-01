from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import config
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


def _score_match(req: Requirement, file_path: Path, inputs_dir: Path, *, allow_name_in_filename: bool) -> Tuple[int, List[str]]:
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
    if allow_name_in_filename and req.name and req.name.lower() in filename:
        score += 70
        reasons.append("name_in_filename:+70")

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
    return scan_inputs_and_bind_evidence_all(conn, plan_id=plan_id, inputs_dirs=[inputs_dir])


def _mtime_utc_iso(file_path: Path) -> str:
    try:
        ts = float(file_path.stat().st_mtime)
    except Exception:
        ts = 0.0
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _load_input_file_cache(conn: sqlite3.Connection, *, plan_id: str) -> Dict[str, Dict[str, Any]]:
    """
    Cache latest observed info per path to avoid hashing unchanged files every loop.
    """
    try:
        rows = conn.execute(
            """
            SELECT path, sha256, size_bytes, mtime_utc, last_seen_at
            FROM input_files
            WHERE plan_id = ?
            """,
            (plan_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    by_path: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        p = str(r["path"] or "")
        if not p:
            continue
        prev = by_path.get(p)
        if prev is None or str(r["last_seen_at"] or "") > str(prev.get("last_seen_at") or ""):
            by_path[p] = {
                "sha256": str(r["sha256"] or ""),
                "size_bytes": int(r["size_bytes"] or 0),
                "mtime_utc": str(r["mtime_utc"] or ""),
                "last_seen_at": str(r["last_seen_at"] or ""),
            }
    return by_path


def _allowed_exts(requirements: List[Requirement]) -> set[str]:
    exts: set[str] = set()
    for r in requirements:
        for t in r.allowed_types or []:
            if not isinstance(t, str):
                continue
            tt = t.strip().lower().lstrip(".")
            if tt:
                exts.add(tt)
    return exts


def scan_inputs_and_bind_evidence_all(conn: sqlite3.Connection, *, plan_id: str, inputs_dirs: List[Path]) -> int:
    requirements = _load_requirements(conn, plan_id)
    if not requirements:
        return 0

    file_cache = _load_input_file_cache(conn, plan_id=plan_id)
    allowed_exts = _allowed_exts(requirements)

    bound = 0
    for inputs_dir in inputs_dirs:
        if not inputs_dir.exists():
            continue
        allow_name_in_filename = inputs_dir.name.lower() == "baseline_inputs"
        max_files = int(config.BASELINE_SCAN_MAX_FILES) if allow_name_in_filename else 0
        max_bytes = int(config.BASELINE_SCAN_MAX_TOTAL_BYTES) if allow_name_in_filename else 0
        total_bytes = 0
        skipped = 0

        files: List[Path] = []
        for p in inputs_dir.rglob("*"):
            if not p.is_file():
                continue
            ext = p.suffix.lower().lstrip(".")
            if allowed_exts and ext and ext not in allowed_exts:
                continue
            if allow_name_in_filename:
                if max_files and len(files) >= max_files:
                    skipped += 1
                    continue
                try:
                    sz = int(p.stat().st_size)
                except Exception:
                    sz = 0
                if max_bytes and (total_bytes + sz) > max_bytes:
                    skipped += 1
                    continue
                total_bytes += sz
            files.append(p)

        if allow_name_in_filename and skipped:
            emit_event(
                conn,
                plan_id=plan_id,
                event_type="BASELINE_INPUTS_SKIPPED",
                task_id=None,
                payload={
                    "baseline_dir": str(inputs_dir),
                    "kept_files": len(files),
                    "skipped_files": skipped,
                    "max_files": max_files,
                    "max_total_bytes": max_bytes,
                    "hint": "baseline_inputs is large; consider moving project-specific files to workspace/inputs/<requirement_name>/ or curating baseline_inputs.",
                },
            )

        seen_keys: set[tuple[str, str]] = set()
        for file_path in files:
            path_str = str(file_path)
            mtime_utc = _mtime_utc_iso(file_path)
            try:
                size_bytes = int(file_path.stat().st_size)
            except Exception:
                size_bytes = 0

            cached = file_cache.get(path_str)
            if cached and cached.get("mtime_utc") == mtime_utc and int(cached.get("size_bytes") or 0) == size_bytes and str(cached.get("sha256") or ""):
                sha = str(cached["sha256"])
            else:
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
                        path_str,
                        sha,
                        size_bytes,
                        mtime_utc,
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
                    (utc_now_iso(), plan_id, path_str, sha),
                )
            except sqlite3.OperationalError:
                # input_files table may not exist if migrations haven't run yet.
                pass

            candidates: List[Tuple[int, Requirement, List[str]]] = []
            for req in requirements:
                score, reasons = _score_match(req, file_path, inputs_dir, allow_name_in_filename=allow_name_in_filename)
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
                        "file": path_str,
                        "sha256": sha,
                        "match_score": score,
                        "match_reasons": reasons,
                        "inputs_dir": str(inputs_dir),
                    },
                )
                    bound += 1
                except sqlite3.IntegrityError:
                    continue
    return bound


def detect_removed_input_files(conn: sqlite3.Connection, *, plan_id: str, inputs_dir: Path) -> int:
    return detect_removed_input_files_all(conn, plan_id=plan_id, inputs_dirs=[inputs_dir])


def detect_removed_input_files_all(conn: sqlite3.Connection, *, plan_id: str, inputs_dirs: List[Path]) -> int:
    """
    Best-effort FILE_REMOVED detection based on input_files table.
    """
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
        # Only consider files under the scanned inputs dirs.
        in_scope = False
        for d in inputs_dirs:
            try:
                path.resolve().relative_to(d.resolve())
                in_scope = True
                break
            except Exception:
                continue
        if not in_scope:
            continue
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
