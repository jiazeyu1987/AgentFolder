# Workflow

## A. `create-plan`（计划生成 + 计划审核）
入口：`agent_cli.py create-plan --top-task "..." --max-attempts N`

高层步骤：
1) `xiaobo` 生成 plan（scope=`PLAN_GEN`）
2) `normalize_plan_json()` + `validate_plan_dict()`：把 plan 修成可入库、可调度的严格结构
3) `xiaojing` 审核 plan（scope=`PLAN_REVIEW`）
4) 若 reviewer 输出不合约：会在 reviewer 内部重试（不会把“schema mismatch”等文本写进用户的 top_task）
5) 若 `total_score>=90 && action_required=APPROVE`：写 `tasks/plan.json` 并落库
6) 否则：把 `suggestions` 作为 `retry_notes` 加到下一轮生成提示里，进入下一轮 attempt

参数：
- `--max-attempts N`：默认 3
- `--keep-trying --max-total-attempts M`：超过 max-attempts 后继续重试，直到达到总上限

## B. `run`（串行执行 + 审核 + 状态推进）
入口：`agent_cli.py run --max-iterations N`（或运行 `run.py`）

每轮循环大致做：
1) 扫描 `workspace/inputs/`，把输入证据绑定到 requirements
2) `recompute_readiness_for_plan()`：根据依赖与输入，推进任务到 `READY/BLOCKED/PENDING/...`
3) `xiaobo` 执行 `READY` 的 `ACTION`（产出 artifact 或 `NEEDS_INPUT`）
4) `xiaojing` 审核 `READY_TO_CHECK` 的节点（决定 `DONE/TO_BE_MODIFY/BLOCKED`）
5) Root 聚合：当 Root 的 `DECOMPOSE` 子任务满足 DONE 条件，Root 才能 DONE

## C. 状态含义（面向用户）
- `PENDING`：依赖未满足或还没轮到它
- `READY`：可以执行（执行器会挑它跑）
- `BLOCKED(WAITING_INPUT)`：缺输入，会生成 `workspace/required_docs/<task_id>.md` 提示你放哪些文件
- `READY_TO_CHECK`：已执行完成，等待 reviewer 审核
- `TO_BE_MODIFY`：reviewer 要求修改（会生成 `workspace/reviews/<task_id>/suggestions.md`）
- `DONE`：该节点完成
- `FAILED`：超过最大尝试次数等硬失败（可用 `reset-failed` 重新置回 READY）

## D. 重试策略（避免“无限报错”）
- 执行器输出不合约：记 `LLM_UNPARSEABLE`，增加 attempt；超过阈值会升级为需要人工介入
- 审核器输出不合约：不会直接把任务打死，会保持/回到 `READY_TO_CHECK` 让 reviewer 自动重试；超过阈值再升级
- JSON 语法不合法：会先做本地修复（如尾逗号、控制字符），必要时会额外调用一次 LLM 让其“只做 JSON 修复”

