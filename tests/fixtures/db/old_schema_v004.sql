PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS schema_migrations (
  filename TEXT PRIMARY KEY,
  applied_at TEXT NOT NULL
);

-- 001_init.sql
CREATE TABLE IF NOT EXISTS plans (
  plan_id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  owner_agent_id TEXT NOT NULL,
  root_task_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  constraints_json TEXT
);

CREATE TABLE IF NOT EXISTS task_nodes (
  task_id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL,
  node_type TEXT NOT NULL,
  title TEXT NOT NULL,
  goal_statement TEXT,
  rationale TEXT,
  owner_agent_id TEXT NOT NULL,
  priority INTEGER DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'PENDING',
  blocked_reason TEXT,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  confidence REAL DEFAULT 0.5,
  active_branch INTEGER NOT NULL DEFAULT 1,
  active_artifact_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(plan_id) REFERENCES plans(plan_id)
);

CREATE TABLE IF NOT EXISTS task_edges (
  edge_id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL,
  from_task_id TEXT NOT NULL,
  to_task_id TEXT NOT NULL,
  edge_type TEXT NOT NULL,
  metadata_json TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(plan_id) REFERENCES plans(plan_id)
);

CREATE TABLE IF NOT EXISTS input_requirements (
  requirement_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,
  required INTEGER NOT NULL,
  min_count INTEGER NOT NULL DEFAULT 1,
  allowed_types_json TEXT,
  source TEXT NOT NULL,
  validation_json TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES task_nodes(task_id)
);

CREATE TABLE IF NOT EXISTS evidences (
  evidence_id TEXT PRIMARY KEY,
  requirement_id TEXT NOT NULL,
  evidence_type TEXT NOT NULL,
  ref_id TEXT NOT NULL,
  ref_path TEXT,
  sha256 TEXT,
  added_at TEXT NOT NULL,
  FOREIGN KEY(requirement_id) REFERENCES input_requirements(requirement_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_evidence_req_ref
ON evidences(requirement_id, ref_id);

CREATE TABLE IF NOT EXISTS artifacts (
  artifact_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  name TEXT NOT NULL,
  path TEXT NOT NULL,
  format TEXT,
  version INTEGER DEFAULT 1,
  sha256 TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES task_nodes(task_id)
);

CREATE TABLE IF NOT EXISTS approvals (
  approval_id TEXT PRIMARY KEY,
  artifact_id TEXT NOT NULL,
  status TEXT NOT NULL,
  approver TEXT,
  comment TEXT,
  decided_at TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(artifact_id) REFERENCES artifacts(artifact_id)
);

CREATE TABLE IF NOT EXISTS reviews (
  review_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  reviewer_agent_id TEXT NOT NULL,
  total_score INTEGER NOT NULL,
  breakdown_json TEXT NOT NULL,
  suggestions_json TEXT NOT NULL,
  summary TEXT,
  action_required TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES task_nodes(task_id)
);

CREATE TABLE IF NOT EXISTS skill_runs (
  skill_run_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  plan_id TEXT NOT NULL,
  skill_name TEXT NOT NULL,
  inputs_json TEXT NOT NULL,
  params_json TEXT,
  status TEXT NOT NULL,
  output_artifacts_json TEXT,
  output_evidences_json TEXT,
  error_code TEXT,
  error_message TEXT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  idempotency_key TEXT,
  FOREIGN KEY(task_id) REFERENCES task_nodes(task_id),
  FOREIGN KEY(plan_id) REFERENCES plans(plan_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_skill_runs_idem
ON skill_runs(idempotency_key);

CREATE TABLE IF NOT EXISTS task_events (
  event_id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL,
  task_id TEXT,
  event_type TEXT NOT NULL,
  payload_json TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(plan_id) REFERENCES plans(plan_id)
);

CREATE INDEX IF NOT EXISTS idx_task_nodes_plan ON task_nodes(plan_id);
CREATE INDEX IF NOT EXISTS idx_task_nodes_status ON task_nodes(status);
CREATE INDEX IF NOT EXISTS idx_task_edges_to ON task_edges(to_task_id);
CREATE INDEX IF NOT EXISTS idx_req_task ON input_requirements(task_id);
CREATE INDEX IF NOT EXISTS idx_evi_req ON evidences(requirement_id);
CREATE INDEX IF NOT EXISTS idx_art_task ON artifacts(task_id);
CREATE INDEX IF NOT EXISTS idx_app_art ON approvals(artifact_id);
CREATE INDEX IF NOT EXISTS idx_rev_task ON reviews(task_id);
CREATE INDEX IF NOT EXISTS idx_evt_plan ON task_events(plan_id);
CREATE INDEX IF NOT EXISTS idx_skill_task ON skill_runs(task_id);

-- 002_prompts.sql
CREATE TABLE IF NOT EXISTS prompts (
  prompt_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  agent TEXT,
  version INTEGER NOT NULL,
  path TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_prompts_kind_agent_sha
ON prompts(kind, agent, sha256);

CREATE INDEX IF NOT EXISTS idx_prompts_kind_agent_version
ON prompts(kind, agent, version);

-- 003_task_nodes_tags.sql
ALTER TABLE task_nodes ADD COLUMN tags_json TEXT;

-- 004_prompts_name_and_indexes.sql
ALTER TABLE prompts ADD COLUMN name TEXT NOT NULL DEFAULT 'default';

DROP INDEX IF EXISTS uidx_prompts_kind_agent_sha;
DROP INDEX IF EXISTS idx_prompts_kind_agent_version;

CREATE UNIQUE INDEX IF NOT EXISTS uidx_prompts_kind_name_agent_sha
ON prompts(kind, name, agent, sha256);

CREATE INDEX IF NOT EXISTS idx_prompts_kind_name_agent_version
ON prompts(kind, name, agent, version);

INSERT INTO schema_migrations(filename, applied_at) VALUES('001_init.sql', datetime('now'));
INSERT INTO schema_migrations(filename, applied_at) VALUES('002_prompts.sql', datetime('now'));
INSERT INTO schema_migrations(filename, applied_at) VALUES('003_task_nodes_tags.sql', datetime('now'));
INSERT INTO schema_migrations(filename, applied_at) VALUES('004_prompts_name_and_indexes.sql', datetime('now'));

-- Minimal data to verify upgrade doesn't drop it.
INSERT INTO plans(plan_id, title, owner_agent_id, root_task_id, created_at, constraints_json)
VALUES('p_old', 'Old Plan', 'xiaobo', 't_root', datetime('now'), '{}');

INSERT INTO task_nodes(
  task_id, plan_id, node_type, title,
  goal_statement, rationale, owner_agent_id, tags_json,
  priority, status, blocked_reason, attempt_count, confidence, active_branch,
  active_artifact_id, created_at, updated_at
)
VALUES(
  't_root', 'p_old', 'GOAL', 'Root Task',
  'Old goal', NULL, 'xiaobo', '[]',
  0, 'PENDING', NULL, 0, 0.5, 1,
  NULL, datetime('now'), datetime('now')
);

