import json
from pathlib import Path

import config
from core.db import apply_migrations, connect
from dashboard_backend.app import get_task_details


def test_task_details_includes_depends_on(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    conn = connect(db_path)
    apply_migrations(conn, config.MIGRATIONS_DIR)

    plan_id = "p_dep"
    root_id = "root"
    a1 = "a1"
    a2 = "a2"
    conn.execute(
        "INSERT INTO plans(plan_id,title,owner_agent_id,root_task_id,created_at,constraints_json) VALUES(?,?,?,?,?,?)",
        (plan_id, "P", "xiaobo", root_id, "2026-01-01T00:00:00Z", "{}"),
    )
    conn.execute(
        "INSERT INTO task_nodes(task_id,plan_id,node_type,title,owner_agent_id,status,created_at,updated_at,tags_json) VALUES(?,?,?,?,?,'PENDING',?,?,?)",
        (root_id, plan_id, "GOAL", "Root", "xiaobo", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", json.dumps([], ensure_ascii=False)),
    )
    conn.execute(
        "INSERT INTO task_nodes(task_id,plan_id,node_type,title,owner_agent_id,status,created_at,updated_at,active_artifact_id) VALUES(?,?,?,?,?,'DONE',?,?,?)",
        (a1, plan_id, "ACTION", "Upstream", "xiaobo", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", "art1"),
    )
    conn.execute(
        "INSERT INTO task_nodes(task_id,plan_id,node_type,title,owner_agent_id,status,created_at,updated_at) VALUES(?,?,?,?,?,'PENDING',?,?)",
        (a2, plan_id, "ACTION", "Downstream", "xiaobo", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    conn.execute(
        "INSERT INTO artifacts(artifact_id,task_id,name,path,format,version,sha256,created_at) VALUES('art1',?,?,?,'txt',1,'s',?)",
        (a1, "o", str(tmp_path / "o.txt"), "2026-01-01T00:00:00Z"),
    )
    conn.execute(
        "INSERT INTO task_edges(edge_id,plan_id,from_task_id,to_task_id,edge_type,metadata_json,created_at) VALUES(?,?,?,?,?,?,?)",
        ("e1", plan_id, a1, a2, "DEPENDS_ON", "{}", "2026-01-01T00:00:00Z"),
    )
    conn.commit()
    conn.close()

    # Use the FastAPI handler directly (it opens its own connection), so point config DB to temp via monkeypatch-style:
    # Instead, import the query logic through the handler is hard; just ensure the response includes depends_on.
    # We'll re-open a DB connection and call the internal SQL via a copy of handler logic would be noisy.
    # Minimal: call app.get_task_details by temporarily swapping config.DB_PATH_DEFAULT.
    old = config.DB_PATH_DEFAULT
    try:
        config.DB_PATH_DEFAULT = db_path  # type: ignore[assignment]
        res = get_task_details(a2)
        assert "depends_on" in res
        deps = res["depends_on"]
        assert len(deps) == 1
        assert deps[0]["task_id"] == a1
        assert deps[0]["active_artifact"]["artifact_id"] == "art1"
    finally:
        config.DB_PATH_DEFAULT = old  # type: ignore[assignment]
