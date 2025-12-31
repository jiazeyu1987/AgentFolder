# Requirement Matcher Specification
## 输入文件 → requirement 匹配规则（evidence 绑定）

> 目标：定义一个确定性规则：当用户把文件放进 `workspace/inputs/` 时，系统如何把它绑定到某些 requirement。
> 该规则必须：
> - 可解释（为什么匹配）
> - 可复现（同样文件总是同样匹配）
> - 尽量避免错绑（宁可少绑也不乱绑）

---

## 1. 匹配优先级（从强到弱）

1) **显式目录映射（最强）**
- 用户按约定目录放：
  - workspace/inputs/<requirement_name>/xxx.pdf
- 则直接绑定到 name=<requirement_name> 的 requirement（同 plan 内）

2) **文件名关键字匹配（强）**
- requirement.validation.filename_keywords 列表
- 若文件名包含任一关键词（大小写不敏感），进入候选

3) **文件类型匹配（基础）**
- 扩展名必须在 allowed_types 中（例如 pdf/docx/xlsx/md/txt）

4) **弱匹配（MVP 禁用）**
- 内容语义匹配（需要 NLP/embedding，后续再做）

---

## 2. 决策规则（避免错绑）

对每个文件，计算每个 requirement 的 match_score：

- 目录命中：+100
- 文件名命中一个关键词：+40（多个可累加但上限 80）
- 类型命中：+10
- source=USER 且文件在 inputs：+10

绑定阈值（MVP）：
- match_score >= 60 才绑定
- 若同一文件匹配多个 requirement：只绑定最高分的前 K 个（MVP K=2）
- 若分数相同且冲突：不绑定，写一个事件提示用户手动放到对应目录

---

## 3. 多版本处理（同 requirement 多个文件）

- 同一 requirement 可绑定多份 evidence（允许）
- 但执行/上下文选择时应选：
  - 最新 modified_time 的文件，或
  - 文件名含 FINAL 的文件优先
- 版本冲突写 task_events(type=INPUT_CONFLICT)，并建议用户确认

---

## 4. 幂等与删除处理

- evidence 绑定用 (requirement_id, ref_id) 唯一约束（已在 SQL 中定义）
- 文件删除：MVP 不自动移除 evidence（避免历史丢失）
  - 只记录 FILE_REMOVED 事件
  - 后续可实现“失效”标记

---

## 5. MVP 验收点
- 用户把文件放进 inputs/<requirement_name>/ 会稳定绑定
- 文件名包含关键词也能绑定
- 不会出现大量错误绑定
