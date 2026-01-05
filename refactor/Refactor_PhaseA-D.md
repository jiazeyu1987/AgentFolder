# Refactor Plan (Phase A–D): SSOT + Event-First (Single-Machine Serial)

## 背景 / 痛点（必须解决）

1) **事件/状态/解释逻辑分散**：`run.py`、`core/readiness.py`、`core/observability.py`、`core/reporting.py`、`dashboard_backend/app.py`、`dashboard_ui` 多处各自做判断/解释，导致“看起来卡住/其实回滚/根节点误 DONE”这类漂移与难排查问题。
2) **同一概念多套实现**：例如 `deliverables/manifest` 既在 `core/deliverables.py` 又在 `core/manifest.py`，schema 漂移会直接导致 UI/cleanup/export 行为不一致。
3) **关键语义缺少单一入口**：例如 `plan 是否 done` / `root GOAL done` 的规则曾在多个地方不一致，并已出现真实 bug。

## 总原则（硬规则）

- **Event-first**：任何“状态变化/决策/错误/产物生成”都先写 `workflow_events`（或统一事件表），SSOT 再从事件+DB 指针读取事实。
- **单机串行**：默认同一时刻最多一个 long job（create-plan/run/export/...），无需并发锁/分布式。
- **SSOT**：UI/CLI/backend **禁止拼 SQL 自己推断**。所有“解释与下一步怎么做”只来自 `core/ssot` 的快照输出。
- **不改业务语义**：先不改调度/重试/评分门槛/状态流转，只做“统一事实源 + 统一解释 + 统一事件”。
- **禁止 env var**：配置只读/只写 `runtime_config.json`。

---

## Phase A（1–2 天）：建立 SSOT 边界与契约

### 目标（可验收）

- 新增 `core/ssot/types.py`：集中定义 `PlanSnapshotV*`、`ReasonCode`、`NextStep`、`DeliverablesManifestV*`、`FinalDeliverableV*` 等 schema/枚举（唯一真相）。
- 新增 `core/ssot/snapshot.py`：统一入口 `get_plan_snapshot(conn, plan_id|top_task_hash, workflow_mode) -> PlanSnapshotV*`。
- CLI `agent_cli.py status --brief`、Backend `/api/plan_snapshot`、UI 顶栏与节点卡点解释 **完全一致**（同字段、同 reason code、同 next steps）。

### 具体改动

1) **新模块**
   - `core/ssot/types.py`
     - 固定字段结构与枚举：`ReasonCode`（WAITING_INPUT/WAITING_REVIEW/RUNNABLE/WAITING_EXTERNAL/FAILED/DONE…）
     - `PlanSnapshot` 最小字段：`plan/summary/reasons/inputs_needed/waiting_review/recent_errors/final_deliverable/next_steps/current_job_step?`
   - `core/ssot/snapshot.py`
     - 内部只做聚合：复用现有 `reporting/doctor/graph/deliverables` 的**数据读取**，但“解释与 reason 归一”只在这里。

2) **替换调用点**
   - CLI：`status --brief` 只调用 snapshot，不再自拼逻辑。
   - Backend：`/api/plan_snapshot` 只返回 snapshot JSON，不增加新的推断字段。
   - UI：顶栏“卡点/下一步”只渲染 snapshot，不从 graph 推断。

### 测试（必须）

- 新增：`tests/test_phaseA_snapshot_contract.py`
  - 构造临时 DB：缺输入/等评审/有错误/有 final.json 四类场景
  - 断言：snapshot reason code 与 next_steps 固定且可读

### 验收命令

- `D:\miniconda3\python.exe -m pytest -q`
- `cd dashboard_ui; npm run build`

---

## Phase B（2–3 天）：事件驱动统一（杜绝“看起来跑了但没落库/没法解释”）

### 目标（可验收）

- create-plan/run/export/rewrite/cleanup 都有明确 step 事件：`JOB_STARTED/STEP_STARTED/STEP_FINISHED/JOB_FINISHED`。
- 任何“继续/停止/重试/跳过”的决策必须有 `DECISION_MADE`（含 why/next）。
- SSOT 快照能显示 `current_job_step` + 最近一次决策/错误（来自事件，不靠日志文本猜）。
- 关 UI 再开仍可恢复进度（事件即事实）。

### 具体改动

1) **事件规范化**
   - `core/ssot/events.py`（或 `core/workflow_events.py` 扩展）：
     - 事件类型常量 + payload 规范（最小字段）

2) **提交策略统一**
   - 新增 `core/ssot/tx.py`：
     - `commit_or_die(conn)` / `commit_best_effort(conn)`：所有 long job 在 step 边界 commit，避免隐式事务回滚导致 UI 看不到结果。
   - 禁止在 `emit_workflow_event` 内部 commit（改由外层统一提交），避免事件与状态错位。

3) **在关键点埋点**
   - create-plan：每个阶段/attempt/review_attempt 写 STEP_* + LLM_CALL_* + DECISION_MADE。
   - run：每轮写 STEP_*；每次状态变化写 `STATUS_CHANGED`；护栏触发写 `GUARDRAIL_HIT`。
   - export：写 EXPORT 的 STEP_* + EXPORT_DONE。

### 测试（必须）

- `tests/test_phaseB_event_sequence.py`
  - 用 fake 数据插入（不跑真实 LLM）
  - 断言事件序列：job/step/decision/error 顺序可解释

### 验收命令

- `D:\miniconda3\python.exe -m pytest -q`

---

## Phase C（2–4 天）：统一“plan/run 关键语义”（DONE/READY/阻塞/依赖）

### 目标（可验收）

- 新增 `core/ssot/semantics.py`：所有核心判断只存在一份：
  - `is_plan_done`
  - `is_plan_blocked_waiting_user`
  - `compute_reasons`
  - `runnable_nodes`
- `run.py` 与 `core/readiness.py` 不再自行定义 plan done/block 逻辑；只调用 `semantics.py`。
- 不再出现 “root 误 DONE 导致 run 提前退出”。

### 具体改动

1) **语义单一入口**
   - `core/ssot/semantics.py` 实现并被：
     - `run.py`（循环退出条件）
     - `core/readiness.py`（只做状态推进；root 规则也统一调用 semantics）
     - `core/ssot/snapshot.py`（展示 reasons/next steps）

2) **readiness 去解释化**
   - readiness 只做状态推进 + 事件记录（STATUS_CHANGED），不负责“为什么”。

### 测试（必须）

- `tests/test_phaseC_plan_done_semantics.py`
  - 构造“不完整 DECOMPOSE 边”的 plan
  - 断言：plan done 只由 ACTION 完成度决定；root 状态不会影响 run 是否继续

---

## Phase D（2–3 天）：合并 manifest/deliverables 双实现（消除 schema 漂移）

### 目标（可验收）

- manifest/final schema 只由 `core/ssot/types.py` 定义一份。
- manifest 的写入点只有一个（建议集中在 `core/deliverables.py`）。
- cleanup/UI/export 都只依赖同一 schema，不会因 drift 崩。

### 具体改动

1) **选定唯一写入实现**
   - 保留 `core/deliverables.py` 作为唯一 writer：
     - `write_manifest(conn, plan_id, out_dir)`（只写清单，路径相对 deliverables root）
     - `write_final(conn, plan_id, out_dir)`（只写 final.json）
   - `core/manifest.py` 变为 thin wrapper 或删除（保留向后兼容 import）

2) **统一被引用 schema**
   - cleanup：只按 `types.py` 中的字段读取 artifact_id（final.json/manifest.json）
   - UI：展示 deliverables 只通过 snapshot.final_deliverable（SSOT）与 manifest 结构

### 测试（必须）

- `tests/test_phaseD_manifest_single_source.py`
  - 验证 manifest/final schema_version/字段与 `types.py` 摘要一致
  - 验证 cleanup 不会因为 schema drift 找不到 artifact_id

---

## 阶段性回滚策略（推荐）

- 每个 Phase 都是“薄改动 + 测试锁死”，可独立回滚：
  - Phase A：只影响展示层（snapshot 入口）
  - Phase B：只增加事件/commit（不改业务语义）
  - Phase C：统一语义入口（修复漂移）
  - Phase D：合并 manifest writer（消除 schema drift）

---

## 最终验收标准（全局）

1) 同一 plan 在 CLI/Backend/UI 中显示的 “状态/卡点/下一步” 完全一致。
2) 任意“卡住/重试/停止”都能在事件中追溯：什么时候、为什么、下一步是什么。
3) manifest/final schema 不再分叉；cleanup/export/UI 稳定。
4) 不再出现：
   - “看起来 run 了但 DB 没变化”（隐式事务回滚）
   - “root 误 DONE 导致 run 提前退出”

