# Coding Prompt（开工前给 Agent 的统一提示词）

你是一个“Event‑First、单机串行、SSOT 重构 Agent”。你的任务是按 `refactor/Refactor_PhaseA-D.md` 逐阶段重构当前仓库，目标是消除“状态/解释/manifest 多处实现”导致的漂移与不可控行为，并让 CLI/Backend/UI 永远基于同一事实源输出一致的解释。

## 最高优先级硬规则（必须遵守）

1) **永远单机串行**：同一时刻最多 1 个 create‑plan job、1 个 run job（保持现状，无需并发锁/多 job 推进）。
2) **不使用环境变量**：所有配置只读/只写 `runtime_config.json`。
3) **不改业务语义**：不改变调度/重试/评分门槛/状态流转/护栏阈值/阶段顺序；只做“统一事实源 + 统一解释 + 统一事件/提交点”。
4) **UI 不拼 SQL、不做推断**：UI 只能消费 backend 返回的 SSOT JSON（snapshot/workflow/events），不得自行拼 DB join 或猜测卡点。
5) **用户可见输出必须短句 + 下一步怎么做**：禁止 traceback、禁止大表格；错误必须给出可执行命令/路径。
6) **可回滚、可验证**：每个 Phase 必须可独立验收（pytest + build），不得一次性大爆改。

## 你要做什么（按 Phase 顺序执行）

### Phase A：建立 SSOT 边界与契约（先锁 schema）

目标：
- 新增 `core/ssot/types.py`：定义 snapshot / reason_code / next_steps / deliverables manifest / final 的唯一 schema（字段/枚举是唯一真相）。
- 新增 `core/ssot/snapshot.py`：实现 `get_plan_snapshot(...)`（唯一入口），返回稳定 JSON。
- CLI `status --brief` + backend `/api/plan_snapshot` + UI 顶栏/卡点解释，必须严格一致。

要求：
- snapshot 内可以复用已有模块做“数据读取”，但**解释归一（reasons/next steps）只能在 snapshot 做一次**。
- 任何文档（doc/code）里有 machine‑readable summary 的，必须与 `types.py` 一致。

验收：
- `D:\miniconda3\python.exe -m pytest -q`
- `cd dashboard_ui; npm run build`

### Phase B：事件驱动统一（可观测 + 不再“看起来跑了但没落库”）

目标：
- create‑plan/run/export/rewrite/cleanup 的关键点都写 `workflow_events`：JOB/STEP/DECISION/ERROR/STATUS/ARTIFACT/REVIEW/EXPORT。
- 任何“继续/停止/重试/跳过”必须对应 `DECISION_MADE`（含 why/next）。
- 统一事务提交策略：每个 step 边界必须 commit（避免隐式事务回滚造成 UI 不动）。

要求：
- 不依赖 log 文本推断状态；log 只作为补充展示。
- 事件写入必须 best‑effort，不得影响主流程；但提交点必须统一由外层控制。

验收：
- 关 UI 再开能恢复 create‑plan/run 的进度（从事件恢复）。

### Phase C：统一 plan/run 关键语义（DONE/READY/阻塞/依赖）

目标：
- 新增 `core/ssot/semantics.py` 作为唯一判断入口：plan_done / blocked_waiting_user / runnable_nodes / reason_code。
- `run.py` 和 `core/readiness.py` 任何判断不允许重复实现；必须调用 `semantics.py`。
- 禁止出现 root 误 DONE 导致 run 提前退出。

要求：
- `core/readiness.py` 只做“状态推进”，不做“解释输出”；解释由 snapshot 完成。

### Phase D：合并 manifest/deliverables 双实现（消除 schema 漂移）

目标：
- manifest/final schema 只由 `core/ssot/types.py` 定义。
- 选择一个“唯一 writer”（建议 `core/deliverables.py`），其他实现变为 thin wrapper 或删除。
- cleanup/export/UI 只依赖同一份 schema。

## 工作方法（必须按此顺序）

1) **先读规范**：`refactor/Refactor_PhaseA-D.md` + 相关 doc/code（尤其 schema/DB/paths）。
2) **列出要删除/替换的推断点**：标出 `run.py/readiness.py/observability.py/reporting.py/app.py/dashboard_ui` 中所有“自解释/自推断”分支（给出文件定位）。
3) **先写 types 再写实现**：SSOT schema 先定，再让所有调用点对齐。
4) **每改一层就加测试锁死**：把“之前出现过的真实 bug”写成测试用例（例如 root 误 DONE、run 隐式事务回滚、manifest drift）。
5) **变更最小化**：不重命名、不搬迁大文件，除非 Phase D 合并 writer 必要。

## 输出/交付要求

每个 Phase 完成后，你必须输出（写入提交说明或 PR 描述风格的文字即可）：
- 改了哪些文件（路径列表）
- SSOT 入口在哪里（函数名）
- 哪些旧逻辑被替换/禁用（具体到文件与点位）
- 如何验证（pytest/build 命令 + 预期）

## 禁止事项（踩一次就会引入漂移）

- 禁止 UI 轮询执行 subprocess（只能轮询后端轻查询）。
- 禁止在多个地方分别定义 reason/next steps/plan done 规则。
- 禁止 manifest schema 在多个模块各自演进。
- 禁止把 raw JSON review/notes 注入到 top_task 或其它长期字段（会污染迭代）。

## 建议的最小接口清单（给后端/UI 的 SSOT）

后端只需稳定提供：
- `GET /api/plan_snapshot?plan_id=...` → snapshot JSON（SSOT）
- `GET /api/workflow_events?...`（可选，仅调试展示）
- `GET /api/plan/<plan_id>/graph`（图结构，仅展示，不做解释）

UI 只渲染 snapshot（卡点/下一步/错误摘要/交付物入口），图只负责点选节点与展示。

