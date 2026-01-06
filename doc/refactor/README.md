# Refactor Plan (Backend + Frontend)

## Goals
- Make workflow/run state deterministic and debuggable (no “loading forever”, no silent dead background run).
- Reduce contract drift between backend responses and frontend expectations.
- Make future changes cheaper by isolating responsibilities and tightening tests.

## Current Pain Points (Observed)
- **Run process staleness**: `state/run_process.json` can exist while the PID is dead; UI appears “stuck”.
- **Backend 500 → UI infinite loading**: e.g. missing SQL selected fields causes UI to hang without actionable error.
- **Workflow logic is scattered**: state transitions, event emission, DB reads/writes live across multiple modules.
- **Frontend refresh strategy is coupled to UX**: task graph polling disabled to avoid layout disruptions, but user can’t see progress.

## Guiding Principles
- Single Source of Truth:
  - DB = authoritative workflow/task state.
  - Background process state is derived/validated (never trusted blindly).
- Contracts are explicit and versioned (response schemas, error codes).
- Move from “script-style orchestration” to “service + state-machine + repo” separation.

## Roadmap (3 PRs)

### PR1 — “Unstick & Observability First”
**Objective**: eliminate “卡住不动” class issues without large redesign.

Backend:
- Introduce a `RunSupervisor` abstraction:
  - Reads/writes `RUN_STATE_PATH` (`state/run_process.json`).
  - Detects dead PID and clears stale state.
  - Provides a unified run status payload: `{alive, pid, job_id, started_at, reason?}`.
- Add `/api/run/status` endpoint returning the above payload (and never 500).
- Standardize error response shape: `{code, message, detail?, hint?}` for key endpoints that UI depends on (`graph`, `snapshot`, `workflow`).

Frontend:
- Show run status in UI (e.g. ControlPanel):
  - If run state is stale: show “后台进程已退出/不可用，建议重新启动 Run” and offer a button.
- Ensure “loading workflow…” / “loading graph…” cannot loop forever:
  - Display last error and a retry button if fetch fails.

Tests:
- Backend unit tests for stale PID detection and `/api/run/status` response stability.
- Minimal contract tests verifying required fields exist for graph/workflow responses.

Deliverables:
- New module(s) under `dashboard_backend/`:
  - `services/run_supervisor.py` (or similar)
  - `routes/run.py` (or similar)

---

### PR2 — “Contract Hardening + Query Layer”
**Objective**: reduce contract drift and improve safety for future schema changes.

Backend:
- Enforce FastAPI `response_model` for core endpoints:
  - `/api/plan/{plan_id}/graph`
  - `/api/plan_snapshot`
  - `/api/workflow`
  - `/api/llm_calls`
- Move DB reads into “query” modules with explicit SELECT lists:
  - `core/queries/graph_query.py`
  - `core/queries/workflow_query.py`
- Adopt versioned payloads (`GraphV1`, `WorkflowV1`, etc.) and bump only when fields change.

Frontend:
- Tighten TypeScript types to match backend response models:
  - Prefer “required fields are required” (avoid implicit `any` and optional sprawl).
- Improve API error display:
  - Show `code` and `hint` when present.

Tests:
- Add backend tests that would fail if a required field is dropped (e.g. provider/ids).
- Add a small frontend test to ensure error states render (no infinite spinner).

---

### PR3 — “Workflow Engine / State Machine Consolidation”
**Objective**: make the workflow rules easy to modify safely (your “pipeline gating” and status transitions).

Backend/Core:
- Introduce a centralized state transition layer:
  - A single module owns transitions and emits events consistently.
  - Other code calls `transition(task_id, from, to, reason, payload)` rather than writing status directly.
- Separate orchestration from persistence:
  - `services/*` orchestrate use cases (create-plan, run-loop, review-gate).
  - `repos/*` own DB writes.
  - `queries/*` own DB reads.
- Make event emission consistent and auditable:
  - Use `workflow_events` for step-level observability and `task_events` for task state changes.

Frontend:
- Optional: enable safe “light polling” for Task Graph:
  - Poll only task statuses + “running task id”, not full dagre relayout.
  - Preserve user viewport (no `fitView` on refresh), or only relayout when plan changes.

Tests:
- State machine transition tests:
  - Validate allowed transitions.
  - Validate events emitted.
- Regression tests for your intended pipeline gating:
  - “Stage pass sticks, failures retry within stage only.”

## Suggested Target Structure (Backend)
- `dashboard_backend/routes/*.py` — FastAPI routers only
- `dashboard_backend/services/*.py` — application use-cases
- `core/queries/*.py` — read models
- `core/repos/*.py` — writes (optional if you prefer keeping writes in services)
- `core/state_machine/*.py` — transition rules + event emission

## Suggested Target Structure (Frontend)
- `dashboard_ui/src/api.ts` — keep as low-level HTTP
- `dashboard_ui/src/hooks/usePlanGraph.ts`
- `dashboard_ui/src/hooks/useWorkflow.ts`
- `dashboard_ui/src/hooks/useRunStatus.ts`
- UI components become mostly “dumb renderers”

## Non-Goals (Explicitly)
- No “big rewrite” or framework migration.
- No schema redesign unless needed for correctness; prefer additive columns/tables.
- No over-abstracting: keep PR1 minimal and outcome-driven.

## Definition of Done
- UI never hangs indefinitely on failed API calls; always shows actionable error + retry.
- `run_process.json` cannot leave the system in a “looks running but not progressing” state.
- Backend responses are validated and versioned; breaking changes are caught by tests.
