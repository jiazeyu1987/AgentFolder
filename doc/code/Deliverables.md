# Deliverables（交付物导出）

## 目标
把“最终交付物”从分散目录集中到一个固定位置，方便直接打包交付。

## 导出命令
- 默认导出最新 plan：`agent_cli.py export`
- 指定 plan：`agent_cli.py export --plan-id <PLAN_ID>`
- 指定目录：`agent_cli.py export --plan-id <PLAN_ID> --out-dir <PATH>`
- 可选带 reviews：`agent_cli.py export --plan-id <PLAN_ID> --include-reviews`
- JSON 输出（含 final.json 内容）：`agent_cli.py export --plan-id <PLAN_ID> --json`
- 可选导出候选版本（调试用）：`agent_cli.py export --plan-id <PLAN_ID> --include-candidates`

## 输出目录结构
默认目录：`workspace/deliverables/<plan_id>/`
- `artifacts/`：拷贝所有 `DONE` 的 `ACTION` 节点交付物（默认只导出 approved 版本；按 task 分子目录）
- `manifest.json`：交付清单（task → artifact 映射、sha256、源路径/目标路径 + entrypoint）
- `final.json`：最终交付单一入口（用户只需要看这个文件）
- `plan_meta.json`：计划信息与导出时间
- `reviews/`（可选）：若 `--include-reviews`，会尝试拷贝 `workspace/reviews/<task_id>/review_*.json`

## final.json（单一入口）

字段：
- `final_entrypoint`：最终交付入口文件（相对 deliverables 根目录）
- `final_task_title`：该入口来自哪个任务
- `final_artifact_id`：对应的 artifact_id（用于追溯）
- `how_to_run`：最短运行步骤
- `acceptance_criteria`：根节点验收标准（若存在 root_acceptance_criteria_json）
- `trace`：导出时的追溯信息（approved/reviewed/verdict/时间）

定位最终交付物：
- 只需要打开 `workspace/deliverables/<plan_id>/final.json`，按 `final_entrypoint` 找到文件即可交付。

## 交付建议
- 直接把 `workspace/deliverables/<plan_id>/` 整个目录打包即可（含 manifest 可追溯）。
