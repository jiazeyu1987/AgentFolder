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
  plans: Array<{ plan_id: string; title: string; root_task_id: string; created_at: string; workflow_version: 1 | 2 }>;
  ts: string;
}

export interface ConfigResp {
  runtime_config: unknown;
  paths: Record<string, string>;
}

export type RuntimeConfigPatch = {
  max_decomposition_depth?: number;
  one_shot_threshold_person_days?: number;
  plan_review_pass_score?: number;
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
export type CreatePlanPhase = "PLAN_GEN" | "PLAN_REVIEW" | "UNKNOWN";

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
  review_attempt: number;
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

export type WorkflowEdgeType = "NEXT" | "PAIR";

export interface WorkflowResp {
  schema_version: "workflow_v1";
  plan: { plan_id: string | null; title: string | null; workflow_mode: string };
  nodes: Array<{
    llm_call_id: string;
    created_at: string;
    plan_id: string | null;
    task_id: string | null;
    task_title: string | null;
    agent: string;
    scope: string;
    attempt: number;
    review_attempt: number;
    error_code: string | null;
    validator_error: string | null;
    total_score?: number | null;
    action_required?: string | null;
  }>;
  edges: Array<{ from: string; to: string; edge_type: WorkflowEdgeType }>;
  groups: Array<{ group_type: "ATTEMPT"; id: string; attempt: number; node_ids: string[] }>;
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
