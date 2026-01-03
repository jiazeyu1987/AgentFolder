import tempfile
from pathlib import Path

from core.db import apply_migrations, connect
from core.graph import build_plan_graph
from core.util import utc_now_iso
import config


def test_graph_running_highlight_ignores_stale_llm_calls():
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        db_path = td_path / "t.db"
        conn = connect(db_path)
        try:
            apply_migrations(conn, config.MIGRATIONS_DIR)
            plan_id = "p"
            root_id = "root"
            conn.execute(
                "INSERT INTO plans(plan_id, title, owner_agent_id, root_task_id, created_at, constraints_json) VALUES(?, 'Plan', 'xiaobo', ?, datetime('now'), '{}')",
                (plan_id, root_id),
            )
            conn.execute(
                "INSERT INTO task_nodes(task_id, plan_id, node_type, title, owner_agent_id, status, blocked_reason, created_at, updated_at) VALUES(?, ?, 'ACTION', 'T', 'xiaobo', 'DONE', NULL, datetime('now'), datetime('now'))",
                ("t1", plan_id),
            )
            # Very old llm_call should not mark the node as running.
            conn.execute(
                "INSERT INTO llm_calls(llm_call_id, created_at, plan_id, task_id, agent, scope, prompt_text, response_text, parsed_json, normalized_json, validator_error, error_code, error_message, meta_json) VALUES(?, ?, ?, ?, 'xiaobo', 'TASK_ACTION', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)",
                ("c1", "2000-01-01T00:00:00Z", plan_id, "t1"),
            )
            conn.commit()
            g = build_plan_graph(conn, plan_id=plan_id).graph
            assert g["running"]["task_id"] is None
            n = next(x for x in g["nodes"] if x["task_id"] == "t1")
            assert n["is_running"] is False
        finally:
            conn.close()


def test_graph_running_highlight_uses_recent_llm_calls():
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        db_path = td_path / "t.db"
        conn = connect(db_path)
        try:
            apply_migrations(conn, config.MIGRATIONS_DIR)
            plan_id = "p"
            root_id = "root"
            conn.execute(
                "INSERT INTO plans(plan_id, title, owner_agent_id, root_task_id, created_at, constraints_json) VALUES(?, 'Plan', 'xiaobo', ?, datetime('now'), '{}')",
                (plan_id, root_id),
            )
            conn.execute(
                "INSERT INTO task_nodes(task_id, plan_id, node_type, title, owner_agent_id, status, blocked_reason, created_at, updated_at) VALUES(?, ?, 'ACTION', 'T', 'xiaobo', 'DONE', NULL, datetime('now'), datetime('now'))",
                ("t1", plan_id),
            )
            now = utc_now_iso()
            conn.execute(
                "INSERT INTO llm_calls(llm_call_id, created_at, plan_id, task_id, agent, scope, prompt_text, response_text, parsed_json, normalized_json, validator_error, error_code, error_message, meta_json) VALUES(?, ?, ?, ?, 'xiaobo', 'TASK_ACTION', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)",
                ("c1", now, plan_id, "t1"),
            )
            conn.commit()
            g = build_plan_graph(conn, plan_id=plan_id).graph
            assert g["running"]["task_id"] == "t1"
            n = next(x for x in g["nodes"] if x["task_id"] == "t1")
            assert n["is_running"] is True
        finally:
            conn.close()

