# DB Migrations（P0.3）

目标：保证数据库迁移可以“一键演练”，支持新库初始化、旧库升级，并提供最小自检（doctor）。同时支持 `workflow_mode=v1|v2` 的灰度与回滚（不破坏旧数据/目录）。

## 迁移文件位置
- 迁移 SQL：`state/migrations/*.sql`
- 迁移记录表：`schema_migrations`

## 一键演练工具：`tools/migration_drill.py`

### 1) 新库初始化（fresh）
创建一个全新的 DB 并应用所有迁移：
- `python tools/migration_drill.py --db state/state.db --fresh --overwrite`

### 2) 旧库升级（upgrade）
对已有 DB 应用缺失迁移：
- `python tools/migration_drill.py --db state/state.db --upgrade`

### 3) 最小自检（doctor）
检查关键表/关键列/最新迁移是否已应用：
- `python tools/migration_drill.py --db state/state.db --doctor`

JSON 输出（便于脚本/CI）：
- `python tools/migration_drill.py --db state/state.db --doctor --json`

## workflow_mode 灰度/回滚（最小约定）
`runtime_config.json`：
- `workflow_mode`: `v1|v2`（默认 `v1`）

约定：
- `workflow_mode=v2` 只是“启用 v2 流程”的开关，不会自动修改旧 DB/旧产物目录。
- 当 v2 流程未完全实现或遇到不兼容时，应给出可读提示，并允许用户切回 `workflow_mode=v1` 继续运行。

## 迁移失败如何定位
1) 先看错误提示中提到的表/列/SQL 文件名
2) 看 `schema_migrations` 当前已应用到哪个文件
3) 跑 doctor 复查：`python tools/migration_drill.py --db <DB> --doctor`

## 近期迁移备注（重要字段）
- `011_m6_llm_calls_truncation.sql`：为 `llm_calls` 增加 `prompt_truncated/response_truncated`（用于 guardrails 的文本截断标记；不破坏旧数据）。

