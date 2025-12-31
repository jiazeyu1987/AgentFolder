# Code Docs

这组文档面向“维护 / 重构 / 扩展”本仓库的开发者，重点回答：
- 系统怎么跑（`create-plan` / `run` / `status`）
- 数据结构契约在哪（`core/contracts.py`）
- DB 表与迁移怎么做（`state/migrations` + `doctor`/`repair-db`）
- LLM 输入输出怎么查（UI LLM Explorer + `llm_calls` + `logs/llm_runs.jsonl`）
- 最终交付物怎么导出（`export` + `workspace/deliverables/`）

目录：
- `doc/code/Architecture.md`：目录职责与模块边界
- `doc/code/Contracts.md`：关键 JSON 契约与兼容策略
- `doc/code/Workflow.md`：`create-plan`/`run` 的状态机与重试策略
- `doc/code/DB_Schema.md`：数据库结构与自检/修复
- `doc/code/Observability.md`：LLM 调用观测与排障
- `doc/code/Operations.md`：常用命令与排障手册
- `doc/code/Deliverables.md`：交付物导出与清单格式
- `doc/code/Glossary.md`：plan_id / task_id / status 等术语解释

参考（规范原文）：
- `doc/plan/RunPY_MVP_Skeleton_Spec.md`
- `doc/plan/LLM_IO_Contract_Spec.md`
- `doc/plan/Plan_Definition_Spec_and_Example.md`
- `doc/plan/StateDB_Migration_v1_SQL.md`
