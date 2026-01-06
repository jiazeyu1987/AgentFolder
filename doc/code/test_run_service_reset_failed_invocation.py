from pathlib import Path


def test_run_service_reset_failed_builds_args(monkeypatch):
    from dashboard_backend.services import run_service

    got = {}

    def fake_run_agent_cli(*, db_path: Path, args, cwd=None, hide_window=True):
        got["db_path"] = str(db_path)
        got["args"] = list(args)
        class R:
            exit_code = 0
            stdout = "x"
            stderr = ""
            cmd = ["PY", "agent_cli.py", *args]
        return R()

    monkeypatch.setattr(run_service, "run_agent_cli", fake_run_agent_cli)
    out = run_service.reset_failed(db_path=Path("db.sqlite"), plan_id="p1", include_blocked=True, reset_attempts=True)
    assert out["exit_code"] == 0
    assert got["args"][:2] == ["reset-failed", "--plan-id"]
    assert "--include-blocked" in got["args"]
    assert "--reset-attempts" in got["args"]

