export type NodeType = "GOAL" | "ACTION" | "CHECK";
export type TaskStatus =
  | "PENDING"
  | "READY"
  | "IN_PROGRESS"
  | "BLOCKED"
  | "READY_TO_CHECK"
  | "TO_BE_MODIFY"
  | "DONE"
  | "FAILED"
  | "ABANDONED";

export type EdgeType = "DEPENDS_ON" | "DECOMPOSE" | "ALTERNATIVE";

export interface GraphV1 {
  schema_version: "graph_v1";
  plan: {
    plan_id: string;
    title: string;
    root_task_id: string;
    created_at: string;
  };
  running: {
    task_id: string | null;
    since: string | null;
    source: string;
  };
  nodes: GraphNode[];
  edges: GraphEdge[];
  ts: string;
  paths?: Record<string, string>;
}

export interface GraphNode {
  task_id: string;
  title: string;
  node_type: NodeType;
  status: TaskStatus;
  owner_agent_id: string;
  priority: number;
  blocked_reason: string | null;
  attempt_count: number;
  tags: string[];
  active_artifact: { artifact_id: string; format: string; path: string } | null;
  missing_inputs: Array<{
    name: string;
    description?: string;
    accepted_types?: string[] | string;
    suggested_path?: string;
    have?: number;
    need?: number;
  }>;
  required_docs_path: string;
  last_error: { created_at: string; error_code: string | null; message: string | null } | null;
  last_review: { total_score: number; action_required: string; summary: string | null; created_at: string } | null;
  artifact_dir: string;
  review_dir: string;
  is_running: boolean;
}

export interface GraphEdge {
  edge_id: string;
  from_task_id: string;
  to_task_id: string;
  edge_type: EdgeType;
  metadata: Record<string, unknown>;
}

export interface PlansResp {
  plans: Array<{ plan_id: string; title: string; root_task_id: string; created_at: string; workflow_version: 1 | 2; top_task_hash: string | null }>;
  ts: string;
}

export interface PlanSnapshotResp {
  schema_version: "plan_snapshot_v1";
  ts: string;
  plan: { plan_id: string; title: string; root_task_id?: string; created_at?: string; workflow_mode?: string };
  summary: { is_done?: boolean } & Record<string, unknown>;
  reasons: Array<{ code: string; count: number; example?: string }>;
  inputs_needed: Array<{ task_title: string; required_docs_path: string; items: Array<{ name: string; accepted_types?: string[]; suggested_path?: string }> }>;
  waiting_review: Array<Record<string, unknown>>;
  recent_errors: Array<{ task_title?: string; created_at?: string; error_code?: string; message?: string; hint?: string; context_excerpt?: string }>;
  final_deliverable: null | { deliverables_dir: string; final_entrypoint: string; how_to_run: string[]; final_task_title: string; final_artifact_id: string };
  doctor: { ok: boolean; findings: Array<Record<string, unknown>> };
  feasibility: Record<string, unknown> | null;
  report: Record<string, unknown>;
  manifest: Record<string, unknown> | null;
}

export interface ConfigResp {
  runtime_config: unknown;
  paths: Record<string, string>;
}

export interface RunStatusResp {
  alive: boolean;
  pid: number | null;
  job_id: string | null;
  started_at: string | null;
  reason?: string;
  detail?: string;
  last_finished?: {
    created_at: string;
    job_id: string | null;
    severity: string | null;
    message: string | null;
    ok?: boolean | null;
    reason?: string | null;
    llm_calls?: number | null;
  };
  last_guardrail_hit?: {
    created_at: string;
    job_id: string | null;
    severity: string | null;
    message: string | null;
    guardrail?: string | null;
    limit?: number | null;
    llm_calls?: number | null;
  };
  last_finished_error?: string;
}

export type RuntimeConfigPatch = {
  max_decomposition_depth?: number;
  one_shot_threshold_person_days?: number;
  create_plan_max_attempts?: number;
  task_max_attempts?: number;
  task_review_pass_score?: number;
  task_review_notes_max_chars?: number;
  plan_review_pass_score?: number;
  plan_review_notes_max_chars?: number;
};

export interface TaskLlmCallsResp {
  task_id: string;
  calls: Array<{
    llm_call_id: string;
    created_at: string;
    plan_id: string | null;
    task_id: string | null;
    agent: string;
    scope: string;
    prompt_text: string | null;
    response_text: string | null;
    parsed_json: string | null;
    normalized_json: string | null;
    validator_error: string | null;
    error_code: string | null;
    error_message: string | null;
  }>;
  ts: string;
}

export type CreatePlanJobStatus = "RUNNING" | "DONE" | "FAILED";
export type CreatePlanPhase = "PLAN_RUBRIC" | "PLAN_GEN" | "PLAN_REVIEW" | "UNKNOWN";

export interface CreatePlanJobResp {
  job_id: string;
  kind: "CREATE_PLAN";
  status: CreatePlanJobStatus;
  pid: number | null;
  started_at: string | null;
  finished_at: string | null;
  exit_code?: number | string | null;
  log_path?: string | null;
  plan_id: string | null;
  attempt: number;
  phase: CreatePlanPhase;
  stage?: string;
  stage_attempt?: number;
  rubric_attempt?: number;
  review_attempt: number;
  current_step?: string | null;
  last_event?: { created_at: string; event_type: string; severity: string; message: string | null; payload: any } | null;
  last_decision?: { created_at: string; message: string | null; payload: any } | null;
  last_error?: { created_at: string; message: string | null; payload: any } | null;
  last_llm_call: { created_at: string; scope: string; agent: string; error_code: string | null; validator_error: string | null } | null;
  hint: string;
  retry_reason?: string;
  ts: string;
}

export interface CreatePlanAsyncResp {
  started: boolean;
  reason?: string;
  job_id?: string;
  pid?: number;
  ts?: string;
}

export interface LlmCallsQueryResp {
  calls: Array<{
    llm_call_id: string;
    created_at: string;
    plan_id: string | null;
    task_id: string | null;
    agent: string;
    scope: string;
    prompt_text: string | null;
    response_text: string | null;
    parsed_json: string | null;
    normalized_json: string | null;
    validator_error: string | null;
    error_code: string | null;
    error_message: string | null;
    meta_json: string | null;
    shared_prompt_path?: string | null;
    agent_prompt_path?: string | null;
    prompt_source_reason?: string | null;
    plan_review_attempt_path?: string | null;
  }>;
  ts: string;
}

export interface TaskDetailsResp {
  task: {
    task_id: string;
    plan_id: string;
    title: string;
    node_type: string;
    status: string;
    owner_agent_id: string;
    blocked_reason: string | null;
    attempt_count: number;
    active_artifact_id: string | null;
  };
  active_artifact: { artifact_id: string; name: string; format: string; path: string; sha256: string; created_at: string } | null;
  artifacts: Array<{ artifact_id: string; name: string; format: string; path: string; sha256: string; created_at: string }>;
  depends_on: Array<{
    task_id: string;
    title: string;
    node_type: string;
    status: string;
    approved_artifact: { artifact_id: string; name: string; format: string; path: string; sha256: string; created_at: string } | null;
    active_artifact: { artifact_id: string; name: string; format: string; path: string; sha256: string; created_at: string } | null;
  }>;
  acceptance_criteria: string[];
  required_docs_path: string;
  artifact_dir: string;
  review_dir: string;
  ts: string;
}

export interface PromptFileResp {
  path: string;
  content: string;
  truncated: boolean;
  ts: string;
}

export type WorkflowEdgeType = "NEXT" | "PAIR" | "STAGE_NEXT";

export interface WorkflowResp {
  schema_version: "workflow_v1";
  plan: { plan_id: string | null; title: string | null; workflow_mode: string };
  nodes: Array<{
    llm_call_id: string;
    source_llm_call_id?: string | null;
    created_at: string;
    plan_id: string | null;
    task_id: string | null;
    task_title: string | null;
    agent: string;
    scope: string;
    attempt: number;
    review_attempt: number;
    stage?: string | null;
    stage_attempt?: number | null;
    node_kind?: string | null;
    error_code: string | null;
    validator_error: string | null;
    total_score?: number | null;
    action_required?: string | null;
  }>;
  edges: Array<{ from: string; to: string; edge_type: WorkflowEdgeType; stage?: string | null }>;
  groups: Array<{ group_type: "ATTEMPT"; id: string; attempt: number; node_ids: string[] }>;
  total_rows?: number;
  returned_rows?: number;
  ts: string;
}

export type ErrorSource = "TASK_EVENT" | "LLM_CALL";

export interface ErrorsResp {
  errors: Array<{
    source: ErrorSource;
    created_at: string;
    plan_id: string | null;
    task_id: string | null;
    task_title: string | null;
    llm_call_id: string | null;
    scope: string | null;
    agent: string | null;
    error_code: string | null;
    message: string | null;
    hint: string | null;
    validator_error?: string | null;
    error_message?: string | null;
  }>;
  ts: string;
}

export interface TopTasksResp {
  top_tasks: Array<{ top_task_hash: string; top_task_title: string | null; last_seen: string }>;
  ts: string;
}

export interface AuditResp {
  events: Array<{
    audit_id: string;
    created_at: string;
    category: string;
    action: string;
    top_task_hash: string | null;
    top_task_title: string | null;
    plan_id: string | null;
    task_id: string | null;
    llm_call_id: string | null;
    job_id: string | null;
    status_before: string | null;
    status_after: string | null;
    ok: number;
    message: string | null;
    payload_json: string | null;
  }>;
  ts: string;
}

export type ResetToPlanResp = { exit_code: number; stdout: string; stderr: string };
