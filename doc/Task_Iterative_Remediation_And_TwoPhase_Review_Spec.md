# Task 迭代整改 + Reviewer 两阶段评分（Spec）

目标：把 Task Graph（TASK_ACTION/TASK_CHECK）做成与 Plan 类似的“可收敛闭环”：**不通过就输出整改单（review notes），下一次生成必须基于上次输出 + 整改单整改**；同时 Reviewer 评分标准可控：先“构建 rubric”，再“按 rubric 打分”。

## 1. 总体原则

1) 单机串行：同一时刻最多 1 个 run loop 在推进；不考虑并发锁。
2) 不用环境变量：所有开关/阈值写入 `runtime_config.json`。
3) 不改业务语义（现有 v2 闭环不变）：
   - ACTION 产出候选 → `READY_TO_CHECK`
   - CHECK 评审锁定版本（reviewed_artifact_id = ACTION.active_artifact_id）
   - 通过：写 `approved_artifact_id`，ACTION→DONE，CHECK→DONE
   - 不通过：ACTION→TO_BE_MODIFY（保留候选 artifact，不覆盖），CHECK→DONE
4) 整改信息必须可追溯：
   - 每次评审不通过都生成整改单（短、可执行、可直接注入下一次 prompt）
   - 整改单必须落盘并可被 UI/Workflow 展示

## 2. 文件与目录约定（沿用 suggestions.md）

### 2.1 整改单（review notes）

- 路径：`workspace/reviews/<task_id>/suggestions.md`
- 内容：**下一次 xiaobo 必须附带**的整改内容（不做“精简版”，但必须限制总字数；默认 500 字，可配置）。

### 2.2 评审结构化记录

- 仍写入 DB `reviews` 表（用于追溯）
- 每次评审必须写：
  - `check_task_id`
  - `review_target_task_id`
  - `reviewed_artifact_id`
  - `verdict`（APPROVED/REJECTED）
  - `total_score`（如有）
  - `summary`/`suggestions`（结构化字段）

## 3. 配置（runtime_config.json）

新增/确认：

- `task_review_pass_score`：Task 评审通过阈值（默认 90）
- `task_review_notes_max_chars`：`suggestions.md` 最大字数（默认 500）

说明：
- **Plan** 的门槛仍由 `plan_review_pass_score` 控制；Task 不复用 Plan 的门槛。

## 4. 提示词体系（四套 prompt）

要求：`xiaobo` 与 `xiaojing` 都必须有“两套提示词”，并且能在 LLM Workflow 中明确区分（建议写入 `llm_calls.meta_json.prompt_variant`）。

### 4.1 xiaobo（执行者）

1) `TASK_ACTION_INIT`（初始生成）
- 条件：`attempt_count == 0` 且 status 非 TO_BE_MODIFY
- 不附带任何历史输出/整改单

2) `TASK_ACTION_ITERATE`（迭代整改）
- 条件：`attempt_count > 0` 或 status=TO_BE_MODIFY
- 必须附带：
  - 上次交付物路径（优先 approved，其次 active；若只有 active 就用 active）
  - 上次整改单内容（读取 `suggestions.md`，截断到 `task_review_notes_max_chars`）
- 明确要求：**在此基础上修改，不要从零重写**；输出必须满足交付物与验收标准。

### 4.2 xiaojing（评审者）

1) `TASK_REVIEW_RUBRIC_BUILD`（构建评分标准）
- 输入：当前任务的 `deliverable_spec` + `acceptance_criteria`
- 输出：固定结构 rubric（建议 `task_rubric_v1`），并落盘/入库（与 task_id 绑定）
- 目的：让后续评分可控、可解释

2) `TASK_REVIEW_SCORE`（按 rubric 打分）
- 输入：
  - rubric（从上一步或缓存读取）
  - 被评审交付物（reviewed_artifact_id 对应的本地路径/内容）
  - `acceptance_criteria`（用于逐条验收输出）
- 输出：
  - `total_score`
  - `verdict`（APPROVED/REJECTED）
  - `summary`
  - `acceptance_results`（逐条验收通过/不通过）
  - `suggestions`（整改建议）
  - **整改单文本**（用于写入 `suggestions.md`，必须满足字数上限）

## 5. Reviewer 两阶段工作流（CHECK 节点增强）

当 CHECK 运行时：

1) 若目标 ACTION 尚无 rubric：
   - 先执行 `TASK_REVIEW_RUBRIC_BUILD`，得到 rubric 并保存（rubric_id/rubric_version）
2) 再执行 `TASK_REVIEW_SCORE`：
   - 锁定 reviewed_artifact_id（ACTION.active_artifact_id）
   - 按 rubric 打分、给 verdict
3) 若 verdict=REJECTED：
   - 写 `workspace/reviews/<task_id>/suggestions.md`（<= task_review_notes_max_chars）
   - ACTION → TO_BE_MODIFY
   - CHECK → DONE
4) 若 verdict=APPROVED：
   - 更新 ACTION.approved_artifact_id
   - ACTION → DONE
   - CHECK → DONE

## 6. Prompt 注入与可观测性

### 6.1 注入规则（xiaobo）

当进入 `TASK_ACTION_ITERATE`：

- 上次交付物：
  - `approved_artifact_id` 存在 → 使用 approved 对应文件路径
  - 否则使用 `active_artifact_id` 对应文件路径
- 整改单：
  - 读取 `workspace/reviews/<task_id>/suggestions.md`
  - 若不存在则不注入，但必须在日志/事件中标记为“整改缺失”（可选）

### 6.2 LLM Calls 记录（建议字段）

- `llm_calls.meta_json.prompt_variant` ∈
  - `TASK_ACTION_INIT`
  - `TASK_ACTION_ITERATE`
  - `TASK_REVIEW_RUBRIC_BUILD`
  - `TASK_REVIEW_SCORE`
- `llm_calls.meta_json.rubric_version` / `rubric_id`（如有）

UI 目标：
- Workflow 节点点击后可看到：
  - 使用的是哪种 prompt_variant
  - review 的 score/verdict
  - 本次写入 suggestions.md 的文本（直接展示）

## 7. 验收与测试（建议）

必须可用 pytest 自动验证（不跑真实 LLM）：

1) 构造 ACTION=READY_TO_CHECK + CHECK=READY + active_artifact_id
2) 第一次 CHECK：
   - 先写 rubric，再打分
   - 分数不足 → 生成 `suggestions.md`，ACTION=TO_BE_MODIFY
3) 下一次 ACTION：
   - prompt_variant 必须为 `TASK_ACTION_ITERATE`
   - prompt 必须包含上次交付物路径与 `suggestions.md` 内容（被截断到上限）
4) rubric 不应每次都重建（除非显式清空/重置）

