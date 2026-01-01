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
  plans: Array<{ plan_id: string; title: string; root_task_id: string; created_at: string }>;
  ts: string;
}

export interface ConfigResp {
  runtime_config: unknown;
  paths: Record<string, string>;
}

