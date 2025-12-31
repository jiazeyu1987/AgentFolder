# Observability（LLM 输入/输出与排障）

## 1) UI：LLM Explorer（推荐）
入口：运行 `agent_ui.py` → `LLM Explorer`

你能看到：
- 每次调用的 `scope/agent/plan/task/错误码/校验错误`
- 选中一行可查看：
  - Prompt（输入）
  - Raw Response（原始输出）
  - Parsed JSON（解析结果）
  - Normalized JSON（归一化后结果）
  - Meta/Errors（validator_error 等）

筛选方式（与 UI 行为一致）：
- 用 `plan-title contains` 查某个任务的整条链路
- 选中表格一行后，会自动把该行的 `plan_id/plan_title` 填到顶部输入框；再点 `Search` 可稳定过滤到正确数据

## 2) CLI：从 DB 查 `llm_calls`
- 摘要：`agent_cli.py llm-calls --plan-id <PLAN_ID> --limit 50`
- 看某个 task：`agent_cli.py llm-calls --task-id <TASK_ID> --limit 50`
- 契约漂移审计：`agent_cli.py contract-audit --plan-id <PLAN_ID> --limit 200`

## 3) 文件：`logs/llm_runs.jsonl`
用途：快速查看每次调用的文本快照（每行一个 JSON）。
- `agent_cli.py llm-log --limit 20`

## 常见排障路径
- “为什么卡住”：`agent_cli.py status`（简洁模式会告诉你缺输入/依赖）
- “为什么失败”：`agent_cli.py errors --task-id <TASK_ID>`
- “Root 一直 READY”：`agent_cli.py doctor --plan-id <PLAN_ID>` → `repair-db`

