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
} from "./types";

async function httpJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  return (await res.json()) as T;
}

export function getConfig(): Promise<ConfigResp> {
  return httpJson<ConfigResp>("/api/config");
}

export function updateRuntimeConfig(patch: { max_decomposition_depth?: number; one_shot_threshold_person_days?: number }): Promise<unknown> {
  return httpJson("/api/runtime_config/update", { method: "POST", body: JSON.stringify(patch) });
}

export function getPlans(): Promise<PlansResp> {
  return httpJson<PlansResp>("/api/plans");
}

export function getGraph(planId: string): Promise<GraphV1> {
  return httpJson<GraphV1>(`/api/plan/${encodeURIComponent(planId)}/graph`);
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

export function createPlan(topTask: string, maxAttempts: number): Promise<unknown> {
  return httpJson("/api/plan/create", { method: "POST", body: JSON.stringify({ top_task: topTask, max_attempts: maxAttempts }) });
}

export function createPlanAsync(topTask: string, maxAttempts: number, keepTrying = false, maxTotalAttempts?: number): Promise<CreatePlanAsyncResp> {
  return httpJson<CreatePlanAsyncResp>("/api/plan/create_async", {
    method: "POST",
    body: JSON.stringify({ top_task: topTask, max_attempts: maxAttempts, keep_trying: keepTrying, max_total_attempts: maxTotalAttempts }),
  });
}

export function getJob(jobId: string): Promise<CreatePlanJobResp> {
  return httpJson<CreatePlanJobResp>(`/api/jobs/${encodeURIComponent(jobId)}`);
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
  scopes?: string;
  agent?: string;
  only_errors?: boolean;
  limit?: number;
  plan_id_missing?: boolean;
}): Promise<WorkflowResp> {
  const usp = new URLSearchParams();
  if (params.plan_id) usp.set("plan_id", params.plan_id);
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

export function resetDb(purgeAll: boolean): Promise<unknown> {
  const payload = purgeAll ? { purge_workspace: true, purge_tasks: true, purge_logs: true } : {};
  return httpJson("/api/reset-db", { method: "POST", body: JSON.stringify(payload) });
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
