# 修改计划：Plan-Review 按管线断点恢复（STRUCTURE→BINDINGS→EXECUTION）

## 目标

- 当 `STRUCTURE` 评分达到阈值后进入 `BINDINGS`。
- 如果 `BINDINGS` 评分未达阈值：下一次重试**只从 `BINDINGS` 管线开始**（不再重新做 `STRUCTURE` 的 `PLAN_REVIEW`）。
- 如果 `EXECUTION` 评分未达阈值：下一次重试**只从 `EXECUTION` 管线开始**（不再重新做 `STRUCTURE/BINDINGS` 的 `PLAN_REVIEW`）。

> 这里的“重试”指 create-plan 的下一次 attempt（仍然会有一次新的 PLAN_GEN，用于根据 review_notes 修复 plan_json；但不会重新跑已通过阶段的 PLAN_REVIEW）。

## 现状与问题

- 当前实现中，`BINDINGS` 或 `EXECUTION` 评分失败会 `continue` 外层 attempt 循环，导致下一次 attempt 仍会从 `STRUCTURE` 做 `PLAN_REVIEW`。
- 这与“失败只在当前管线返工”的预期不一致。

## 设计要点（状态机）

- 引入 `resume_stage`：
  - 初始为 `STRUCTURE`。
  - `STRUCTURE` 通过后置为 `BINDINGS`。
  - `BINDINGS` 通过后置为 `EXECUTION`。
  - `BINDINGS` 失败时置为 `BINDINGS`，下次 attempt 跳过 `STRUCTURE` review。
  - `EXECUTION` 失败时置为 `EXECUTION`，下次 attempt 跳过 `STRUCTURE/BINDINGS` review。
- 引入 `passed_stage_reviews` 记录已通过阶段的 review（用于：
  - 跳过已通过阶段的 `PLAN_REVIEW`；
  - 同时在 `PLAN_APPROVED` telemetry 的 stages 字段里提供结构化分数来源）。

## 代码修改点（已实现）

- `core/plan_workflow.py`
  - 增加 `resume_stage` 与 `passed_stage_reviews`。
  - PLAN_GEN 的 `meta.stage`/workflow payload 中的 `stage` 改为 `resume_stage`（便于观测“从哪条管线在重试”）。
  - STRUCTURE review：仅当 `resume_stage == STRUCTURE` 才执行；通过后写入 `passed_stage_reviews["STRUCTURE"]` 并将 `resume_stage` 置为 `BINDINGS`。
  - BINDINGS review：仅当 `resume_stage != EXECUTION` 才执行；通过后写入 `passed_stage_reviews["BINDINGS"]` 并将 `resume_stage` 置为 `EXECUTION`；失败时将 `resume_stage` 置为 `BINDINGS` 再进入下一次 attempt。
  - EXECUTION review：失败时将 `resume_stage` 置为 `EXECUTION` 再进入下一次 attempt。

## 测试计划（pytest，已实现）

- 位置：`doc/code/test_plan_workflow_stage_resume.py`
- 用例：
  1. `STRUCTURE` 通过、`BINDINGS` 失败后下一次 attempt 不再出现第二次 `STRUCTURE` 的 `PLAN_REVIEW`（统计 `llm_calls.meta_json.stage`）。
  2. `STRUCTURE/BINDINGS` 通过、`EXECUTION` 失败后下一次 attempt 不再出现第二次 `STRUCTURE/BINDINGS` 的 `PLAN_REVIEW`，只出现 `EXECUTION` 的复审。

## 验收标准

- 在一次 create-plan 运行中：
  - `BINDINGS` 失败后的后续 attempt 中，`PLAN_REVIEW` 不再出现 `stage=STRUCTURE`。
  - `EXECUTION` 失败后的后续 attempt 中，`PLAN_REVIEW` 不再出现 `stage=STRUCTURE` 或 `stage=BINDINGS`。
- `pytest -q doc/code/test_plan_workflow_stage_resume.py` 全绿。

