# Error Codes & Recovery Spec
## 错误码与恢复策略（MVP）

> 目标：把“失败”变成系统可处理的结构化事件，避免数据混乱与状态悬空。

---

## 1. 错误码定义（MVP）

### 1.1 LLM 类
- LLM_UNPARSEABLE：输出非 JSON 或 schema_version 不匹配
- LLM_TIMEOUT：单次调用超时
- LLM_REFUSAL：模型拒绝（如安全原因）

### 1.2 Skill 类
- SKILL_FAILED：skill 返回 FAILED
- SKILL_TIMEOUT：skill 超时
- SKILL_BAD_INPUT：输入缺失/格式不支持

### 1.3 输入类
- INPUT_CONFLICT：同一 requirement 多版本冲突
- INPUT_MISSING：required 输入不足

### 1.4 运行时保险丝
- PLAN_TIMEOUT：总运行时间超
- MAX_LLM_CALLS_EXCEEDED：LLM 调用次数超
- MAX_ATTEMPTS_EXCEEDED：节点修改轮次超

---

## 2. 错误 → 状态落点（强制表）

| error_code | task.status | blocked_reason | attempt_count |
|---|---|---|---|
| LLM_UNPARSEABLE | FAILED | null | +1 |
| LLM_TIMEOUT | FAILED | null | +1 |
| SKILL_FAILED | BLOCKED | WAITING_SKILL | 0 或 +1（按可重试） |
| SKILL_BAD_INPUT | BLOCKED | WAITING_INPUT | 0 |
| INPUT_MISSING | BLOCKED | WAITING_INPUT | 0 |
| INPUT_CONFLICT | BLOCKED | WAITING_EXTERNAL | 0 |
| MAX_ATTEMPTS_EXCEEDED | BLOCKED | WAITING_EXTERNAL | 不再增加 |
| PLAN_TIMEOUT | (plan stop) | - | - |

---

## 3. 恢复策略（MVP）

### 3.1 FAILED 的恢复
- FAILED 不自动重试
- 若仍可继续：下一轮 recompute 时可将 FAILED 置为 READY（可配置）
- 默认：FAILED 需要小波重新执行（attempt_count 控制）

### 3.2 WAITING_INPUT 的恢复
- 当新 evidence 绑定后：自动 READY

### 3.3 WAITING_SKILL 的恢复
- 可重试 skill（同 idempotency_key 不重复执行）
- 若连续失败 >= 3：升级 WAITING_EXTERNAL

---

## 4. 事件记录（强制）
每次错误必须写：
- task_events(event_type=ERROR)
- payload_json = {error_code, message, context}

---
