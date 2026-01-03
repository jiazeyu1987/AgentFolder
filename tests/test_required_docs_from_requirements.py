import tempfile
from pathlib import Path

import config
from core.db import apply_migrations, connect
from core.readiness import recompute_readiness_for_plan


def test_waiting_input_writes_required_docs_from_input_requirements(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        monkeypatch.setattr(config, "REQUIRED_DOCS_DIR", td_path / "required_docs")
        monkeypatch.setattr(config, "WORKSPACE_DIR", td_path / "workspace")
        monkeypatch.setattr(config, "INPUTS_DIR", (td_path / "workspace" / "inputs"))
        monkeypatch.setattr(config, "BASELINE_INPUTS_DIR", (td_path / "workspace" / "baseline_inputs"))
        config.INPUTS_DIR.mkdir(parents=True, exist_ok=True)
        config.BASELINE_INPUTS_DIR.mkdir(parents=True, exist_ok=True)

        db_path = td_path / "state.db"
        conn = connect(db_path)
        try:
            apply_migrations(conn, config.MIGRATIONS_DIR)
            plan_id = "p"
            root_id = "root"
            task_id = "t1"
            conn.execute(
                "INSERT INTO plans(plan_id, title, owner_agent_id, root_task_id, created_at, constraints_json) VALUES(?, 'Plan', 'xiaobo', ?, datetime('now'), '{}')",
                (plan_id, root_id),
            )
            conn.execute(
                "INSERT INTO task_nodes(task_id, plan_id, node_type, title, owner_agent_id, status, blocked_reason, created_at, updated_at) VALUES(?, ?, 'ACTION', 'NeedConfig', 'xiaobo', 'PENDING', NULL, datetime('now'), datetime('now'))",
                (task_id, plan_id),
            )
            # Required input requirement with no evidence.
            conn.execute(
                "INSERT INTO input_requirements(requirement_id, task_id, name, kind, required, min_count, allowed_types_json, source, validation_json, created_at) VALUES(?, ?, ?, 'DOC', 1, 1, ?, 'plan', '{\"description\":\"Provide game_config.json\"}', datetime('now'))",
                ("r1", task_id, "game_config", "[\"json\"]"),
            )
            conn.commit()

            recompute_readiness_for_plan(conn, plan_id=plan_id)
            conn.commit()

            req_path = config.REQUIRED_DOCS_DIR / f"{task_id}.md"
            assert req_path.exists()
            txt = req_path.read_text(encoding="utf-8")
            assert "game_config" in txt
            assert "workspace/inputs/game_config/" in txt
        finally:
            conn.close()

