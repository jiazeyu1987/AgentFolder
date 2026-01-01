# Contracts

本仓库把关键输入/输出收敛为契约（单一事实来源：`core/contracts_v2.py`，并复用 `core/contracts.py` 的归一化/兼容逻辑）。

## P0.2 Contract Summary（machine-readable）
<!-- CONTRACT_SUMMARY_JSON_START -->
{
  "TASK_ACTION": {
    "schema_version": "xiaobo_action_v1",
    "required_keys": ["schema_version", "task_id", "result_type"],
    "enums": {
      "result_type": ["ARTIFACT", "NEEDS_INPUT", "NOOP", "ERROR"],
      "artifact.format": ["md", "txt", "json", "html", "css", "js"]
    }
  },
  "PLAN_REVIEW": {
    "schema_version": "xiaojing_review_v1",
    "required_keys": ["schema_version", "task_id", "review_target", "total_score", "action_required", "summary", "breakdown", "suggestions"],
    "enums": {
      "review_target": ["PLAN"],
      "action_required": ["APPROVE", "MODIFY", "REQUEST_EXTERNAL_INPUT"],
      "suggestions[*].priority": ["HIGH", "MED", "LOW"]
    }
  },
  "TASK_CHECK": {
    "schema_version": "xiaojing_review_v1",
    "required_keys": ["schema_version", "task_id", "review_target", "total_score", "action_required", "summary", "breakdown", "suggestions"],
    "enums": {
      "review_target": ["NODE"],
      "action_required": ["APPROVE", "MODIFY", "REQUEST_EXTERNAL_INPUT"],
      "suggestions[*].priority": ["HIGH", "MED", "LOW"]
    }
  },
  "PLAN_GEN": {
    "schema_version": "plan_json_v1",
    "required_keys": ["plan", "nodes", "edges"],
    "enums": {
      "nodes[*].node_type": ["GOAL", "ACTION", "CHECK"],
      "edges[*].edge_type": ["DECOMPOSE", "DEPENDS_ON", "ALTERNATIVE"]
    }
  }
}
<!-- CONTRACT_SUMMARY_JSON_END -->

## 1) `xiaobo_action_v1`（执行器输出，scope=`TASK_ACTION`）
用途：驱动工作流推进（产出 artifact / 需要输入 / 无事可做 / 报错）。

最小字段：
- `schema_version`：必须为 `xiaobo_action_v1`
- `task_id`：当前任务（UUID）
- `result_type`：`ARTIFACT|NEEDS_INPUT|NOOP|ERROR`

当 `result_type=ARTIFACT`：
- `artifact.name`
- `artifact.format`：允许值由 `core/contracts.py::ALLOWED_ARTIFACT_FORMATS` 决定（当前：`md|txt|json|html|css|js`）
- `artifact.content`

当 `result_type=NEEDS_INPUT`：
- `needs_input.required_docs[]`：每项包含 `name/description/accepted_types/suggested_path`

实现：
- 归一化：`core/contracts.py::normalize_xiaobo_action`
- 校验：`core/contracts.py::validate_xiaobo_action`

## 2) `xiaojing_review_v1`（审核输出，scope=`PLAN_REVIEW|TASK_REVIEW|CHECK_NODE_REVIEW`）
用途：决定任务/计划是否通过审核与下一步（`APPROVE|MODIFY|REQUEST_EXTERNAL_INPUT`）。

最小字段（校验严格）：
- `schema_version`：必须为 `xiaojing_review_v1`
- `task_id`：被审核对象标识（字符串）
  - 兼容说明：当 `review_target=PLAN` 时，系统会把 `task_id` 填成 `plan_id`（为了沿用同一字段名）
- `review_target`：`PLAN` 或 `NODE`
- `total_score`：`0..100` 的整数
- `action_required`：`APPROVE|MODIFY|REQUEST_EXTERNAL_INPUT`
- `summary`：字符串（必须存在，即使为空也会被 normalize 修补）
- `breakdown[]`：维度评分结构（见 `core/contracts.py::validate_xiaojing_review`）
- `suggestions[]`：每项必须包含 `priority/change/steps/acceptance_criteria`

实现：
- 归一化：`core/contracts.py::normalize_xiaojing_review`
- 校验：`core/contracts.py::validate_xiaojing_review`

## 3) `plan.json`（计划结构，scope=`PLAN_GEN`）
用途：把计划写入 `tasks/plan.json` 并落库到 `plans/task_nodes/task_edges/...`，驱动调度与 Root 聚合。

顶层字段：
- `plan`：`plan_id/title/root_task_id/created_at/owner_agent_id/constraints`
- `nodes[]`：每项至少包含 `task_id/plan_id/node_type/title/owner_agent_id/priority/tags`
- `edges[]`：每项至少包含 `edge_id/plan_id/from_task_id/to_task_id/edge_type/metadata`
- `requirements[]`：可选输入要求（若出现，必须是对象数组）

兼容与自动修复（都在 normalize 做）：
- 别名容器（JSON key）：`tasks` / `links` / `inputs` → `nodes` / `edges` / `requirements`
- 字段别名：`id/type/from/to/...` → 规范字段
- 外部 planner 的 `START->...->END` 链会被重写为 Root 的 `DECOMPOSE`，以保证 Root 可聚合 DONE
- 若缺少 Root 的 `DECOMPOSE` 边，会补最小集合（Root → 所有子任务）

实现：
- 归一化：`core/contracts.py::normalize_plan_json`
- 严格校验：`core/models.py::validate_plan_dict`
