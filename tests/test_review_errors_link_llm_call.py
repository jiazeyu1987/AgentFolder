import json
from pathlib import Path

import config
from core.db import apply_migrations, connect
from dashboard_backend.app import query_plan_errors


def test_task_event_error_can_link_to_specific_llm_call(tmp_path: Path) -> None:
    # This verifies the UI can show per-review failure reasons instead of a merged blob.
    db_path = tmp_path / "t.db"
    conn = connect(db_path)
    apply_migrations(conn, config.MIGRATIONS_DIR)

    plan_id = "p_link"
    task_id = "check1"
    conn.execute(
        "INSERT INTO plans(plan_id, title, owner_agent_id, root_task_id, created_at, constraints_json) VALUES(?, ?, ?, ?, ?, ?)",
        (plan_id, "T", "xiaobo", "root", "2026-01-01T00:00:00Z", "{}"),
    )
    conn.execute(
        """
        INSERT INTO task_nodes(
          task_id, plan_id, node_type, title, owner_agent_id,
          status, created_at, updated_at
        ) VALUES(?, ?, 'CHECK', 'Review', 'xiaojing', 'READY', ?, ?)
        """,
        (task_id, plan_id, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )

    llm_call_id = "c_link_1"
    # Simulate a reviewer call that produced a contract mismatch.
    conn.execute(
        """
        INSERT INTO llm_calls(
          llm_call_id, created_at, plan_id, task_id, agent, scope,
          prompt_text, response_text, parsed_json, normalized_json,
          validator_error, error_code, error_message, meta_json
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            llm_call_id,
            "2026-01-01T00:00:10Z",
            plan_id,
            task_id,
            "xiaojing",
            "TASK_REVIEW",
            "P",
            "R",
            None,
            None,
            "schema mismatch",
            "VALIDATOR_ERROR",
            None,
            json.dumps({"attempt": 1}, ensure_ascii=False),
        ),
    )
    # Simulate the run loop creating an ERROR event that references that llm_call_id.
    conn.execute(
        """
        INSERT INTO task_events(event_id, plan_id, task_id, event_type, payload_json, created_at)
        VALUES(?, ?, ?, 'ERROR', ?, ?)
        """,
        (
            "evt1",
            plan_id,
            task_id,
            json.dumps(
                {
                    "error_code": "LLM_UNPARSEABLE",
                    "message": "schema mismatch",
                    "context": {"llm_call_id": llm_call_id, "agent": "xiaojing", "scope": "TASK_REVIEW"},
                },
                ensure_ascii=False,
            ),
            "2026-01-01T00:00:11Z",
        ),
    )
    conn.commit()

    items = query_plan_errors(conn, plan_id=plan_id, plan_id_missing=False, task_id=task_id, include_related=False, limit=50)
    assert any(i.get("llm_call_id") == llm_call_id for i in items), items

