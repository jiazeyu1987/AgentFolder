# Coding Agent Implementation Checklist
## 模块划分 + 逐文件 TODO + 验收脚本（可直接交给自动 coding Agent）

> 目标：把你当前已有全部规格文档转成“可直接落地”的工程任务清单。
> 输出包含：
> - 模块边界与依赖
> - 逐文件实现 TODO（按 repo layout）
> - 最小验收脚本与验收步骤
>
> 适配：MVP 单机、串行、skills 真执行、全量日志可追溯。

---

## 0. 依赖与运行环境（MVP）

- Python 3.11+（建议 3.11）
- 依赖包（最小）：
  - pyyaml
  - pydantic（或 dataclasses + jsonschema，二选一）
  - python-magic（可选，用于 mime 识别）
  - PyMuPDF 或 pdfplumber（pdf 文本提取）
  - python-docx（docx 提取）
- SQLite（标准库）

---

## 1. Repo 结构（建议）

```text
agent_root/
├── run.py
├── config.py
├── core/
│   ├── db.py
│   ├── models.py
│   ├── plan_loader.py
│   ├── readiness.py
│   ├── scheduler.py
│   ├── matcher.py
│   ├── llm_client.py
│   ├── prompts.py
│   ├── events.py
│   ├── artifacts.py
│   ├── reviews.py
│   └── errors.py
├── skills/
│   ├── registry.yaml
│   ├── registry.py
│   └── impl/
│       ├── file_fingerprint.py
│       ├── text_extract.py
│       ├── template_render.py
│       ├── diff_artifact.py
│       └── validator_basic.py
├── tasks/
│   └── plan.json
├── state/
│   ├── state.db
│   └── migrations/001_init.sql
├── workspace/
│   ├── inputs/
│   ├── artifacts/
│   ├── reviews/
│   └── required_docs/
└── logs/llm_runs.jsonl
```

---

## 2. 模块职责与依赖（必须遵守）

### 2.1 core/db.py
- 连接 SQLite
- 执行 migrations
- 提供事务 helper（with transaction）
- 提供基础 CRUD（或 DAO）

### 2.2 core/models.py
- 定义 Pydantic models / dataclasses：
  - Plan, TaskNode, TaskEdge, InputRequirement
  - Evidence, Artifact, Review, SkillRun, TaskEvent
- 强制校验：plan.json schema、LLM 输出合同 schema_version

### 2.3 core/plan_loader.py
- 读取 tasks/plan.json
- 校验（按 Plan_Definition_Spec_and_Example.md）
- Upsert 到 plans/task_nodes/task_edges/input_requirements
- 初始化 node.status=PENDING, active_branch=1

### 2.4 core/matcher.py
- 实现 Requirement_Matcher_Spec.md 的确定性匹配
- scan workspace/inputs
- 计算 sha256、扩展名
- bind evidences（幂等：uidx_evidence_req_ref）
- 写 task_events(EVIDENCE_ADDED, FILE_OBSERVED)

### 2.5 core/readiness.py
- 实现 Task_Tree_Runtime_Rules.md 的 is_ready()
- 实现全量/局部 recompute（MVP 可全量）
- blocked_reason 推导（WAITING_INPUT/WAITING_SKILL/WAITING_EXTERNAL）
- 写 STATUS_CHANGED events

### 2.6 core/scheduler.py
- 从 READY + TO_BE_MODIFY 选任务
- MVP 策略：
  - TO_BE_MODIFY 优先
  - priority 高优先
  - attempt_count 少优先

### 2.7 core/prompts.py
- 加载 shared_prompt.md 与 agents/*_prompt.md
- 维护 version + hash（SHA256）
- 提供 build_final_prompt(shared, agent, runtime_context)

### 2.8 core/llm_client.py
- 统一 LLM 调用接口（同步）
- 超时控制（LLM_TIMEOUT）
- 返回必须是 JSON（失败 -> LLM_UNPARSEABLE）
- 写 logs/llm_runs.jsonl（按 RunPY_MVP_Skeleton_Spec.md）

### 2.9 skills/registry.py
- 解析 skills/registry.yaml（按 Skills_Registry_Spec.md）
- 动态加载 implementation（module:function）
- 生成 idempotency_key
- cache=true 时复用已有 SUCCEEDED skill_runs

### 2.10 core/artifacts.py
- 写 artifact 文件到 workspace/artifacts/<task_id>/
- 计算 sha256
- upsert artifacts 表
- 更新 task_nodes.active_artifact_id（激活版本）
- 写 ARTIFACT_CREATED event

### 2.11 core/reviews.py
- 写 review JSON 到 workspace/reviews/<task_id>/review_<ts>.json
- 校验 xiaojing_review_v1 schema（LLM_IO_Contract_Spec.md）
- upsert reviews 表
- 写 REVIEW_CREATED event

### 2.12 core/events.py
- emit_event helper
- emit_status_change helper（STATUS_CHANGED）

### 2.13 core/errors.py
- 错误码枚举（Error_Recovery_Spec.md）
- error -> 状态落点函数 apply_error()

---

## 3. 逐文件 TODO（Coding Agent 按顺序实现）

### 3.1 config.py
- 常量：MAX_PLAN_RUNTIME_SECONDS, MAX_TASK_ATTEMPTS, MAX_LLM_CALLS, POLL_INTERVAL_SECONDS
- 路径：ROOT, WORKSPACE_INPUTS, WORKSPACE_ARTIFACTS, WORKSPACE_REVIEWS, WORKSPACE_REQUIRED_DOCS, LOGS_LLM_RUNS

### 3.2 state/migrations/001_init.sql
- 直接拷贝 StateDB_Migration_v1_SQL.md 的 SQL（注意 ALTER TABLE 部分可合并进 CREATE TABLE）

### 3.3 core/db.py
- `init_db(db_path)`：执行 migrations（只执行一次，可用 migrations 表记录）
- `execute(sql, params)`、`fetchone`、`fetchall`
- `transaction()` context manager

### 3.4 core/models.py
- 定义 PlanJsonSchema（plan/nodes/edges/requirements）
- 定义 LLMActionOutput（xiaobo_action_v1）
- 定义 LLMReviewOutput（xiaojing_review_v1）
- 定义 SkillResult（skills run 返回结构）

### 3.5 core/plan_loader.py
- `load_plan_json(path) -> PlanSpec`
- `upsert_plan(plan_spec)`
- `upsert_nodes(nodes)`
- `upsert_edges(edges)`
- `upsert_requirements(reqs)`

### 3.6 core/matcher.py
- `scan_inputs(plan_id)`
- `match_file_to_requirements(file_path, sha256, requirements) -> list[requirement_id]`
- `bind_evidence(requirement_id, ref_id=sha256, ref_path=file_path, sha256=sha256)`（幂等）
- 多版本冲突：写 INPUT_CONFLICT event（先不阻塞）

### 3.7 core/readiness.py
- `is_ready(task_id) -> (bool, blocked_reason|None)`
- `recompute_plan(plan_id)`：遍历 tasks 更新 READY/BLOCKED
- 依赖：load prereqs (DEPENDS_ON)，检查 DONE
- 需求：required inputs evidence_count >= min_count

### 3.8 core/scheduler.py
- `pick_next_task(plan_id)` 返回一个 task
- 选择集合：TO_BE_MODIFY + READY
- 排序：status_weight + priority desc + attempt_count asc + created_at asc

### 3.9 core/prompts.py
- `load_prompt(path)->(text, version, hash)`（version 可用文件头注释或单独 json 维护；MVP 用 hash 即可）
- `build_final_prompt(shared, agent, runtime_context)->str`

### 3.10 core/llm_client.py
- `call_llm(agent, final_prompt, timeout)->dict`
- 记录日志：写 jsonl（包含 prompt hashes、runtime_context_hash）
- JSON 解析与 schema_version 校验（失败抛 LLM_UNPARSEABLE）

### 3.11 skills/registry.py
- `load_registry(yaml_path)->SkillRegistry`
- `run_skill(skill_name, task, inputs, params)->SkillRunResult`
  - 写 skill_runs RUNNING
  - 执行 impl.run()
  - 写 skill_runs SUCCEEDED/FAILED + outputs
  - emit_event(SKILL_RUN)

### 3.12 skills/impl/file_fingerprint.py
- 输入：path
- 输出：sha256, size_bytes, mime_type（mime_type 可选）
- 必须稳定、无副作用

### 3.13 skills/impl/text_extract.py
- 支持：pdf/docx/md/txt
- 输出：workspace/artifacts/{task_id}/extracted_{input_sha256}.txt
- max_chars 限制
- 失败返回 SKILL_BAD_INPUT 或 SKILL_FAILED

### 3.14 skills/impl/template_render.py
- 输入：template_path + data_json
- 输出：md 文件

### 3.15 skills/impl/diff_artifact.py
- 输入：old_path, new_path（文本）
- 输出：diff_summary.md（简化：统一 diff 或 line diff）

### 3.16 skills/impl/validator_basic.py
- 输入：artifact_path + rules（章节列表等）
- 输出：pass/fail + 缺项列表（evidence）

### 3.17 core/artifacts.py & core/reviews.py
- 文件写入、hash、落库、事件
- 更新 task_nodes.active_artifact_id

### 3.18 run.py（主入口）
必须实现：
- init_db
- load_plan_into_db_if_needed
- loop：scan_inputs -> recompute -> xiaobo_round -> xiaojing_round -> stop conditions
- timeout / max calls / attempts 保险丝
- BLOCKED summary 写入 workspace/required_docs/blocked_summary.md

---

## 4. 小波/小京轮实现要点（按合同）

### 4.1 小波（执行者）
- 对 READY 或 TO_BE_MODIFY 的 task 调用 LLM，必须返回 xiaobo_action_v1
- ARTIFACT：写 artifact 文件，status -> READY_TO_CHECK
- NEEDS_INPUT：写 required_docs/<task_id>.md，status -> BLOCKED(WAITING_INPUT)
- ERROR：apply_error（通常 FAILED 或 WAITING_EXTERNAL）

### 4.2 小京（监督者）
- 对 READY_TO_CHECK 的 task 调用 LLM，必须返回 xiaojing_review_v1
- score>=90：status -> DONE
- score<90：status -> TO_BE_MODIFY，attempt+1；attempt>=MAX_TASK_ATTEMPTS -> BLOCKED(WAITING_EXTERNAL)

---

## 5. 最小验收脚本（建议）

### 5.1 scripts/acceptance_smoke.py（建议新增）
功能：
1) 清空 state.db 与 workspace（保留目录）
2) 导入 tasks/plan.json（Fruit 示例）
3) 启动 run.py（或调用 main loop N 次）
4) 自动检查：
   - state.db 中存在 plans/task_nodes/task_edges/input_requirements
   - logs/llm_runs.jsonl 有记录（至少 1 条）
   - workspace/required_docs 至少生成一个缺资料清单（首次运行大概率）
5) 往 workspace/inputs/ 里放一个匹配文件（可用空 md/pdf 替代）
6) 再跑 N 次循环，检查：
   - 至少一个 task 进入 READY_TO_CHECK
   - 至少一个 review JSON 写入 workspace/reviews
   - 产生 artifacts 文件

> 注意：真正 LLM 环境下 smoke 会消耗 token；可在本地用较小模型或加开关限制回合数。

---

## 6. 完工判定（MVP）

必须满足：
- 不做 mock：skill_runs 有真实记录，artifacts 有真实文件
- 输入文件到达 -> evidence 绑定 -> READY 解锁
- 小波产出 -> READY_TO_CHECK
- 小京审查 -> DONE 或 TO_BE_MODIFY
- 修改轮不超过 MAX_TASK_ATTEMPTS
- 全流程 events 与 llm_runs 可追溯

---

## 7. 最后建议（给 Coding Agent 的执行顺序）
1) 先把 DB + plan_loader + matcher 做通（不接 LLM）
2) 再接 skills（text_extract）并落库
3) 再接 LLM（只跑一个任务）
4) 最后补 scheduler + stop conditions + blocked summary

这样最稳，不会一上来全耦合。
