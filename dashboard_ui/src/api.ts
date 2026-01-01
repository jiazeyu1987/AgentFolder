import type { ConfigResp, GraphV1, PlansResp, TaskDetailsResp, TaskLlmCallsResp } from "./types";

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
