# 强约束工作流（v2）对齐文档

> 目的：把系统升级为“每个节点都有明确交付物与验收标准、每个交付物必须评审并留痕、计划拆分递归直到所有叶子都可在一次 LLM 内完成”的强约束工作流。  
> 本文只做对齐与规格说明，不落代码。

## 0. 术语
- **TopTask**：用户输入的顶层任务描述。
- **Plan**：TopTask 的任务分解图（DAG）。
- **Node / 节点**：一个任务单元（对应 DB `task_nodes.task_id`）。
  - `GOAL`：聚合节点（Root Task）
  - `ACTION`：产出交付物的执行节点
  - `CHECK`：评审节点（产出“通过/不通过文件”）
- **Deliverable / 交付物**：ACTION 节点产出、可落盘的产物（artifact）。
- **Acceptance Criteria / 验收标准**：判定该交付物是否通过的标准集合。
- **一次 LLM 可完成**：对某个叶子节点的工作量评估 ≤ 阈值（默认 10 人日），可配置。
- **人日（person-days）**：用于估算“输入已齐全后，从开始到产出可评审交付物”的工作量（允许小数）。
- **叶子 ACTION 节点**：在当前计划中，沿 `DECOMPOSE` 不再向下分解的 ACTION 节点（即“没有子 DECOMPOSE 边”的 ACTION）。

## 1. 总体强约束（你提出的 3 条需求的可执行化）

### 1.1 每个节点必须明确交付物与验收标准
规则：
1) 每个 **ACTION** 节点必须显式声明：
   - 交付物规格（deliverable spec）
   - 验收标准（acceptance criteria）
   - 预计完成工作量（人/日，`estimated_person_days`）
2) **Root(GOAL)** 的交付物必须能满足 TopTask 的所有内容：
   - Root 的“验收标准集合”必须覆盖 TopTask 的验收条目（建议将 TopTask 的验收拆成 checklist，并映射到 Root）。

边界：
- `CHECK` 节点的交付物是“评审结果文件”（见 1.2），不等同于功能产物。
- `GOAL` 节点本身不产出功能文件，但它的交付物可以来自两种方式（见 1.4）。

### 1.2 每个交付物都必须评审，不通过就重做，并留存历史
规则：
1) 每个 ACTION 节点必须有且仅有一个对应的 CHECK 节点（评审者：`xiaojing`，或可配置 reviewer）。
2) 依赖关系固定为：
   - `ACTION -> CHECK`（没有通过评审，ACTION 不算 DONE）
3) 评审节点的交付物：
   - 必须输出文件化结果：`APPROVED.md` 或 `REJECTED.md`
   - 内容必须包含：评分、依据、未通过原因、修改建议、对应验收标准的逐条判定
4) 不通过处理：
   - CHECK 输出 `REJECTED.md` 后，ACTION 进入“需要修改”状态，重新生成新的交付物版本，再次进入 CHECK。
5) 历史留存：
   - ACTION 每次产出的 artifact 都必须保留（版本化），不能只覆盖 `active_artifact_id`
   - CHECK 的 APPROVED/REJECTED 文件也必须保留（版本化）

补充（评审可重复进行，且不丢历史）：
- CHECK 不是“一次性节点”：同一个 ACTION 的新版本交付物产生后，会触发同一个 CHECK 节点再次评审。
- CHECK 每次评审都必须产出一份结果文件（`APPROVED.md` 或 `REJECTED.md`），并能追溯其评审对应的交付物版本（artifact_id）。

### 1.3 递归拆分直到所有叶子都可一次 LLM 完成
规则（计划生成阶段）：
1) `create-plan` 的结果必须先经过 **计划评审（Plan Review）**。
2) 计划评审通过后，进入 **可执行性检查（Feasibility Check）**：
   - 对每个叶子 ACTION 节点评估“人日工作量（person-days）”
3) 若某叶子节点评估 > 阈值（默认 10 人日）：
   - 必须按“拆分规则”进一步拆分该叶子为子树（并对新子树再次 Plan Review + Feasibility）
4) 递归终止条件：
   - 所有叶子节点都通过可执行性检查（≤阈值）
   - 或达到最大深度（默认 5，可配置）/最大叶子数/最大总尝试次数（建议有硬上限）
5) 若达到最大深度仍无法满足阈值：
   - 进入 `REQUEST_EXTERNAL_INPUT`（需要用户提供更多约束/资料/澄清），或人工介入调整拆分规则。

### 1.4 Root(GOAL) 的交付物生成规则（你补充的要求）
当 Root 的子节点交付物为 A/B/C/D 时，Root 的交付物有两种合法模式：
1) **直接取用（Pass-through）**
   - Root 的最终交付物可以直接由子节点交付物集合构成（例如交付包内包含 A/B/C/D），前提是 Root 的 `final_deliverable_spec` 明确声明需要哪些子交付物以及组织方式。
2) **组合生成（Assemble）**
   - Root 可以定义一个“组合生成”的最终 ACTION 节点（例如 `Assemble Final Package`），其输入是子节点交付物 A/B/C/D，输出是新的交付物 X/Y/Z（例如一个可运行的单文件 `index.html`，或一个发布包目录/zip）。
   - 在这种模式下，Root 的完成以“最终组合节点通过评审”为准；子节点 A/B/C/D 仍需各自评审通过才能作为组合输入。

约束：
- 无论采用哪种模式，Root 的验收标准必须覆盖 TopTask 的全部要求（可以分解为 checklist 并映射到 A/B/C/D 或 X/Y/Z）。

## 2. 可配置项（对齐你给的设置）
### 2.1 最大深度（默认 5）
- `max_decomposition_depth`: 默认 5，可配置。
- 深度定义：Root 为 depth=0，沿 **DECOMPOSE** 边每向下分解一层 +1（`DEPENDS_ON`/`CHECK` 不计入深度）。

### 2.2 一次 LLM 可完成阈值（默认 10 人日）
- `one_shot_threshold_person_days`: 默认 10，可配置。
- 解释：若一个节点的“预计工作量” ≤ 10 人日，则认为可以在一次 LLM（一次 executor 调用）中完成；否则必须继续拆分。

> 注：这是启发式标准，用于自动拆分决策；并不保证 100% 一次成功，但可以显著降低“巨型节点一次写不完”的概率。

## 3. 数据结构扩展（建议）
> 目标：让“交付物规格/验收标准/评审结果/版本留存/可执行性评估”可被机器消费，UI 可展示。

### 3.1 Node 扩展字段（逻辑结构）
对每个 ACTION 节点新增：
- `estimated_person_days`（必须）
  - 数值：`0.5/1/2/...`（允许小数）
  - 含义：该节点从“输入已齐全”到“产出可评审交付物”的预计工作量（人/日）
- `deliverable_spec`（必须）
  - `format`：`html|md|json|...`
  - `filename`：期望输出文件名（或模式）
  - `output_dir`：期望落盘目录策略（例如 `workspace/artifacts/<task_id>/`）
  - `single_file`：是否必须单文件
  - `description`：人类可读描述
- `acceptance_criteria[]`（必须）
  - 每项结构化：`id`, `type`, `statement`, `check_method`, `severity`
  - `check_method` 例：`manual_review`/`static_check`/`run_smoke_test`（MVP 可只做 manual）

对每个 CHECK 节点新增：
- `review_target_task_id`：它评审哪个 ACTION
- `review_output_spec`：固定为 `APPROVED.md|REJECTED.md`
补充规则：
- 可执行性检查（Feasibility）只对 **ACTION 的叶子节点**进行；CHECK 节点默认视为“一次 LLM 可完成”，不参与拆分递归。

对 Root(GOAL) 节点新增（可选但推荐）：
- `root_acceptance_criteria[]`：TopTask 的总验收标准清单
- `final_deliverable_spec`：最终交付包的形态与来源（Pass-through 或 Assemble）
  - `mode`: `PASS_THROUGH|ASSEMBLE`
  - `include_children`: 需要直接纳入交付包的子交付物集合（仅 PASS_THROUGH）
  - `assemble_task_id`: 最终组合生成节点 task_id（仅 ASSEMBLE）
  - `output`: Root 的最终交付物规格（目录/zip/单文件等）

### 3.2 Artifact 版本化（必须）
现状 DB 有 `artifacts` 表（可多行），但调度使用 `task_nodes.active_artifact_id` 指向“当前有效版本”。
需要规范：
- 每次 ACTION 重做都写一条新 artifact 行
- 不通过的历史 artifact 保留，但需要明确 “当前版本/通过版本” 的指针语义（否则导出与 UI 会混乱）：
  - 推荐：`active_artifact_id` 始终指向**最新候选版本**（等待评审或刚产出）
  - 另新增：`approved_artifact_id`（或等价记录）指向**最近一次通过评审的版本**
- CHECK 节点同样写 artifact（APPROVED/REJECTED 文件）

补充（导出规则必须固定）：
- `export` / “最终交付包”默认只导出 **通过评审的版本**（approved），不能导出未通过的候选版本。
- 若需要导出候选版本用于调试，应显式加参数（例如 `--include-candidates`），默认关闭。

补充（多文件交付物的表示）：
- 交付物如果不是单文件，需要把“交付物”定义为 **Bundle**：
  - 方案（选定）：输出到 **同一个计划级文件夹**，并生成 `manifest` 区分与追溯（不使用 zip）。
  - 具体约束：
    - 对于同一个 TopTask/Plan，所有多文件交付物在“最终交付态”都落到同一个目录：`workspace/deliverables/<plan_id>/bundle/`
    - 每个 ACTION 节点的交付物通过 `manifest.json` 的条目区分（task_id → files[]）。
    - 文件命名需包含 task_id 前缀或 task_slug，避免同名覆盖。
    - `manifest.json` 必须包含：每个文件的相对路径、sha256、来源 artifact/version 信息、以及该文件属于哪个 `deliverable_spec`。
    - `deliverable_spec.single_file=false` 时必须声明 `bundle_mode=MANIFEST`，并声明期望的文件集合或文件类型约束。
  - 版本留痕与“候选/通过”不混淆（必须明确，否则会与 3.2 的导出规则冲突）：
    - 运行时（每次生成/重做）文件仍写到 `workspace/artifacts/<task_id>/...`（按版本区分，例如包含 artifact_id 子目录）。
    - `workspace/deliverables/<plan_id>/bundle/` 是 **导出结果**（export 生成），默认只拷贝“通过评审”的版本文件进入该目录。
    - 若需要把“未通过候选版本”也导出到 bundle 用于调试，必须显式开启（例如 `--include-candidates`）。

补充（版本目录命名规则，避免同名覆盖/便于追溯）：
- 运行期落盘目录（建议固定）：
  - 单文件：`workspace/artifacts/<task_id>/<artifact_id>/<filename>`
  - 多文件：`workspace/artifacts/<task_id>/<artifact_id>/...`（目录下包含该版本的所有文件）
- CHECK 评审结果落盘目录（建议固定）：
  - `workspace/reviews/<check_task_id>/<review_id>/APPROVED.md` 或 `REJECTED.md`
- 上述目录结构保证：
  - 同一 task 多版本不会互相覆盖
  - UI 能按版本（artifact_id/review_id）展示历史链
  - export 能稳定选择“approved 版本”复制到 `workspace/deliverables/<plan_id>/bundle/`

补充（manifest 的最小字段建议）：
- `manifest.json`（位于 `workspace/deliverables/<plan_id>/bundle/manifest.json`）至少包含：
  - `plan_id`, `exported_at`
  - `items[]`：每项包含
    - `task_id`, `task_title`
    - `deliverable_spec` 摘要（format/filename/single_file/bundle_mode）
    - `approved_artifact_id`（或等价字段）
    - `files[]`：每个文件的 `dest_path`, `sha256`, `source_path`
    - `review`：对应的 `check_task_id`、`review_id`、`verdict`、`score`（若有）

### 3.3 Review 记录与验收条目映射（建议）
`reviews` 表中应记录：
- 评审针对的 artifact_id（当前没有，需要补充或在 payload 中记录），否则无法做到“未通过交付物留存可追溯”
- 每条 acceptance_criteria 的判定结果（pass/fail + evidence）
并建议补充：
- `reviewed_artifact_id`：本次评审针对的具体 artifact 版本
- `verdict`：APPROVED/REJECTED（与 CHECK 产物一致）
并明确：
- 每条 `acceptance_criteria` 的判定结果应能映射到 `reviewed_artifact_id`，从而形成“哪个版本为何通过/不通过”的可追溯链。

## 4. 工作流状态机（建议）
对一个 ACTION + CHECK 配对：
1) ACTION 执行产出交付物（artifact v1）：
   - ACTION 进入 `READY_TO_CHECK`（表示“等待评审门控”；此时 ACTION **不算 DONE**）
   - 对应 CHECK 节点进入 `READY`（可被 reviewer 执行）
2) CHECK 根据验收标准评审（输入为 ACTION 的当前候选 artifact）并产出结果文件：
   - 通过：CHECK 产出 `APPROVED.md` → CHECK `DONE`，并把 ACTION 置为 `DONE`（表示该版本通过验收）
   - 不通过：CHECK 产出 `REJECTED.md` → CHECK `DONE`，并把 ACTION 置为 `TO_BE_MODIFY`（保留 artifact v1），进入下一轮 ACTION 重做生成 v2
3) 超过最大尝试：
   - ACTION 或 CHECK 升级为 `WAITING_EXTERNAL`（需要人工介入）

补充（评审与执行的“可重复性”）：
- 当 ACTION 从 `TO_BE_MODIFY` 再次产出 artifact v2 后：
  - ACTION 再次进入 `READY_TO_CHECK`
  - CHECK 需要再次运行：CHECK 从 `DONE` 回到 `READY`（同一个 CHECK 节点复用）
  - 评审的输出文件必须与 v2 关联（`reviewed_artifact_id=v2`）

Root(GOAL)：
- 当 Root 的所有 DECOMPOSE 子树（叶子 ACTION）都 `DONE`，Root 才 `DONE`。
注意：
- Root 的完成条件不应直接依赖 CHECK 节点；因为 ACTION 的 DONE 已经由 CHECK 通过“门控”保证。

## 5. 计划生成阶段的递归拆分流程（建议）
流程（伪步骤）：
1) PLAN_GEN：生成 plan（包含每个 ACTION 的 `deliverable_spec` + `acceptance_criteria` + `estimated_person_days` 初始估算）
2) PLAN_REVIEW：小京评审 plan（覆盖度/依赖合理性/可执行性描述/每节点交付与验收是否完整）
3) FEASIBILITY_CHECK（新增 Agent/步骤）：
   - 对每个叶子 ACTION 输出：`estimated_person_days` + `one_shot_pass` + `why`
   - 若与 PLAN_GEN 初始估算不一致：允许“修正并写回”每个 ACTION 节点的 `estimated_person_days`（并记录修正理由）
4) 若存在 FAIL：
   - 对 FAIL 节点进行再拆分（替换为子树，深度+1）
   - 回到步骤 2
5) 直至所有叶子 PASS 或触发终止条件（最大深度/最大叶子数/最大尝试）

补充（拆分规则必须“可操作”）：
- 拆分应优先按“交付物可组合”的边界切分（例如：需求/设计/核心逻辑/集成打包/验收报告）。
- 拆分后每个新叶子 ACTION 必须重新满足：`deliverable_spec + acceptance_criteria + estimated_person_days`。

## 6. UI 需要展示什么（为了这套强约束可用）
节点点击详情应至少展示：
- deliverable_spec（期望产物类型/文件名/是否单文件）
- acceptance_criteria（逐条）
- 当前版本 artifact（路径/格式/sha256/created_at）
- 历史 artifact 列表（含哪些被 REJECTED）
- CHECK 节点评审结果（APPROVED/REJECTED 文件内容/评分/原因）

## 7. 需要你确认的点（对齐问题）
1) “10 人日以下算一次 LLM 可完成”：
   - 这是对 **单个叶子 ACTION** 的阈值，对吗？
2) 估算方法：
   - 是“由可执行性检查 Agent 生成估算（主观）”，还是你希望引入更硬的规则（文件数、预估代码行数、模块数量）？
3) 最大深度达到仍 FAIL：
   - 默认进入 `REQUEST_EXTERNAL_INPUT`，你是否接受？
4) Root 交付物：
   - 你希望 Root 的最终交付物形态固定为“单一文件”（例如 `index.html`），还是“导出目录 + manifest”也可接受？
