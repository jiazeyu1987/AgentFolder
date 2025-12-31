# Deliverables（交付物导出）

## 目标
把“最终交付物”从分散目录集中到一个固定位置，方便直接打包交付。

## 导出命令
- 默认导出最新 plan：`agent_cli.py export`
- 指定 plan：`agent_cli.py export --plan-id <PLAN_ID>`
- 指定目录：`agent_cli.py export --plan-id <PLAN_ID> --out-dir <PATH>`
- 可选带 reviews：`agent_cli.py export --plan-id <PLAN_ID> --include-reviews`

## 输出目录结构
默认目录：`workspace/deliverables/<plan_id>/`
- `artifacts/`：拷贝所有 `DONE` 的 `ACTION` 节点的 active_artifact（按 task 分子目录）
- `manifest.json`：交付清单（task → artifact 映射、sha256、源路径/目标路径）
- `plan_meta.json`：计划信息与导出时间
- `reviews/`（可选）：若 `--include-reviews`，会尝试拷贝 `workspace/reviews/<task_id>/review_*.json`

## 交付建议
- 直接把 `workspace/deliverables/<plan_id>/` 整个目录打包即可（含 manifest 可追溯）。

