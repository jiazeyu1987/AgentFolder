# Refactor Plan (Event-First, Single-Machine Serial)

## Scope / Goal
本重构不是为修单点 bug，而是为了后续**更容易加功能、更少连锁破坏、更强可观测性**。

硬前提：
- **event-first**：任何关键行为先写事件，再由事件派生状态/视图。
- **永远单机串行**：同一时刻只允许 1 个 workflow/job 在跑（无需并发锁/分布式一致性）。
- **SSOT**：CLI / Backend / UI 不各自推断逻辑；统一从 core 的聚合器拿“同一份解释”。

非目标：
- 不引入并发/分布式执行。
- 不做大规模 UI 重做（UI 只消费 SSOT）。

## 当前主要问题（架构视角）
- **流程隐式**：create-plan/run 主要靠散落 if/continue + meta_json/scope 约定组合，难扩展、难定位“卡住点”。
- **事实源分裂**：同一概念（状态/原因/下一步）在 CLI/core/backend/UI 各写一套，容易漂移。
- **可观测性后验**：很多“决策点/重试原因/停止原因”没第一手记录，只能猜（尤其卡在 LLM 调用前/中时）。
- **错误/日志不可靠**：log 可能为空/旧；部分失败没有落库，导致 UI 无法解释。

## 目标形态：分层 + 显式状态机 + 事件溯源

### 分层（建议边界）
1) **Domain（纯数据结构）**
   - Plan/Node/Edge/Artifact/Review/Rubric/Job/Guardrails
2) **Engine（工作流状态机，显式 Step）**
   - create-plan / run / export / rewrite / cleanup 等
3) **Persistence（Repo/SQL）**
   - 只做 CRUD/事务；不含业务分支
4) **Observability Views（只读聚合器，SSOT）**
   - snapshot/report/workflow_graph/errors/audit
5) **Adapters（CLI/FastAPI/React）**
   - 参数解析、调用 core、渲染输出

关键原则：**业务判断只允许出现在 Engine 与 Views**；Adapters 不能“自己解释/自己拼 SQL”。

## Event-First 设计

### 事件表（核心）
新增统一事件表（可与现有 `task_events/audit_events` 逐步合并，先不强行合并）：
- `event_id`（uuid）
- `created_at`（utc iso）
- `workflow`（CREATE_PLAN|RUN|EXPORT|REWRITE|CLEANUP）
- `event_type`（见下）
- 关联键：`top_task_hash`、`plan_id?`、`task_id?`、`llm_call_id?`、`job_id?`
- `severity`（INFO|WARN|ERROR）
- `message`（<=500，面向用户短句）
- `payload_json`（结构化细节：step、attempt、score、expected/actual、paths、hint）

### 事件类型（最小枚举，后续可扩展）
通用：
- `JOB_STARTED` / `JOB_FINISHED`
- `STEP_STARTED` / `STEP_FINISHED`
- `DECISION_MADE`（例如：score<threshold → NEXT_STEP=RETRY_GEN）
- `ERROR_RAISED`（统一错误对象：code/json_path/expected/actual/example_fix/hint）

LLM：
- `LLM_CALL_REQUESTED`（将要发起）
- `LLM_CALL_RECORDED`（已落库到 llm_calls）
- `CONTRACT_VALIDATION_FAILED`（带 json_path 等）

输入/依赖：
- `INPUT_REQUIRED`（写 required_docs）
- `INPUT_SATISFIED`（evidence 写入）

交付物/评审：
- `ARTIFACT_CREATED`（active）
- `REVIEW_WRITTEN`（reviewed_artifact_id/verdict）
- `ARTIFACT_APPROVED`（approved_artifact_id 更新）

护栏/清理：
- `GUARDRAIL_HIT`
- `CLEANUP_APPLIED`

### 单机串行的 Job/Step 模型
所有长流程统一为：
- `Job`：一个执行单元（create-plan/run/export…）
- `Step`：可命名、可追溯、可超时、可重试

每个 step 必须：
- 在开始时写 `STEP_STARTED`（含 step_name、attempt、stage）
- 在结束时写 `STEP_FINISHED`（ok、耗时、输出引用）
- 失败时写 `ERROR_RAISED`（可读+可执行下一步）

这能保证：哪怕卡在 LLM 调用前/中，也能通过“最后一个 STEP_STARTED”定位卡点。

## Rubric 固化（Plan Review 两阶段的标准形态）
以 create-plan 为例：
1) `PLAN_RUBRIC`：先产出/确认 rubric（评分标准），存到 DB 并固定 version/id
2) `PLAN_REVIEW`：后续所有阶段（STRUCTURE/BINDINGS/EXECUTION）必须引用 rubric_id 评分

要求：
- 任何 review 结果必须可追溯：review 引用 rubric_id + reviewed_object_hash
- 评分决策只看 `score>=threshold`（阈值来自 runtime_config），并写 `DECISION_MADE`

## SSOT Views（给 CLI/UI 的唯一解释）
核心产物：`snapshot(plan_id/top_task_hash)`：
- summary：DONE/NOT_DONE、当前 workflow/step、是否卡住
- reasons：WAITING_INPUT/WAITING_REVIEW/WAITING_EXTERNAL/FAILED/RUNNABLE 等（枚举化）
- inputs_needed：required_docs 路径 + item 列表
- recent_errors：按 event/llm_calls 归并（与节点/llm_call_id 绑定）
- workflow_graph：用事件与 llm_calls 生成链路（不靠 UI 猜）
- final_deliverable：final.json/entrypoint
- next_steps：可执行命令列表（run/doctor/export/reset…）

Adapters 规则：
- CLI `status --brief`、FastAPI `/api/plan_snapshot`、UI 都只展示 snapshot。

## 迁移路径（分三期，每期可验收）

### Phase 1：收敛“解释层”（不改业务行为）
- 把现有分散逻辑迁移到 `core/observability.py`（或等价聚合器）
- CLI/backend/UI 全部改为消费 snapshot
- 验收：同一 plan 在 CLI 与 UI 的“卡住原因/缺输入/下一步”一致

### Phase 2：create-plan 引入 Job/Step 事件（显式状态机）
- create-plan 的每个阶段写 step 事件（含 attempt/stage/review_attempt）
- 所有失败都落 `ERROR_RAISED`（不依赖 log 文本）
- 验收：workflow “不动”时仍能从 snapshot 看到当前 step 与卡住原因

### Phase 3：run/export/rewrite 统一到同一事件模型
- READY_TO_CHECK → CHECK → DONE/TO_BE_MODIFY 的决策写事件
- export 产物/入口定位写事件
- cleanup 裁剪策略写事件（可追溯）
- 验收：同一任务多轮 artifact+review 可完整追溯；不会出现“无事件但流程断了”

## 约束与不变量（必须用测试锁死）
- contracts（`core/contracts_v2.py` 与 `doc/code/Contracts.md` machine-readable 摘要一致）
- db/migrations 可 fresh init + upgrade
- doctor 在 v1/v2 模式下的挡板行为稳定
- snapshot 输出 schema 稳定（关键键/枚举不可漂移）
- runtime_config.json 是唯一配置来源（禁止 env vars）

## 对 UI 的影响（原则）
- UI 不再轮询启动子进程；只轮询 snapshot/workflow/errors
- UI 不再拼 SQL/推断阶段；显示 step/reason/hint/next_steps

