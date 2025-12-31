# DB Schema & Migrations

## 数据库位置
- 默认：`state/state.db`
- 迁移：`state/migrations/*.sql`（只增不改，靠版本演进）

## 关键表（高层）
- `plans`：计划元信息（`plan_id/title/root_task_id/created_at/...`）
- `task_nodes`：任务节点（`task_id/plan_id/node_type/status/attempt_count/active_artifact_id/...`）
- `task_edges`：依赖/分解关系（`DECOMPOSE/DEPENDS_ON/ALTERNATIVE`）
- `input_requirements` + `input_evidence`：输入需求与绑定证据
- `artifacts`：任务产物（`path/format/sha256/...`）
- `reviews`：审核结果（评分/建议等）
- `task_events`：事件流（`ERROR/STATUS_CHANGED/ARTIFACT_CREATED/...`）
- `llm_calls`：LLM 遥测（prompt/raw/parsed/normalized/validator_error；**无外键**，允许先记日志后入库计划/任务）

## 自检与修复
- 自检：`agent_cli.py doctor --plan-id <PLAN_ID>`
  - 典型问题：缺 Root 节点、缺 DECOMPOSE、孤儿 edge / event 等
- 修复：`agent_cli.py repair-db --plan-id <PLAN_ID>`
  - 安全修复：补 root 节点、补 Root→子任务 的最小 DECOMPOSE 边（让 Root 可聚合 DONE）

## 重置
- `agent_cli.py reset-db`：删除整个 `state/state.db`（等同从零开始）
- 可选清文件：
  - `--purge-workspace`：清 `workspace/*`
  - `--purge-tasks`：清 `tasks/*`
  - `--purge-logs`：清 `logs/*`

