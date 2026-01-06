from pathlib import Path


def test_cli_runner_uses_configured_python(monkeypatch, tmp_path):
    from dashboard_backend.services import cli_runner

    called = {}

    def fake_python_executable():
        return "PY310"

    def fake_run(cmd, **kwargs):
        called["cmd"] = cmd
        called["kwargs"] = kwargs
        class P:
            returncode = 0
            stdout = "ok"
            stderr = ""
        return P()

    monkeypatch.setattr(cli_runner, "python_executable", fake_python_executable)
    monkeypatch.setattr(cli_runner.subprocess, "run", fake_run)

    res = cli_runner.run_agent_cli(db_path=Path("db.sqlite"), args=["status"])
    assert res.exit_code == 0
    assert called["cmd"][0] == "PY310"
    assert "agent_cli.py" in called["cmd"][1]

