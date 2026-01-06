import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    import dashboard_backend.app as app_mod
    import dashboard_backend.app_helpers as h
    import dashboard_backend.services.processes as proc
    from core.db import apply_migrations, connect

    # Isolate state paths to tmp.
    monkeypatch.setattr(h, "RUN_STATE_PATH", tmp_path / "run_process.json")
    db_path = tmp_path / "state.db"
    monkeypatch.setattr(h.config, "DB_PATH_DEFAULT", db_path)

    # Ensure DB exists for audit logging.
    conn = connect(db_path)
    apply_migrations(conn, h.config.MIGRATIONS_DIR)
    conn.commit()
    conn.close()

    # Stub process spawn.
    monkeypatch.setattr(proc, "python_executable", lambda: "PY310")

    class _P:
        pid = 99999

    monkeypatch.setattr(proc, "popen_hidden", lambda *_a, **_k: _P())

    return TestClient(app_mod.app)


def test_run_start_writes_run_state(client: TestClient, tmp_path: Path):
    state_path = tmp_path / "run_process.json"
    assert not state_path.exists()

    res = client.post("/api/run/start", json={"max_iterations": 2})
    assert res.status_code == 200
    body = res.json()
    assert body["started"] is True
    assert body["pid"] == 99999
    assert state_path.exists()

    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["pid"] == 99999
    assert saved["cmd"][0] == "PY310"

