# Glossary（术语与 ID）

## ID
- `plan_id`：一次“计划”的唯一 ID（UUID）。一般一个 `plan_title` 对应一个 `plan_id`（一次 create-plan 成功落库后固定）。
- `task_id`：计划里每个节点（任务）的唯一 ID（UUID）。一个 plan 下有多个 task_id。
- `node_type`：节点类型
  - `GOAL`：聚合节点（Root Task），靠 `DECOMPOSE` 子任务聚合 DONE
  - `ACTION`：可执行节点（由 `xiaobo` 执行产物）
  - `CHECK`：检查/审核类节点（通常由 reviewer 执行）

## 状态（高频）
- `PENDING`：依赖未满足 / 还没轮到
- `READY`：可以执行
- `BLOCKED`：缺输入或需要外部介入（常见 `WAITING_INPUT`）
- `READY_TO_CHECK`：等待 reviewer 审核
- `TO_BE_MODIFY`：需要修改后再跑
- `DONE`：完成
- `FAILED`：超过最大尝试等硬失败

## 为什么 LLM Explorer 里会看到多个 `plan_id`
- 一个计划可能多次 `create-plan` 尝试，每次尝试都会产生新的候选 `plan_id`（PLAN_GEN/PLAN_REVIEW 也会记录遥测）。
- 只有被 `APPROVE` 并写入 `tasks/plan.json` / 落库的那个 `plan_id` 才是“当前有效计划”。

## reviewer 的 `task_id` 为啥像 plan_id
- `xiaojing_review_v1` 结构要求有 `task_id` 字段；当审核对象是 `PLAN` 时，系统把 `task_id` 填成 `plan_id` 以保持统一字段名。

