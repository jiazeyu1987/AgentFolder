# Recommended End-State Architecture (Practical)

## Backend
- **Routes thin**: `dashboard_backend/*` endpoints only parse/validate input, call services, and shape responses.
- **Services own use-cases**: `dashboard_backend/services/*` implement `create-plan`, `run`, `export`, `reset`, etc.
- **Read models centralized**: `core/queries/*` contains all read-only SQL with explicit SELECT lists; endpoints and UI depend on these stable shapes.
- **State changes centralized**: `core/state_machine/*` is the only place allowed to change `task_nodes.status` and emit `task_events/workflow_events/audit_events` consistently.
- **Process vs DB state separated**: process liveness is reported via `RunSupervisor` (e.g. `/api/run/status`), while task progress is derived from DB/events.

## Frontend
- **API stays dumb**: `dashboard_ui/src/api.ts` only does HTTP; no business logic.
- **Data fetching via hooks**: `useRunStatus`, `usePlanGraph`, `useWorkflow` own polling, retries, and error states; components are mostly pure render.
- **Unified request state**: standardize `loading/error/data` handling so no view can “load forever” on failures.
- **Refresh strategy decoupled from UX**: allow lightweight polling that updates statuses without re-layout/fitView; optionally add “pause refresh / lock viewport” control.

