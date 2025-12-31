from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import config
from core.db import apply_migrations, connect
from core.doctor import run_doctor
from core.events import emit_event
from core.llm_client import LLMClient
from core.plan_workflow import PlanNotApprovedError, PlanWorkflowError, _summarize_plan_review, generate_and_review_plan
from core.prompts import load_prompts, register_prompt_versions
from core.repair import repair_missing_root_tasks
from core.util import stable_hash_text, utc_now_iso
from core.contract_audit import audit_llm_calls
from core.deliverables import export_deliverables
from skills.registry import load_registry


def _print_table(rows: List[Dict[str, Any]], *, columns: List[str]) -> None:
    if not rows:
        print("(empty)")
        return
    widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in columns}
    header = "  ".join(c.ljust(widths[c]) for c in columns)
    print(header)
    print("-" * len(header))
    for r in rows:
        print("  ".join(str(r.get(c, "")).ljust(widths[c]) for c in columns))


def cmd_status(db_path: Path, plan_id: Optional[str], *, verbose: bool = False) -> int:
    conn = connect(db_path)
    apply_migrations(conn, config.MIGRATIONS_DIR)

    if plan_id is None:
        row = conn.execute("SELECT plan_id FROM plans ORDER BY created_at DESC LIMIT 1").fetchone()
        if not row:
            print("No plan found in DB.")
            return 1
        plan_id = row["plan_id"]

    plan = conn.execute("SELECT plan_id, title, root_task_id FROM plans WHERE plan_id = ?", (plan_id,)).fetchone()
    if not plan:
        print(f"Plan not found: {plan_id}")
        return 1

    rows = conn.execute(
        """
        SELECT
          n.task_id,
          n.active_branch,
          n.status,
          n.blocked_reason,
          n.attempt_count,
          n.priority,
          n.owner_agent_id,
          n.node_type,
          n.title,
          n.tags_json,
          (
            SELECT json_extract(e.payload_json, '$.error_code')
            FROM task_events e
            WHERE e.plan_id = n.plan_id AND e.task_id = n.task_id AND e.event_type = 'ERROR'
            ORDER BY e.created_at DESC
            LIMIT 1
          ) AS last_error_code,
          (
            SELECT json_extract(e.payload_json, '$.message')
            FROM task_events e
            WHERE e.plan_id = n.plan_id AND e.task_id = n.task_id AND e.event_type = 'ERROR'
            ORDER BY e.created_at DESC
            LIMIT 1
          ) AS last_error_message,
          (
            SELECT e.created_at
            FROM task_events e
            WHERE e.plan_id = n.plan_id AND e.task_id = n.task_id AND e.event_type = 'ERROR'
            ORDER BY e.created_at DESC
            LIMIT 1
          ) AS last_error_at,
          (
            SELECT c.validator_error
            FROM llm_calls c
            WHERE c.task_id = n.task_id
            ORDER BY c.created_at DESC
            LIMIT 1
          ) AS last_validator_error,
          (
            SELECT c.count
            FROM task_error_counters c
            WHERE c.plan_id = n.plan_id AND c.task_id = n.task_id AND c.key = 'WAITING_SKILL'
          ) AS waiting_skill_count
        FROM task_nodes n
        WHERE n.plan_id = ?
        ORDER BY n.priority DESC, n.task_id ASC
        """,
        (plan_id,),
    ).fetchall()
    data = [dict(r) for r in rows]

    # Add missing requirements summary for WAITING_INPUT tasks.
    for row in data:
        row["missing_requirements"] = ""
        if row.get("status") != "BLOCKED" or row.get("blocked_reason") != "WAITING_INPUT":
            continue
        reqs = conn.execute(
            "SELECT requirement_id, name, required, min_count FROM input_requirements WHERE task_id = ?",
            (row["task_id"],),
        ).fetchall()
        missing = []
        for req in reqs:
            if int(req["required"]) != 1:
                continue
            count = conn.execute("SELECT COUNT(1) AS cnt FROM evidences WHERE requirement_id = ?", (req["requirement_id"],)).fetchone()["cnt"]
            if int(count) < int(req["min_count"]):
                missing.append(f"{req['name']}({int(count)}/{int(req['min_count'])})")
        row["missing_requirements"] = ", ".join(missing[:8])
        for k in ("last_error_message", "last_validator_error"):
            v = row.get(k)
            if isinstance(v, str) and len(v) > 80:
                row[k] = v[:80] + "..."

    if verbose:
        print(f"plan_id: {plan['plan_id']}")
        print(f"title:   {plan['title']}")
        print(f"root:    {plan['root_task_id']}")
        print("")

        counts = conn.execute(
            """
            SELECT status, COUNT(1) AS cnt
            FROM task_nodes
            WHERE plan_id = ?
            GROUP BY status
            ORDER BY cnt DESC
            """,
            (plan_id,),
        ).fetchall()
        print("Status counts:")
        _print_table([dict(r) for r in counts], columns=["status", "cnt"])
        print("")

        print("Tasks:")
        _print_table(
            data,
            columns=[
                "task_id",
                "active_branch",
                "status",
                "blocked_reason",
                "missing_requirements",
                "attempt_count",
                "waiting_skill_count",
                "last_error_code",
                "last_error_message",
                "last_error_at",
                "last_validator_error",
                "priority",
                "owner_agent_id",
                "node_type",
                "title",
                "tags_json",
            ],
        )
        return 0

    # Concise mode (default): focus on why we are blocked/failed and what to do next.
    print(f"plan: {plan['title']}")
    print(f"plan_id: {plan['plan_id']}")
    print(f"root: {plan['root_task_id']}")
    print("")

    rows = data
    counts = conn.execute(
        """
        SELECT node_type, status, COUNT(1) AS cnt
        FROM task_nodes
        WHERE plan_id = ? AND active_branch = 1
        GROUP BY node_type, status
        ORDER BY node_type ASC, cnt DESC
        """,
        (plan_id,),
    ).fetchall()
    print("By node_type/status:")
    for r in counts:
        print(f"- {r['node_type']}: {r['status']} = {int(r['cnt'])}")
    print("")

    failed = [r for r in rows if r.get("status") == "FAILED"]
    blocked = [r for r in rows if r.get("status") == "BLOCKED"]
    pending = [r for r in rows if r.get("status") == "PENDING"]
    ready = [r for r in rows if r.get("status") == "READY" and r.get("node_type") == "ACTION"]

    if failed:
        print("FAILED tasks (why failed):")
        for r in failed[:20]:
            msg = (r.get("last_error_message") or r.get("last_validator_error") or "").strip()
            if len(msg) > 180:
                msg = msg[:180] + "..."
            print(f"- {r['title']} ({r['task_id']}): {r.get('last_error_code') or 'FAILED'} {msg}".strip())
        print("")

    if blocked:
        print("BLOCKED tasks (what is missing):")
        for r in blocked[:20]:
            br = r.get("blocked_reason") or "BLOCKED"
            missing = (r.get("missing_requirements") or "").strip()
            extra = f" missing: {missing}" if missing else ""
            req_path = config.REQUIRED_DOCS_DIR / f"{r['task_id']}.md"
            print(f"- {r['title']} ({r['task_id']}): {br}{extra}")

            # Always show where the user should fill inputs, even if the file isn't created yet.
            print(f"  - 填写/查看缺输入清单：{req_path}")

            # If required_docs exists, show its concrete suggested paths (most intuitive for users).
            if req_path.exists():
                try:
                    lines = req_path.read_text(encoding="utf-8", errors="replace").splitlines()
                    current_name: str | None = None
                    current_desc: str = ""
                    current_types: str = ""
                    current_suggested: str = ""
                    parsed: list[dict[str, str]] = []

                    def flush() -> None:
                        nonlocal current_name, current_desc, current_types, current_suggested
                        if current_name:
                            parsed.append(
                                {
                                    "name": current_name.strip(),
                                    "description": current_desc.strip(),
                                    "accepted_types": current_types.strip(),
                                    "suggested_path": current_suggested.strip(),
                                }
                            )
                        current_name = None
                        current_desc = ""
                        current_types = ""
                        current_suggested = ""

                    for ln in lines:
                        s = ln.rstrip()
                        if s.startswith("- ") and not s.startswith("  - "):
                            flush()
                            body = s[2:].strip()
                            if ":" in body:
                                n, d = body.split(":", 1)
                                current_name = n.strip()
                                current_desc = d.strip()
                            else:
                                current_name = body.strip()
                            continue
                        if s.strip().startswith("- accepted_types:"):
                            current_types = s.split(":", 1)[1].strip()
                            continue
                        if s.strip().startswith("- suggested_path:"):
                            current_suggested = s.split(":", 1)[1].strip()
                            continue
                    flush()

                    if parsed:
                        print("  - 需要补充：")
                        for item in parsed[:12]:
                            sp = item.get("suggested_path") or ""
                            at = item.get("accepted_types") or ""
                            nm = item.get("name") or "doc"
                            line = f"    - {nm}"
                            if sp:
                                line += f" -> 放到 {sp}"
                            if at:
                                line += f" (types={at})"
                            print(line)
                except Exception:
                    pass
            else:
                # If the file doesn't exist yet, fall back to DB counts already shown.
                if br == "WAITING_INPUT" and missing:
                    print("  - 提示：先创建上述 required_docs 文件里写的 suggested_path 对应文件后，再运行 `run`。")
                elif br == "WAITING_EXTERNAL":
                    print("  - 提示：需要外部决策/资料，查看 `errors --task-id ...` 或 UI 的 LLM Explorer。")
        print("")

    if ready:
        print("READY to run next:")
        for r in ready[:20]:
            print(f"- {r['title']} ({r['task_id']})")
        print("")

    if pending and not (ready or blocked or failed):
        # If everything is pending, it's usually either missing deps or readiness hasn't recomputed.
        print("PENDING tasks (not runnable yet):")
        for r in pending[:20]:
            # deps not done?
            dep_missing = conn.execute(
                """
                SELECT COUNT(1)
                FROM task_edges e
                JOIN task_nodes n ON n.task_id = e.from_task_id
                WHERE e.plan_id = ? AND e.to_task_id = ? AND e.edge_type='DEPENDS_ON'
                  AND (n.status IS NULL OR n.status != 'DONE')
                """,
                (plan_id, r["task_id"]),
            ).fetchone()[0]
            req_missing = 0
            try:
                reqs = conn.execute(
                    "SELECT requirement_id, required, min_count FROM input_requirements WHERE task_id=?",
                    (r["task_id"],),
                ).fetchall()
                for req in reqs:
                    if int(req["required"]) != 1:
                        continue
                    have = conn.execute("SELECT COUNT(1) FROM evidences WHERE requirement_id=?", (req["requirement_id"],)).fetchone()[0]
                    if int(have) < int(req["min_count"]):
                        req_missing += 1
            except Exception:
                req_missing = 0
            why = []
            if int(dep_missing) > 0:
                why.append(f"deps_not_done={int(dep_missing)}")
            if int(req_missing) > 0:
                why.append(f"missing_inputs={int(req_missing)}")
            print(f"- {r['title']} ({r['task_id']}): " + (", ".join(why) if why else "waiting readiness recompute"))
        print("")

    if pending and (ready or blocked or failed):
        print(f"Other pending: {len(pending)} (run loop will pick them when deps/inputs are satisfied)")

    return 0


def cmd_events(db_path: Path, plan_id: Optional[str], limit: int) -> int:
    conn = connect(db_path)
    apply_migrations(conn, config.MIGRATIONS_DIR)

    if plan_id is None:
        row = conn.execute("SELECT plan_id FROM plans ORDER BY created_at DESC LIMIT 1").fetchone()
        if not row:
            print("No plan found in DB.")
            return 1
        plan_id = row["plan_id"]

    rows = conn.execute(
        """
        SELECT created_at, event_type, task_id, payload_json
        FROM task_events
        WHERE plan_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (plan_id, int(limit)),
    ).fetchall()
    for r in rows:
        payload = {}
        try:
            payload = json.loads(r["payload_json"] or "{}")
        except Exception:
            payload = {"raw": r["payload_json"]}
        print(json.dumps({"created_at": r["created_at"], "event_type": r["event_type"], "task_id": r["task_id"], "payload": payload}, ensure_ascii=False))
    return 0


def cmd_errors(db_path: Path, plan_id: Optional[str], task_id: Optional[str], limit: int) -> int:
    conn = connect(db_path)
    apply_migrations(conn, config.MIGRATIONS_DIR)

    if plan_id is None:
        row = conn.execute("SELECT plan_id FROM plans ORDER BY created_at DESC LIMIT 1").fetchone()
        if not row:
            print("No plan found in DB.")
            return 1
        plan_id = row["plan_id"]

    params: List[Any] = [plan_id]
    where = "plan_id = ? AND event_type = 'ERROR'"
    if task_id:
        where += " AND task_id = ?"
        params.append(task_id)

    rows = conn.execute(
        f"""
        SELECT created_at, task_id, payload_json
        FROM task_events
        WHERE {where}
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (*params, int(limit)),
    ).fetchall()
    for r in rows:
        try:
            payload = json.loads(r["payload_json"] or "{}")
        except Exception:
            payload = {"raw": r["payload_json"]}
        print(json.dumps({"created_at": r["created_at"], "task_id": r["task_id"], "payload": payload}, ensure_ascii=False))

    # Also show error counters if task_id is specified.
    if task_id:
        try:
            counters = conn.execute(
                "SELECT key, count, updated_at FROM task_error_counters WHERE plan_id = ? AND task_id = ? ORDER BY key",
                (plan_id, task_id),
            ).fetchall()
            if counters:
                print("--- counters ---")
                for c in counters:
                    print(json.dumps({"key": c["key"], "count": int(c["count"]), "updated_at": c["updated_at"]}, ensure_ascii=False))
        except Exception:
            pass

    return 0


def _tail_jsonl(path: Path, limit: int) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    out: List[Dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except Exception:
            out.append({"raw": line})
    return out


def cmd_llm_log(path: Path, limit: int) -> int:
    for item in _tail_jsonl(path, limit):
        # Keep log output compact.
        err = item.get("error")
        if isinstance(err, dict):
            err = {"code": err.get("code"), "message": err.get("message")}
        print(
            json.dumps(
                {
                    "ts": item.get("ts"),
                    "plan_id": item.get("plan_id"),
                    "task_id": item.get("task_id"),
                    "agent": item.get("agent"),
                    "error": err,
                },
                ensure_ascii=False,
            )
        )
    return 0


def cmd_llm_calls(db_path: Path, plan_id: Optional[str], task_id: Optional[str], limit: int) -> int:
    conn = connect(db_path)
    apply_migrations(conn, config.MIGRATIONS_DIR)

    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='llm_calls'").fetchone()
    if not exists:
        print("llm_calls table not found (run migrations / update repo).", file=sys.stderr)
        return 2

    params: List[Any] = []
    where_parts: List[str] = []
    if plan_id:
        where_parts.append("plan_id = ?")
        params.append(plan_id)
    if task_id:
        where_parts.append("task_id = ?")
        params.append(task_id)
    where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    rows = conn.execute(
        f"""
        SELECT
          created_at,
          plan_id,
          task_id,
          agent,
          scope,
          provider,
          error_code,
          error_message,
          validator_error
        FROM llm_calls
        {where}
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (*params, int(limit)),
    ).fetchall()

    for r in rows:
        ve = r["validator_error"]
        if isinstance(ve, str) and len(ve) > 240:
            ve = ve[:240] + "..."
        em = r["error_message"]
        if isinstance(em, str) and len(em) > 240:
            em = em[:240] + "..."
        print(
            json.dumps(
                {
                    "created_at": r["created_at"],
                    "plan_id": r["plan_id"],
                    "task_id": r["task_id"],
                    "agent": r["agent"],
                    "scope": r["scope"],
                    "provider": r["provider"],
                    "error_code": r["error_code"],
                    "error_message": em,
                    "validator_error": ve,
                },
                ensure_ascii=False,
            )
        )
    return 0


def cmd_doctor(db_path: Path, plan_id: Optional[str]) -> int:
    conn = connect(db_path)
    apply_migrations(conn, config.MIGRATIONS_DIR)

    issues = run_doctor(conn, plan_id=plan_id)
    if not issues:
        print("OK")
        return 0
    for i in issues:
        print(json.dumps({"code": i.code, "message": i.message}, ensure_ascii=False))
    return 1


def cmd_repair_db(db_path: Path, plan_id: Optional[str]) -> int:
    conn = connect(db_path)
    apply_migrations(conn, config.MIGRATIONS_DIR)
    n_roots = repair_missing_root_tasks(conn, plan_id=plan_id)
    from core.repair import repair_missing_decompose_edges

    n_edges = repair_missing_decompose_edges(conn, plan_id=plan_id)
    conn.commit()
    print(json.dumps({"repaired_root_tasks": int(n_roots), "repaired_decompose_edges": int(n_edges)}, ensure_ascii=False))
    return 0


def cmd_contract_audit(db_path: Path, plan_id: Optional[str], limit: int) -> int:
    conn = connect(db_path)
    apply_migrations(conn, config.MIGRATIONS_DIR)
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='llm_calls'").fetchone()
    if not exists:
        print("llm_calls table not found.", file=sys.stderr)
        return 2

    rows, key_freq = audit_llm_calls(conn, plan_id=plan_id, limit=limit)
    for r in rows:
        print(json.dumps({"scope": r.scope, "agent": r.agent, "total": r.total, "with_error_code": r.with_error_code, "with_validator_error": r.with_validator_error}, ensure_ascii=False))
    if key_freq:
        print("--- keys_by_scope ---")
        for scope, freq in sorted(key_freq.items()):
            top = sorted(freq.items(), key=lambda x: x[1], reverse=True)[:30]
            print(json.dumps({"scope": scope, "top_keys": top}, ensure_ascii=False))
    return 0


def cmd_export(db_path: Path, plan_id: Optional[str], out_dir: Optional[Path], include_reviews: bool) -> int:
    conn = connect(db_path)
    apply_migrations(conn, config.MIGRATIONS_DIR)

    if plan_id is None:
        row = conn.execute("SELECT plan_id FROM plans ORDER BY created_at DESC LIMIT 1").fetchone()
        if not row:
            print("No plan found in DB.", file=sys.stderr)
            return 1
        plan_id = row["plan_id"]

    if out_dir is None:
        out_dir = config.DELIVERABLES_DIR / plan_id

    res = export_deliverables(conn, plan_id=plan_id, out_dir=out_dir, include_reviews=include_reviews)
    print(json.dumps({"plan_id": res.plan_id, "out_dir": str(res.out_dir), "files_copied": int(res.files_copied)}, ensure_ascii=False))
    return 0


@dataclass(frozen=True)
class PromptSlot:
    kind: str  # SHARED|AGENT
    name: str
    agent: Optional[str]
    path: Path


def _prompt_slots() -> List[PromptSlot]:
    return [
        PromptSlot(kind="SHARED", name="shared", agent=None, path=config.PROMPTS_SHARED_PATH),
        PromptSlot(kind="AGENT", name="default", agent="xiaobo", path=config.PROMPTS_AGENTS_DIR / "xiaobo_prompt.md"),
        PromptSlot(kind="AGENT", name="default", agent="xiaojing", path=config.PROMPTS_AGENTS_DIR / "xiaojing_prompt.md"),
        PromptSlot(kind="AGENT", name="default", agent="xiaoxie", path=config.PROMPTS_AGENTS_DIR / "xiaoxie_prompt.md"),
    ]


def _register_and_get_versions(conn) -> Dict[tuple[str, str, Optional[str]], Dict[str, Any]]:
    bundle = register_prompt_versions(conn, load_prompts(config.PROMPTS_SHARED_PATH, config.PROMPTS_AGENTS_DIR))
    mapping: Dict[tuple[str, str, Optional[str]], Dict[str, Any]] = {}
    # Mirror the same keys used in register_prompt_versions
    mapping[("SHARED", "shared", None)] = {"version": bundle.shared.version, "sha256": bundle.shared.sha256, "path": str(bundle.shared.path)}
    mapping[("AGENT", "default", "xiaobo")] = {"version": bundle.xiaobo.version, "sha256": bundle.xiaobo.sha256, "path": str(bundle.xiaobo.path)}
    mapping[("AGENT", "default", "xiaojing")] = {"version": bundle.xiaojing.version, "sha256": bundle.xiaojing.sha256, "path": str(bundle.xiaojing.path)}
    mapping[("AGENT", "default", "xiaoxie")] = {"version": bundle.xiaoxie.version, "sha256": bundle.xiaoxie.sha256, "path": str(bundle.xiaoxie.path)}
    return mapping


def cmd_prompt_list(db_path: Path) -> int:
    conn = connect(db_path)
    apply_migrations(conn, config.MIGRATIONS_DIR)
    versions = _register_and_get_versions(conn)

    rows: List[Dict[str, Any]] = []
    for s in _prompt_slots():
        key = (s.kind, s.name, s.agent)
        v = versions.get(key) or {}
        rows.append(
            {
                "slot": f"{s.kind}:{s.name}:{s.agent or '-'}",
                "path": str(s.path),
                "version": v.get("version"),
                "sha256": v.get("sha256"),
            }
        )
    _print_table(rows, columns=["slot", "version", "sha256", "path"])
    return 0


def cmd_prompt_show(db_path: Path, slot: str) -> int:
    conn = connect(db_path)
    apply_migrations(conn, config.MIGRATIONS_DIR)
    versions = _register_and_get_versions(conn)

    parts = slot.split(":")
    if len(parts) != 3:
        print("slot must be KIND:NAME:AGENT (agent can be '-')", file=sys.stderr)
        return 2
    kind, name, agent = parts[0], parts[1], parts[2]
    agent_val = None if agent == "-" else agent

    slot_def = next((s for s in _prompt_slots() if s.kind == kind and s.name == name and (s.agent or "-") == agent), None)
    if not slot_def:
        print(f"Unknown slot: {slot}", file=sys.stderr)
        return 2

    content = slot_def.path.read_text(encoding="utf-8")
    sha = stable_hash_text(content)
    v = versions.get((kind, name, agent_val)) or {}

    print(f"slot:   {slot}")
    print(f"path:   {slot_def.path}")
    print(f"sha256: {sha}")
    print(f"db_ver: {v.get('version')}")
    print("")
    print(content)
    return 0


def cmd_prompt_set(db_path: Path, slot: str, *, text: Optional[str], file_path: Optional[Path]) -> int:
    if bool(text) == bool(file_path):
        print("Provide exactly one of --text or --file", file=sys.stderr)
        return 2
    new_content = text if text is not None else file_path.read_text(encoding="utf-8")

    parts = slot.split(":")
    if len(parts) != 3:
        print("slot must be KIND:NAME:AGENT (agent can be '-')", file=sys.stderr)
        return 2
    kind, name, agent = parts[0], parts[1], parts[2]

    slot_def = next((s for s in _prompt_slots() if s.kind == kind and s.name == name and (s.agent or "-") == agent), None)
    if not slot_def:
        print(f"Unknown slot: {slot}", file=sys.stderr)
        return 2

    slot_def.path.write_text(new_content, encoding="utf-8")

    conn = connect(db_path)
    apply_migrations(conn, config.MIGRATIONS_DIR)
    versions = _register_and_get_versions(conn)
    key = (kind, name, None if agent == "-" else agent)
    v = versions.get(key) or {}
    print(f"Updated {slot_def.path}")
    print(f"new_version: {v.get('version')}")
    print(f"new_sha256:  {v.get('sha256')}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="agent_cli", description="CLI helpers for the workflow agent.")
    parser.add_argument("--db", type=Path, default=config.DB_PATH_DEFAULT)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="Run the main loop (same as run.py)")
    p_run.add_argument("--plan", type=Path, default=config.PLAN_PATH_DEFAULT)
    p_run.add_argument("--max-iterations", type=int, default=10_000)

    p_create = sub.add_parser("create-plan", help="Generate tasks/plan.json from a top task and approve it (xiaojing>=90).")
    p_create.add_argument("--top-task", type=str, default=None, help="Top task text")
    p_create.add_argument("--top-task-file", type=Path, default=None, help="Read top task text from file")
    p_create.add_argument("--priority", type=str, default="HIGH", choices=["LOW", "MED", "HIGH"])
    p_create.add_argument("--deadline", type=str, default=None, help="ISO8601 deadline or null")
    p_create.add_argument("--max-attempts", type=int, default=3)
    p_create.add_argument("--keep-trying", action="store_true", help="Keep retrying after max-attempts until max-total-attempts.")
    p_create.add_argument("--max-total-attempts", type=int, default=None, help="Total attempts cap when --keep-trying is set.")
    p_create.add_argument("--out", type=Path, default=config.PLAN_PATH_DEFAULT)

    p_status = sub.add_parser("status", help="Show plan/task status from state.db")
    p_status.add_argument("--plan-id", type=str, default=None)
    p_status.add_argument("--verbose", action="store_true", help="Show full table output")

    p_events = sub.add_parser("events", help="Show recent task events (JSON)")
    p_events.add_argument("--plan-id", type=str, default=None)
    p_events.add_argument("--limit", type=int, default=50)

    p_errors = sub.add_parser("errors", help="Show recent ERROR events (JSON) and counters for a task")
    p_errors.add_argument("--plan-id", type=str, default=None)
    p_errors.add_argument("--task-id", type=str, default=None)
    p_errors.add_argument("--limit", type=int, default=50)

    p_llm = sub.add_parser("llm-log", help="Show recent LLM runs (compact)")
    p_llm.add_argument("--path", type=Path, default=config.LLM_RUNS_LOG_PATH)
    p_llm.add_argument("--limit", type=int, default=20)

    p_llm_calls = sub.add_parser("llm-calls", help="Show recent LLM calls from DB (includes validator errors).")
    p_llm_calls.add_argument("--plan-id", type=str, default=None)
    p_llm_calls.add_argument("--task-id", type=str, default=None)
    p_llm_calls.add_argument("--limit", type=int, default=50)

    p_doctor = sub.add_parser("doctor", help="Self-check DB schema + basic integrity.")
    p_doctor.add_argument("--plan-id", type=str, default=None)

    p_repair = sub.add_parser("repair-db", help="Repair common DB integrity issues (safe).")
    p_repair.add_argument("--plan-id", type=str, default=None)

    p_audit = sub.add_parser("contract-audit", help="Audit recent llm_calls for contract drift (scopes/keys/errors).")
    p_audit.add_argument("--plan-id", type=str, default=None)
    p_audit.add_argument("--limit", type=int, default=200)

    p_export = sub.add_parser("export", help="Export final deliverables for a plan into one folder.")
    p_export.add_argument("--plan-id", type=str, default=None)
    p_export.add_argument("--out-dir", type=Path, default=None)
    p_export.add_argument("--include-reviews", action="store_true")

    p_prompt = sub.add_parser("prompt", help="Manage shared/agent prompts (versioned)")
    p_prompt_sub = p_prompt.add_subparsers(dest="prompt_cmd", required=True)
    p_prompt_list = p_prompt_sub.add_parser("list", help="List prompt slots and versions")
    p_prompt_show = p_prompt_sub.add_parser("show", help="Show a prompt slot content")
    p_prompt_show.add_argument("slot", type=str, help="KIND:NAME:AGENT (agent can be '-')")
    p_prompt_set = p_prompt_sub.add_parser("set", help="Set a prompt slot content (writes file + bumps version)")
    p_prompt_set.add_argument("slot", type=str, help="KIND:NAME:AGENT (agent can be '-')")
    p_prompt_set.add_argument("--text", type=str, default=None)
    p_prompt_set.add_argument("--file", type=Path, default=None)

    p_reset = sub.add_parser("reset-failed", help="Reset FAILED tasks to READY for a plan (re-run after fixing prompts/config).")
    p_reset.add_argument("--plan-id", type=str, default=None, help="Plan id (defaults to current plan in tasks/plan.json)")
    p_reset.add_argument("--include-blocked", action="store_true", help="Also reset BLOCKED tasks to READY")
    p_reset.add_argument("--reset-attempts", action="store_true", help="Also reset attempt_count to 0")

    p_reset_db = sub.add_parser("reset-db", help="Delete ALL state.db data (removes the DB file).")
    p_reset_db.add_argument("--purge-workspace", action="store_true", help="Also delete workspace/* contents (inputs/artifacts/reviews/required_docs).")
    p_reset_db.add_argument("--purge-tasks", action="store_true", help="Also delete tasks/* contents (e.g. tasks/plan.json).")
    p_reset_db.add_argument("--purge-logs", action="store_true", help="Also delete logs/* contents (e.g. logs/llm_runs.jsonl).")

    args = parser.parse_args(argv)

    if args.cmd == "run":
        import run as run_mod

        return run_mod.main(["--plan", str(args.plan), "--db", str(args.db), "--max-iterations", str(args.max_iterations)])
    if args.cmd == "create-plan":
        if bool(args.top_task) == bool(args.top_task_file):
            print("Provide exactly one of --top-task or --top-task-file", file=sys.stderr)
            return 2
        top_task = args.top_task if args.top_task else args.top_task_file.read_text(encoding="utf-8")
        constraints = {"deadline": args.deadline, "priority": args.priority}

        conn = connect(args.db)
        apply_migrations(conn, config.MIGRATIONS_DIR)
        prompts = register_prompt_versions(conn, load_prompts(config.PROMPTS_SHARED_PATH, config.PROMPTS_AGENTS_DIR))
        skills = load_registry(config.SKILLS_REGISTRY_PATH)
        llm = LLMClient()

        try:
            res = generate_and_review_plan(
                conn,
                prompts=prompts,
                llm=llm,
                top_task=top_task,
                constraints=constraints,
                available_skills=sorted(skills.keys()),
                max_plan_attempts=int(args.max_attempts),
                keep_trying=bool(getattr(args, "keep_trying", False)),
                max_total_attempts=getattr(args, "max_total_attempts", None),
                plan_output_path=args.out,
            )
            print(f"Approved plan written to: {res.plan_path}")
            print(f"plan_id: {res.plan_json['plan']['plan_id']}")
            print(f"score:   {res.review_json.get('total_score')}")
            return 0
        except PlanNotApprovedError as exc:
            plan_id = exc.plan_id or "(unknown)"
            print(_summarize_plan_review(exc.last_review))
            print(f"plan_id: {plan_id}")
            print(f"max_attempts: {exc.max_attempts}")
            return 1
        except PlanWorkflowError as exc:
            print(f"create-plan 失败：{exc}", file=sys.stderr)
            print("建议：打开 UI 的 LLM Explorer 查看 PLAN_GEN/PLAN_REVIEW 的输入输出，或运行 `agent_cli.py llm-calls --limit 50`。", file=sys.stderr)
            return 1
    if args.cmd == "status":
        return cmd_status(args.db, args.plan_id, verbose=bool(getattr(args, "verbose", False)))
    if args.cmd == "events":
        return cmd_events(args.db, args.plan_id, args.limit)
    if args.cmd == "errors":
        return cmd_errors(args.db, args.plan_id, args.task_id, args.limit)
    if args.cmd == "llm-log":
        return cmd_llm_log(args.path, args.limit)
    if args.cmd == "llm-calls":
        return cmd_llm_calls(args.db, args.plan_id, args.task_id, args.limit)
    if args.cmd == "doctor":
        return cmd_doctor(args.db, args.plan_id)
    if args.cmd == "repair-db":
        return cmd_repair_db(args.db, args.plan_id)
    if args.cmd == "contract-audit":
        return cmd_contract_audit(args.db, args.plan_id, args.limit)
    if args.cmd == "export":
        return cmd_export(args.db, args.plan_id, args.out_dir, bool(getattr(args, "include_reviews", False)))
    if args.cmd == "prompt":
        if args.prompt_cmd == "list":
            return cmd_prompt_list(args.db)
        if args.prompt_cmd == "show":
            return cmd_prompt_show(args.db, args.slot)
        if args.prompt_cmd == "set":
            return cmd_prompt_set(args.db, args.slot, text=args.text, file_path=args.file)
        return 2
    if args.cmd == "reset-failed":
        conn = connect(args.db)
        apply_migrations(conn, config.MIGRATIONS_DIR)
        plan_id = args.plan_id
        if not plan_id:
            # Best-effort: read from tasks/plan.json
            try:
                import json as _json

                plan_id = _json.loads(config.PLAN_PATH_DEFAULT.read_text(encoding="utf-8"))["plan"]["plan_id"]
            except Exception:
                plan_id = None
        if not plan_id:
            print("Provide --plan-id or ensure tasks/plan.json exists", file=sys.stderr)
            return 2
        statuses = ["FAILED"]
        if bool(getattr(args, "include_blocked", False)):
            statuses.append("BLOCKED")
        placeholders = ",".join(["?"] * len(statuses))
        rows = conn.execute(
            f"SELECT task_id, status FROM task_nodes WHERE plan_id=? AND active_branch=1 AND status IN ({placeholders})",
            (plan_id, *statuses),
        ).fetchall()
        for r in rows:
            if bool(getattr(args, "reset_attempts", False)):
                conn.execute(
                    "UPDATE task_nodes SET status='READY', blocked_reason=NULL, attempt_count=0, updated_at=? WHERE task_id=?",
                    (utc_now_iso(), r["task_id"]),
                )
            else:
                conn.execute(
                    "UPDATE task_nodes SET status='READY', blocked_reason=NULL, updated_at=? WHERE task_id=?",
                    (utc_now_iso(), r["task_id"]),
                )
            emit_event(conn, plan_id=plan_id, task_id=r["task_id"], event_type="STATUS_CHANGED", payload={"status": "READY", "blocked_reason": None})
        conn.commit()
        print(f"reset_failed: {len(rows)}")
        return 0
    if args.cmd == "reset-db":
        db_path = Path(args.db)
        wal_path = Path(str(db_path) + "-wal")
        shm_path = Path(str(db_path) + "-shm")

        deleted: List[str] = []
        for p in (wal_path, shm_path, db_path):
            try:
                if p.exists():
                    os.remove(str(p))
                    deleted.append(str(p))
            except Exception as exc:  # noqa: BLE001
                print(f"Failed to delete {p}: {type(exc).__name__}: {exc}", file=sys.stderr)
                return 1

        def purge_dir_contents(dir_path: Path) -> None:
            if not dir_path.exists() or not dir_path.is_dir():
                return
            for child in dir_path.iterdir():
                try:
                    if child.is_dir():
                        shutil.rmtree(child, ignore_errors=True)
                    else:
                        try:
                            child.unlink()
                        except FileNotFoundError:
                            pass
                except Exception:
                    pass

        if bool(getattr(args, "purge_workspace", False)):
            purge_dir_contents(config.WORKSPACE_DIR)
            deleted.append(str(config.WORKSPACE_DIR / "*"))
            for p in (config.INPUTS_DIR, config.ARTIFACTS_DIR, config.REVIEWS_DIR, config.REQUIRED_DOCS_DIR):
                try:
                    p.mkdir(parents=True, exist_ok=True)
                except Exception:
                    pass
        if bool(getattr(args, "purge_tasks", False)):
            purge_dir_contents(config.TASKS_DIR)
            deleted.append(str(config.TASKS_DIR / "*"))
            try:
                config.TASKS_DIR.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
        if bool(getattr(args, "purge_logs", False)):
            purge_dir_contents(config.LOGS_DIR)
            deleted.append(str(config.LOGS_DIR / "*"))
            try:
                config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass

        print(json.dumps({"deleted": deleted}, ensure_ascii=False))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
