你是一个资深全栈工程师。请在仓库 `d:\ProjectPackage\AgentFolder` 中按 `doc/refactor/END_STATE_ARCHITECTURE.md` 的终态目标，执行“分阶段、可回滚”的重构。禁止大爆炸式改造：每个阶段都必须保持系统可运行、可测试、可验证。

---

# 总目标（必须达成）
后端：
- Routes 薄：仅做 HTTP/参数校验/响应组装
- Services 承载用例：create-plan/run/export/reset 等
- `core/queries/*`：所有 read-only SQL 集中、显式 SELECT、契约稳定
- `core/state_machine/*`：唯一状态变更入口（更新 + task_events + workflow_events + audit）
- RunSupervisor 与 DB 状态分离：run 进程只提供 liveness/status，任务推进看 DB/events

前端：
- `dashboard_ui/src/api.ts` 只负责 HTTP
- hooks 负责数据拉取/轮询/重试/错误态
- 统一 RequestState（loading/error/data）
- 刷新策略不打断图交互（轻量轮询只更新状态，不重排布局）

---

# 阶段划分（建议 5 个阶段，每个阶段一条 PR）

## Phase 1 — Backend Routes 拆分为 routers（不动业务逻辑）
**改动目标**：把 `dashboard_backend/app.py` 的 endpoint 拆出去，保持行为一致，降低后续改动耦合。

**新增/修改文件**
- 新增：`dashboard_backend/routes/__init__.py`
- 新增：`dashboard_backend/routes/run.py`（run/status/start/stop/once）
- 新增：`dashboard_backend/routes/plan.py`（/plans、/plan/{id}/graph、/plan_snapshot、/plan/create_async、/jobs 等与 plan 强相关的）
- 新增：`dashboard_backend/routes/llm.py`（/workflow、/llm_calls、/task/{id}/llm）
- 新增：`dashboard_backend/routes/misc.py`（/config、/prompt_file、/errors、/audit、/top_tasks 等）
- 修改：`dashboard_backend/app.py` 只保留：
  - `app = FastAPI(...)`
  - middleware
  - `include_router(...)`
  - 共享依赖（如 `_db_conn()`）可以暂留或迁到 `dashboard_backend/deps.py`

**职责迁移**
- 把 route 函数从 `dashboard_backend/app.py` 搬到对应 router 文件
- 保持原有 response_model（PR2 已对 graph/workflow 加了 response_model）

**风险点**
- import 循环：router 里不要 import `app`，只 import共享依赖函数/RunSupervisor/queries
- 路由路径、参数默认值、Query 限制要保持一致

**回归验证**
- 后端 import：`python -c "import dashboard_backend.app; print('ok')"`
- pytest：`python -m pytest -q doc/code`
- 手动：启动 dashboard，访问 Task Graph / Workflow Graph / LLM Explorer / create-plan/run 按钮，确认无 404

---

## Phase 2 — 引入 Services 层（把“用例”从 routes 抽离）
**改动目标**：routes 只负责 HTTP；所有用例逻辑归 services，便于测试与重用。

**新增/修改文件**
- 新增：`dashboard_backend/services/__init__.py`
- 新增：`dashboard_backend/services/run_service.py`
  - `start_run(max_iterations)`, `stop_run()`, `run_once()`, `get_run_status()`
  - 内部使用 `dashboard_backend/run_supervisor.py` 与 `_start_run_process/_stop_run_process`（可逐步搬入 service）
- 新增：`dashboard_backend/services/plan_service.py`
  - `get_graph(plan_id)`, `get_snapshot(plan_id)`, `list_plans()`, `create_plan_async(...)` 等
- 新增：`dashboard_backend/services/workflow_service.py`
  - `get_workflow(query)`（内部调用 `core/queries/workflow_query.py`）

**职责迁移**
- routes → services：所有“组合行为”（读写 DB + 组装返回）都放 services
- services 只返回 python dict/模型；routes 把它返回给 FastAPI

**风险点**
- 共享 db conn 的生命周期：保持 `with _db_conn()` 在 routes 或 services 二选一，但必须一致
- 不要在 services 里直接打印/exit

**回归验证**
- pytest：`python -m pytest -q doc/code`
- 手动：点 run/status、graph/workflow 页面正常

---

## Phase 3 — Read Models 完整化（queries 成为唯一读 SQL 来源）
**改动目标**：任何“读接口”都只从 `core/queries/*` 来，减少 SQL 分散与字段漏选。

**新增/修改文件**
- 已有：`core/queries/graph_query.py`, `core/queries/workflow_query.py`
- 新增（分批）：`core/queries/errors_query.py`, `core/queries/snapshot_query.py`, `core/queries/plans_query.py`
- 修改：services 中的 read path 改为只调用 queries

**职责迁移**
- 把散落在 routes/services 里的 SELECT 迁入 `core/queries/*`
- 每个 query 文件：
  - 只做 read-only SQL
  - SELECT 列表显式写全
  - 返回结构带 `schema_version`（如果是 API 读模型）
- 保留 facade：`core/graph.py`, `core/workflow_graph.py` 可以继续作为兼容入口

**风险点**
- queries 会变成核心依赖：要用契约测试锁住关键字段
- 性能：保持 limit、索引使用不变（当前只读 UI，一般可接受）

**回归验证**
- 契约测试必须覆盖：Graph/Workflow（已有 `doc/code/test_contract_endpoints_pr2.py`）
- pytest：`python -m pytest -q doc/code`
- 手动：Workflow Graph 不缺字段、无 500

---

## Phase 4 — State Machine 彻底收敛（禁止散落 status 更新）
**改动目标**：所有状态变更只通过 `core/state_machine/*`，并保证事件一致。

**新增/修改文件**
- 已有：`core/state_machine/task_status.py`, `core/state_machine/plan_stage.py`
- 新增（可选）：`core/state_machine/constants.py`（集中 status/stage 枚举）
- 修改：扫描并迁移所有 status 更新点（目标：仓库内除了 state_machine 不再出现 `UPDATE task_nodes SET status...`）
  - 重点：`run.py`, `core/readiness.py`, `core/errors.py`, `core/v2_review_gate.py`, `core/plan_workflow.py`, `core/rewriter_v2.py`

**职责迁移**
- 把模块内 `_set_status` 逐步替换为 `transition_task_status`
- CAS/lock 场景使用 `compare_and_transition_task_status`
- 事件 payload 要统一字段：before/after/blocked_reason/source（`transition_task_status` 已提供）

**风险点**
- 状态机 allowed transitions 过严会引发新异常：若出现真实流程需要的转移，必须补 allowed transitions + 补测试，而不是到处绕过
- 某些模块可能依赖“同状态重复写入”的副作用：state_machine 里已做“相同状态 + 相同 blocked_reason → no-op”，如果需要强制事件，要显式传 payload 并改逻辑（默认不建议）

**回归验证**
- `rg` 检查：`rg -n "UPDATE\\s+task_nodes\\s+SET\\s+status|SET\\s+status='" -S core run.py` 结果应只剩 `core/state_machine/task_status.py`
- pytest：`python -m pytest -q doc/code`
- 手动：run 时状态变化在 Task Graph 和 Workflow events 中可见

---

## Phase 5 — Frontend hooks + RequestState（消除组件内散落抓取逻辑）
**改动目标**：组件变“纯渲染”，数据获取集中在 hooks；统一 loading/error/retry；轮询不打断图交互。

**新增/修改文件**
- 新增：`dashboard_ui/src/hooks/useRequestState.ts`
  - 统一 `{data, error, loading, refresh}` 语义
- 新增：`dashboard_ui/src/hooks/useRunStatus.ts`
- 新增：`dashboard_ui/src/hooks/usePlanGraph.ts`
- 新增：`dashboard_ui/src/hooks/useWorkflow.ts`
- 修改：`dashboard_ui/src/App.tsx` 不直接 fetch；改为使用 hooks 输出 state
- 修改：`dashboard_ui/src/components/ControlPanel.tsx` 使用 `useRunStatus`
- 可选增强：Task Graph 轻量轮询
  - 只更新 node.status/is_running（不重新 layout、不 fitView）
  - 交互（拖拽/缩放）时暂停轮询或延迟更新

**职责迁移**
- 从 App.tsx/ControlPanel.tsx 中移除 try/catch + setState 组合逻辑
- 统一显示策略：
  - error 一定可见 + retry
  - loading 不可无限持续（超时/错误应切换为 error）

**风险点**
- 轮询导致 reactflow 重算布局：必须确保“轻量更新”不触发 layoutDagre 或 fitView（仅更新 data/color）
- hooks 的依赖数组导致频繁刷新：注意 memoization

**回归验证**
- `npm` 构建/类型检查（如果项目已有脚本）：例如 `cd dashboard_ui && npm run build`（如可用）
- 手动：Workflow Graph 与 Task Graph 不会无限 loading，错误有提示，拖拽缩放不被打断

---

# 统一验收（每个阶段都要通过）
- pytest：`python -m pytest -q doc/code`
- 后端 import：`python -c "import dashboard_backend.app; print('ok')"`
- UI 核心流程手动验证：
  - create-plan 能触发并有 workflow 进度
  - run/status 能显示存活/退出并给出可操作提示
  - Task Graph / Workflow Graph / LLM Explorer 可打开且遇错不无限 loading

开始执行前，请先用 `rg` 列出要迁移的点：
- `rg -n "SELECT\\s+.*FROM|conn\\.execute\\(" dashboard_backend core/queries -S`（确认读 SQL 分布）
- `rg -n "UPDATE\\s+task_nodes\\s+SET\\s+status|SET\\s+status='" core run.py -S`（确认写状态点）
然后按 Phase 1→5 顺序逐步实施，每个 Phase 一个 PR。

