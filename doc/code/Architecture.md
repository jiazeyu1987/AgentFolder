# Architecture

## 目标
- 单机串行：一个进程内按阶段顺序调度（`xiaobo` 执行 → `xiaojing` 审核 → 状态推进）。
- 强契约：所有 LLM 输出必须先 `normalize_*()` 再 `validate_*()`，通过后才入库/落盘。
- 可观测：每次 LLM 调用都可追溯（DB `llm_calls` + `logs/llm_runs.jsonl` + UI LLM Explorer）。

## 目录与职责
- `agent_cli.py`：命令入口（`create-plan/run/status/errors/doctor/repair-db/llm-calls/contract-audit/export/reset-*`）。
- `agent_ui.py`：Tk UI（执行 CLI、LLM Explorer、导出交付物、重置 DB 等）。
- `run.py`：主循环（扫描输入、调度执行、审核、状态推进、输出 required_docs）。
- `core/`：核心库
  - `core/contracts.py`：契约单一事实来源（`normalize_*` + `validate_*`）。
  - `core/plan_workflow.py`：`create-plan` 工作流（生成计划 + 计划审核 + 重试）。
  - `core/readiness.py`：依赖/输入满足后推进状态，Root 聚合 DONE。
  - `core/db.py`：sqlite 连接 + migrations 应用。
  - `core/doctor.py`：DB 自检（外键/孤儿数据/缺 DECOMPOSE 等）。
  - `core/repair.py`：DB 安全修复（补 root 节点、补 DECOMPOSE 等）。
  - `core/llm_client.py`：LLM 调用适配（JSON 容错解析 + 可选“让 LLM 修 JSON”）。
  - `core/llm_calls.py`：LLM 调用遥测入库（`llm_calls` 表）。
  - `core/deliverables.py`：交付物导出（拷贝 DONE 产物 + `manifest.json`）。
- `state/`：DB 与迁移
  - `state/state.db`
  - `state/migrations/*.sql`
- `tasks/`：计划文件（默认 `tasks/plan.json`）。
- `workspace/`：运行输入/输出
  - `workspace/inputs/`：用户输入
  - `workspace/baseline_inputs/`：基础资料库（优先于 inputs 自动匹配）
  - `workspace/artifacts/`：任务产物（按 task_id）
  - `workspace/reviews/`：审核输出
  - `workspace/required_docs/`：缺输入提示（按 task_id）
  - `workspace/deliverables/`：最终交付物导出目录
- `logs/llm_runs.jsonl`：每次 LLM 调用的一行 JSON（文本快照）。

## 设计约束（为了避免“东边补了西边又错”）
- 所有“字段别名/包装层/枚举差异/START-END 链”只允许在 `core/contracts.py` 做兼容与修复。
- DB schema 只允许通过 `state/migrations` 演进；不要运行时隐式改表。
- 任何“不可解析/不合约”的输出必须在 UI/CLI 中能看到原因与原文（Prompt/Raw Response）。
