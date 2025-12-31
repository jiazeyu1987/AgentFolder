# State DB Migration v1 (SQLite)
## state.db 建库/迁移 SQL（含 skill_runs）

> 目标：提供一份可直接执行的 SQLite 建库脚本（MVP v1）。
> - 覆盖任务树运行态（task_nodes / task_edges / requirements / evidences）
> - 覆盖产物与审查（artifacts / approvals / reviews）
> - 覆盖技能执行记录（skill_runs）
> - 覆盖审计（task_events）
>
> 说明：
> - 本脚本可作为 `migrations/001_init.sql`
> - 所有 ID 推荐使用 UUID（TEXT）

---

## 1. PRAGMA（建议）
```sql
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
```

---

## 2. 核心表

### 2.1 plans
```sql
CREATE TABLE IF NOT EXISTS plans (
  plan_id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  owner_agent_id TEXT NOT NULL,
  root_task_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  constraints_json TEXT
);
```

### 2.2 task_nodes
```sql
CREATE TABLE IF NOT EXISTS task_nodes (
  task_id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL,
  node_type TEXT NOT NULL,             -- GOAL|ACTION|CHECK
  title TEXT NOT NULL,
  goal_statement TEXT,
  rationale TEXT,
  owner_agent_id TEXT NOT NULL,

  priority INTEGER DEFAULT 0,

  status TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING|READY|IN_PROGRESS|BLOCKED|DONE|FAILED|ABANDONED
  blocked_reason TEXT,                      -- WAITING_INPUT|WAITING_APPROVAL|WAITING_SKILL|WAITING_EXTERNAL

  attempt_count INTEGER NOT NULL DEFAULT 0,
  confidence REAL DEFAULT 0.5,

  active_branch INTEGER NOT NULL DEFAULT 1, -- 1 active, 0 inactive

  active_artifact_id TEXT,                  -- points to artifacts.artifact_id (latest approved/active artifact)

  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,

  FOREIGN KEY(plan_id) REFERENCES plans(plan_id)
);
```

### 2.3 task_edges
```sql
CREATE TABLE IF NOT EXISTS task_edges (
  edge_id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL,
  from_task_id TEXT NOT NULL,
  to_task_id TEXT NOT NULL,
  edge_type TEXT NOT NULL,              -- DECOMPOSE|DEPENDS_ON|ALTERNATIVE
  metadata_json TEXT,                   -- e.g. { "and_or": "AND|OR", "group_id": "..." }
  created_at TEXT NOT NULL,

  FOREIGN KEY(plan_id) REFERENCES plans(plan_id)
);
```

---

## 3. 输入需求与证据

### 3.1 input_requirements
```sql
CREATE TABLE IF NOT EXISTS input_requirements (
  requirement_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,                   -- FILE|CONFIRMATION|SKILL_OUTPUT
  required INTEGER NOT NULL,            -- 0/1
  min_count INTEGER NOT NULL DEFAULT 1,
  allowed_types_json TEXT,
  source TEXT NOT NULL,                 -- USER|AGENT|ANY
  validation_json TEXT,
  created_at TEXT NOT NULL,

  FOREIGN KEY(task_id) REFERENCES task_nodes(task_id)
);
```

### 3.2 evidences
```sql
CREATE TABLE IF NOT EXISTS evidences (
  evidence_id TEXT PRIMARY KEY,
  requirement_id TEXT NOT NULL,
  evidence_type TEXT NOT NULL,          -- FILE|CONFIRMATION|SKILL_OUTPUT
  ref_id TEXT NOT NULL,                 -- file_sha256 OR confirmation_id OR skill_run_id
  ref_path TEXT,                        -- optional
  sha256 TEXT,                          -- for file evidence / artifact evidence
  added_at TEXT NOT NULL,

  FOREIGN KEY(requirement_id) REFERENCES input_requirements(requirement_id)
);
```

### 3.3 evidences 唯一性约束（幂等）
> 同一 requirement 同一 ref_id 不应重复绑定。
```sql
CREATE UNIQUE INDEX IF NOT EXISTS uidx_evidence_req_ref
ON evidences(requirement_id, ref_id);
```

---

## 4. 产物与审批

### 4.1 artifacts
```sql
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
```

### 4.2 approvals
```sql
CREATE TABLE IF NOT EXISTS approvals (
  approval_id TEXT PRIMARY KEY,
  artifact_id TEXT NOT NULL,
  status TEXT NOT NULL,                 -- PENDING|APPROVED|REJECTED
  approver TEXT,
  comment TEXT,
  decided_at TEXT,
  created_at TEXT NOT NULL,

  FOREIGN KEY(artifact_id) REFERENCES artifacts(artifact_id)
);
```

---

## 5. 审查结果（Rubric 输出）

### 5.1 reviews
```sql
CREATE TABLE IF NOT EXISTS reviews (
  review_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  reviewer_agent_id TEXT NOT NULL,      -- xiaojing / xiaoxie
  total_score INTEGER NOT NULL,
  breakdown_json TEXT NOT NULL,         -- rubric breakdown
  suggestions_json TEXT NOT NULL,       -- structured suggestions
  summary TEXT,
  action_required TEXT NOT NULL,        -- MODIFY|APPROVE|REQUEST_EXTERNAL_INPUT
  created_at TEXT NOT NULL,

  FOREIGN KEY(task_id) REFERENCES task_nodes(task_id)
);
```

---

## 6. Skills 执行记录（不做 mock 的关键）

### 6.1 skill_runs
```sql
CREATE TABLE IF NOT EXISTS skill_runs (
  skill_run_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  plan_id TEXT NOT NULL,

  skill_name TEXT NOT NULL,
  inputs_json TEXT NOT NULL,            -- [{path, sha256, kind}...]
  params_json TEXT,
  status TEXT NOT NULL,                 -- RUNNING|SUCCEEDED|FAILED

  output_artifacts_json TEXT,           -- [{path, sha256, name, format}...]
  output_evidences_json TEXT,           -- [{requirement_id, ref_id, sha256, path}...]
  error_code TEXT,
  error_message TEXT,

  started_at TEXT NOT NULL,
  finished_at TEXT,

  FOREIGN KEY(task_id) REFERENCES task_nodes(task_id),
  FOREIGN KEY(plan_id) REFERENCES plans(plan_id)
);
```

### 6.2 skill_runs 幂等建议（可选）
> 若你希望同输入同参数不重复执行，可建立“幂等键”索引：
- idempotency_key = sha256(skill_name + sorted_input_hashes + params_json)
```sql
ALTER TABLE skill_runs ADD COLUMN idempotency_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS uidx_skill_runs_idem
ON skill_runs(idempotency_key);
```

> SQLite ALTER TABLE ADD COLUMN 对已存在表可执行；首次建库可直接把字段放进 CREATE TABLE。

---

## 7. 审计日志

### 7.1 task_events
```sql
CREATE TABLE IF NOT EXISTS task_events (
  event_id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL,
  task_id TEXT,
  event_type TEXT NOT NULL,             -- STATUS_CHANGED|EVIDENCE_ADDED|ARTIFACT_CREATED|APPROVAL_DECIDED|REWRITE|TIMEOUT|SKILL_RUN
  payload_json TEXT,
  created_at TEXT NOT NULL,

  FOREIGN KEY(plan_id) REFERENCES plans(plan_id)
);
```

---

## 8. 索引建议
```sql
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
```

---

## 9. 最小运行校验（你应当能跑通）

- 插入一个 plan + root task
- 插入若干 task_nodes + edges
- 插入 requirements
- 添加 evidence → 触发 READY
- 生成 artifact → READY_TO_CHECK（或在应用层表示）
- review 写入 → DONE / ToBeModify
- skill_runs 写入 → artifacts/evidences 更新

---
