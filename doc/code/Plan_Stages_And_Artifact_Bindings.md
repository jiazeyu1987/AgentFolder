# Plan 多阶段评审（v1/v2/v3）与交付物依赖绑定（Artifact Bindings）

## 目标

解决当前痛点：

- `DEPENDS_ON` 只做顺序门控，不会把上游交付物自动注入下游 prompt，导致下游 ACTION “自由发挥”、接口漂移、返工。
- review 失败原因需要可追溯到每一次 review（1 次 review ↔ 1 份结果/失败说明），而不是混在一起。
- 让计划在进入 Run 前就能被“强约束校验”：每个节点知道自己要什么输入、产出什么交付物、如何验收、预计人日。

本文定义一个可重复、可评分、可落 DB 的 **Plan 多阶段门控** 方案，并补充交付物依赖绑定的机制，匹配“Claude Code 可读取本地文件”的执行方式（prompt 中写本地路径，模型自行读文件）。

---

## 核心概念

### 1) `DEPENDS_ON` 的定位

- `DEPENDS_ON` = **时序/门控**：确保下游在上游 `DONE` 前不进入可运行状态。
- `DEPENDS_ON` ≠ 参数传递：不自动把上游 artifact 内容传入下游。

因此需要独立的 **Artifact Bindings（交付物依赖绑定）** 来定义“下游需要哪些上游交付物”。

### 2) Artifact Bindings（交付物依赖绑定）

对每个 ACTION 节点，定义它需要的上游交付物集合（角色/格式/来源节点），并落到 DB 的 `input_requirements/evidences`：

- `input_requirements.kind = upstream_artifact`
- `input_requirements.name = <artifact_role>`（例如 `core_arch_spec`、`engine_api`）
- `source = <from_task_id>`（或 `source=artifact:<from_task_id>`）
- `validation_json` 中描述：允许格式、文件名约定、用途说明

Run 阶段构造 prompt 时，系统不注入内容，只注入 **本地路径列表**：

```
UPSTREAM_ARTIFACTS:
- core_arch_spec: D:\...\workspace\artifacts\<task_id>\core_arch_spec.md
- engine_api:      D:\...\workspace\artifacts\<task_id>\engine_api.md
```

Claude Code 读取这些路径的文件内容完成任务。

### 3) Plan 多阶段门控（Plan Stages）

把 create-plan 拆成 3 个阶段，每阶段都有 reviewer 打分，只有通过才进入下一阶段。每阶段可重复执行，且产物可追溯。

---

## Plan Stages 设计

### Stage 1：结构评审（STRUCTURE）

**目的**：拆分任务 + DAG 合理，节点可执行（不要求输入绑定完成）。

输出最低要求：

- nodes/edges 完整；root GOAL 存在
- 每个 ACTION 有 deliverable_spec / acceptance_criteria / estimated_person_days（v2 强约束）
- 状态机字段合法（见 `Glossary.md` / `status_rules.py`）

评分维度建议：

- Completeness / Dependency Soundness / Executability / Clarity

通过门槛：`score >= plan_review_pass_score`（可配置）

### Stage 2：依赖绑定评审（BINDINGS）

**目的**：让每个 ACTION 明确“需要哪些上游交付物”，并且这些交付物能被定位到本地路径。

输出最低要求：

- 每个 ACTION 具备 `required_upstream_artifacts[]`（或等价落在 `input_requirements`）
- 每条 binding 指明：
  - `from_task_id`
  - `artifact_role`
  - `accepted_formats`
  - `required=true/false`
  - `why`（一句话说明用途/验收）
- 绑定必须与 DAG 一致：若 B 绑定了 A 的交付物，则图上应存在 A → B 的 `DEPENDS_ON`（或等价的可证明依赖路径）

通过门槛：`score >= plan_review_pass_score`

### Stage 3：可落地执行评审（EXECUTION）

**目的**：验证“路径注入 + Claude Code 读文件”的运行策略不会卡死，失败可解释、可恢复。

输出最低要求：

- 规则清单（checklist）：
  - 上游 artifact 选择策略：优先 `approved_artifact_id`，否则 `active_artifact_id`
  - artifact 目录规范与命名
  - 缺失时如何 BLOCKED：生成 required_docs 指引
  - guardrails：prompt/response 截断、单 task LLM 调用上限等
- “一键定位最终交付物”规则（final.json/manifest.json）

通过门槛：`score >= plan_review_pass_score`

---

## 建议的优化点（在多阶段基础上）

### A) 评审结果与错误的“1:1 关联”

目标：每一次 reviewer 调用都能在 UI 上看到对应的失败原因/结果，不再混杂。

建议机制（最小但强）：

- 所有 review 相关的 `task_events(ERROR)` 必须带 `context.llm_call_id`
- 无论通过/不通过，写入 `task_events(REVIEW_RESULT)`：
  - `{llm_call_id, verdict, total_score, action_required, summary}`

### B) 防止“自由发挥”的强约束

仅靠 `DEPENDS_ON` 不够；必须通过 Stage 2 的 bindings，让下游 ACTION 在 prompt 中看到明确的上游文件路径。

### C) 内容爆炸控制

只注入路径，不注入全文；并限制：

- 每个 ACTION 不绑定 N 个上游 artifact,claude code会自动控制
- 每个 artifact 文件大小上限（由 guardrails 控制，超限提示拆分/摘要）



## 用户可见行为（期望）

1) `create-plan` 会显示阶段：STRUCTURE → BINDINGS → EXECUTION
2) 每阶段都有评分与建议；未通过时只重试该阶段（不进入下一阶段）
3) Run 时：
   - 下游 ACTION 的 prompt 会包含上游交付物本地路径列表（按 Stage 2 bindings）
   - 缺失时直接 `BLOCKED(WAITING_INPUT)`，并生成 required_docs 指引“缺哪个上游交付物/期望文件名/路径”

