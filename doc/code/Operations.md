# Operations（CLI Cheat Sheet）

说明：本仓库在 Windows 上通常用 `D:\\miniconda3\\python.exe` 运行（避免 `python` 不在 PATH）。

## 常用命令
- 生成计划：`D:\miniconda3\python.exe agent_cli.py create-plan --top-task "创建一个2048的游戏" --max-attempts 3`
  - 不稳定时继续尝试：`--keep-trying --max-total-attempts 20`
- 跑主循环：`D:\miniconda3\python.exe agent_cli.py run --max-iterations 200`
- 看状态（默认简洁）：`D:\miniconda3\python.exe agent_cli.py status`
  - 看全表：`D:\miniconda3\python.exe agent_cli.py status --verbose`
- 看错误：`D:\miniconda3\python.exe agent_cli.py errors --task-id <TASK_ID> --limit 50`
- 看 LLM 输入输出：`D:\miniconda3\python.exe agent_cli.py llm-calls --plan-id <PLAN_ID> --limit 50`
- 自检/修复：`D:\miniconda3\python.exe agent_cli.py doctor --plan-id <PLAN_ID>` / `D:\miniconda3\python.exe agent_cli.py repair-db --plan-id <PLAN_ID>`
- 导出交付物：`D:\miniconda3\python.exe agent_cli.py export --plan-id <PLAN_ID>`
  - 可选带 reviews：`--include-reviews`

## 常见问题与处理
### 1) `BLOCKED (WAITING_INPUT)`
现象：`status` 提示 required_docs 文件路径  
处理：打开 `workspace/required_docs/<task_id>.md`，按 suggested_path 放入输入文件后，再跑一次 `run`。

### 2) Root 一直 `READY`
原因：通常是缺 `DECOMPOSE` 或依赖关系异常，导致 Root 无法聚合 DONE。  
处理：`doctor` → `repair-db` → 再 `run` 一次。

### 3) `LLM_UNPARSEABLE` / `schema_version mismatch`
处理：用 UI 的 LLM Explorer 查看该次调用的 Prompt/Raw Response/Validator Error；必要时 `reset-failed --reset-attempts` 重新跑。

### 4) 重置（重新开始）
- 只把失败任务置回 READY：`agent_cli.py reset-failed --plan-id <PLAN_ID> --include-blocked --reset-attempts`
- 清空 DB（可选清文件）：`agent_cli.py reset-db --purge-workspace --purge-tasks --purge-logs`

