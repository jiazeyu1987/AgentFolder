import pytest


def _setup_db(tmp_path):
    from core.db import apply_migrations, connect
    import config

    db_path = tmp_path / "state.db"
    conn = connect(db_path)
    apply_migrations(conn, config.MIGRATIONS_DIR)
    return conn, db_path


def _insert_min_plan_and_task(conn, *, plan_id: str, task_id: str, status: str):
    conn.execute(
        "INSERT INTO plans(plan_id,title,owner_agent_id,root_task_id,created_at,constraints_json) VALUES (?,?,?,?,?,?)",
        (plan_id, "T", "xiaobo", task_id, "2026-01-01T00:00:00Z", None),
    )
    conn.execute(
        """
        INSERT INTO task_nodes(task_id,plan_id,node_type,title,owner_agent_id,status,active_branch,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (task_id, plan_id, "ACTION", "A", "xiaobo", status, 1, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )


def test_task_status_transitions_emit_events(tmp_path):
    conn, _db = _setup_db(tmp_path)
    plan_id = "p1"
    task_id = "t1"
    _insert_min_plan_and_task(conn, plan_id=plan_id, task_id=task_id, status="PENDING")
    conn.commit()

    from core.state_machine.task_status import transition_task_status

    transition_task_status(conn, plan_id=plan_id, task_id=task_id, to_status="READY", source="test")
    transition_task_status(conn, plan_id=plan_id, task_id=task_id, to_status="IN_PROGRESS", source="test")
    transition_task_status(conn, plan_id=plan_id, task_id=task_id, to_status="READY_TO_CHECK", source="test")
    transition_task_status(conn, plan_id=plan_id, task_id=task_id, to_status="DONE", source="test")
    conn.commit()

    row = conn.execute("SELECT status FROM task_nodes WHERE task_id=?", (task_id,)).fetchone()
    assert row["status"] == "DONE"

    n_task_events = int(conn.execute("SELECT COUNT(1) FROM task_events WHERE plan_id=? AND task_id=? AND event_type='STATUS_CHANGED'", (plan_id, task_id)).fetchone()[0])
    assert n_task_events >= 4
    n_wf_events = int(conn.execute("SELECT COUNT(1) FROM workflow_events WHERE plan_id=? AND task_id=? AND event_type='STATUS_CHANGED'", (plan_id, task_id)).fetchone()[0])
    assert n_wf_events >= 4


def test_task_status_more_transitions_and_illegal(tmp_path):
    conn, _db = _setup_db(tmp_path)
    plan_id = "p2"
    task_id = "t2"
    _insert_min_plan_and_task(conn, plan_id=plan_id, task_id=task_id, status="TO_BE_MODIFY")
    conn.commit()

    from core.state_machine.task_status import TaskStatusTransitionError, transition_task_status

    transition_task_status(conn, plan_id=plan_id, task_id=task_id, to_status="READY", source="test")
    transition_task_status(conn, plan_id=plan_id, task_id=task_id, to_status="IN_PROGRESS", source="test")
    transition_task_status(conn, plan_id=plan_id, task_id=task_id, to_status="FAILED", source="test")

    # Illegal: FAILED -> DONE is not allowed
    with pytest.raises(TaskStatusTransitionError):
        transition_task_status(conn, plan_id=plan_id, task_id=task_id, to_status="DONE", source="test")


def test_ready_can_transition_to_failed(tmp_path):
    conn, _db = _setup_db(tmp_path)
    plan_id = "p3"
    task_id = "t3"
    _insert_min_plan_and_task(conn, plan_id=plan_id, task_id=task_id, status="READY")
    conn.commit()

    from core.state_machine.task_status import transition_task_status

    transition_task_status(conn, plan_id=plan_id, task_id=task_id, to_status="FAILED", source="test")
    row = conn.execute("SELECT status FROM task_nodes WHERE task_id=?", (task_id,)).fetchone()
    assert row["status"] == "FAILED"
