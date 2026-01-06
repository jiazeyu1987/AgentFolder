from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client_with_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import dashboard_backend.app as app_mod
    import dashboard_backend.app_helpers as h
    from core.db import apply_migrations, connect

    db_path = tmp_path / "state.db"
    monkeypatch.setattr(h.config, "DB_PATH_DEFAULT", db_path)

    conn = connect(db_path)
    apply_migrations(conn, h.config.MIGRATIONS_DIR)

    plan_id = "p1"
    root_task_id = "t_root"
    conn.execute(
        "INSERT INTO plans(plan_id,title,owner_agent_id,root_task_id,created_at,constraints_json) VALUES (?,?,?,?,?,?)",
        (plan_id, "Test Plan", "xiaobo", root_task_id, "2026-01-01T00:00:00Z", None),
    )
    conn.execute(
        """
        INSERT INTO task_nodes(task_id,plan_id,node_type,title,owner_agent_id,status,active_branch,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (root_task_id, plan_id, "GOAL", "Root Task", "xiaobo", "READY", 1, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    conn.execute(
        """
        INSERT INTO llm_calls(llm_call_id,created_at,plan_id,task_id,agent,scope,provider,meta_json)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            "c1",
            "2026-01-01T00:00:01Z",
            plan_id,
            root_task_id,
            "xiaojing",
            "PLAN_GEN",
            "TEST",
            '{"attempt":1,"review_attempt":1,"stage":"STRUCTURE","stage_attempt":1}',
        ),
    )
    conn.commit()
    conn.close()

    return TestClient(app_mod.app), plan_id


def test_graph_endpoint_contract(client_with_db):
    client, plan_id = client_with_db
    res = client.get(f"/api/plan/{plan_id}/graph")
    assert res.status_code == 200
    body = res.json()
    assert body["schema_version"] == "graph_v1"
    assert isinstance(body["plan"]["plan_id"], str) and body["plan"]["plan_id"]
    assert "running" in body and "task_id" in body["running"]
    assert isinstance(body["nodes"], list) and body["nodes"]
    assert isinstance(body["nodes"][0]["task_id"], str) and body["nodes"][0]["task_id"]


def test_workflow_endpoint_contract(client_with_db):
    client, plan_id = client_with_db
    res = client.get(f"/api/workflow?plan_id={plan_id}&limit=50")
    assert res.status_code == 200
    body = res.json()
    assert body["schema_version"] == "workflow_v1"
    assert body["plan"]["plan_id"] == plan_id
    assert isinstance(body["nodes"], list) and body["nodes"]
    assert isinstance(body["nodes"][0]["llm_call_id"], str) and body["nodes"][0]["llm_call_id"]
    assert isinstance(body["edges"], list)
    assert isinstance(body["groups"], list)
