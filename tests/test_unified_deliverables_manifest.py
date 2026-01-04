import json
import tempfile
import uuid
from pathlib import Path

import config
from core.db import apply_migrations, connect
from core.manifest import write_manifest_json
from core.prompts import build_xiaobo_prompt, load_prompts


def _uuid() -> str:
    return str(uuid.uuid4())


def test_manifest_written_and_upstream_paths_injected(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        ws = td_path / "workspace"
        monkeypatch.setattr(config, "WORKSPACE_DIR", ws)
        monkeypatch.setattr(config, "DELIVERABLES_DIR", ws / "deliverables")

        db_path = td_path / "state.db"
        conn = connect(db_path)
        try:
            apply_migrations(conn, config.MIGRATIONS_DIR)

            plan_id = _uuid()
            root_id = _uuid()
            upstream_task_id = _uuid()
            downstream_task_id = _uuid()

            conn.execute(
                "INSERT INTO plans(plan_id, title, owner_agent_id, root_task_id, created_at, constraints_json) VALUES(?, 'Plan', 'xiaobo', ?, datetime('now'), '{}')",
                (plan_id, root_id),
            )
            conn.execute(
                "INSERT INTO task_nodes(task_id, plan_id, node_type, title, goal_statement, owner_agent_id, status, created_at, updated_at) VALUES(?, ?, 'GOAL', 'Root', 'Top', 'xiaobo', 'READY', datetime('now'), datetime('now'))",
                (root_id, plan_id),
            )
            conn.execute(
                "INSERT INTO task_nodes(task_id, plan_id, node_type, title, owner_agent_id, status, created_at, updated_at, approved_artifact_id) VALUES(?, ?, 'ACTION', 'Upstream', 'xiaobo', 'DONE', datetime('now'), datetime('now'), ?)",
                (upstream_task_id, plan_id, f"{upstream_task_id}_approved"),
            )
            conn.execute(
                "INSERT INTO task_nodes(task_id, plan_id, node_type, title, owner_agent_id, status, created_at, updated_at) VALUES(?, ?, 'ACTION', 'Downstream', 'xiaobo', 'PENDING', datetime('now'), datetime('now'))",
                (downstream_task_id, plan_id),
            )
            conn.execute(
                "INSERT INTO task_edges(edge_id, plan_id, from_task_id, to_task_id, edge_type, metadata_json, created_at) VALUES(?, ?, ?, ?, 'DEPENDS_ON', '{}', datetime('now'))",
                (_uuid(), plan_id, upstream_task_id, downstream_task_id),
            )

            deliver_dir = config.DELIVERABLES_DIR / plan_id / "tasks" / f"Upstream_{upstream_task_id[:8]}"
            deliver_dir.mkdir(parents=True, exist_ok=True)
            upstream_file = deliver_dir / "spec.md"
            upstream_file.write_text("hello upstream", encoding="utf-8")
            conn.execute(
                "INSERT INTO artifacts(artifact_id, task_id, name, path, format, version, sha256, created_at) VALUES(?, ?, 'spec', ?, 'md', 1, 'x', datetime('now'))",
                (f"{upstream_task_id}_approved", upstream_task_id, str(upstream_file)),
            )
            conn.commit()

            manifest_path = write_manifest_json(conn, plan_id=plan_id, include_candidates=True)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            assert any((f.get("artifact") or {}).get("artifact_id") == f"{upstream_task_id}_approved" for f in (manifest.get("files") or []))

            prompts = load_prompts(config.PROMPTS_SHARED_PATH, config.PROMPTS_AGENTS_DIR)
            prompt_downstream = build_xiaobo_prompt(prompts, conn=conn, plan_id=plan_id, task_id=downstream_task_id)
            assert str(upstream_file).replace("\\", "\\\\") in prompt_downstream

            prompt_upstream = build_xiaobo_prompt(prompts, conn=conn, plan_id=plan_id, task_id=upstream_task_id)
            assert "UPSTREAM_ARTIFACTS (local paths" not in prompt_upstream
        finally:
            conn.close()
