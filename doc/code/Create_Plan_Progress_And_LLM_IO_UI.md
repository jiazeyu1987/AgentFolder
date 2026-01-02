# Create-Plan 进展展示与 LLM 输入/输出（UI 方案）

目标：当用户在前端输入 TopTask 并点击 `Create Plan` 后：
- 能实时看到：当前是第几次 attempt、卡在 `PLAN_GEN` 还是 `PLAN_REVIEW`
- 能逐步查看每一步 LLM 的输入与输出（prompt/raw/parsed/normalized/validator_error）
- 前端仍是看板：不做调度；CLI 仍是主驱动；前端关闭不影响 create-plan 继续跑

约束：
- 不频繁启动 subprocess 做轮询（会卡）
- 轮询走后端“轻查询”（读 DB、读少量文件）
- 单机串行：MVP 默认只允许同时运行一个 create-plan job（避免 plan_id=None 的 llm_calls 混淆）

---

## 1) 数据事实源（SSOT）

### 1.1 `llm_calls` 已具备的字段（用于进展与输入输出）
`core/plan_workflow.py` 在 create-plan 流程中会写入 `llm_calls`：
- `scope="PLAN_GEN"`：生成计划
  - `plan_id` 初期为 `null`，待解析出 plan_id 后会回填（同一次调用的行会被 update）
  - `meta_json.attempt`：第几次 attempt
- `scope="PLAN_REVIEW"`：审核计划
  - `plan_id` 有值（已解析出）
  - `meta_json.attempt`：第几次 attempt
  - `meta_json.review_attempt`：同一次 attempt 下 reviewer 重试次数

因此进展可用规则推断：
- 当前 attempt：取最近一次 `PLAN_GEN/PLAN_REVIEW` 的 `meta_json.attempt`
- 当前阶段 phase：
  - 最近一条相关 llm_calls.scope 为 `PLAN_GEN` ⇒ 处于 PLAN_GEN
  - 最近一条相关 llm_calls.scope 为 `PLAN_REVIEW` ⇒ 处于 PLAN_REVIEW
- 卡住的具体原因：
  - `validator_error` / `error_code` / `error_message` 决定（例如 contract mismatch / unparseable json / timeout）

### 1.2 `task_events` 辅助信息（可选增强）
`core/plan_workflow.py` 还会写入：
- `PLAN_REVIEWED`（payload 含 attempt/score/action_required）
- `PLAN_APPROVED`
用于 UI 展示“review 结果摘要”，但 UI 不依赖事件推断阶段（以 llm_calls 为准）。

---

## 2) 后端改造（必须）：create-plan 异步 + 进度查询

当前 `POST /api/plan/create` 是同步阻塞，前端无法显示实时进展。

### 2.1 新增异步接口
新增：
- `POST /api/plan/create_async`
  - body：`{ top_task: string, max_attempts: number, keep_trying?: bool, max_total_attempts?: number }`
  - 行为：
    - 启动后台进程（Windows detached）：`agent_cli.py create-plan ...`
    - 写入一个 job 状态文件：`state/create_plan_process.json`（包含 pid、started_at、top_task_hash、max_attempts）
    - 返回：`{ started: true, job_id, pid }`
  - 并发约束：
    - 若已有 create-plan job 在跑（pid alive），返回 `{ started:false, reason:"already running", job_id }`

新增：
- `GET /api/jobs/{job_id}`
  - 返回：
    ```json
    {
      "job_id": "...",
      "kind": "CREATE_PLAN",
      "status": "RUNNING|DONE|FAILED",
      "pid": 1234,
      "started_at": "...",
      "finished_at": "...",
      "plan_id": "..." | null,
      "attempt": 1,
      "phase": "PLAN_GEN|PLAN_REVIEW|UNKNOWN",
      "review_attempt": 1,
      "last_llm_call": { ... llm_calls row subset ... },
      "stdout_tail": "...",
      "stderr_tail": "...",
      "hint": "下一步怎么做（短句）"
    }
    ```

说明：
- `attempt/phase/review_attempt/last_llm_call` 通过 DB 查询 `llm_calls` 推断（见 3.1）
- `stdout_tail/stderr_tail` 可通过：
  - 方案 A（简单）：后台进程 stdout/stderr 重定向到 `logs/jobs/<job_id>.log`，后端读 tail
  - 方案 B（纯 DB）：不返回 stdout_tail，仅依赖 llm_calls（推荐，性能更稳）

### 2.2 新增查询接口：按 plan/job 读取 LLM I/O（必须）
现有接口仅支持 task_id：`GET /api/task/{task_id}/llm`。
create-plan 的 llm_calls 通常是 task_id=null，因此需要新增：
- `GET /api/llm_calls`
  - query：
    - `plan_id`（可选）
    - `scopes`（可选，逗号分隔：PLAN_GEN,PLAN_REVIEW,TASK_ACTION,TASK_CHECK...）
    - `agent`（可选）
    - `limit`（默认 50）
  - 返回：llm_calls 行的子集字段（prompt/response/parsed/normalized/validator_error/error_code/error_message/meta_json）

MVP 关联策略（单机串行）：
- create-plan job 运行时若 plan_id 仍未知：
  - 以“最新的一条 agent=xiaobo + scope=PLAN_GEN 且 plan_id IS NULL”的行作为进展来源
- 一旦 plan_id 已回填：
  - 直接按 plan_id 过滤

若未来要支持“同时多个 create-plan”：
- 需要把 `job_id` 写入 llm_calls.meta_json（plan_workflow/record_llm_call 增加 meta 字段），再按 job_id 过滤。

---

## 3) 进度推断规则（后端实现）

### 3.1 attempt/phase 的确定规则（稳定）
输入：`job_id`（以及可选 plan_id）

1) 找到“候选 llm_calls 集合”
- 若 job 已解析出 plan_id：`WHERE plan_id=? AND scope IN ('PLAN_GEN','PLAN_REVIEW') ORDER BY created_at DESC`
- 否则：`WHERE plan_id IS NULL AND agent='xiaobo' AND scope='PLAN_GEN' ORDER BY created_at DESC`（单机串行假设）

2) phase：
- 最新一条 scope == PLAN_GEN ⇒ phase=PLAN_GEN
- 最新一条 scope == PLAN_REVIEW ⇒ phase=PLAN_REVIEW
- 否则 UNKNOWN

3) attempt：
- 从最新一条的 `meta_json.attempt`（若缺则 1）

4) review_attempt（只有 PLAN_REVIEW 阶段）：
- 从最新一条的 `meta_json.review_attempt`（若缺则 1）

5) 卡住原因：
- 优先用 `validator_error`（contract mismatch / JSON parse error）
- 其次 `error_code/error_message`
- 输出给前端的文案必须是短句 + 下一步怎么做（不返回 traceback）

---

## 4) 前端 UI 改造（React）

### 4.1 Create Plan 按钮行为
从同步调用改为：
1) `POST /api/plan/create_async` → 得到 job_id
2) UI 展示一个 “Create Plan Progress” 面板（左栏或右栏）
3) 每 0.5–1s 轮询：`GET /api/jobs/{job_id}`
4) 当 status=DONE 且有 plan_id：
   - 自动切换选中 plan
   - 刷新 `graph` 与 `plan_snapshot`

### 4.2 进度面板展示字段
必须展示：
- `attempt`: 第 N 次
- `phase`: PLAN_GEN / PLAN_REVIEW
- `review_attempt`:（若在 PLAN_REVIEW）第 M 次 reviewer 重试
- 最近一次 llm_call 的 summary：
  - created_at、scope、agent
  - validator_error / error_code（如果有）

### 4.3 每一步 LLM 输入/输出查看（必须）
在进度面板下方展示一个“LLM Timeline”列表（仅 PLAN_GEN/PLAN_REVIEW）：
- 每行：created_at / scope / attempt / review_attempt / error_code
- 点击一行：展开显示
  - Prompt（输入）
  - Raw response（输出）
  - Parsed JSON
  - Normalized JSON
  - validator_error / error_message

数据来源：
- `GET /api/llm_calls?plan_id=...&scopes=PLAN_GEN,PLAN_REVIEW&limit=200`
- 若 plan_id 未知：用 job 推断的“最新 PLAN_GEN(plan_id is null)”查询结果（MVP 单机串行）

---

## 5) 验收（可验证）
- 点击 Create Plan 后 1 秒内可看到：
  - 第几次 attempt
  - 当前阶段 PLAN_GEN/PLAN_REVIEW
- 任意阶段可展开查看该阶段的 prompt/response/parsed/normalized/validator_error
- create-plan 在前端关闭后仍继续运行；再次打开前端可通过 jobs 状态恢复显示

