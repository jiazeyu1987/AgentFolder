# Prompt Management Specification
## 提示词体系与可控参与机制（Shared + Agent Prompt）

> 本文档定义系统中 **提示词（Prompt）** 的组织、版本、使用与审计规则，
> 目标是：
> - 让用户可控地“参与方法论”，而不是直接干预执行
> - 保证 Agent 行为稳定、可回滚、可对比
> - 防止 Prompt 漂移导致不可解释结果

---

## 1. Prompt 体系总览

系统中存在三类 Prompt：

1. **Shared Prompt（公用提示词）**
2. **Agent Prompt（Agent 独立提示词）**
3. **Runtime Context Prompt（运行时上下文，自动生成）**

每一次 LLM 调用，最终 Prompt 必须由三者拼接而成。

---

## 2. Shared Prompt（公用提示词）

### 2.1 定义
- 对 **所有 Agent 生效**
- 用于定义：
  - 系统整体哲学
  - 通用约束（如：可审计、结构化输出、避免幻想）
  - 统一风格（如：建议必须可执行）

### 2.2 修改规则
- 用户可随时修改
- 修改后生成新版本 `shared_prompt_vN`
- 默认对后续 LLM 调用生效

---

## 3. Agent Prompt（独立提示词）

### 3.1 每个 Agent 必须独立一份
- 小波（执行者）
- 小京（监督者）
- 小谢（监督者）

### 3.2 修改与生效
- 用户可单独修改任一 Agent Prompt
- 修改生成新版本 `agent_{name}_prompt_vN`
- 新版本默认只对之后的 LLM 调用生效

---

## 4. Runtime Context Prompt（系统自动生成）

- 包含当前任务、节点、依赖、已有证据、上一轮审查意见
- 不允许用户直接编辑

---

## 5. Prompt 拼接规则（强制）

```
Final Prompt =
[Shared Prompt]
+
[Agent Prompt]
+
[Runtime Context Prompt]
```

---

## 6. Prompt 版本与审计

每一次 LLM 调用必须记录：
- shared_prompt_version + hash
- agent_prompt_version + hash
- runtime_context_hash
- prompt 内容或可重建引用
- LLM 返回结果

---

## 7. 安全边界

- Prompt 不得绕过状态机
- 不得自行标记任务完成
- 不得伪造输入或确认

---

## 8. MVP 验收点
- Prompt 可编辑
- Prompt 版本可追溯
- Prompt 修改不破坏系统规则
