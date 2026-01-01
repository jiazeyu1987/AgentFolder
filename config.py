from __future__ import annotations

from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent

TASKS_DIR = ROOT_DIR / "tasks"
STATE_DIR = ROOT_DIR / "state"
MIGRATIONS_DIR = STATE_DIR / "migrations"
WORKSPACE_DIR = ROOT_DIR / "workspace"
INPUTS_DIR = WORKSPACE_DIR / "inputs"
BASELINE_INPUTS_DIR = WORKSPACE_DIR / "baseline_inputs"
ARTIFACTS_DIR = WORKSPACE_DIR / "artifacts"
REVIEWS_DIR = WORKSPACE_DIR / "reviews"
REQUIRED_DOCS_DIR = WORKSPACE_DIR / "required_docs"
DELIVERABLES_DIR = WORKSPACE_DIR / "deliverables"
LOGS_DIR = ROOT_DIR / "logs"

PLAN_PATH_DEFAULT = TASKS_DIR / "plan.json"
DB_PATH_DEFAULT = STATE_DIR / "state.db"
LLM_RUNS_LOG_PATH = LOGS_DIR / "llm_runs.jsonl"

PROMPTS_SHARED_PATH = ROOT_DIR / "shared_prompt.md"
PROMPTS_AGENTS_DIR = ROOT_DIR / "agents"
REVIEW_RUBRIC_PATH = ROOT_DIR / "rubric" / "review_rubric.json"
SKILLS_REGISTRY_PATH = ROOT_DIR / "skills" / "registry.yaml"
RUNTIME_CONFIG_PATH = ROOT_DIR / "runtime_config.json"

MAX_PLAN_RUNTIME_SECONDS = 2 * 60 * 60
MAX_TASK_ATTEMPTS = 3
MAX_LLM_CALLS = 200
POLL_INTERVAL_SECONDS = 3

SKILL_TIMEOUT_SECONDS = 120
MAX_SKILL_RETRIES = 3

# Baseline inputs scanning safety limits (to avoid slow scans on huge folders).
# These are soft caps: scanning will skip extra files beyond the cap for baseline_inputs.
BASELINE_SCAN_MAX_FILES = 5000
BASELINE_SCAN_MAX_TOTAL_BYTES = 500 * 1024 * 1024  # 500MB

# Error recovery policy (MVP default: do not auto-retry FAILED tasks).
FAILED_AUTO_RESET_READY = False
