import tempfile
import uuid
from pathlib import Path

import config
from core.db import apply_migrations, connect
from core.readiness import recompute_readiness_for_plan


def _uuid() -> str:
    return str(uuid.uuid4())


def test_root_goal_not_done_when_other_actions_remaining(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        db_path = td_path / "state.db"
        conn = connect(db_path)
        try:
            apply_migrations(conn, config.MIGRATIONS_DIR)
            plan_id = _uuid()
            root_id = _uuid()
            a1 = _uuid()
            a2 = _uuid()

            conn.execute(
                "INSERT INTO plans(plan_id, title, owner_agent_id, root_task_id, created_at, constraints_json) VALUES(?, 'Plan', 'xiaobo', ?, datetime('now'), '{}')",
                (plan_id, root_id),
            )
            conn.execute(
                "INSERT INTO task_nodes(task_id, plan_id, node_type, title, goal_statement, owner_agent_id, status, created_at, updated_at) VALUES(?, ?, 'GOAL', 'Root Task', 'Top', 'xiaobo', 'DONE', datetime('now'), datetime('now'))",
                (root_id, plan_id),
            )
            conn.execute(
                "INSERT INTO task_nodes(task_id, plan_id, node_type, title, owner_agent_id, status, created_at, updated_at) VALUES(?, ?, 'ACTION', 'A1', 'xiaobo', 'DONE', datetime('now'), datetime('now'))",
                (a1, plan_id),
            )
            conn.execute(
                "INSERT INTO task_nodes(task_id, plan_id, node_type, title, owner_agent_id, status, created_at, updated_at) VALUES(?, ?, 'ACTION', 'A2', 'xiaobo', 'READY', datetime('now'), datetime('now'))",
                (a2, plan_id),
            )
            # Only DECOMPOSE root->A1; A2 exists but is not decomposed (bad plan shape).
            conn.execute(
                "INSERT INTO task_edges(edge_id, plan_id, from_task_id, to_task_id, edge_type, metadata_json, created_at) VALUES(?, ?, ?, ?, 'DECOMPOSE', '{\"and_or\":\"AND\"}', datetime('now'))",
                (_uuid(), plan_id, root_id, a1),
            )
            conn.commit()

            recompute_readiness_for_plan(conn, plan_id=plan_id)
            conn.commit()

            root_status = conn.execute("SELECT status FROM task_nodes WHERE task_id=?", (root_id,)).fetchone()["status"]
            assert root_status != "DONE"
        finally:
            conn.close()

