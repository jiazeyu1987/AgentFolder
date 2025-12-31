# Minimal Runnable MVP Skeleton (run.py)
## 主循环骨架 + 状态机落点 + LLM/Skills 记录规范（可直接照抄实现）

> 目标：提供一份**最小可运行**的工程骨架说明，确保你从 Day 1 开始就：
> - 不做 mock（skills 真执行）
> - 状态不混乱（所有变化落库 + 可追溯）
> - 可控（timeout / max iterations / max llm calls）
>
> 本文档是“工程实现蓝图”，你可以直接让 Coding Agent 按本文档生成代码。

---

## 1. 目录结构（强制）

```text
agent_root/
├── shared_prompt.md
├── agents/
│   ├── xiaobo_prompt.md
│   ├── xiaojing_prompt.md
│   └── xiaoxie_prompt.md
├── rubric/
│   └── review_rubric.json               # 来自 Review_Rubric_Spec.md 的可执行版本
├── skills/
│   ├── registry.yaml                    # Skills_Registry_Spec.md 定义
│   └── impl/
│       ├── file_fingerprint.py
│       ├── text_extract.py
│       ├── template_render.py
│       ├── diff_artifact.py
│       └── validator_basic.py
├── tasks/
│   └── plan.json                        # 任务树定义态（Plan + nodes + edges + requirements）
├── state/
│   ├── state.db                         # SQLite（用 StateDB_Migration_v1_SQL.md 建库）
│   └── migrations/
│       └── 001_init.sql
├── workspace/
│   ├── inputs/                          # 用户放资料
│   ├── artifacts/                       # Agent 产物
│   ├── reviews/                         # 审查输出（结构化 JSON）
│   └── required_docs/                   # 缺资料清单（给用户看）
├── logs/
│   └── llm_runs.jsonl                   # 每次 LLM 调用记录一行 JSON
└── run.py
```

---

## 2. 关键配置（config 常量，MVP 固定即可）

```python
MAX_PLAN_RUNTIME_SECONDS = 2 * 60 * 60     # 2h 总运行时长保险丝
MAX_TASK_ATTEMPTS = 3                      # 同一节点最多修改轮次
MAX_LLM_CALLS = 200                        # 防止 prompt 循环爆炸
POLL_INTERVAL_SECONDS = 3                  # 无 READY 时轮询
```

---

## 3. 状态机（应用层补充状态）

SQLite schema 里 task_nodes.status 只有：
PENDING/READY/IN_PROGRESS/BLOCKED/DONE/FAILED/ABANDONED

但你设计里还有：ReadyToCheck / ToBeModify。MVP 推荐用两种方式之一：

### 方案 A（推荐）：扩展 status 枚举（最直观）
直接把 status 增加：
- READY_TO_CHECK
- TO_BE_MODIFY

> SQLite 不会强校验枚举，应用层控制即可。

### 方案 B：用 task_events + blocked_reason 表达（不推荐）
可做但会绕，影响可读性。

本文档按 **方案 A** 写伪代码。

---

## 4. plan.json 的最小结构（定义态）

```json
{
  "plan": { "plan_id": "...", "title": "...", "owner_agent_id": "xiaobo", "root_task_id": "..." },
  "nodes": [ { "task_id": "...", "node_type": "GOAL", "title": "...", "owner_agent_id": "xiaobo", "priority": 0 } ],
  "edges": [ { "edge_id": "...", "from_task_id": "...", "to_task_id": "...", "edge_type": "DECOMPOSE", "metadata": {"and_or":"AND"} } ],
  "requirements": [
    { "requirement_id": "...", "task_id": "...", "name": "product_materials", "kind": "FILE", "required": 1, "min_count": 1, "allowed_types": ["pdf","docx"], "source": "USER" }
  ]
}
```

MVP 要求：启动时把 plan.json 写入 state.db（upsert）。

---

## 5. run.py 主循环（伪代码，按此实现）

### 5.1 初始化
```python
def main():
    t0 = now()
    llm_calls = 0

    ensure_db_initialized()
    load_plan_into_db_if_needed()

    skills = load_skills_registry("skills/registry.yaml")
    prompts = load_prompts()

    while True:
        if seconds_since(t0) > MAX_PLAN_RUNTIME_SECONDS:
            emit_timeout_event(plan_id, scope="PLAN")
            break

        # 0) 扫描 inputs 文件夹，绑定 evidence（文件hash驱动）
        scan_inputs_and_bind_evidence(plan_id)

        # 1) READY 计算（局部/或简化：每轮扫一遍）
        recompute_readiness_for_plan(plan_id)

        # 2) 小波执行轮
        llm_calls = xiaobo_round(plan_id, prompts, skills, llm_calls)
        if llm_calls > MAX_LLM_CALLS:
            emit_timeout_event(plan_id, scope="LLM_CALLS")
            break

        # 3) 小京审查轮
        llm_calls = xiaojing_round(plan_id, prompts, llm_calls)
        if llm_calls > MAX_LLM_CALLS:
            emit_timeout_event(plan_id, scope="LLM_CALLS")
            break

        # 4) 结束判定
        if is_plan_done(plan_id):
            break

        if is_plan_blocked_waiting_user(plan_id):
            write_blocked_summary(plan_id)
            break

        sleep(POLL_INTERVAL_SECONDS)
```

---

## 6. 文件扫描与 evidence 绑定（不做 mock 的关键）

### 6.1 目标
用户把资料放进 `workspace/inputs/` 后：
- 系统计算 sha256
- 匹配 requirement（按 allowed_types + 简单规则）
- 写入 evidences（幂等）
- 写 task_events(EVIDENCE_ADDED)

### 6.2 伪代码
```python
def scan_inputs_and_bind_evidence(plan_id):
    for file_path in list_files("workspace/inputs"):
        sha = sha256(file_path)
        meta = file_fingerprint(file_path)

        # 可选：将 meta 写到一个 files 表；MVP 可直接写 event
        emit_event("FILE_OBSERVED", payload={"path": file_path, "sha256": sha, "meta": meta})

        matched = match_requirements(plan_id, file_path, sha)
        for req in matched:
            bind_evidence(req.requirement_id, ref_id=sha, ref_path=file_path, sha256=sha)
            emit_event("EVIDENCE_ADDED", task_id=req.task_id, payload={"requirement_id": req.requirement_id, "sha256": sha, "path": file_path})
```

---

## 7. READY 计算（MVP 简化版）

MVP 可以先全量扫一遍（plan 规模不大时可接受）：

```python
def recompute_readiness_for_plan(plan_id):
    for task in list_tasks(plan_id):
        if task.status in ("DONE","ABANDONED","READY_TO_CHECK"):
            continue

        ready, blocked_reason = is_ready(task.task_id)
        if ready:
            if task.status not in ("READY","IN_PROGRESS"):
                set_status(task.task_id, "READY", None)
                emit_status_change(task.task_id, "READY")
        else:
            if task.status in ("READY","FAILED","PENDING"):
                set_status(task.task_id, "BLOCKED", blocked_reason)
                emit_status_change(task.task_id, "BLOCKED", {"reason": blocked_reason})
```

---

## 8. 小波执行轮（执行者）

### 8.1 选择任务
- 只处理 status=READY 且 active_branch=1
- 选择策略：priority 高的先（MVP）

### 8.2 执行一个任务的通用步骤
1) status -> IN_PROGRESS
2) 若需要 skills：执行 skills（写 skill_runs）→ artifacts/evidence 入库
3) 调 LLM（写 logs/llm_runs.jsonl）生成产物或缺资料清单
4) 若缺资料：写 required_docs 文件 + task -> BLOCKED(WAITING_INPUT)
5) 若产物生成：写 artifact 文件 + task -> READY_TO_CHECK

### 8.3 伪代码
```python
def xiaobo_round(plan_id, prompts, skills, llm_calls):
    ready_tasks = list_ready_tasks(plan_id)

    for t in ready_tasks:
        if seconds_since_plan_start_exceeded():
            emit_timeout_event(plan_id, scope="PLAN")
            return llm_calls

        set_status(t.task_id, "IN_PROGRESS", None)
        emit_status_change(t.task_id, "IN_PROGRESS")

        # 1) skills（按节点需要，MVP 可以固定：先 text_extract 再写入 context）
        llm_context_parts = []
        input_files = get_task_input_files(t.task_id)

        if input_files:
            run = run_skill("text_extract", task=t, inputs=input_files, params={"max_chars": 200000})
            if run.status == "FAILED":
                set_status(t.task_id, "BLOCKED", "WAITING_SKILL")
                emit_status_change(t.task_id, "BLOCKED", {"reason":"WAITING_SKILL"})
                continue
            llm_context_parts.append(load_extracted_text_snippets(run))

        # 2) LLM
        prompt = build_final_prompt(prompts.shared, prompts.xiaobo, runtime_context(t, llm_context_parts))
        resp = call_llm(prompt)
        llm_calls += 1
        log_llm_run(t.task_id, prompt, resp)

        if resp.get("needs_input"):
            write_required_docs_file(t.task_id, resp["required_docs"])
            set_status(t.task_id, "BLOCKED", "WAITING_INPUT")
            emit_status_change(t.task_id, "BLOCKED", {"reason":"WAITING_INPUT"})
            continue

        if resp.get("artifact"):
            artifact_path = write_artifact_file(t.task_id, resp["artifact"])
            upsert_artifact_row(t.task_id, artifact_path)
            emit_event("ARTIFACT_CREATED", task_id=t.task_id, payload={"path": artifact_path})

            set_status(t.task_id, "READY_TO_CHECK", None)
            emit_status_change(t.task_id, "READY_TO_CHECK")
            continue

        # fallback: unknown response
        increment_attempt(t.task_id)
        set_status(t.task_id, "FAILED", None)
        emit_status_change(t.task_id, "FAILED", {"reason":"UNPARSEABLE_RESPONSE"})
    return llm_calls
```

---

## 9. 小京审查轮（监督者）

### 9.1 审查对象
- status=READY_TO_CHECK 的节点

### 9.2 审查输出必须符合 Rubric JSON
- total_score
- breakdown
- suggestions
- action_required

### 9.3 伪代码
```python
def xiaojing_round(plan_id, prompts, llm_calls):
    to_check = list_tasks_by_status(plan_id, "READY_TO_CHECK")

    for t in to_check:
        artifact = load_latest_artifact(t.task_id)
        review_prompt = build_review_prompt(prompts.shared, prompts.xiaojing, t, artifact)
        resp = call_llm(review_prompt)
        llm_calls += 1
        log_llm_run(t.task_id, review_prompt, resp)

        save_review_json_file(t.task_id, resp)
        upsert_review_row(t.task_id, resp)

        score = resp["total_score"]
        if score >= 90:
            set_status(t.task_id, "DONE", None)
            emit_status_change(t.task_id, "DONE")
            recompute_dependents_and_parents(t.task_id)
        else:
            increment_attempt(t.task_id)
            if get_attempt(t.task_id) >= MAX_TASK_ATTEMPTS:
                set_status(t.task_id, "BLOCKED", "WAITING_EXTERNAL")
                emit_status_change(t.task_id, "BLOCKED", {"reason":"WAITING_EXTERNAL"})
                write_blocked_reason_file(t.task_id, resp)
            else:
                set_status(t.task_id, "TO_BE_MODIFY", None)
                emit_status_change(t.task_id, "TO_BE_MODIFY")
                write_modify_suggestions_file(t.task_id, resp["suggestions_json"])
    return llm_calls
```

---

## 10. 小波修改轮（合并进 xiaobo_round）
当 status=TO_BE_MODIFY 时，小波的 runtime_context 必须带上：
- 修改建议（workspace/required_docs or modify_suggestions）
- 上一版产物 diff（可用 diff_artifact skill）

MVP 简化：
- 将 TO_BE_MODIFY 视为 READY（允许执行）或在执行轮单独取出处理

推荐：TO_BE_MODIFY 也进入可执行队列，但 priority 更高。

---

## 11. LLM 日志格式（logs/llm_runs.jsonl，强制）

每行一个 JSON：
```json
{
  "ts": "ISO8601",
  "plan_id": "...",
  "task_id": "...",
  "agent": "xiaobo|xiaojing|xiaoxie",
  "shared_prompt_version": "v3",
  "shared_prompt_hash": "....",
  "agent_prompt_version": "v5",
  "agent_prompt_hash": "....",
  "runtime_context_hash": "....",
  "final_prompt": "....",
  "response": { "...": "..." }
}
```

---

## 12. Skills 执行的落库规范（强制）

每次 skills 调用必须：
- 写 skill_runs(status=RUNNING)
- 执行
- 写 skill_runs(status=SUCCEEDED/FAILED) + outputs
- 将 outputs 写入 artifacts/evidences
- 写 task_events(event_type=SKILL_RUN)

---

## 13. 结束条件（MVP）

### 13.1 Plan Done
- root_task DONE（或按 AND/OR 聚合推导为 DONE）

### 13.2 Plan Blocked（等待用户）
- 所有未完成任务均 BLOCKED 且 reason=WAITING_INPUT/WAITING_EXTERNAL

输出：
- `workspace/required_docs/blocked_summary.md`

---

## 14. 最小验收用例（你必须能跑通）

1) 创建 plan.json（含 3~8 个节点，至少一个生成类任务）
2) 启动 run.py
3) 小波发现缺资料 → 生成 required_docs 清单
4) 用户放入资料 → evidence 绑定 → READY
5) 小波生成 artifact → READY_TO_CHECK
6) 小京评分 <90 → TO_BE_MODIFY（写建议）
7) 小波改版 → READY_TO_CHECK
8) 小京评分 ≥90 → DONE
9) 下游任务解锁 → 最终 root DONE 或 BLOCKED summary

---
