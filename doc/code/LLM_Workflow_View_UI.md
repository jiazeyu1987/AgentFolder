# LLM Workflow View UI（Timeline → 可点击工作流）

## 1. 背景与目标

当前 UI 的 LLM Timeline 只能线性列表查看，难以快速定位：
- “卡在 PLAN_GEN 还是 PLAN_REVIEW？”
- “第几次 attempt / reviewer 重试？”
- “每一步是谁在做（agent）？”
- “该步 prompt（公共/私有）是什么，返回值是什么，为何失败/通过？”

本需求把 LLM Timeline 升级为“可点击工作流视图（节点+连线）”，并提供与任务图（Task Graph）一键切换。

## 2. 范围（MVP）

### 必做（MVP）
- 中间区域支持视图切换：`Task Graph` ↔ `LLM Workflow`
- LLM Workflow 中每一次 LLM 调用（llm_calls 一行）显示为节点
- 节点可点击：右侧显示该节点的 agent、prompt（公共+私有+合成）、输出（raw/parsed/normalized）、错误/校验信息
- 重点支持 create-plan 阶段：`PLAN_GEN` 与 `PLAN_REVIEW`
- attempt / review_attempt 必须可见
- 支持过滤：plan、scope、agent、仅错误、时间范围（最小：limit + scopes + agent）

### 暂不做（后续）
- 自动修复/自动改写（3B）
- WebSocket/SSE 推送（先轮询 DB）
- 多进程并发展示与冲突合并（单机串行 MVP）

## 3. 页面布局与交互

### 3.1 三栏布局（沿用现有）
- 左：控制面板（Plan 选择、TopTask、Create Plan、Run、Status、Reset DB、Export 等）
- 中：主视图（可切换）
  - `Task Graph`：现有任务图（节点=task_nodes）
  - `LLM Workflow`：新增工作流图（节点=llm_calls）
- 右：详情面板
  - 当选中 Task Graph 的 task node → 显示 task 详情（现有）
  - 当选中 LLM Workflow 的 llm_call node → 显示 LLM 调用详情（新增）

### 3.2 视图切换
- 中间顶部提供 toggle：
  - `Task Graph`
  - `LLM Workflow`
- 默认：沿用当前（Task Graph）
- 切换后：
  - 选中态独立保存（task_id 与 llm_call_id 各自独立）

### 3.3 轮询策略（必须）
- 前端禁止频繁 subprocess 轮询
- 轮询只走后端轻查询（SQLite）
- 刷新频率建议：
  - job/progress：0.8–1.0s
  - workflow graph：1.0–2.0s（可配置）

## 4. LLM Workflow 视图（中间）

### 4.1 节点（Node = 1 条 llm_calls）
每个节点至少展示：
- created_at（简写）
- scope（PLAN_GEN / PLAN_REVIEW / TASK_ACTION / TASK_CHECK …）
- agent（xiaobo / xiaojing …）
- attempt / review_attempt（从 meta_json 推断；缺省为 1）
- 状态徽标：
  - OK：error_code/validator_error 为空
  - ERROR：error_code 或 validator_error 存在

### 4.2 连线（Edge）规则（MVP）
为避免前端自行猜测，连线规则在后端实现，返回 edges：
- 同一 plan_id 内按 created_at 串联（线性）
- PLAN_GEN → PLAN_REVIEW 成对连线（同 attempt 内最近配对）
- 当 plan_id 未知（PLAN_GEN 可能 plan_id=null）：
  - 仅展示线性串联，不强制配对（或按 top_task_hash/时间窗口尝试配对）

### 4.3 分组（Group）规则（MVP）
后端返回 groups，用于 UI 折叠：
- group = attempt（meta_json.attempt）
  - attempt 内可包含多个 scope 节点
- PLAN_REVIEW 可额外显示 review_attempt（meta_json.review_attempt）

### 4.4 过滤与搜索（MVP）
最小过滤器：
- plan_id（必选/默认当前选中 plan）
- scopes（逗号分隔）
- agent（可选）
- only_errors（bool）
- limit（默认 200）

## 5. 右侧详情面板（LLM Call Details）

点击 workflow 节点后，右侧必须显示：

### 5.1 基本信息
- created_at
- scope
- agent
- plan_title（若可推断/关联）
- plan_id、task_title（若有；不强制 task_id 展示）
- error_code、error_message
- validator_error（必要时截断+可展开）

### 5.2 Prompt（重点）
必须显示三段（每段支持 Copy）：
- **Shared Prompt（公共）**
  - 内容（文本）
  - source_path（文件路径）
- **Agent Prompt（私有）**
  - 内容（文本）
  - source_path（文件路径）
- **Final Prompt（合成后）**
  - 本次最终发送给模型的 prompt_text（来自 llm_calls.prompt_text）

> 注：shared/agent prompt 的路径解析由后端负责；前端只展示。

### 5.3 Output（重点）
四段（可折叠+Copy）：
- Raw response（llm_calls.response_text）
- Parsed JSON（llm_calls.parsed_json）
- Normalized JSON（llm_calls.normalized_json）
- Validator Error（llm_calls.validator_error）

### 5.4 Review 专区（当 scope 属于 *REVIEW 或 normalized_json 含 review 结构）
展示：
- schema_version
- total_score
- dimension_scores（维度分）
- action_required（APPROVE/MODIFY/REQUEST_EXTERNAL_INPUT）
- summary
- suggestions（含 acceptance_criteria）

## 6. 后端接口（SSOT）

### 6.1 GET /api/workflow
用途：返回 LLM Workflow 图数据（nodes/edges/groups），前端不拼 SQL。

Query：
- plan_id?（可空：用于 create-plan 早期 plan_id 未知场景）
- scopes?（逗号分隔）
- agent?（可选）
- only_errors?（可选）
- limit（默认 200）
- plan_id_missing=true（可选：plan_id IS NULL）

返回（schema_version=workflow_v1）：
```json
{
  "schema_version": "workflow_v1",
  "plan": { "plan_id": "…", "title": "…", "workflow_mode": "v1|v2" },
  "nodes": [
    {
      "llm_call_id": "…",
      "created_at": "…",
      "plan_id": "…",
      "task_title": "…",
      "agent": "…",
      "scope": "PLAN_GEN|PLAN_REVIEW|TASK_ACTION|TASK_CHECK|…",
      "attempt": 1,
      "review_attempt": 1,
      "error_code": null,
      "validator_error": null
    }
  ],
  "edges": [
    { "from": "llm_call_id", "to": "llm_call_id", "edge_type": "NEXT|PAIR" }
  ],
  "groups": [
    { "group_type": "ATTEMPT", "id": "attempt_1", "attempt": 1, "node_ids": ["..."] }
  ],
  "ts": "…"
}
```

### 6.2 GET /api/llm_calls（已存在，增强可复用）
用于详情展开：
- 需要支持返回 prompt_text/response_text/parsed_json/normalized_json/validator_error/meta_json

### 6.3 Prompt Source Path 解析（后端）
后端需要能返回：
- shared_prompt_path（例如 `shared_prompt.md` 或 shared_prompt 的来源）
- agent_prompt_path（例如 `agents/xiaobo/...` 或 `agents/xiaojing/...`）
以及对应内容（可选：由前端通过另一个接口拉取；MVP 可直接返回内容片段/或复用 llm_calls.prompt_text）

> 约束：不用环境变量；路径来源必须来自 repo 内固定约定或 runtime_config.json。

## 7. 数据来源与推断规则（MVP）

### 7.1 attempt / review_attempt
- 来自 llm_calls.meta_json：
  - attempt = meta_json.attempt（缺省 1）
  - review_attempt = meta_json.review_attempt（缺省 1）

### 7.2 plan_title / task_title
- plan_title：由 plans 表 plan_id 关联；plan_id 为空时可留空
- task_title：若 llm_calls.task_id 非空，关联 task_nodes.title；否则为空

## 8. 验收标准

- UI 中间可切换到 `LLM Workflow`
- 能看到节点+连线，节点至少展示 created_at/scope/agent/attempt/review_attempt/错误标记
- 点击节点：右侧可看到 prompt（公共/私有/合成）与 output（raw/parsed/normalized/validator_error）
- PLAN_REVIEW 节点可看到 score/action_required/summary/suggestions
- UI 关闭再打开：仍可通过 DB 恢复历史 workflow（不依赖内存）

## 9. 风险与注意事项

- create-plan 早期 PLAN_GEN 可能 plan_id 为空：需要 plan_id_missing 支持或时间窗口关联
- prompt 文件路径解析需要统一约定；若无法定位，必须给出可读提示（unknown + why）
- 大文本可能超长：后端应按 guardrails 截断并标记 TRUNCATED，前端可展开查看尾部

