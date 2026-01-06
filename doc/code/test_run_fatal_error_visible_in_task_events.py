from types import SimpleNamespace


def _setup_db(tmp_path):
    import config
    from core.db import apply_migrations, connect

    db_path = tmp_path / "state.db"
    conn = connect(db_path)
    apply_migrations(conn, config.MIGRATIONS_DIR)
    return conn


def _insert_min_plan(conn, *, plan_id: str):
    conn.execute(
        "INSERT INTO plans(plan_id,title,owner_agent_id,root_task_id,created_at,constraints_json) VALUES (?,?,?,?,?,?)",
        (plan_id, "T", "xiaobo", "root", "2026-01-01T00:00:00Z", None),
    )


def test_run_fatal_records_task_event_error_and_job_finished(tmp_path, monkeypatch):
    import run as run_mod

    conn = _setup_db(tmp_path)
    plan_id = "p1"
    _insert_min_plan(conn, plan_id=plan_id)
    conn.commit()

    run_mod._record_run_fatal(conn, plan_id=plan_id, job_id="job1", exc=RuntimeError("boom"))
    conn.commit()

    # 1) Existing error viewing mechanism: task_events ERROR
    rows = conn.execute(
        "SELECT payload_json FROM task_events WHERE plan_id=? AND event_type='ERROR' ORDER BY created_at DESC LIMIT 1",
        (plan_id,),
    ).fetchall()
    assert rows, "expected an ERROR task_event"
    payload = __import__("json").loads(rows[0][0] or "{}")
    assert payload.get("error_code") == "RUN_FATAL"
    assert "boom" in str(payload.get("message") or "")
    assert payload.get("context", {}).get("job_id") == "job1"

    # 2) Workflow timeline: JOB_FINISHED severity=ERROR
    row = conn.execute(
        "SELECT severity, message, payload_json FROM workflow_events WHERE workflow='RUN' AND event_type='JOB_FINISHED' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    assert row is not None
    assert row["severity"] == "ERROR"
    assert "crashed" in str(row["message"] or "")
    p2 = __import__("json").loads(row["payload_json"] or "{}")
    assert p2.get("why") == "RUN_FATAL"

