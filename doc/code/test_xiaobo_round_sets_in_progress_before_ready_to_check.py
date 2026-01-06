import json
from types import SimpleNamespace


def _setup_db(tmp_path):
    import config
    from core.db import apply_migrations, connect

    db_path = tmp_path / "state.db"
    conn = connect(db_path)
    apply_migrations(conn, config.MIGRATIONS_DIR)
    return conn


def _insert_min_plan_and_task(conn, *, plan_id: str, task_id: str):
    conn.execute(
        "INSERT INTO plans(plan_id,title,owner_agent_id,root_task_id,created_at,constraints_json) VALUES (?,?,?,?,?,?)",
        (plan_id, "T", "xiaobo", task_id, "2026-01-01T00:00:00Z", None),
    )
    conn.execute(
        """
        INSERT INTO task_nodes(task_id,plan_id,node_type,title,owner_agent_id,status,attempt_count,active_branch,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (task_id, plan_id, "ACTION", "A", "xiaobo", "READY", 0, 1, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )


def test_xiaobo_round_sets_in_progress_then_ready_to_check(tmp_path, monkeypatch):
    import run as run_mod
    from core.llm_client import LLMCallResult

    conn = _setup_db(tmp_path)
    plan_id = "p1"
    task_id = "t1"
    _insert_min_plan_and_task(conn, plan_id=plan_id, task_id=task_id)
    conn.commit()

    monkeypatch.setattr(
        run_mod,
        "get_runtime_config",
        lambda: SimpleNamespace(guardrails=SimpleNamespace(max_llm_calls_per_task=10, max_llm_calls_per_run=100, max_run_iterations=10), workflow_mode="v1"),
    )
    monkeypatch.setattr(run_mod, "pick_xiaobo_tasks", lambda _conn, plan_id, limit: [{"task_id": task_id, "status": "READY", "attempt_count": 0}])
    monkeypatch.setattr(run_mod, "consume_per_task_budget", lambda *a, **k: True)
    monkeypatch.setattr(run_mod, "_list_task_input_files", lambda _conn, _task_id: [])
    monkeypatch.setattr(run_mod, "build_xiaobo_prompt", lambda *a, **k: "PROMPT")
    monkeypatch.setattr(run_mod, "_append_jsonl", lambda *a, **k: None)
    monkeypatch.setattr(run_mod, "emit_workflow_event", lambda *a, **k: None)
    monkeypatch.setattr(run_mod, "emit_event", lambda *a, **k: None)
    monkeypatch.setattr(run_mod, "run_skill", lambda *a, **k: {"status": "OK", "artifacts": []})

    class _StubLLM:
        def call_json(self, _prompt: str):
            parsed = {"schema_version": "xiaobo_action_v1", "task_id": task_id, "result_type": "NOOP"}
            raw = json.dumps(parsed)
            return LLMCallResult(
                started_at_ts=1.0,
                finished_at_ts=2.0,
                prompt=_prompt,
                raw_response_text=raw,
                parsed_json=parsed,
                error_code=None,
                error=None,
                provider="test",
            )

    llm = _StubLLM()
    prompts = SimpleNamespace(shared=SimpleNamespace(version="shared_v1", sha256="h1"), xiaobo=SimpleNamespace(version="xiaobo_v1", sha256="h2"))

    run_mod.xiaobo_round(conn=conn, plan_id=plan_id, prompts=prompts, llm=llm, llm_calls=0, skills_registry={}, per_task_llm_calls={})
    conn.commit()

    row = conn.execute("SELECT status FROM task_nodes WHERE task_id=?", (task_id,)).fetchone()
    assert row["status"] == "READY_TO_CHECK"

