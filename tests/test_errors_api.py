import json
from pathlib import Path

import config
from core.db import apply_migrations, connect
from dashboard_backend.app import query_plan_errors


def _insert_plan(conn, *, plan_id: str, title: str) -> None:
    conn.execute(
        """
        INSERT INTO plans(plan_id, title, owner_agent_id, root_task_id, created_at, constraints_json)
        VALUES(?, ?, ?, ?, ?, ?)
        """,
        (plan_id, title, "xiaobo", "root_task", "2026-01-01T00:00:00Z", None),
    )


def _insert_task(conn, *, task_id: str, plan_id: str, title: str) -> None:
    conn.execute(
        """
        INSERT INTO task_nodes(
          task_id, plan_id, node_type, title, goal_statement, rationale,
          owner_agent_id, priority, status, blocked_reason, attempt_count,
          confidence, active_branch, active_artifact_id, created_at, updated_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task_id,
            plan_id,
            "ACTION",
            title,
            None,
            None,
            "xiaobo",
            0,
            "PENDING",
            None,
            0,
            0.5,
            1,
            None,
            "2026-01-01T00:00:00Z",
            "2026-01-01T00:00:00Z",
        ),
    )


def test_query_plan_errors_merges_task_events_and_llm_calls(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    conn = connect(db_path)
    apply_migrations(conn, config.MIGRATIONS_DIR)

    plan_id = "plan_err_1"
    task_id = "task_err_1"
    _insert_plan(conn, plan_id=plan_id, title="Err Plan")
    _insert_task(conn, task_id=task_id, plan_id=plan_id, title="Broken Task")

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
                    "error_code": "INPUT_MISSING",
                    "message": "Missing required input(s).",
                    "context": {"hint": "Create required_docs file"},
                },
                ensure_ascii=False,
            ),
            "2026-01-01T00:00:02Z",
        ),
    )
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
            "c1",
            "2026-01-01T00:00:03Z",
            plan_id,
            task_id,
            "xiaojing",
            "PLAN_REVIEW",
            "P",
            "R",
            None,
            None,
            "schema_version mismatch",
            "CONTRACT_MISMATCH",
            None,
            json.dumps({"attempt": 1, "review_attempt": 2}, ensure_ascii=False),
        ),
    )
    conn.commit()

    items = query_plan_errors(conn, plan_id=plan_id, plan_id_missing=False, limit=50)
    assert len(items) >= 2
    assert items[0]["created_at"] >= items[1]["created_at"]  # sorted desc

    sources = {i["source"] for i in items}
    assert "TASK_EVENT" in sources
    assert "LLM_CALL" in sources

    ev = next(i for i in items if i["source"] == "TASK_EVENT")
    assert ev["task_title"] == "Broken Task"
    assert ev["error_code"] == "INPUT_MISSING"
    assert "required" in (ev["message"] or "").lower()

    lc = next(i for i in items if i["source"] == "LLM_CALL")
    assert lc["llm_call_id"] == "c1"
    assert lc["agent"] == "xiaojing"
    assert lc["scope"] == "PLAN_REVIEW"
    assert lc["error_code"] in {"CONTRACT_MISMATCH", "VALIDATOR_ERROR"}


def test_query_plan_errors_can_filter_by_task_id(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    conn = connect(db_path)
    apply_migrations(conn, config.MIGRATIONS_DIR)

    plan_id = "plan_err_2"
    task_id1 = "task_err_a"
    task_id2 = "task_err_b"
    _insert_plan(conn, plan_id=plan_id, title="Err Plan")
    _insert_task(conn, task_id=task_id1, plan_id=plan_id, title="A")
    _insert_task(conn, task_id=task_id2, plan_id=plan_id, title="B")

    conn.execute(
        "INSERT INTO task_events(event_id, plan_id, task_id, event_type, payload_json, created_at) VALUES(?, ?, ?, 'ERROR', ?, ?)",
        ("evtA", plan_id, task_id1, json.dumps({"error_code": "E1", "message": "m1"}, ensure_ascii=False), "2026-01-01T00:00:02Z"),
    )
    conn.execute(
        "INSERT INTO task_events(event_id, plan_id, task_id, event_type, payload_json, created_at) VALUES(?, ?, ?, 'ERROR', ?, ?)",
        ("evtB", plan_id, task_id2, json.dumps({"error_code": "E2", "message": "m2"}, ensure_ascii=False), "2026-01-01T00:00:03Z"),
    )
    conn.commit()

    items = query_plan_errors(conn, plan_id=plan_id, plan_id_missing=False, task_id=task_id1, limit=50)
    assert items
    assert all(i.get("task_id") == task_id1 for i in items)


def test_query_plan_errors_include_related_check_nodes(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    conn = connect(db_path)
    apply_migrations(conn, config.MIGRATIONS_DIR)

    plan_id = "plan_err_3"
    action_id = "task_action"
    check_id = "task_check"
    _insert_plan(conn, plan_id=plan_id, title="Err Plan")
    _insert_task(conn, task_id=action_id, plan_id=plan_id, title="ACTION")

    # v2: CHECK bound to ACTION by review_target_task_id
    conn.execute(
        """
        INSERT INTO task_nodes(
          task_id, plan_id, node_type, title, goal_statement, rationale,
          owner_agent_id, priority, status, blocked_reason, attempt_count,
          confidence, active_branch, active_artifact_id, created_at, updated_at,
          review_target_task_id
        )
        VALUES(?, ?, 'CHECK', ?, NULL, NULL, 'xiaojing', 0, 'FAILED', NULL, 0, 0.5, 1, NULL, ?, ?, ?)
        """,
        (check_id, plan_id, "CHECK", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", action_id),
    )

    conn.execute(
        "INSERT INTO task_events(event_id, plan_id, task_id, event_type, payload_json, created_at) VALUES(?, ?, ?, 'ERROR', ?, ?)",
        ("evtC", plan_id, check_id, json.dumps({"error_code": "E_CHECK", "message": "bad review"}, ensure_ascii=False), "2026-01-01T00:00:03Z"),
    )
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
            "c2",
            "2026-01-01T00:00:04Z",
            plan_id,
            check_id,
            "xiaojing",
            "TASK_CHECK",
            "P",
            "R",
            None,
            None,
            "missing key",
            "CONTRACT_MISMATCH",
            None,
            json.dumps({"attempt": 1}, ensure_ascii=False),
        ),
    )
    conn.commit()

    # Query by ACTION task_id but include related CHECK nodes
    items = query_plan_errors(conn, plan_id=plan_id, plan_id_missing=False, task_id=action_id, include_related=True, limit=50)
    assert items
    # Related CHECK task should appear
    assert any(i.get("task_id") == check_id for i in items)
