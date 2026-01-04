import json
from pathlib import Path

from core.db import apply_migrations, connect
from core.workflow_graph import WorkflowQuery, build_workflow
import config


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


def _insert_llm_call(
    conn,
    *,
    llm_call_id: str,
    created_at: str,
    plan_id: str,
    task_id: str,
    scope: str,
    meta: dict,
    top_task_hash: str | None = None,
    error_code: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO llm_calls(
          llm_call_id, created_at, plan_id, task_id, agent, scope, top_task_hash,
          prompt_text, response_text, parsed_json, normalized_json,
          validator_error, error_code, error_message, meta_json
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            llm_call_id,
            created_at,
            plan_id,
            task_id,
            "xiaobo" if scope == "PLAN_GEN" else "xiaojing",
            scope,
            top_task_hash,
            "PROMPT",
            "RESPONSE",
            None,
            None,
            None,
            error_code,
            None,
            json.dumps(meta, ensure_ascii=False),
        ),
    )


def test_workflow_build_attempt_pair_and_next(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    conn = connect(db_path)
    apply_migrations(conn, config.MIGRATIONS_DIR)

    plan_id = "plan_1"
    task_id = "task_1"
    _insert_plan(conn, plan_id=plan_id, title="Demo Plan")
    _insert_task(conn, task_id=task_id, plan_id=plan_id, title="Task One")

    _insert_llm_call(
        conn,
        llm_call_id="c1",
        created_at="2026-01-01T00:00:01Z",
        plan_id=plan_id,
        task_id=task_id,
        scope="PLAN_GEN",
        meta={"attempt": 2},
    )
    _insert_llm_call(
        conn,
        llm_call_id="c2",
        created_at="2026-01-01T00:00:02Z",
        plan_id=plan_id,
        task_id=task_id,
        scope="PLAN_REVIEW",
        meta={"attempt": 2, "review_attempt": 3},
    )
    conn.commit()

    wf = build_workflow(conn, WorkflowQuery(plan_id=plan_id, scopes=["PLAN_GEN", "PLAN_REVIEW"], limit=50))
    assert wf["schema_version"] == "workflow_v1"
    assert wf["total_rows"] >= 2
    assert wf["returned_rows"] == 2
    llm_nodes = [n for n in wf["nodes"] if n.get("node_kind") == "LLM_CALL"]
    assert len(llm_nodes) == 2
    assert llm_nodes[0]["attempt"] == 2
    assert llm_nodes[0]["review_attempt"] == 1
    assert llm_nodes[1]["attempt"] == 2
    assert llm_nodes[1]["review_attempt"] == 3
    assert llm_nodes[1]["task_title"] == "Task One"

    edge_types = [e["edge_type"] for e in wf["edges"]]
    assert "NEXT" in edge_types
    assert "PAIR" in edge_types
    assert "STAGE_NEXT" in edge_types


def test_workflow_only_errors_filter(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    conn = connect(db_path)
    apply_migrations(conn, config.MIGRATIONS_DIR)

    plan_id = "plan_2"
    task_id = "task_2"
    _insert_plan(conn, plan_id=plan_id, title="Err Plan")
    _insert_task(conn, task_id=task_id, plan_id=plan_id, title="Task Two")
    _insert_llm_call(
        conn,
        llm_call_id="c1",
        created_at="2026-01-01T00:00:01Z",
        plan_id=plan_id,
        task_id=task_id,
        scope="PLAN_GEN",
        meta={"attempt": 1},
        error_code="CONTRACT_MISMATCH",
    )
    _insert_llm_call(
        conn,
        llm_call_id="c2",
        created_at="2026-01-01T00:00:02Z",
        plan_id=plan_id,
        task_id=task_id,
        scope="PLAN_REVIEW",
        meta={"attempt": 1},
    )
    conn.commit()

    wf = build_workflow(conn, WorkflowQuery(plan_id=plan_id, only_errors=True, limit=50))
    assert len(wf["nodes"]) == 1
    assert wf["nodes"][0]["error_code"] == "CONTRACT_MISMATCH"


def test_workflow_started_at_includes_multiple_plan_ids(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    conn = connect(db_path)
    apply_migrations(conn, config.MIGRATIONS_DIR)

    # Two separate plans, but the same create-plan job window.
    _insert_plan(conn, plan_id="p1", title="T1")
    _insert_plan(conn, plan_id="p2", title="T2")
    _insert_task(conn, task_id="t1", plan_id="p1", title="Task One")
    _insert_task(conn, task_id="t2", plan_id="p2", title="Task Two")

    _insert_llm_call(conn, llm_call_id="c1", created_at="2026-01-01T00:00:01Z", plan_id="p1", task_id="t1", scope="PLAN_GEN", meta={"attempt": 1})
    _insert_llm_call(conn, llm_call_id="c2", created_at="2026-01-01T00:00:02Z", plan_id="p1", task_id="t1", scope="PLAN_REVIEW", meta={"attempt": 1, "review_attempt": 1})
    _insert_llm_call(conn, llm_call_id="c3", created_at="2026-01-01T00:00:03Z", plan_id="p2", task_id="t2", scope="PLAN_GEN", meta={"attempt": 2})
    _insert_llm_call(conn, llm_call_id="c4", created_at="2026-01-01T00:00:04Z", plan_id="p2", task_id="t2", scope="PLAN_REVIEW", meta={"attempt": 2, "review_attempt": 1})
    conn.commit()

    wf = build_workflow(conn, WorkflowQuery(started_at="2026-01-01T00:00:00Z", scopes=["PLAN_GEN", "PLAN_REVIEW"], limit=50))
    llm_nodes = [n for n in wf["nodes"] if n.get("node_kind") == "LLM_CALL"]
    assert len(llm_nodes) == 4
    assert {n["plan_id"] for n in llm_nodes} == {"p1", "p2"}


def test_workflow_top_task_hash_includes_multiple_plan_ids(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    conn = connect(db_path)
    apply_migrations(conn, config.MIGRATIONS_DIR)

    _insert_plan(conn, plan_id="p1", title="Same Task")
    _insert_plan(conn, plan_id="p2", title="Same Task")
    _insert_task(conn, task_id="t1", plan_id="p1", title="Task One")
    _insert_task(conn, task_id="t2", plan_id="p2", title="Task Two")

    h = "deadbeef" * 8
    _insert_llm_call(conn, llm_call_id="c1", created_at="2026-01-01T00:00:01Z", plan_id="p1", task_id="t1", scope="PLAN_GEN", meta={"attempt": 1}, top_task_hash=h)
    _insert_llm_call(conn, llm_call_id="c2", created_at="2026-01-01T00:00:02Z", plan_id="p1", task_id="t1", scope="PLAN_REVIEW", meta={"attempt": 1, "review_attempt": 1}, top_task_hash=h)
    _insert_llm_call(conn, llm_call_id="c3", created_at="2026-01-01T00:00:03Z", plan_id="p2", task_id="t2", scope="PLAN_GEN", meta={"attempt": 2}, top_task_hash=h)
    _insert_llm_call(conn, llm_call_id="c4", created_at="2026-01-01T00:00:04Z", plan_id="p2", task_id="t2", scope="PLAN_REVIEW", meta={"attempt": 2, "review_attempt": 1}, top_task_hash=h)
    conn.commit()

    wf = build_workflow(conn, WorkflowQuery(top_task_hash=h, scopes=["PLAN_GEN", "PLAN_REVIEW"], limit=50))
    llm_nodes = [n for n in wf["nodes"] if n.get("node_kind") == "LLM_CALL"]
    assert len(llm_nodes) == 4
    assert {n["plan_id"] for n in llm_nodes} == {"p1", "p2"}


def test_workflow_top_task_hash_falls_back_to_title_hash_when_llm_calls_missing_hash(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    conn = connect(db_path)
    apply_migrations(conn, config.MIGRATIONS_DIR)

    # Two plans share the same normalized title, but llm_calls.top_task_hash is NULL (legacy).
    _insert_plan(conn, plan_id="p1", title="Same Title (abc123)")
    _insert_plan(conn, plan_id="p2", title="Same Title (def456)")
    _insert_task(conn, task_id="t1", plan_id="p1", title="Task One")
    _insert_task(conn, task_id="t2", plan_id="p2", title="Task Two")

    _insert_llm_call(conn, llm_call_id="c1", created_at="2026-01-01T00:00:01Z", plan_id="p1", task_id="t1", scope="PLAN_GEN", meta={"attempt": 1}, top_task_hash=None)
    _insert_llm_call(conn, llm_call_id="c2", created_at="2026-01-01T00:00:02Z", plan_id="p1", task_id="t1", scope="PLAN_REVIEW", meta={"attempt": 1, "review_attempt": 1}, top_task_hash=None)
    _insert_llm_call(conn, llm_call_id="c3", created_at="2026-01-01T00:00:03Z", plan_id="p2", task_id="t2", scope="PLAN_GEN", meta={"attempt": 2}, top_task_hash=None)
    _insert_llm_call(conn, llm_call_id="c4", created_at="2026-01-01T00:00:04Z", plan_id="p2", task_id="t2", scope="PLAN_REVIEW", meta={"attempt": 2, "review_attempt": 1}, top_task_hash=None)
    conn.commit()

    # The grouping key is the stable hash of normalized title "Same Title".
    from core.util import stable_hash_text, normalize_title

    h = stable_hash_text(normalize_title("Same Title"))
    wf = build_workflow(conn, WorkflowQuery(top_task_hash=h, scopes=["PLAN_GEN", "PLAN_REVIEW"], limit=50))
    llm_nodes = [n for n in wf["nodes"] if n.get("node_kind") == "LLM_CALL"]
    assert len(llm_nodes) == 4
    assert {n["plan_id"] for n in llm_nodes} == {"p1", "p2"}
