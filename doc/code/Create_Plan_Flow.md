# Create-Plan 流程图（MVP）

```
create-plan(top_task)
  |
  v
attempt = 1..max_total_attempts
  |
  v
[PLAN_GEN xiaobo]  ->  写 llm_calls(scope=PLAN_GEN)
  |
  +-- 失败/不可解析/不符合 xiaobo_plan_v1 --> 记录 gen_notes -> (keep_trying/attempt<max_plan_attempts ? 下一次 attempt : FAIL 退出)
  |
  v
解析 plan_json + validate_plan_dict
  |
  +-- 校验失败 --> task_events(ERROR: PLAN_INVALID) -> gen_notes -> (重试 attempt 或 FAIL 退出)
  |
  v
拿到 plan_id -> 写 plans stub + 回填 PLAN_GEN 的 llm_calls.plan_id
  |
  v
review_attempt = 1..max_review_attempts_per_plan
  |
  v
[PLAN_REVIEW xiaojing] -> 写 llm_calls(scope=PLAN_REVIEW, meta: attempt/review_attempt)
  |
  +-- 输出不可解析/不符合 PLAN_REVIEW 合同 --> 生成“重发”prompt -> (review_attempt<上限 ? 重试 PLAN_REVIEW : FAIL 退出)
  |
  v
review_json 合法
  |
  +-- (total_score>=90 && action_required=APPROVE)
  |        |
  |        v
  |     写 tasks/plan.json + upsert_plan(DB) + task_events(PLAN_APPROVED) -> DONE 返回
  |
  +-- 否则 (MODIFY/REQUEST_EXTERNAL_INPUT/低分)
           |
           v
      task_events(PLAN_REVIEWED)
      生成整改笔记(<=500字) -> workspace/review_notes/<plan_id>/plan_review_attempt_<attempt>.md
      review_notes 注入下一次 PLAN_GEN（只带最新一份）
           |
           v
      (attempt<max_plan_attempts ? 下一次 attempt : PlanNotApprovedError 退出)
```

