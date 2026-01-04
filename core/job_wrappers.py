from __future__ import annotations

import json
from typing import List


def render_create_plan_wrapper_py(*, root_dir: str, log_path: str, exit_path: str, cmd: List[str], job_id: str) -> str:
    """
    Generate a tiny Python wrapper script to run agent_cli create-plan and persist:
    - a non-empty UTF-8 log (header + stdout/stderr)
    - exit_code JSON

    This avoids Windows console encoding/popups and gives the UI something to read while running.
    """
    cmd_json = json.dumps([str(x) for x in cmd], ensure_ascii=True)
    job_id_json = json.dumps(str(job_id))
    return "\n".join(
        [
            "import json, subprocess, sys, time",
            "from pathlib import Path",
            f'ROOT = Path(r\"{root_dir}\")',
            f'LOG = Path(r\"{log_path}\")',
            f'EXIT = Path(r\"{exit_path}\")',
            f"CMD = {cmd_json}",
            "LOG.parent.mkdir(parents=True, exist_ok=True)",
            "EXIT.parent.mkdir(parents=True, exist_ok=True)",
            "with open(LOG, 'w', encoding='utf-8', errors='replace') as f:",
            "  f.write(f'create-plan job started: {time.strftime(\"%Y-%m-%d %H:%M:%S\", time.localtime())}\\n')",
            "  f.write('cmd: ' + ' '.join(CMD) + '\\n\\n')",
            "  f.flush()",
            "  p = subprocess.run(CMD, cwd=str(ROOT), capture_output=True, text=True, encoding='utf-8', errors='replace')",
            "  if p.stdout: f.write(p.stdout)",
            "  if p.stderr: f.write('\\n[STDERR]\\n' + p.stderr)",
            "  f.flush()",
            "ec = int(getattr(p, 'returncode', 1) or 0)",
            f"EXIT.write_text(json.dumps({{'job_id': {job_id_json}, 'exit_code': ec}}, ensure_ascii=False), encoding='utf-8')",
            "sys.exit(ec)",
            "",
        ]
    )

