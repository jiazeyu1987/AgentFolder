# Plan_GEN 迭代整改（最终版）

## 问题定义
当前 create-plan 的迭代只把 `review_notes`（整改说明，≤500字）喂给下一次 `PLAN_GEN`。但 `PLAN_GEN` 不知道“要在上一版 plan_json 的基础上改哪里”，容易重新发散生成新方案，导致分数 **不升反降**、修改信息丢失、链路难追溯。

## 目标
让 create-plan 的每次迭代都能“在上一版基础上小步修正”，使评分随迭代 **单调趋好（至少更稳定）**，并且可在 UI 中明确看到“这一版是基于哪一版改出来的、改了什么、为什么改”。

## 核心原则（最小闭环）
1. **必须带上上一版的 PLAN_GEN 输出**：下一次 `PLAN_GEN` 不是从 0 生成，而是“基于 previous_plan_json 修改”。
2. **整改说明必须可操作**：reviewer 输出的问题点/修改步骤/验收标准必须完整、可执行，且限制在 ≤500 字（避免内容爆炸）。
3. **单次 review 不合格不进入下一次 PLAN_GEN（review contract 强制）**：reviewer 输出合同不合法时，必须重发直到合法，否则这次 create-plan 直接失败（不污染后续）。
4. **全链路可追溯**：每次迭代都要能关联到：
   - 上一版 `PLAN_GEN` 的输出（plan_json）
   - 本次 `PLAN_REVIEW` 的结论（review_notes + 原始 review JSON）
   - 下一版 `PLAN_GEN` 的输入（包含 previous_plan_json + review_notes）

## 建议实现（最终版）

### A. 输入增强：PLAN_GEN Prompt 必须包含“上一版 + 整改说明”
下一次 `PLAN_GEN` 的 prompt 追加两个区块（顺序固定）：
1) `PREVIOUS_PLAN_JSON`：上一版 `PLAN_GEN` 产出的 **完整 plan_json**（建议用 `normalized_json` 或 plan 文件快照）。
2) `REMEDIATION_NOTE`：上一版 `PLAN_REVIEW` 生成的整改说明（≤500字）。

并在 prompt 中加入强约束（必须写在显眼位置）：
- “你必须在 `PREVIOUS_PLAN_JSON` 的基础上修改，不允许重写一个全新计划。”
- “输出必须是 **完整的 plan_json**（不是 patch），并保持 node/task_id 稳定（能复用则复用，避免大量重建 ID）。”
- “逐条对照 `REMEDIATION_NOTE` 的 change/steps/acceptance_criteria，必须显式回应（可在 JSON 的 `description` 或 `requirements` 增补一段变更说明，但不要污染 top_task）。”

实现约定（代码侧）：
- `RUNTIME_CONTEXT_JSON.mode`：首次为 `INITIAL`；当且仅当存在 `previous_plan_json` 且 `review_notes` 非空时，为 `ITERATIVE_REMEDIATION`。
- `RUNTIME_CONTEXT_JSON.iteration_rules`：仅在 `ITERATIVE_REMEDIATION` 下提供，作为硬规则文本（必须遵守）。

### B. 输出约束：PLAN_GEN 必须“稳定可改”
为避免“每次重写 ID”导致 reviewer/依赖链混乱，建议：
- 能复用的节点必须复用 `task_id`（除非 reviewer 明确要求删除/新增）。
- 只对必要节点做局部修改；新增节点要有明确依赖边和交付物定义（v2 工作流）。

### C. 关联与追溯：为每次迭代建立显式链路
每次 `PLAN_GEN` 的记录（llm_calls.meta_json）建议写入：
- `attempt`: N
- `prev_plan_gen_llm_call_id`: 上一版 PLAN_GEN 的 llm_call_id（如果存在）
- `remediation_source_review_llm_call_id`: 产生本次整改说明的 PLAN_REVIEW llm_call_id

这样 UI 可以稳定展示：
`PLAN_GEN(N) <-based on- PLAN_GEN(N-1)`，并能点开看到“上一版输出 + 本版输入 + reviewer 整改”。

### D. 整改说明（review_notes）的内容规范（≤500字）
reviewer 输出整改说明必须包含三段，且每段尽量短：
1) **问题点**（Problems）：列出 2–5 条关键缺陷
2) **修改步骤**（Steps）：每条问题对应 1–3 个可执行步骤
3) **验收标准**（Acceptance）：每条问题对应 1 条验收标准（可量化/可检查）

> 注意：整改说明要“指向 plan_json 的具体部位”（例如节点 title、edge、缺少字段），避免空泛。

整改说明字数上限为配置项 `plan_review_notes_max_chars`（默认 500），可在 `runtime_config.json` 中调整。

### E. 防止内容爆炸（上下文控制）
当 plan_json 很大时，不要把所有历史都喂进去：
- `PREVIOUS_PLAN_JSON`：只给“上一版完整 plan_json”（单一版本）
- `REMEDIATION_NOTE`：只给“上一版 review_notes”（单一版本）
- 不要把更早的历史版本拼接进入 prompt（否则会爆 token / 干扰模型）

### F. 评分下降的处理策略（稳定性）
出现“后面的分数比前面更低”时，建议有两种策略（任选其一，建议先做 1）：
1) **强制基于上一版修改**（推荐）：prompt 明确禁止重写；并在 reviewer checklist 里增加“本版是否保留并修正上一版结构”。
2) **回滚并重试**：若分数下降且 reviewer 指出偏离整改方向，则回到上一版 plan_json 重新生成（仍然基于上一版+整改）。

## UI/可观测性（必须能看懂）
在 LLM Workflow / 详情页中必须能看到：
- 本次 `PLAN_GEN` 是否携带了 `PREVIOUS_PLAN_JSON`（可以显示 “based_on=<llm_call_id>”）
- 本次 `PLAN_REVIEW` 的整改说明（review_notes）内容（≤500字）
- `PLAN_GEN` 输入（prompt_text 原样）与输出（response_text 原样）

## 验收标准（工程）
1) 同一个 top_task 的 create-plan 在多次 attempt 下，`PLAN_GEN` 明确基于上一版修改（可从 prompt_text 直接验证）。
2) Workflow 可回溯每次迭代的“上一版输出 + 本版输入 + reviewer 整改说明”。
3) 评分波动显著降低；整改命中率提升（不要求严格单调上升，但不再频繁“越改越差”）。
