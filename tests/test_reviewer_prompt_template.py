import sqlite3
from pathlib import Path

import config
from core.db import apply_migrations, connect
from core.prompts import build_xiaojing_plan_review_prompt, load_prompts


def test_plan_review_prompt_includes_minimal_output_template(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    conn = connect(db_path)
    apply_migrations(conn, config.MIGRATIONS_DIR)
    bundle = load_prompts(config.PROMPTS_SHARED_PATH, config.PROMPTS_AGENTS_DIR)

    prompt = build_xiaojing_plan_review_prompt(
        bundle,
        plan_id="plan_123",
        rubric_json={"total_score": 100, "dimensions": []},
        plan_json={"plan": {"plan_id": "plan_123", "title": "T", "root_task_id": "r", "created_at": "x", "owner_agent_id": "a", "constraints": {}}},
    )
    assert "OUTPUT_JSON_TEMPLATE" in prompt
    assert "\"schema_version\": \"xiaojing_review_v1\"" in prompt
    assert "\"review_target\": \"PLAN\"" in prompt
    assert "\"breakdown\"" in prompt
    assert "\"suggestions\"" in prompt

    conn.close()
