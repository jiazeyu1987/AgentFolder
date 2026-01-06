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


def test_xiaobo_round_retries_contract_mismatch_once(tmp_path, monkeypatch):
    from core.llm_client import LLMCallResult
    import run as run_mod

    conn = _setup_db(tmp_path)
    plan_id = "p1"
    task_id = "t1"
    _insert_min_plan_and_task(conn, plan_id=plan_id, task_id=task_id)
    conn.commit()

    # Make runtime config stable for the test.
    monkeypatch.setattr(
        run_mod,
        "get_runtime_config",
        lambda: SimpleNamespace(guardrails=SimpleNamespace(max_llm_calls_per_task=10, max_llm_calls_per_run=100)),
    )

    # Avoid side paths.
    monkeypatch.setattr(run_mod, "pick_xiaobo_tasks", lambda _conn, plan_id, limit: [{"task_id": task_id, "status": "READY", "attempt_count": 0}])
    monkeypatch.setattr(run_mod, "consume_per_task_budget", lambda *a, **k: True)
    monkeypatch.setattr(run_mod, "_list_task_input_files", lambda _conn, _task_id: [])
    monkeypatch.setattr(run_mod, "build_xiaobo_prompt", lambda *a, **k: "PROMPT")
    monkeypatch.setattr(run_mod, "_append_jsonl", lambda *a, **k: None)
    monkeypatch.setattr(run_mod, "emit_workflow_event", lambda *a, **k: None)

    status_set = {"called": 0}

    def _fake_set_status(*_a, **_k):
        status_set["called"] += 1

    monkeypatch.setattr(run_mod, "_set_status", _fake_set_status)

    errors = []

    def _fake_handle_error(_conn, *, plan_id, task_id, error_code, message, context=None):
        errors.append({"error_code": error_code, "message": message, "context": context})

    monkeypatch.setattr(run_mod, "_handle_error", _fake_handle_error)

    recorded_calls = []
    call_seq = {"n": 0}

    def _fake_record_llm_call(*_a, **kw):
        call_seq["n"] += 1
        llm_call_id = f"c{call_seq['n']}"
        recorded_calls.append({"llm_call_id": llm_call_id, **kw})
        return llm_call_id

    monkeypatch.setattr(run_mod, "record_llm_call", _fake_record_llm_call)

    annotated = []

    def _fake_annotate_llm_output_for_retry(_conn, *, llm_call_id: str, retry_kind: str, retry_reason: str):
        annotated.append({"llm_call_id": llm_call_id, "retry_kind": retry_kind, "retry_reason": retry_reason})

    monkeypatch.setattr(run_mod, "annotate_llm_output_for_retry", _fake_annotate_llm_output_for_retry)

    class _StubLLM:
        def __init__(self):
            self.calls = 0

        def call_json(self, _prompt: str):
            self.calls += 1
            if self.calls == 1:
                # Contract mismatch: missing result_type.
                parsed = {"schema_version": "1.0.0", "task_id": task_id}
            else:
                parsed = {"schema_version": "1.0.0", "task_id": task_id, "result_type": "NOOP"}
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
    prompts = SimpleNamespace(
        shared=SimpleNamespace(version="shared_v1", sha256="h1"),
        xiaobo=SimpleNamespace(version="xiaobo_v1", sha256="h2"),
    )

    llm_calls = run_mod.xiaobo_round(conn=conn, plan_id=plan_id, prompts=prompts, llm=llm, llm_calls=0, skills_registry={}, per_task_llm_calls={})

    assert llm.calls == 2
    assert llm_calls >= 2
    assert annotated and annotated[0]["llm_call_id"] == "c1"
    assert all(e["error_code"] != "LLM_UNPARSEABLE" for e in errors)
    assert status_set["called"] >= 1

