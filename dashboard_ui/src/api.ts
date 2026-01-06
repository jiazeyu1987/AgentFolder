import type {
  ConfigResp,
  CreatePlanAsyncResp,
  CreatePlanJobResp,
  GraphV1,
  LlmCallsQueryResp,
  PlansResp,
  PromptFileResp,
  TaskDetailsResp,
  TaskLlmCallsResp,
  WorkflowResp,
  ErrorsResp,
  TopTasksResp,
  AuditResp,
  ResetToPlanResp,
  PlanSnapshotResp,
  RunStatusResp,
} from "./types";

async function _readErrorBody(res: Response): Promise<string> {
  const ct = res.headers.get("content-type") ?? "";
  if (ct.includes("application/json")) {
    try {
      const j: any = await res.json();
      const code = j?.code ?? j?.error_code ?? j?.reason ?? null;
      const message = j?.message ?? j?.detail ?? null;
      if (code && message) return `${String(code)}: ${String(message)}`;
      if (code) return String(code);
      if (message) return String(message);
      return JSON.stringify(j);
    } catch {
      // fall through to text
    }
  }
  try {
    return (await res.text()) || "";
  } catch {
    return "";
  }
}

async function httpJson<T>(
  url: string,
  init?: (RequestInit & { timeoutMs?: number }) | undefined,
): Promise<T> {
  const timeoutMs = init?.timeoutMs ?? 15_000;
  const controller = init?.signal ? null : new AbortController();
  const timeout = controller
    ? setTimeout(() => {
        try {
          controller.abort();
        } catch {
          // ignore
        }
      }, timeoutMs)
    : null;
  try {
    const res = await fetch(url, {
      ...init,
      signal: init?.signal ?? controller?.signal,
      headers: {
        "Content-Type": "application/json",
        ...(init?.headers ?? {}),
      },
    });
    if (!res.ok) {
      const body = await _readErrorBody(res);
      const suffix = body ? `: ${body}` : "";
      throw new Error(`${res.status} ${res.statusText} (${url})${suffix}`);
    }
    return (await res.json()) as T;
  } catch (e: any) {
    const msg = String(e?.message ?? e);
    if (msg.includes("AbortError") || msg.includes("aborted")) {
      throw new Error(`REQUEST_TIMEOUT (${timeoutMs}ms) (${url})`);
    }
    throw e;
  } finally {
    if (timeout) clearTimeout(timeout);
  }
}

export function getConfig(): Promise<ConfigResp> {
  return httpJson<ConfigResp>("/api/config");
}

export function updateRuntimeConfig(patch: {
  max_decomposition_depth?: number;
  one_shot_threshold_person_days?: number;
  create_plan_max_attempts?: number;
  task_max_attempts?: number;
  task_review_pass_score?: number;
  task_review_notes_max_chars?: number;
  plan_review_pass_score?: number;
  plan_review_notes_max_chars?: number;
}): Promise<unknown> {
  return httpJson("/api/runtime_config/update", { method: "POST", body: JSON.stringify(patch) });
}

export function getPlans(): Promise<PlansResp> {
  return httpJson<PlansResp>("/api/plans");
}

export function getGraph(planId: string): Promise<GraphV1> {
  return httpJson<GraphV1>(`/api/plan/${encodeURIComponent(planId)}/graph`);
}

export function getPlanSnapshot(planId: string): Promise<PlanSnapshotResp> {
  const usp = new URLSearchParams({ plan_id: String(planId) });
  return httpJson<PlanSnapshotResp>(`/api/plan_snapshot?${usp.toString()}`);
}

export function runStart(maxIterations: number): Promise<unknown> {
  return httpJson("/api/run/start", { method: "POST", body: JSON.stringify({ max_iterations: maxIterations }) });
}

export function runStop(): Promise<unknown> {
  return httpJson("/api/run/stop", { method: "POST" });
}

export function runOnce(): Promise<unknown> {
  return httpJson("/api/run/once", { method: "POST" });
}

export function getRunStatus(): Promise<RunStatusResp> {
  return httpJson<RunStatusResp>("/api/run/status");
}

export function getRunStatusForPlan(planId?: string | null): Promise<RunStatusResp> {
  if (!planId) return getRunStatus();
  const usp = new URLSearchParams({ plan_id: String(planId) });
  return httpJson<RunStatusResp>(`/api/run/status?${usp.toString()}`);
}

export function createPlan(topTask: string, maxAttempts: number): Promise<unknown> {
  return httpJson("/api/plan/create", { method: "POST", body: JSON.stringify({ top_task: topTask, max_attempts: maxAttempts }) });
}

export function createPlanAsync(topTask: string, opts?: { max_attempts?: number; keep_trying?: boolean; max_total_attempts?: number }): Promise<CreatePlanAsyncResp> {
  const body: any = { top_task: topTask };
  if (opts?.max_attempts !== undefined) body.max_attempts = opts.max_attempts;
  if (opts?.keep_trying !== undefined) body.keep_trying = opts.keep_trying;
  if (opts?.max_total_attempts !== undefined) body.max_total_attempts = opts.max_total_attempts;
  return httpJson<CreatePlanAsyncResp>("/api/plan/create_async", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getJob(jobId: string): Promise<CreatePlanJobResp> {
  return httpJson<CreatePlanJobResp>(`/api/jobs/${encodeURIComponent(jobId)}`);
}

export function getJobLog(jobId: string, maxChars = 50_000): Promise<PromptFileResp> {
  const usp = new URLSearchParams({ job_id: jobId, max_chars: String(maxChars) });
  return httpJson<PromptFileResp>(`/api/job_log?${usp.toString()}`);
}

export function getLlmCallsQuery(params: {
  llm_call_id?: string;
  plan_id?: string;
  scopes?: string;
  agent?: string;
  limit?: number;
  plan_id_missing?: boolean;
}): Promise<LlmCallsQueryResp> {
  const usp = new URLSearchParams();
  if (params.llm_call_id) usp.set("llm_call_id", params.llm_call_id);
  if (params.plan_id) usp.set("plan_id", params.plan_id);
  if (params.scopes) usp.set("scopes", params.scopes);
  if (params.agent) usp.set("agent", params.agent);
  if (params.limit) usp.set("limit", String(params.limit));
  if (params.plan_id_missing) usp.set("plan_id_missing", "true");
  return httpJson<LlmCallsQueryResp>(`/api/llm_calls?${usp.toString()}`);
}

export function getWorkflow(params: {
  plan_id?: string;
  job_id?: string;
  top_task_hash?: string;
  scopes?: string;
  agent?: string;
  only_errors?: boolean;
  limit?: number;
  plan_id_missing?: boolean;
}): Promise<WorkflowResp> {
  const usp = new URLSearchParams();
  if (params.plan_id) usp.set("plan_id", params.plan_id);
  if (params.job_id) usp.set("job_id", params.job_id);
  if (params.top_task_hash) usp.set("top_task_hash", params.top_task_hash);
  if (params.scopes) usp.set("scopes", params.scopes);
  if (params.agent) usp.set("agent", params.agent);
  if (params.only_errors) usp.set("only_errors", "true");
  if (params.limit) usp.set("limit", String(params.limit));
  if (params.plan_id_missing) usp.set("plan_id_missing", "true");
  return httpJson<WorkflowResp>(`/api/workflow?${usp.toString()}`);
}

export function getPromptFile(path: string, maxChars = 200_000): Promise<PromptFileResp> {
  const usp = new URLSearchParams({ path, max_chars: String(maxChars) });
  return httpJson<PromptFileResp>(`/api/prompt_file?${usp.toString()}`);
}

export function getErrors(params: { plan_id?: string; plan_id_missing?: boolean; task_id?: string; include_related?: boolean; limit?: number }): Promise<ErrorsResp> {
  const usp = new URLSearchParams();
  if (params.plan_id) usp.set("plan_id", params.plan_id);
  if (params.plan_id_missing) usp.set("plan_id_missing", "true");
  if (params.task_id) usp.set("task_id", params.task_id);
  if (params.include_related) usp.set("include_related", "true");
  if (params.limit) usp.set("limit", String(params.limit));
  return httpJson<ErrorsResp>(`/api/errors?${usp.toString()}`);
}

export function getTopTasks(limit = 50): Promise<TopTasksResp> {
  const usp = new URLSearchParams({ limit: String(limit) });
  return httpJson<TopTasksResp>(`/api/top_tasks?${usp.toString()}`);
}

export function getAudit(params: { top_task_hash?: string; plan_id?: string; job_id?: string; category?: string; limit?: number }): Promise<AuditResp> {
  const usp = new URLSearchParams();
  if (params.top_task_hash) usp.set("top_task_hash", params.top_task_hash);
  if (params.plan_id) usp.set("plan_id", params.plan_id);
  if (params.job_id) usp.set("job_id", params.job_id);
  if (params.category) usp.set("category", params.category);
  if (params.limit) usp.set("limit", String(params.limit));
  return httpJson<AuditResp>(`/api/audit?${usp.toString()}`);
}

export function resetDb(purgeAll: boolean): Promise<unknown> {
  const payload = purgeAll ? { purge_workspace: true, purge_tasks: true, purge_logs: true } : {};
  return httpJson("/api/reset-db", { method: "POST", body: JSON.stringify(payload) });
}

export function resetFailed(planId: string, opts?: { include_blocked?: boolean; reset_attempts?: boolean }): Promise<{ exit_code: number; stdout: string; stderr: string }> {
  return httpJson("/api/reset-failed", {
    method: "POST",
    body: JSON.stringify({
      plan_id: planId,
      include_blocked: Boolean(opts?.include_blocked),
      reset_attempts: Boolean(opts?.reset_attempts),
    }),
  });
}

export function resetToPlan(planId: string): Promise<ResetToPlanResp> {
  return httpJson<ResetToPlanResp>("/api/reset-to-plan", { method: "POST", body: JSON.stringify({ plan_id: planId }) });
}

export function exportDeliverables(planId: string, includeReviews: boolean): Promise<unknown> {
  return httpJson("/api/export", { method: "POST", body: JSON.stringify({ plan_id: planId, include_reviews: includeReviews }) });
}

export function getTaskLlmCalls(taskId: string, limit = 20): Promise<TaskLlmCallsResp> {
  return httpJson<TaskLlmCallsResp>(`/api/task/${encodeURIComponent(taskId)}/llm?limit=${limit}`);
}

export function getTaskDetails(taskId: string): Promise<TaskDetailsResp> {
  return httpJson<TaskDetailsResp>(`/api/task/${encodeURIComponent(taskId)}/details`);
}
