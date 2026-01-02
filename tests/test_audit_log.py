import json
from pathlib import Path

import config
from core.audit_log import (
    AuditQuery,
    annotate_llm_output_for_retry,
    backfill_audit_llm_call_plan_id,
    log_audit,
    query_audit_events,
)
from core.db import apply_migrations, connect
from core.llm_calls import record_llm_call


def _insert_plan(conn, *, plan_id: str, title: str) -> None:
    conn.execute(
        """
        INSERT INTO plans(plan_id, title, owner_agent_id, root_task_id, created_at, constraints_json)
        VALUES(?, ?, ?, ?, ?, ?)
        """,
        (plan_id, title, "xiaobo", "root_task", "2026-01-01T00:00:00Z", None),
    )


def test_audit_log_write_and_query(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    conn = connect(db_path)
    apply_migrations(conn, config.MIGRATIONS_DIR)
    _insert_plan(conn, plan_id="p1", title="Top Task A")

    # Direct audit writes.
    log_audit(conn, category="API_CALL", action="X", message="m1", plan_id="p1")
    log_audit(conn, category="API_CALL", action="Y", message="m2", plan_id="p1")
    conn.commit()

    items = query_audit_events(conn, AuditQuery(plan_id="p1", limit=50))
    assert len(items) >= 2
    assert items[0]["created_at"] >= items[1]["created_at"]
    assert items[0]["category"] == "API_CALL"


def test_record_llm_call_emits_llm_input_output_audit(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    conn = connect(db_path)
    apply_migrations(conn, config.MIGRATIONS_DIR)
    _insert_plan(conn, plan_id="p2", title="Top Task B")

    llm_call_id = record_llm_call(
        conn,
        plan_id="p2",
        task_id=None,
        agent="xiaobo",
        scope="PLAN_GEN",
        provider="demo",
        prompt_text="P",
        response_text="R",
        parsed_json={"x": 1},
        normalized_json=None,
        validator_error=None,
        error_code=None,
        error_message=None,
        meta={"attempt": 1},
    )
    assert llm_call_id != "UNKNOWN"
    conn.commit()

    rows = query_audit_events(conn, AuditQuery(plan_id="p2", limit=50))
    cats = [r["category"] for r in rows]
    assert "LLM_INPUT" in cats
    assert "LLM_OUTPUT" in cats
    # Ensure the audit entries reference the llm_call_id.
    assert any(r.get("llm_call_id") == llm_call_id for r in rows)


def test_backfill_audit_for_plan_gen_without_plan_id(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    conn = connect(db_path)
    apply_migrations(conn, config.MIGRATIONS_DIR)
    _insert_plan(conn, plan_id="p3", title="Top Task C")

    # Simulate create-plan PLAN_GEN: plan_id unknown at LLM call record time.
    llm_call_id = record_llm_call(
        conn,
        plan_id=None,
        task_id=None,
        agent="xiaobo",
        scope="PLAN_GEN",
        provider="demo",
        prompt_text="P",
        response_text="R",
        parsed_json={"x": 1},
        normalized_json=None,
        validator_error=None,
        error_code=None,
        error_message=None,
        meta={"attempt": 1},
    )
    conn.commit()

    # Before backfill: querying by plan_id should not include the PLAN_GEN audit rows.
    rows0 = query_audit_events(conn, AuditQuery(plan_id="p3", limit=50))
    assert not any(r.get("llm_call_id") == llm_call_id for r in rows0)

    # Backfill like plan_workflow does after plan_id is known.
    backfill_audit_llm_call_plan_id(conn, llm_call_id=llm_call_id, plan_id="p3")
    conn.commit()

    rows1 = query_audit_events(conn, AuditQuery(plan_id="p3", limit=50))
    assert any(r.get("llm_call_id") == llm_call_id for r in rows1)


def test_annotate_llm_output_for_retry_updates_audit_payload(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    conn = connect(db_path)
    apply_migrations(conn, config.MIGRATIONS_DIR)
    _insert_plan(conn, plan_id="p4", title="Top Task D")

    llm_call_id = record_llm_call(
        conn,
        plan_id="p4",
        task_id=None,
        agent="xiaojing",
        scope="PLAN_REVIEW",
        provider="demo",
        prompt_text="P",
        response_text="R",
        parsed_json={"x": 1},
        normalized_json=None,
        validator_error=None,
        error_code=None,
        error_message=None,
        meta={"attempt": 1, "review_attempt": 1},
    )
    annotate_llm_output_for_retry(conn, llm_call_id=llm_call_id, retry_kind="CONTRACT_MISMATCH", retry_reason="schema_version mismatch")
    conn.commit()

    rows = query_audit_events(conn, AuditQuery(plan_id="p4", limit=50))
    out = [r for r in rows if r.get("llm_call_id") == llm_call_id and r.get("category") == "LLM_OUTPUT"]
    assert out, "expected LLM_OUTPUT audit row"
    payload = json.loads(out[0]["payload_json"] or "{}")
    assert payload.get("retry_kind") == "CONTRACT_MISMATCH"
    assert "schema_version" in (payload.get("retry_reason") or "")
