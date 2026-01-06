from types import SimpleNamespace


def test_create_plan_service_start_async_writes_state(tmp_path, monkeypatch):
    from dashboard_backend.services import create_plan_service as svc

    state_path = tmp_path / "create_plan_process.json"
    job_root = tmp_path / "jobs"
    root_dir = tmp_path / "repo"
    root_dir.mkdir(parents=True, exist_ok=True)

    # Fake agent_cli.py location (not executed because popen is stubbed).
    (root_dir / "agent_cli.py").write_text("# stub\n", encoding="utf-8")

    # Stub wrapper render so file is created.
    monkeypatch.setattr(svc, "render_create_plan_wrapper_py", lambda **_k: "print('ok')\n")
    monkeypatch.setattr(svc, "python_executable", lambda: "D:/Anakonda/envs/py310/python.exe")

    class _Proc:
        pid = 12345

    monkeypatch.setattr(svc, "popen_hidden", lambda *_a, **_k: _Proc())
    monkeypatch.setattr(svc, "is_process_alive", lambda _pid: False)

    res = svc.start_async(
        state_path=state_path,
        job_root=job_root,
        root_dir=root_dir,
        db_path=tmp_path / "state.db",
        top_task="hello",
        max_attempts=3,
        keep_trying=False,
        max_total_attempts=None,
    )

    assert res["started"] is True
    assert state_path.exists()
    saved = svc.read_state(state_path)
    assert isinstance(saved, dict)
    assert saved["pid"] == 12345
    assert saved["status"] == "RUNNING"

