# Frontend Change Plan: v2 View + Config Controls

目标：让 React Dashboard 与 `Strong_Workflow_Dev_Plan.md` / `Strong_Workflow_Spec_v2.md` 的“v2 强约束闭环”保持一致，并在 UI 增加可写配置：
- `max_decomposition_depth`（最大拆分深度）
- `one_shot_threshold_person_days`（一次 LLM 可完成阈值：人/日）

约束：
- 前端是看板：CLI 仍是主驱动；UI 关闭不影响 CLI。
- 不频繁启动 subprocess；轮询只走后端轻查询（读 DB/读文件）。
- 所有配置写入 `runtime_config.json`（禁止环境变量）。

## 1) SSOT 数据流（前端不自行推断）

前端数据以 SSOT 为准：
- Snapshot（推荐）：`GET /api/plan_snapshot?plan_id=...`
  - 用于：顶部摘要（reasons/next_steps）、卡住原因、缺输入、最终交付入口、doctor/feasibility 结果。
- Graph：`GET /api/plan/{plan_id}/graph`
  - 用于：中间 DAG 渲染（task_nodes/task_edges）。

前端必须遵守：
- 不用“表格输出/字符串解析”推断状态；
- 不用前端规则猜“下一步怎么做”；直接展示 snapshot 的 `report.next_steps`。

## 2) 任务图（v2 门控可视化）

在 v2 模式下，图里要表达 2 类关系：
1) 依赖边（DB：`task_edges`）
   - `DEPENDS_ON`（实线）
   - `DECOMPOSE`（虚线）
   - `ALTERNATIVE`（点划线，可选）
2) 评审绑定边（v2：`CHECK.review_target_task_id`）
   - 用不同样式（例如蓝色细线）
   - 表示“CHECK 评审 ACTION”，与依赖边分离

节点状态要突出门控闭环：
- `ACTION READY_TO_CHECK`：等待评审（有候选 active_artifact）
- `CHECK READY/IN_PROGRESS`：可评审/正在评审
- `ACTION DONE`：必须意味着 approved_artifact_id 已写入（doctor 可验证）
- `ACTION TO_BE_MODIFY`：评审不通过，等待重做

## 3) 节点详情（只显示 v2 关键信息）

点击节点后：
- ACTION：
  - 交付物声明（deliverable_spec）
  - 验收标准（acceptance_criteria）
  - 估算人/日（estimated_person_days）
  - active_artifact vs approved_artifact（只显示路径/指针，不展示大内容）
- CHECK：
  - review_target_task_id 对应的 ACTION 标题
  - 最新 verdict/reviewed_artifact_id/created_at（追溯）
  - 若 stale_review：提示“需要评审最新候选版本”
- BLOCKED(WAITING_INPUT)：
  - 直接展示 `required_docs_path` + items(name/accepted_types/suggested_path)
  - 支持一键复制 suggested_path

## 4) 新增全局配置（写入 runtime_config.json）

新增 UI 配置区（左侧控制面板）：
- 最大拆分深度 `max_decomposition_depth`（整数，默认 5）
- 一次 LLM 阈值 `one_shot_threshold_person_days`（浮点/整数，默认 10）

后端新增配置写接口：
- `POST /api/runtime_config/update`
  - body: `{ max_decomposition_depth?: number, one_shot_threshold_person_days?: number }`
  - 行为：合并写入 `runtime_config.json`，并用 `core.runtime_config.load_runtime_config()` 校验；失败则回滚并返回 400。

前端行为：
- 启动时通过 `GET /api/config` 读取当前 runtime_config 并填入输入框。
- 点击“Save Config”时调用 update 接口并刷新配置/快照。

## 5) 轮询策略（不卡）

建议：
- Graph：2s 轮询（或 3–5s）即可
- Snapshot：2–3s 轮询即可（轻查询）
- 不要通过 subprocess 去 poll `agent_cli.py status` / `llm-calls`

## 6) 验收点（可验证）
- UI 可读取并显示当前 `max_decomposition_depth` / `one_shot_threshold_person_days`
- UI 可修改并保存到 `runtime_config.json`，刷新后值保持一致
- Snapshot/Feasibility 的阈值与 UI 配置一致（后端用同一 runtime_config 读取）

