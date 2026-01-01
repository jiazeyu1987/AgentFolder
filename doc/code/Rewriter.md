# Rewriter (v2)

目标：当 `workflow_mode=v2` 下计划不满足强约束（doctor/report 显示缺字段/缺 CHECK/超大 ACTION 等），提供：
- `rewrite` dry-run：只生成“结构化改写计划（patch plan）”，不改 DB
- `rewrite --apply`：按 patch plan 做最小、安全的 DB 改写，并写入 snapshot + task_events 追溯

## 使用

- 生成建议（不改 DB）：`python agent_cli.py rewrite --plan-id <PLAN_ID>`
- 生成 JSON：`python agent_cli.py rewrite --plan-id <PLAN_ID> --json`
- 应用改写：`python agent_cli.py rewrite --plan-id <PLAN_ID> --apply`

应用改写时会写入：
- `workspace/rewrites/<plan_id>/snapshot_<ts>.json`（改写前快照）
- `task_events`: `REWRITE_PROPOSED` / `REWRITE_APPLIED`

## Patch Types（MVP）

### `ADD_MISSING_V2_FIELDS`
当 ACTION 缺以下字段时自动补齐（v2 必需）：
- `estimated_person_days`
- `deliverable_spec_json`
- `acceptance_criteria_json`

默认值：
- `estimated_person_days = max(1.0, threshold*0.5)`
- `deliverable_spec_json`：`format=md filename=deliverable.md bundle_mode=MANIFEST`
- `acceptance_criteria_json`：最小 1 条 `manual_review`

### `ADD_CHECK_BINDING`
当 ACTION 没有任何 CHECK 绑定时，自动创建一个 CHECK：
- `node_type=CHECK`
- `status=READY`
- `review_target_task_id=<ACTION.task_id>`

注意：若一个 ACTION 被多个 CHECK 绑定，只提示风险，不自动删除。

### `SPLIT_OVERSIZED_ACTION`
当 `estimated_person_days > one_shot_threshold_person_days`：
- 将该 ACTION 转为 GOAL（保留原 `task_id`，避免破坏现有边）
- 创建 N 个子 ACTION（每个 `estimated_person_days <= threshold`）
- 为每个子 ACTION 创建 1:1 CHECK（`review_target_task_id`）
- 建立 `DECOMPOSE` 边：父 -> 子
- 若原 ACTION 存在 CHECK，自动将这些 CHECK 标记为 `ABANDONED` 并清空 `review_target_task_id`（避免 doctor v2 报错）

深度限制：
- 若该节点当前 depth 已达到 `max_decomposition_depth`，只生成建议，不在 `--apply` 中执行。

## 与 doctor/report 的推荐链路
- `doctor FAIL` → `report` 定位 → `rewrite` 生成建议 → `rewrite --apply` 落地 → `doctor` 复检

