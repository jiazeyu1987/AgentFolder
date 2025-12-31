# LLM I/O Contract Specification
## LLM 输出合同（小波执行 & 小京审查）

> 目标：把“模型输出”约束成可解析、可落库、可驱动状态机的结构化 JSON。
> 系统必须严格解析，不符合合同则视为 FAILED（UNPARSEABLE）。

---

## 1. 通用要求（强制）
- 输出必须是 **单个 JSON 对象**
- 不允许夹带自然语言解释（解释放在 JSON 字段内）
- 必须包含 `schema_version`

---

## 2. 小波执行输出合同（xiaobo_action_v1）

### 2.1 Schema
```json
{
  "schema_version": "xiaobo_action_v1",
  "task_id": "string",
  "result_type": "NEEDS_INPUT|ARTIFACT|NOOP|ERROR",
  "needs_input": {
    "required_docs": [
      {
        "name": "string",
        "description": "string",
        "accepted_types": ["pdf","docx","xlsx","md","txt"],
        "suggested_path": "workspace/inputs/..."
      }
    ]
  },
  "artifact": {
    "name": "string",
    "format": "md|txt|json",
    "content": "string",
    "path_hint": "workspace/artifacts/{task_id}/...",
    "summary": "string"
  },
  "error": {
    "code": "string",
    "message": "string",
    "suggestion": "string"
  }
}
```

### 2.2 约束
- 当 `result_type=NEEDS_INPUT`：必须提供 `needs_input.required_docs`
- 当 `result_type=ARTIFACT`：必须提供 `artifact.content/name/format`
- NOOP 用于“当前无需动作”（MVP 可不用）
- ERROR 表示模型认为无法完成（会触发 FAILED 或 BLOCKED）

---

## 3. 小京审查输出合同（xiaojing_review_v1）

### 3.1 Schema（必须符合 Rubric 结构）
```json
{
  "schema_version": "xiaojing_review_v1",
  "task_id": "string",
  "review_target": "PLAN|NODE",
  "total_score": 0,
  "breakdown": [
    {
      "dimension": "string",
      "score": 0,
      "max_score": 0,
      "issues": [
        {
          "problem": "string",
          "evidence": "string",
          "impact": "string",
          "suggestion": "string",
          "acceptance_criteria": "string"
        }
      ]
    }
  ],
  "summary": "string",
  "action_required": "APPROVE|MODIFY|REQUEST_EXTERNAL_INPUT",
  "suggestions": [
    {
      "priority": "HIGH|MED|LOW",
      "change": "string",
      "steps": ["string"],
      "acceptance_criteria": "string"
    }
  ]
}
```

### 3.2 约束
- `total_score >= 90` 必须 `action_required=APPROVE`
- `total_score < 90` 必须 `action_required=MODIFY` 或 REQUEST_EXTERNAL_INPUT
- 每条 issue 必须含 evidence 与 acceptance_criteria

---

## 4. 解析失败处理（强制）
- JSON 无法解析或 schema_version 不匹配：
  - 写 task_events(type=LLM_UNPARSEABLE)
  - task.status -> FAILED
  - attempt_count + 1
