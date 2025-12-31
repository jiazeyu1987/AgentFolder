# CLI Workflow Agent (MVP)

This project implements a single-machine, serial multi-agent workflow:
- **xiaobo** (executor): decomposes and executes tasks
- **xiaojing** (reviewer): reviews outputs with a ≥90 quality gate

## Quick start

1) Generate an approved task plan:

```bash
python agent_cli.py create-plan --top-task "将Fruit注册成国内的二类证"
```

2) Run the main loop:

```bash
python run.py
```

3) Put required docs under `workspace/inputs/<requirement_name>/...` and re-run.

## Prompt management (versioned)

List available prompt slots:

```bash
python agent_cli.py prompt list
```

Show a prompt:

```bash
python agent_cli.py prompt show AGENT:default:xiaobo
```

Update a prompt (writes the underlying file and records a new version in `state.db`):

```bash
python agent_cli.py prompt set SHARED:shared:- --file my_shared_prompt.md
```

## Inspect errors

Show recent ERROR events:

```bash
python agent_cli.py errors --limit 50
```

Show ERROR events and counters for a single task:

```bash
python agent_cli.py errors --task-id <TASK_ID>
```

`python agent_cli.py status` also shows `last_error_code/last_error_at` and `waiting_skill_count` per task for quick diagnosis.
For `BLOCKED(WAITING_INPUT)` tasks it also shows a `missing_requirements` summary like `product_spec(0/1)`.

## Dependencies

Minimum:
- Python 3.11+
- SQLite (built-in)

Optional but recommended (skills):
- `pyyaml` (required for full `skills/registry.yaml` schema parsing)
- `PyMuPDF` (package `pymupdf`) or `pdfplumber` (PDF extraction)
- `python-docx` (DOCX extraction)

## LLM configuration (JSON)

LLM selection is controlled by `runtime_config.json`:
- `llm.provider`: `llm_demo` or `claude_code`
- `llm.claude_code_bin`: executable name/path for claude_code
- `llm.timeout_s`: per-call timeout

## Recovery knobs

- `SKILL_TIMEOUT_SECONDS`: per-skill timeout (default 120s)
- `MAX_SKILL_RETRIES`: after N skill failures, escalates to WAITING_EXTERNAL
- `FAILED_AUTO_RESET_READY`: if true, FAILED tasks can be reset to READY on the next loop when inputs/deps are satisfied

## Review loops

- Any review score `< 90` transitions the target task to `TO_BE_MODIFY` and increments `attempt_count`.
- When `attempt_count >= MAX_TASK_ATTEMPTS`, the task escalates to `BLOCKED(WAITING_EXTERNAL)` via `MAX_ATTEMPTS_EXCEEDED`.
