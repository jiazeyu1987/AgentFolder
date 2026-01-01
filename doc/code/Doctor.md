# Doctor（P0.5 预检/硬挡板）

目的：在进入 `run` 之前，先做 DB + plan 的最小自检（preflight），把“数据不兼容/结构缺失/非法状态”提前挡住，并给出可读提示，避免跑到一半才爆 traceback。

## 运行方式
- 自检当前 DB：`python agent_cli.py doctor`
- 自检指定 plan：`python agent_cli.py doctor --plan-id <PLAN_ID>`
- JSON 输出（给 UI/脚本用）：`python agent_cli.py doctor --plan-id <PLAN_ID> --json`

## run 的默认行为
`agent_cli.py run` / `run.py` 默认会先执行 doctor：
- 发现问题会直接退出（exit code 非 0），并打印“短句 + 下一步怎么做”
- 调试时可跳过（不推荐）：`agent_cli.py run --skip-doctor`

## doctor 检查什么
### 1) DB（doctor_db）
- 关键表是否存在（符合 migrations 预期）
- `PRAGMA foreign_keys` 是否开启（best-effort）
- 最小一致性：孤儿 nodes/edges/events、root_task 缺失等

### 2) Plan（doctor_plan）
v1（MVP）最小要求：
- plan 存在，root_task 存在且为 `GOAL`
- 至少一个 `ACTION` 节点
- 每个节点的 `status` 与 `node_type` 合法（P0.1：`READY_TO_CHECK` 只允许 ACTION）
- 若节点数 > 1，必须存在 `DECOMPOSE`（否则 root 无法聚合 DONE）

v2（硬挡板/强约束）：
- `workflow_mode=v2` 时，检查 v2 必需字段/绑定关系（estimated_person_days、deliverable_spec、acceptance_criteria、1:1 CHECK 绑定等）
- 发现缺列/缺字段会给出可读提示，并建议切回 v1 或执行 rewrite

## 常见失败与修复
- `DB_MISSING_TABLE` / `DB_MIGRATION_NOT_APPLIED`：运行 `tools/migration_drill.py --upgrade` 或确保启动时调用 `apply_migrations`
- `PLAN_ROOT_TASK_NOT_FOUND` / `PLAN_MISSING_DECOMPOSE`：尝试 `agent_cli.py repair-db --plan-id <PLAN_ID>` 或重新 `create-plan`
- `PLAN_BAD_STATUS`（例如 CHECK=READY_TO_CHECK）：说明 DB 状态不合法；通常建议 `reset-db` 或重新生成计划

## v2 推荐链路（doctor FAIL → report → rewrite）
当 `runtime_config.json` 设置 `workflow_mode=v2` 且 `doctor` 失败时，推荐按以下顺序定位与修复：

1) `python agent_cli.py doctor --plan-id <PLAN_ID>`
2) `python agent_cli.py report --plan-id <PLAN_ID>`（人类可读报告，含 next steps）
3) `python agent_cli.py rewrite --plan-id <PLAN_ID>`（dry-run：只生成建议，不改 DB）
4) `python agent_cli.py rewrite --plan-id <PLAN_ID> --apply`（应用改写：写入 snapshot + task_events）
5) 重新执行 `doctor` 复检，直到通过

