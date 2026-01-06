import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _setup_db(tmp_path: Path) -> Path:
    import config
    from core.db import apply_migrations, connect

    db_path = tmp_path / "state.db"
    conn = connect(db_path)
    apply_migrations(conn, config.MIGRATIONS_DIR)
    conn.close()
    return db_path


def _insert_min_plan(conn, *, plan_id: str):
    conn.execute(
        "INSERT INTO plans(plan_id,title,owner_agent_id,root_task_id,created_at,constraints_json) VALUES (?,?,?,?,?,?)",
        (plan_id, "T", "xiaobo", "root", "2026-01-01T00:00:00Z", None),
    )


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    import dashboard_backend.app as app_mod
    import dashboard_backend.app_helpers as h

    monkeypatch.setattr(h, "RUN_STATE_PATH", tmp_path / "run_process.json")
    return TestClient(app_mod.app)


def test_run_status_no_state_file(client: TestClient):
    res = client.get("/api/run/status")
    assert res.status_code == 200
    body = res.json()
    assert body["alive"] is False
    assert body["pid"] is None
    assert body["reason"] == "NO_STATE_FILE"


def test_run_status_process_dead_cleans_state_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import dashboard_backend.app as app_mod
    import dashboard_backend.app_helpers as h

    state_path = tmp_path / "run_process.json"
    monkeypatch.setattr(h, "RUN_STATE_PATH", state_path)
    monkeypatch.setattr(h, "_is_process_alive", lambda pid: False)

    state_path.write_text(json.dumps({"pid": 12345, "job_id": "j1", "started_at": "2026-01-01T00:00:00Z"}), encoding="utf-8")
    assert state_path.exists()

    res = TestClient(app_mod.app).get("/api/run/status")
    assert res.status_code == 200
    body = res.json()
    assert body["alive"] is False
    assert body["pid"] == 12345
    assert body["job_id"] == "j1"
    assert body["reason"] == "PROCESS_DEAD"

    # PR1 choice: endpoint cleans stale state file.
    assert not state_path.exists()


def test_run_status_includes_last_finished_reason_when_stopped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import sqlite3

    import config
    import dashboard_backend.app as app_mod
    import dashboard_backend.app_helpers as h

    # Stop state: no run_process.json
    monkeypatch.setattr(h, "RUN_STATE_PATH", tmp_path / "run_process.json")

    # Seed DB with a last RUN/JOB_FINISHED payload.
    db_path = _setup_db(tmp_path)
    monkeypatch.setattr(config, "DB_PATH_DEFAULT", db_path)

    plan_id = "p1"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _insert_min_plan(conn, plan_id=plan_id)
    conn.execute(
        "INSERT INTO workflow_events(event_id,created_at,workflow,event_type,severity,message,job_id,top_task_hash,plan_id,task_id,llm_call_id,payload_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "e1",
            "2026-01-06T05:17:12Z",
            "RUN",
            "JOB_FINISHED",
            "INFO",
            "run job finished",
            "job1",
            None,
            plan_id,
            None,
            None,
            json.dumps({"ok": True, "reason": "GUARDRAIL_HIT", "llm_calls": 51}),
        ),
    )
    conn.execute(
        "INSERT INTO workflow_events(event_id,created_at,workflow,event_type,severity,message,job_id,top_task_hash,plan_id,task_id,llm_call_id,payload_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "e2",
            "2026-01-06T05:17:11Z",
            "RUN",
            "GUARDRAIL_HIT",
            "WARN",
            "guardrail hit: max_llm_calls_per_run",
            "job1",
            None,
            plan_id,
            None,
            None,
            json.dumps({"guardrail": "max_llm_calls_per_run", "limit": 50, "llm_calls": 51}),
        ),
    )
    conn.commit()
    conn.close()

    res = TestClient(app_mod.app).get(f"/api/run/status?plan_id={plan_id}")
    assert res.status_code == 200
    body = res.json()
    assert body["alive"] is False
    assert body["reason"] == "NO_STATE_FILE"

    assert body["last_finished"]["reason"] == "GUARDRAIL_HIT"
    assert body["last_finished"]["llm_calls"] == 51
    assert body["last_guardrail_hit"]["guardrail"] == "max_llm_calls_per_run"
    assert body["last_guardrail_hit"]["limit"] == 50
    assert body["last_guardrail_hit"]["llm_calls"] == 51
