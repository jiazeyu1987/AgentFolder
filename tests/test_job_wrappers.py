from __future__ import annotations

from core.job_wrappers import render_create_plan_wrapper_py


def test_create_plan_wrapper_is_non_empty_and_writes_header():
    s = render_create_plan_wrapper_py(
        root_dir=r"D:\repo",
        log_path=r"D:\repo\state\jobs\j\create_plan.log",
        exit_path=r"D:\repo\state\jobs\j\exit.json",
        cmd=[r"D:\python.exe", r"D:\repo\agent_cli.py", "create-plan", "--max-attempts", "3"],
        job_id="job123",
    )
    assert "create-plan job started" in s
    assert "capture_output=True" in s
    assert "exit_code" in s

