# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CLI Workflow Agent (MVP) is a Python-based multi-agent workflow system implementing a serial execution model with two LLM-powered agents:
- **xiaobo** (executor): decomposes and executes tasks
- **xiaojing** (reviewer): reviews outputs with a ≥90 quality gate

The system features strong contract validation, SQLite state management, and a React-based observability dashboard.

## Common Commands

**Python Backend:**
```bash
# Generate task plan
python agent_cli.py create-plan --top-task "Create a 2048 game" --max-attempts 3
python agent_cli.py create-plan --top-task "..." --keep-trying --max-total-attempts 20

# Run main workflow loop
python agent_cli.py run --max-iterations 200
# or simply: python run.py

# Status and debugging
python agent_cli.py status                           # Show task status
python agent_cli.py status --verbose                 # Detailed view
python agent_cli.py errors --limit 50                # View ERROR events
python agent_cli.py errors --task-id <TASK_ID>       # Task-specific errors
python agent_cli.py llm-calls --limit 50             # View LLM call history
python agent_cli.py llm-calls --task-id <TASK_ID>    # Task-specific LLM calls

# Recovery and reset
python agent_cli.py reset-failed --plan-id <PLAN_ID>                    # Reset FAILED tasks to READY
python agent_cli.py reset-failed --plan-id <PLAN_ID> --include-blocked  # Include BLOCKED tasks
python agent_cli.py reset-failed --plan-id <PLAN_ID> --reset-attempts   # Reset attempt counters
python agent_cli.py reset-db --purge-workspace --purge-tasks --purge-logs  # Clear all state

# Database utilities
python agent_cli.py doctor --plan-id <PLAN_ID>      # Self-check database
python agent_cli.py repair-db --plan-id <PLAN_ID>   # Auto-repair database issues

# Export deliverables
python agent_cli.py export --plan-id <PLAN_ID>              # Export final deliverables
python agent_cli.py export --plan-id <PLAN_ID> --include-reviews

# Prompt management
python agent_cli.py prompt list                     # List available prompts
python agent_cli.py prompt show AGENT:default:xiaobo  # Show specific prompt
python agent_cli.py prompt set SHARED:shared:- --file my_prompt.md  # Update prompt
```

**Testing:**
```bash
# Run specific test file
python -m unittest tests.test_contracts
python -m unittest tests.test_2a_min_loop

# Run all tests
python -m unittest discover tests
```

**Dashboard (Web UI):**
```bash
# Frontend (React + TypeScript + Vite)
cd dashboard_ui
npm install
npm run dev          # Development server (http://localhost:5173)
npm run build        # Production build
npm run preview      # Preview production build

# Backend (FastAPI)
cd dashboard_backend
pip install -r requirements.txt
uvicorn app:app --reload  # Runs on http://localhost:8000
```

## Architecture Overview

The system follows a **serial workflow** with three main phases:

### 1. Plan Generation (`create-plan`)
- `xiaobo` generates task breakdown (scope=`PLAN_GEN`)
- Plan validated via `normalize_plan_json()` + `validate_plan_dict()`
- `xiaojing` reviews plan (scope=`PLAN_REVIEW`)
- Iterative retry until `total_score>=90 && action_required=APPROVE`
- Approved plan written to `tasks/plan.json` and database

### 2. Task Execution (`run`)
- Tasks progress through states: `PENDING` → `READY` → `EXECUTING` → `READY_TO_CHECK` → `DONE`
- Dependencies resolved automatically via `recompute_readiness_for_plan()`
- `xiaobo` executes `READY` tasks (produces artifacts or `NEEDS_INPUT`)
- Artifacts stored in `workspace/artifacts/<task_id>/`

### 3. Review Loop
- `xiaojing` reviews `READY_TO_CHECK` tasks
- Quality gate ≥90% required for `DONE` status
- Failed reviews trigger `TO_BE_MODIFY` → increment `attempt_count`
- When `attempt_count >= MAX_TASK_ATTEMPTS`: escalates to `BLOCKED(WAITING_EXTERNAL)`

## Core Design Principles

**Strong Contracts (Critical):**
- All LLM outputs **must** pass `normalize_*()` then `validate_*()` before storage
- Contract definitions are the single source of truth in `core/contracts.py`
- All compatibility fixes and schema transformations belong ONLY in contracts
- Never bypass validation or store unvalidated LLM outputs

**Database Schema Evolution:**
- Schema changes ONLY via `state/migrations/*.sql` files
- Never runtime schema modifications
- Use `python agent_cli.py doctor` to check integrity, `repair-db` to fix

**Observability:**
- Every LLM call logged to `logs/llm_runs.jsonl` and database `llm_calls` table
- UI provides LLM Explorer for inspecting prompts, responses, and validator errors
- Audit trail enables debugging "why did this task fail?"

**Error Handling Strategy:**
- LLM output unparseable: mark `LLM_UNPARSEABLE`, increment attempt
- Reviewer output invalid: keep task in `READY_TO_CHECK` for auto-retry
- JSON syntax errors: local fix first (trailing commas, control chars), then optional LLM re-parse call
- After thresholds exceeded: escalate to human intervention

## Key Directories

```
AgentFolder/
├── core/                    # Core libraries (business logic)
│   ├── contracts.py       # ALL contract definitions (normalize/validate)
│   ├── plan_workflow.py   # Plan generation and review workflow
│   ├── db.py              # SQLite connection + migrations
│   ├── llm_client.py      # LLM integration layer
│   ├── scheduler.py       # Task scheduling logic
│   ├── readiness.py       # Dependency resolution and state transitions
│   ├── doctor.py          # Database integrity checker
│   └── repair.py          # Database repair utilities
│
├── dashboard_ui/           # React frontend (TypeScript + Vite)
│   └── src/               # Components, API client, types
│
├── dashboard_backend/      # FastAPI backend for dashboard
│   └── app.py             # REST API endpoints
│
├── agents/                 # Agent-specific prompts (xiaobo, xiaojing)
├── skills/                 # External skill registry (skills/registry.yaml)
│
├── state/                  # Database and migrations
│   ├── state.db           # SQLite database (created on first run)
│   └── migrations/        # SQL migration files
│
├── tasks/                  # Generated task plans
│   └── plan.json          # Current approved plan
│
├── workspace/              # Runtime I/O
│   ├── inputs/            # User input files (per requirement)
│   ├── baseline_inputs/   # Shared reference library (auto-matched first)
│   ├── artifacts/         # Task outputs (per task_id)
│   ├── reviews/           # Review results (per task_id)
│   ├── required_docs/     # Missing input hints (per task_id)
│   └── deliverables/      # Final export directory
│
├── logs/                   # Runtime logs
│   └── llm_runs.jsonl     # Line-by-line LLM call snapshots
│
├── tests/                  # Unit tests (unittest framework)
├── agent_cli.py           # Main CLI entry point
├── run.py                 # Main workflow orchestrator
├── runtime_config.json    # LLM provider config (claude_code, llm_demo)
└── shared_prompt.md       # Shared prompts across agents
```

## Task States

- `PENDING`: Dependencies not satisfied or not yet scheduled
- `READY`: Ready to execute (scheduler will pick it up)
- `BLOCKED(WAITING_INPUT)`: Missing required input files (check `workspace/required_docs/<task_id>.md`)
- `EXECUTING`: Currently being processed by `xiaobo`
- `READY_TO_CHECK`: Completed execution, awaiting `xiaojing` review
- `TO_BE_MODIFY`: Reviewer requested changes (check `workspace/reviews/<task_id>/suggestions.md`)
- `DONE`: Task completed successfully
- `FAILED`: Hard failure (exceeded max attempts) - use `reset-failed` to retry

## Configuration Files

- **`runtime_config.json`**: LLM provider settings
  - `llm.provider`: `llm_demo` or `claude_code`
  - `llm.claude_code_bin`: Path to claude_code executable
  - `llm.timeout_s`: Per-call timeout

- **`skills/registry.yaml`**: External skill definitions with timeout/retry config

- **Environment variables**:
  - `SKILL_TIMEOUT_SECONDS`: Default 120s per skill
  - `MAX_SKILL_RETRIES`: Escalate to WAITING_EXTERNAL after N failures
  - `FAILED_AUTO_RESET_READY`: Auto-reset FAILED tasks to READY when inputs satisfied

## Common Workflows

**Handling `BLOCKED(WAITING_INPUT)`:**
1. System auto-searches `workspace/baseline_inputs/` first
2. If still missing: check `workspace/required_docs/<task_id>.md`
3. Place files at suggested paths in `workspace/inputs/`
4. Re-run `python agent_cli.py run`

**Debugging LLM Failures:**
1. Use `python agent_cli.py errors --task-id <TASK_ID>`
2. Use dashboard UI's LLM Explorer to view full prompt/response
3. Check `logs/llm_runs.jsonl` for raw call snapshots
4. If prompts are broken: update via `prompt set` command
5. Reset with `reset-failed --reset-attempts` and retry

**Recovering from Failures:**
1. Fix underlying issue (prompts, inputs, config)
2. `python agent_cli.py reset-failed --plan-id <PLAN_ID> --include-blocked --reset-attempts`
3. Re-run `python agent_cli.py run`

## Testing

Uses Python's built-in `unittest` framework. Test files follow pattern `test_*.py` in `tests/` directory.

Key test categories:
- `test_contracts.py`: Contract validation
- `test_2a_min_loop.py`: Minimal workflow loop
- `test_plan_review_*.py`: Plan generation and review
- `test_m5_observability.py`: LLM call logging
- `test_m6_guardrails.py`: Error handling

## Important Constraints

1. **Contract modifications**: Only edit `core/contracts.py` for schema changes
2. **Database migrations**: Always use SQL files in `state/migrations/`
3. **LLM output validation**: Never skip `validate_*()` - this is core to system reliability
4. **Baseline inputs**: Keep lean - only stable, reusable reference material
5. **File naming**: Use version suffixes or `FINAL` to avoid conflicts (e.g., `spec_FINAL_2025-12-31.md`)
