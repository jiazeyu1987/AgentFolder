import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    import dashboard_backend.app as app_mod
    import dashboard_backend.app_helpers as h
    import dashboard_backend.services.create_plan_service as cps
    import dashboard_backend.services.processes as proc
    from core.db import apply_migrations, connect

    # Isolate create-plan state + jobs to tmp.
    monkeypatch.setattr(h, "CREATE_PLAN_STATE_PATH", tmp_path / "create_plan_process.json")
    monkeypatch.setattr(h.config, "STATE_DIR", tmp_path)

    db_path = tmp_path / "state.db"
    monkeypatch.setattr(h.config, "DB_PATH_DEFAULT", db_path)

    conn = connect(db_path)
    apply_migrations(conn, h.config.MIGRATIONS_DIR)
    conn.commit()
    conn.close()

    # Stub runtime python + wrapper render + process spawn.
    monkeypatch.setattr(proc, "python_executable", lambda: "PY310")
    monkeypatch.setattr(cps, "python_executable", lambda: "PY310")
    monkeypatch.setattr(cps, "render_create_plan_wrapper_py", lambda **_k: "print('ok')\n")

    class _P:
        pid = 88888

    monkeypatch.setattr(cps, "popen_hidden", lambda *_a, **_k: _P())
    monkeypatch.setattr(cps, "is_process_alive", lambda _pid: False)

    return TestClient(app_mod.app)


def test_create_plan_async_writes_state_and_job_files(client: TestClient, tmp_path: Path):
    state_path = tmp_path / "create_plan_process.json"
    assert not state_path.exists()

    res = client.post("/api/plan/create_async", json={"top_task": "hello", "max_attempts": 2, "keep_trying": False})
    assert res.status_code == 200
    body = res.json()
    assert body["started"] is True
    assert body["pid"] == 88888
    assert isinstance(body["job_id"], str) and body["job_id"]
    assert state_path.exists()

    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["pid"] == 88888
    assert saved["cmd"][0] == "PY310"
    assert Path(saved["top_task_file"]).exists()
    assert Path(saved["log_path"]).parent.exists()
    # wrapper should be created under job dir
    wrapper = Path(saved["top_task_file"]).parent / "run_create_plan_wrapper.py"
    assert wrapper.exists()

