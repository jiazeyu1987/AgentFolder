# Doctor（P0.5 预检/硬挡板）

目的：在进入 `run` 之前，先做 DB + plan 的最小自检（preflight），把“数据不兼容/结构缺失/非法状态”提前挡住，并给出可读提示，避免跑到一半才爆 traceback。

## 运行方式
自检当前 DB（可选指定 plan）：
- `python agent_cli.py doctor`
- `python agent_cli.py doctor --plan-id <PLAN_ID>`

JSON 输出（给 UI/脚本用）：
- `python agent_cli.py doctor --plan-id <PLAN_ID> --json`

## run 的默认行为
`agent_cli.py run` / `run.py` 默认会先执行 doctor：
- 发现问题会直接退出（exit code 非 0），并打印“短句 + 下一步怎么做”
- 调试时可跳过（不推荐）：`agent_cli.py run --skip-doctor`

## doctor 检查什么

### 1) DB（doctor_db）
- 关键表是否存在（按 migrations 预期）
- PRAGMA foreign_keys 是否开启
- 最新 migration 是否已应用（best-effort）
- 轻量一致性检查：孤儿 task_nodes/edges/events、root_task 缺失等

### 2) Plan（doctor_plan）
v1（MVP）最小要求：
- plan 存在，root_task 存在且为 `GOAL`
- 至少一个 `ACTION` 节点
- 每个节点的 `status` 对应的 `node_type` 合法（P0.1：`READY_TO_CHECK` 只允许 ACTION）
- 如果节点数 > 1，必须存在 `DECOMPOSE`（否则 Root 无法聚合 DONE）

v2（硬挡板，未实现完整流程）：
- 若 `runtime_config.json` 中 `workflow_mode=v2`，但 DB 尚未具备 v2 必要列，会直接给出可读提示：
  - “缺哪些列”
  - “请切回 v1 或升级迁移（当 v2 迁移可用时）”

## 常见失败与修复
- `DB_MISSING_TABLE` / `DB_MIGRATION_NOT_APPLIED`：运行 `tools/migration_drill.py --upgrade` 或确保启动时调用 `apply_migrations`
- `PLAN_ROOT_TASK_NOT_FOUND` / `PLAN_MISSING_DECOMPOSE`：尝试 `agent_cli.py repair-db --plan-id <PLAN_ID>` 或重新 `create-plan`
- `PLAN_BAD_STATUS`（例如 CHECK=READY_TO_CHECK）：说明 DB 状态不合法；通常建议 `reset-db` 或重新生成计划

