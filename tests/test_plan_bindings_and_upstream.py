import tempfile
import uuid
from pathlib import Path

import config
from core.db import apply_migrations, connect
from core.models import validate_plan_dict
from core.plan_bindings import add_default_upstream_bindings
from core.readiness import recompute_readiness_for_plan


def _uuid() -> str:
    return str(uuid.uuid4())


def test_validate_plan_allows_upstream_artifact_requirement():
    plan_id = _uuid()
    root_id = _uuid()
    a_id = _uuid()
    b_id = _uuid()
    plan = {
        "plan": {"plan_id": plan_id, "title": "T", "root_task_id": root_id, "created_at": "2026-01-01T00:00:00Z", "owner_agent_id": "xiaobo", "constraints": {"priority": "HIGH"}},
        "nodes": [
            {"task_id": root_id, "plan_id": plan_id, "node_type": "GOAL", "title": "Root", "goal_statement": "Top", "owner_agent_id": "xiaobo", "priority": 0, "tags": []},
            {"task_id": a_id, "plan_id": plan_id, "node_type": "ACTION", "title": "A", "owner_agent_id": "xiaobo", "priority": 0, "tags": []},
            {"task_id": b_id, "plan_id": plan_id, "node_type": "ACTION", "title": "B", "owner_agent_id": "xiaobo", "priority": 0, "tags": []},
        ],
        "edges": [
            {"edge_id": _uuid(), "plan_id": plan_id, "from_task_id": root_id, "to_task_id": a_id, "edge_type": "DECOMPOSE", "metadata": {"and_or": "AND"}},
            {"edge_id": _uuid(), "plan_id": plan_id, "from_task_id": root_id, "to_task_id": b_id, "edge_type": "DECOMPOSE", "metadata": {"and_or": "AND"}},
            {"edge_id": _uuid(), "plan_id": plan_id, "from_task_id": a_id, "to_task_id": b_id, "edge_type": "DEPENDS_ON", "metadata": {}},
        ],
        "requirements": [
            {
                "requirement_id": _uuid(),
                "task_id": b_id,
                "name": "upstream:A",
                "kind": "UPSTREAM_ARTIFACT",
                "required": True,
                "min_count": 1,
                "allowed_types": ["md"],
                "source": a_id,
                "validation": {"description": "use upstream A"},
            }
        ],
    }
    validate_plan_dict(plan)


def test_add_default_upstream_bindings_adds_requirements_from_depends_on_edges():
    plan_id = _uuid()
    root_id = _uuid()
    a_id = _uuid()
    b_id = _uuid()
    plan = {
        "plan": {"plan_id": plan_id, "title": "T", "root_task_id": root_id, "created_at": "2026-01-01T00:00:00Z", "owner_agent_id": "xiaobo", "constraints": {"priority": "HIGH"}},
        "nodes": [
            {"task_id": root_id, "plan_id": plan_id, "node_type": "GOAL", "title": "Root", "goal_statement": "Top", "owner_agent_id": "xiaobo", "priority": 0, "tags": []},
            {"task_id": a_id, "plan_id": plan_id, "node_type": "ACTION", "title": "Core", "owner_agent_id": "xiaobo", "priority": 0, "tags": []},
            {"task_id": b_id, "plan_id": plan_id, "node_type": "ACTION", "title": "Feature", "owner_agent_id": "xiaobo", "priority": 0, "tags": []},
        ],
        "edges": [
            {"edge_id": _uuid(), "plan_id": plan_id, "from_task_id": root_id, "to_task_id": a_id, "edge_type": "DECOMPOSE", "metadata": {"and_or": "AND"}},
            {"edge_id": _uuid(), "plan_id": plan_id, "from_task_id": root_id, "to_task_id": b_id, "edge_type": "DECOMPOSE", "metadata": {"and_or": "AND"}},
            {"edge_id": _uuid(), "plan_id": plan_id, "from_task_id": a_id, "to_task_id": b_id, "edge_type": "DEPENDS_ON", "metadata": {}},
        ],
        "requirements": [],
    }
    plan2 = add_default_upstream_bindings(plan)
    reqs = plan2.get("requirements")
    assert isinstance(reqs, list)
    assert reqs == []
    validate_plan_dict(plan2)


def test_readiness_auto_binds_upstream_artifacts_as_evidence(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        monkeypatch.setattr(config, "REQUIRED_DOCS_DIR", td_path / "required_docs")
        monkeypatch.setattr(config, "WORKSPACE_DIR", td_path / "workspace")
        monkeypatch.setattr(config, "ARTIFACTS_DIR", (td_path / "workspace" / "artifacts"))
        monkeypatch.setattr(config, "INPUTS_DIR", (td_path / "workspace" / "inputs"))
        monkeypatch.setattr(config, "BASELINE_INPUTS_DIR", (td_path / "workspace" / "baseline_inputs"))
        config.ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

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
                "INSERT INTO task_nodes(task_id, plan_id, node_type, title, owner_agent_id, status, blocked_reason, attempt_count, created_at, updated_at) VALUES(?, ?, 'ACTION', 'Upstream', 'xiaobo', 'DONE', NULL, 0, datetime('now'), datetime('now'))",
                (upstream_task_id, plan_id),
            )
            conn.execute(
                "INSERT INTO task_nodes(task_id, plan_id, node_type, title, owner_agent_id, status, blocked_reason, attempt_count, created_at, updated_at) VALUES(?, ?, 'ACTION', 'Downstream', 'xiaobo', 'PENDING', NULL, 0, datetime('now'), datetime('now'))",
                (downstream_task_id, plan_id),
            )
            conn.execute(
                "INSERT INTO task_edges(edge_id, plan_id, from_task_id, to_task_id, edge_type, metadata_json, created_at) VALUES(?, ?, ?, ?, 'DEPENDS_ON', '{}', datetime('now'))",
                (_uuid(), plan_id, upstream_task_id, downstream_task_id),
            )

            art_path = config.ARTIFACTS_DIR / upstream_task_id / "spec.md"
            art_path.parent.mkdir(parents=True, exist_ok=True)
            art_path.write_text("hello", encoding="utf-8")
            artifact_id = _uuid()
            conn.execute(
                "INSERT INTO artifacts(artifact_id, task_id, name, path, format, version, sha256, created_at) VALUES(?, ?, 'spec', ?, 'md', 1, 'x', datetime('now'))",
                (artifact_id, upstream_task_id, str(art_path)),
            )
            conn.execute("UPDATE task_nodes SET approved_artifact_id=? WHERE task_id=?", (artifact_id, upstream_task_id))

            req_id = _uuid()
            conn.execute(
                "INSERT INTO input_requirements(requirement_id, task_id, name, kind, required, min_count, allowed_types_json, source, validation_json, created_at) VALUES(?, ?, ?, 'UPSTREAM_ARTIFACT', 1, 1, ?, ?, '{}', datetime('now'))",
                (req_id, downstream_task_id, "upstream:Upstream", "[\"md\"]", upstream_task_id),
            )
            conn.commit()

            recompute_readiness_for_plan(conn, plan_id=plan_id)
            conn.commit()

            have = conn.execute("SELECT COUNT(1) FROM evidences WHERE requirement_id=?", (req_id,)).fetchone()[0]
            assert int(have) >= 1
        finally:
            conn.close()
