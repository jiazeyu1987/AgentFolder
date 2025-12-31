# Skills Registry Specification
## skills 注册表规范（可扩展、可审计、可幂等）

> 目标：定义一个“技能注册表”（skills registry），用于：
> - 声明有哪些 skills（不做 mock，全部真实执行）
> - 声明每个 skill 的输入/输出契约
> - 为小波（执行者）提供可调用清单
> - 为审计与复现提供参数与版本

---

## 1. 文件位置与命名

推荐目录：
```text
skills/
  registry.yaml
  impl/
    file_fingerprint.py
    text_extract.py
    template_render.py
    diff_artifact.py
    validator_basic.py
```

---

## 2. Registry（YAML）结构（规范）

> 说明：
> - registry.yaml 是“声明”，impl 是“实现”
> - 每个 skill 都必须声明：inputs、outputs、params、idempotency

### 2.1 示例 registry.yaml
```yaml
version: 1
skills:
  - name: file_fingerprint
    description: Compute sha256 and basic metadata of files
    implementation: skills.impl.file_fingerprint:run
    inputs:
      - kind: FILE
        required: true
        schema:
          fields:
            - path
    outputs:
      artifacts: []
      evidences:
        - kind: FILE_HASH
          schema:
            fields: [sha256, path, size_bytes, mime_type]
    params:
      schema: {}
    idempotency:
      strategy: INPUT_HASHES
      cache: true

  - name: text_extract
    description: Extract text from pdf/docx/md/txt
    implementation: skills.impl.text_extract:run
    inputs:
      - kind: FILE
        required: true
        schema:
          fields: [path, sha256]
    outputs:
      artifacts:
        - name: extracted_text
          format: txt
          path_template: "workspace/artifacts/{task_id}/extracted_{input_sha256}.txt"
      evidences: []
    params:
      schema:
        type: object
        properties:
          max_chars:
            type: integer
            default: 200000
    idempotency:
      strategy: INPUT_HASHES_PLUS_PARAMS
      cache: true
```

---

## 3. Skill 输入/输出契约（强制）

### 3.1 Skill 输入（inputs）
每个输入项必须包含：
- kind：FILE / CONFIRMATION / ARTIFACT
- required：true/false
- schema：必须列出字段（最小：path, sha256）

### 3.2 Skill 输出（outputs）
Skill 输出必须拆成两类：
- **artifacts**：生成到文件系统的产物（必须落地 path）
- **evidences**：结构化结果（可写入 state.db 或附在 skill_runs.output_evidences_json）

### 3.3 统一返回结构（Python）
所有 skill 的 `run()` 必须返回：
```python
{
  "status": "SUCCEEDED" | "FAILED",
  "artifacts": [
    {"name": "...", "path": "...", "sha256": "...", "format": "md|txt|json|..."}
  ],
  "evidences": [
    {"kind": "...", "data": {...}}
  ],
  "error": {"code": "...", "message": "..."} | null
}
```

---

## 4. 幂等与缓存（不做 mock 也不混乱的关键）

### 4.1 幂等键策略
registry 中 `idempotency.strategy` 可选：
- INPUT_HASHES
- INPUT_HASHES_PLUS_PARAMS
- DISABLED（不推荐）

系统生成：
- idempotency_key = sha256(skill_name + sorted(input_sha256) + canonical(params_json))

若 cache=true：
- 查 skill_runs 是否存在同 idempotency_key 且 SUCCEEDED
- 若存在：直接复用其 outputs（不重复执行）

### 4.2 产物版本化
即使复用缓存，artifacts 也必须指向同一文件（不可重复生成新文件）
- 避免产生“重复版本垃圾”

---

## 5. Skill 调用流程（执行者小波）

1. 根据任务节点需要选择 skill
2. 生成 inputs_json + params_json
3. 写 skill_runs(status=RUNNING)
4. 执行 skill 实现
5. 写 skill_runs(status=SUCCEEDED/FAILED) + outputs
6. 将 outputs.artifacts 写入 artifacts 表
7. 将 outputs.evidences 绑定为 evidences（如匹配到 requirement）
8. 写 task_events(event_type=SKILL_RUN)

---

## 6. MVP 必备 skills（建议内置）

1. file_fingerprint（sha256 + metadata）
2. text_extract（从 pdf/docx 提取文本）
3. template_render（模板渲染生成 md）
4. diff_artifact（生成版本差异摘要）
5. validator_basic（最小结构校验：章节/字段）

---

## 7. MVP 验收点
- registry.yaml 能加载并列出 skills
- 能执行一个 skill 并写入 skill_runs
- 产物文件真实存在且可复用（幂等）
- skill 输出能作为 evidence 驱动 READY 解锁
