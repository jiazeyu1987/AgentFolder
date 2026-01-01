# 强约束工作流（v2）开发计划

来源规格：`doc/code/Strong_Workflow_Spec_v2.md`

目标：把现有系统升级为“每个 ACTION 明确交付物/验收/人日 + 必有 CHECK 门控评审 + 递归拆分直到叶子可一次 LLM 完成 + 交付物 bundle/manifest 导出”的闭环。

---

## 里程碑 0：冻结范围、配置与兼容策略（1–2 天）

**0.1 配置项落地**
- 新增/确认配置项（写入 `runtime_config.json`，不使用环境变量）：
  - `max_decomposition_depth`（默认 5）
  - `one_shot_threshold_person_days`（默认 10）
  - （建议）`max_artifact_versions_per_task`、`max_review_versions_per_check`（防止无限增长）
  - （建议）`export_include_candidates`（默认 false）

**0.2 旧数据兼容策略（必须先定，否则后面必返工）**
- 旧 plan/旧 task 缺少 v2 必填字段时的策略三选一（建议默认 A）：
  - A（推荐，最稳）：**只读兼容**：旧数据仍可 status/graph/export（仅能导出已有 approved/旧 artifact），但 `run` 拒绝执行并提示“需要升级 plan（重新 create-plan）”
  - B：自动补全（高风险）：系统尝试自动生成 deliverable_spec/acceptance/person-days/对应 CHECK（容易引入错误）
  - C：强制迁移（激进）：迁移时直接填入 placeholder（会导致大量 READY_TO_CHECK/评审无意义）

**0.3 状态/字段字典确认**
- 明确“强约束 v2”新增字段与语义后，补齐 `doc/code/Glossary.md` 与 `doc/code/DB_Schema.md`。

**0.4 灰度/回滚开关（建议必须做）**
- 增加一个可回滚的运行模式开关（避免“一把梭全切”）：
  - `workflow_mode`: `v1|v2`（默认 v1）
  - 允许按 plan 维度覆盖（例如 plan 表/plan_json 内标记 `workflow_version`），使得旧 plan 仍按 v1 运行、新 plan 按 v2 运行
- 明确回滚策略：
  - v2 失败时：不破坏 v1 数据路径（artifact/review/export 目录独立或可区分）
  - UI/后端读取逻辑优先兼容 v1/v2 并存

**0.5 最小可验证样例集（固定回归用例，后续每个阶段都复用）**
- 固定 3 个 top_task 作为回归基准（不要每次临时挑案例）：
  - S（小）：`创建一个2048的游戏（单文件 index.html）`
  - M（中）：`创建一个斗地主的游戏（多文件：index.html + js + css + README，manifest 导出）`
  - L（大，必触发拆分/或外部输入）：`创建一个3D射击游戏`
- 固定每个用例的“预期终态”：
  - S：所有叶子 ACTION `DONE` + export bundle 有可运行交付物
  - M：所有叶子 ACTION `DONE` + bundle/manifest 有多文件交付物，且能一眼定位入口文件
  - L：要么拆分收敛到阈值，要么进入 `REQUEST_EXTERNAL_INPUT` 且 required_docs 指引清晰

阶段验收（可验证）：
- `runtime_config.json` 支持上述键，CLI/后端读取一致。
- 旧数据兼容策略已落在文档/实现里，并且 CLI 提示清晰（“旧 plan 不能 run，需要重新 create-plan”或等价信息）。
- 文档中所有状态名与字段名一致且无歧义（特别是 `READY` vs `READY_TO_CHECK`）。
 - `workflow_mode` 能在 v1/v2 间切换，且切换不导致 DB/文件夹不可逆破坏。
 - 样例集三条 top_task 可被一键执行（即便尚未成功，也必须能稳定复现并产出可读错误）。

---

## 里程碑 1：数据结构与 DB 迁移（核心底座，2–4 天）

**1.1 Task Node 扩展字段（ACTION/GOAL/CHECK）**
- ACTION 必填：
  - `estimated_person_days`（float）
  - `deliverable_spec`（结构化 JSON）
  - `acceptance_criteria`（结构化 JSON 数组）
- CHECK 必填：
  - `review_target_task_id`（绑定目标 ACTION）
  - `review_output_spec`（固定：APPROVED/REJECTED 文件）
- GOAL 推荐：
  - `root_acceptance_criteria`
  - `final_deliverable_spec`（PASS_THROUGH / ASSEMBLE）

**1.2 Artifact/Review 指针与追溯**
- 现有：`active_artifact_id`（候选版本）
- 新增：`approved_artifact_id`（最近一次通过版本）
- Reviews/评审记录补齐：
  - `review_id`（每次评审唯一）
  - `check_task_id`
  - `review_target_task_id`
  - `reviewed_artifact_id`（锁定版本）
  - `verdict`（APPROVED/REJECTED）
  - （可选）逐条 `acceptance_criteria` 判定结果结构

**1.3 DB 自检（doctor）规则**
- 1:1 绑定：
  - 每个 ACTION 必须且仅能被一个 CHECK 评审
  - 每个 CHECK 必须且只能评审一个 ACTION
- Contract/字段完整性：
  - ACTION 必有 deliverable/acceptance/person-days
  - CHECK 必有 review_target_task_id
  - GOAL 必有 root 目标/最终交付 spec（按配置要求）

验收：
- 新 migration 可全新建库 & 旧库升级。
- `doctor` 能给出可读的错误定位（task title + 缺失字段）。

阶段验收（可验证）：
- `agent_cli.py doctor`（或等价命令）能：
  - 对 v2 plan：返回 OK
  - 对缺字段的 plan：返回可读错误（不输出大表格，指出“哪个 task 缺哪个字段/如何修复”）

---

## 里程碑 2：执行状态机 + 调度门控（拆成 2A/2B，3–6 天）

### 2A 最小闭环（先跑通再加复杂性）

**2A.1 ACTION 产出候选 → READY_TO_CHECK**
- ACTION 完成一次生成后：
  - 写 artifact(vN) + `active_artifact_id=vN`
  - ACTION 状态置为 `READY_TO_CHECK`（等待评审）

**2A.2 CHECK 调度条件（方案 2）**
- CHECK 可运行条件（不依赖 ACTION DONE）：
  - 找到 `review_target_task_id` 对应 ACTION
  - ACTION 状态为 `READY_TO_CHECK`
  - ACTION 有 `active_artifact_id`
- CHECK 评审时必须记录 `reviewed_artifact_id = action.active_artifact_id`（锁定版本）

**2A.3 CHECK 结果回写（不含并发竞态）**
- 若 verdict=APPROVED：
  - 写 review 产物（APPROVED.md）+ reviews 记录
  - 更新 `approved_artifact_id = reviewed_artifact_id`
  - 只有当 `action.active_artifact_id == reviewed_artifact_id` 才把 ACTION 置为 `DONE`
  - 否则 ACTION 保持 `READY_TO_CHECK`（等待新版本评审）
- 若 verdict=REJECTED：
  - 写 REJECTED.md + reviews 记录
  - ACTION 置为 `TO_BE_MODIFY`（或现有等价状态）
  - 保留历史 artifact，不覆盖

**2A.4 重评审循环（REJECT → REGEN → RECHECK）**
- ACTION 由 `TO_BE_MODIFY` 再次生成 v2 后：
  - ACTION 回到 `READY_TO_CHECK`
  - CHECK 需要能再次运行（从 `DONE` 重置为 `READY` 或等价）

2A 阶段验收（可验证）：
- 用一个最小 plan（2 个 ACTION + 2 个 CHECK）验证闭环：
  - `create-plan` 生成后能看到每个 ACTION 都有对应 CHECK（且 `review_target_task_id` 有值）
  - `run` 一轮后：ACTION 从 `READY_TO_CHECK` 经 CHECK 评审后进入 `DONE` 或 `TO_BE_MODIFY`
  - `export` 只导出 approved 版本（未通过版本不进入 bundle）

### 2B 可靠性与一致性（竞态/重试/边界）

**2B.1 评审“锁定版本”与竞态处理**
- 若 CHECK 评审通过 v1 时 ACTION 已经生成 v2：
  - 允许 `approved_artifact_id=v1`
  - 但 ACTION 不得进入 `DONE`，必须保持 `READY_TO_CHECK` 等待 v2 的评审

**2B.2 重试与降级**
- 对 LLM_UNPARSEABLE/contract mismatch：
  - 记录可读错误（必须指出：哪个 schema、哪个字段/JSONPath 不符合、期望值枚举、示例修复）
  - 进入下一次 attempt（不直接中断整个 run 循环）
  - 达到 max_attempts：转 `WAITING_EXTERNAL` 并生成 required_docs 指引

**2B.2.1 错误提示“先做早做”（减少后续调试成本）**
- 把“错误可读化”作为 2B 的硬验收门槛：没有可读错误就不进入里程碑 3/4/5。
- 目标：用户不需要看 traceback/大 JSON 就能理解“哪里错、该怎么改、下一步点哪个按钮/补哪个文件”。

**2B.3 依赖输入“通过版本优先”**
- 下游 ACTION 默认只消费上游的 `approved_artifact_id`
- 若缺少 `approved_artifact_id`（或上游非 DONE），下游应保持不可运行，并在 status 中指出缺少哪个上游的通过版本

2B 阶段验收（可验证）：
- 人为制造竞态（让 ACTION 生成 v2 后再让 CHECK 才评审 v1），最终状态符合规则：`approved=v1` 且 ACTION 仍需评审 v2 才能 DONE。
- 触发一次 LLM 结构错误时：run 不崩溃、会重试、最终给出“可读错误 + 下一步怎么做”。
 - 用样例集 S/M/L 各跑一次：即便失败也必须能给出可读错误（且不会把 raw review JSON 注入到 top_task/retry_notes）。

总验收：
- 同一个 ACTION 连续失败/通过的多轮 artifact + review 全可追溯。
- 不出现死锁：CHECK 不要求 ACTION DONE；ACTION 不在未评审时 DONE。
- status 能清晰解释“卡住原因”（等待评审/等待通过版本/等待输入文件）。

---

## 里程碑 3：计划生成 + 递归拆分（拆成 3A/3B，2–5 天）

### 3A 先做“可报告”，再做“可自动改写”

**3A.1 PLAN_GEN 输出升级**
- 计划中每个 ACTION 都带：
  - deliverable_spec + acceptance_criteria + estimated_person_days
- 自动生成 CHECK 节点并设置 `review_target_task_id`
- GOAL 填充 root_acceptance_criteria + final_deliverable_spec

**3A.2 PLAN_REVIEW 强化（小京）**
- 把“字段完整性 + 1:1 CHECK 绑定 + 依赖图合理性”纳入硬规则
- 不通过时给出结构化可修复建议（避免把 raw JSON 混入 top_task/retry_notes）

**3A.3 FEASIBILITY_CHECK（只报告，不改 plan）**
- 对每个叶子 ACTION 评估：
  - `estimated_person_days`
  - `one_shot_pass = (<= threshold)`
  - `why`
- 超阈值：先返回“需要拆分的节点清单 + 建议拆分方式”，但不自动改写 plan（先保证可解释/可控）

3A 阶段验收（可验证）：
- `create-plan` 生成的 plan 满足 v2 必填字段 + doctor 通过。
- `feasibility-check`（或等价输出）能列出“超阈值节点”和原因。

### 3B 自动拆分改写（在 3A 稳定后再做）

**3B.1 自动拆分**
- 对超阈值叶子 ACTION：
  - 生成子树（新 ACTION/新 CHECK）
  - 替换原叶子为 GOAL/聚合（或保留为父 ACTION 但不再是叶子）
  - 深度+1，并再次 PLAN_REVIEW + FEASIBILITY

**3B.2 终止条件与用户提示**
- 到最大深度仍不满足阈值：
  - 进入 `REQUEST_EXTERNAL_INPUT`
  - 生成 required_docs 指引，说明需要哪些约束/资料才能继续拆分

3B 阶段验收（可验证）：
- 对“明显超大 top_task”能在有限轮次内：
  - 要么收敛到所有叶子 ≤ 阈值
  - 要么清晰进入 REQUEST_EXTERNAL_INPUT 并给出可执行的补充资料清单

总验收：
- `create-plan` 对同一个 top_task 可稳定在有限轮次内收敛（或进入 REQUEST_EXTERNAL_INPUT）。
- 叶子节点工作量均 ≤ 阈值（默认 10 人日）。

---

## 里程碑 4：交付物导出（bundle/manifest）与“最终交付”定位（1–3 天）

**4.1 bundle/manifest 规则实现**
- `export` 生成：
  - `workspace/deliverables/<plan_id>/bundle/manifest.json`
  - 每个 ACTION 的通过版本文件复制到：
    - `workspace/deliverables/<plan_id>/bundle/<task_slug>_<task_id8>/...`
- 默认只导出 `approved_artifact_id` 对应版本（不导出候选）
- 可选 `--include-candidates`（仅用于调试）

**4.2 Root 输出模式**
- PASS_THROUGH：
  - bundle 包含所有叶子 ACTION 的通过版本
- ASSEMBLE：
  - bundle 至少包含 assemble_task 的通过版本
  - （默认）也包含叶子通过版本便于追溯（可配置精简）

验收：
- 用户无需“找半天”：固定在 `workspace/deliverables/<plan_id>/bundle/` 找到最终交付与 manifest。

阶段验收（可验证）：
- 对一个已完成 plan：
  - `export` 产物目录固定、manifest 可读且能反查每个文件来源（task_id + artifact_id + review_id）
  - bundle 内**不会**出现未通过版本（除非显式 `--include-candidates`）

---

## 里程碑 5：CLI/Status/UI 同步可观测性（2–4 天）

**5.1 status 输出“可读化”**
- 按 node_type 汇总：DONE/PENDING/READY/READY_TO_CHECK/TO_BE_MODIFY/BLOCKED/FAILED
- 对 BLOCKED/READY_TO_CHECK：
  - 显示缺少的输入文件路径
  - 显示“等待哪个上游的 approved_artifact”
  - 显示“等待评审（对应 check 节点）”

**5.2 graph 接口对齐 v2**
- `agent_cli.py graph --json` 增加：
  - deliverable_spec、acceptance_criteria、estimated_person_days（ACTION）
  - review_target_task_id（CHECK）
  - active/approved artifact 简要信息
  - review 最新 verdict/原因（若有）

**5.3 Dashboard（React）适配（最后做，且只做“看板”）**
- 任务图：
  - 画出 edges（DEPENDS_ON/DECOMPOSE/ALTERNATIVE + 可选 ACTION->CHECK 展示边）
  - 节点形态/颜色体现 `READY_TO_CHECK` 与 `TO_BE_MODIFY`
- 节点详情：
  - 显示交付物（通过版本 + 当前候选）
  - 显示验收标准与评审结果（APPROVED/REJECTED 内容）
  - 显示该节点相关 agent prompts/outputs（从 llm_calls）
约束：
- UI 不参与调度，不跑循环；只读 DB/文件系统（避免拖慢电脑/影响 CLI 独立运行）。

验收：
- UI 能直观看到“哪个节点在等评审/等输入/等上游通过版本”，并能点开看到证据（artifact/review 文件）。

阶段验收（可验证）：
- status 输出中至少能明确三类阻塞：
  - WAITING_INPUT（缺什么文件、写到哪里）
  - WAITING_REVIEW（哪个 ACTION 在等哪个 CHECK）
  - WAITING_UPSTREAM_APPROVAL（缺哪个上游的 approved_artifact）

---

## 里程碑 6：回归与压力控制（1–3 天）

**6.1 回归场景**
- 单文件交付（2048）
- 多文件交付（带 manifest）
- 多轮 REJECT → REGEN → APPROVE
- 并发竞态模拟（生成 v2 后 v1 才被评审通过）
- 达到最大深度/阈值无法满足 → REQUEST_EXTERNAL_INPUT

**6.2 增长控制**
- 防止 artifact/review 无限膨胀：
  - 超过阈值的历史版本保留策略（只保留最近 N 个 + 所有 APPROVED 的版本）
  - 清理命令（可选）：按 plan_id 清理候选版本

验收：
- 一轮完整 top_task（含重试）不会让 DB/磁盘无限增长；关键历史可追溯不丢。

阶段验收（可验证）：
- 连续运行 3 个 plan（含重试）后：
  - DB 体积增长在可控范围（受配置阈值控制）
  - artifact/review 的清理策略不会删除“已通过版本”与其评审记录
