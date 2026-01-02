# Guardrails (Milestone 6)

This project must not grow unbounded in retries, output size, files, or DB rows. Guardrails are configured in `runtime_config.json` (no environment variables).

## Config Keys

All keys are optional; defaults apply when missing.

```json
{
  "guardrails": {
    "max_run_iterations": 200,
    "max_llm_calls_per_run": 50,
    "max_llm_calls_per_task": 10,
    "max_prompt_chars": 120000,
    "max_response_chars": 200000,
    "max_task_events_per_task": 200,
    "max_llm_calls_rows": 5000,
    "max_task_events_rows": 20000,
    "max_artifact_versions_per_task": 50,
    "max_review_versions_per_check": 50
  }
}
```

Notes:
- `max_artifact_versions_per_task` / `max_review_versions_per_check` are also accepted as legacy top-level keys for backward compatibility.

## Behavior

### Run loop limits

`agent_cli.py run` enforces:
- `guardrails.max_run_iterations`: caps the effective `--max-iterations`.
- `guardrails.max_llm_calls_per_run`: stops the loop when reached and prints a short hint (no traceback).

### LLM call truncation

When `prompt_text` or `response_text` exceeds the configured limits:
- The value is truncated (keeps head + tail with an `...[TRUNCATED]...` marker).
- The `llm_calls` row is marked with `prompt_truncated=1` and/or `response_truncated=1`.
- A small flag is also written into `llm_calls.meta_json` under `truncated`.

### Cleanup (DB/file growth)

`agent_cli.py cleanup` (default: dry-run) trims growth while keeping traceability:
- Keeps `approved_artifact_id`, `active_artifact_id`, and any `reviews.reviewed_artifact_id`.
- Also keeps artifacts referenced by `workspace/deliverables/**/final.json` and `manifest.json`.
- Trims global tables:
  - `llm_calls` to `guardrails.max_llm_calls_rows`
  - `task_events` to `guardrails.max_task_events_rows`
- Trims per-task artifacts/reviews to configured caps (while preserving the keepers above).

Run:
- Dry-run: `python agent_cli.py cleanup`
- Apply: `python agent_cli.py cleanup --apply`

## Regression Runner (S/M/L fixtures)

`tools/regression_run.py` runs fixtures and always writes snapshots when a plan exists:
- Single case: `python tools/regression_run.py --case S_2048`
- All cases: `python tools/regression_run.py --all`

Outputs:
- `workspace/regression/regression_<ts>.json`
- Snapshots: `workspace/observability/<plan_id>/snapshot_*.json` and `.md`

