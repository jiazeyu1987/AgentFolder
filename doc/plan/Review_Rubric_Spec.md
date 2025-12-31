# Review Rubric Specification
## 审查评分 Rubric 规范（Plan Review & Node Review）

> 本文档定义 **监督者 Agent（小京 / 小谢）** 的评分标准，
> 用于将“90 分门槛”从主观判断转化为**稳定、可执行、可复现的质量门控机制**。
>
> 本 Rubric 是系统的 **硬约束规范**，即使用户修改 Prompt，也不得违反本规则。

---

## 1. Rubric 总体原则

1. **评分必须可解释**
   - 每一项扣分都必须指向具体问题与证据
2. **评分必须可执行**
   - 每一条修改建议都必须能直接指导小波修改
3. **评分必须可复现**
   - 同一输入在相同 Rubric 下，评分应高度接近
4. **90 分是“可进入下一阶段”的门槛**
   - 不是完美，而是“当前阶段已足够可靠”

---

## 2. 评分通用结构（强制）

所有审查输出必须采用以下结构：

```json
{
  "total_score": 85,
  "breakdown": [
    {
      "dimension": "Completeness",
      "score": 30,
      "max_score": 40,
      "issues": [
        {
          "problem": "Missing post-market surveillance tasks",
          "evidence": "No node covers adverse event reporting",
          "impact": "Regulatory incompleteness",
          "suggestion": "Add a task node for PMS reporting and dependency"
        }
      ]
    }
  ],
  "summary": "Task tree lacks mandatory regulatory coverage",
  "action_required": "MODIFY|APPROVE|REQUEST_EXTERNAL_INPUT"
}
```

禁止：
- 只给总分不给理由
- 只给情绪化评价（“不太好”“感觉不全”）

---

## 3. 任务树拆解审查 Rubric（Plan Review）

### 3.1 评分维度与权重（总分 100）

| 维度 | 权重 | 说明 |
|----|----|----|
| Completeness（完整性） | 40 | 是否覆盖所有必要子目标 |
| Dependency Soundness（依赖合理性） | 25 | 依赖关系是否正确 |
| Executability（可执行性） | 20 | 节点是否能被执行 |
| Clarity（清晰度） | 15 | 命名与目标是否清晰 |

### 3.2 各维度达标标准（≥90 分）

#### Completeness（40）
- 覆盖法规/业务必须步骤
- 无明显“缺失模块”

❌ 常见扣分：
- 缺少关键法规节点
- 忽略后市场/维护类任务

#### Dependency Soundness（25）
- 无循环依赖
- 依赖不冗余、不缺失

❌ 常见扣分：
- 把“审查”放在“生成”之前
- 依赖链断裂

#### Executability（20）
- 每个 ACTION 节点都能说明“谁来做、怎么做”
- 输入需求可被满足

❌ 常见扣分：
- 节点描述抽象
- 需要“未知资料”

#### Clarity（15）
- 节点命名可读
- 目标描述无歧义

---

## 4. 节点完成质量审查 Rubric（Node Review）

### 4.1 评分维度与权重（总分 100）

| 维度 | 权重 | 说明 |
|----|----|----|
| Correctness（正确性） | 40 | 是否符合标准/事实 |
| Coverage（覆盖性） | 30 | 是否覆盖该节点目标 |
| Traceability（可追溯性） | 20 | 是否引用证据 |
| Actionability（可用性） | 10 | 是否可直接使用 |

### 4.2 各维度达标标准（≥90 分）

#### Correctness（40）
- 无明显错误
- 引用标准/资料正确

❌ 常见扣分：
- 错误法规版本
- 逻辑矛盾

#### Coverage（30）
- 节点目标全部回应
- 无重要遗漏

❌ 常见扣分：
- 只回答部分问题

#### Traceability（20）
- 明确引用文件/数据来源
- 结论可回溯

❌ 常见扣分：
- 结论无依据
- 未引用输入文件

#### Actionability（10）
- 文档可直接提交或使用
- 不需要二次“翻译”

---

## 5. 修改建议规范（强制）

每条建议必须包含：
1. **问题点**
2. **违反的 Rubric 维度**
3. **修改动作**
4. **验收标准（如何达到 ≥90）**

示例：
> 问题：未覆盖不良事件上报流程  
> 维度：Completeness  
> 修改：新增一个 ACTION 节点描述 PMS 流程  
> 验收：任务树中存在 PMS 节点并被依赖

---

## 6. 评分与状态机映射

| total_score | action |
|----|----|
| ≥ 90 | APPROVE → Done |
| 70–89 | MODIFY → ToBeModify |
| < 70 | REQUEST_EXTERNAL_INPUT 或 REPLAN |

---

## 7. 最大迭代与升级规则

- 同一对象（任务树 / 节点）最多允许 **3 次 MODIFY**
- 第 4 次仍 <90，必须：
  - 请求外部资料，或
  - 调整任务目标/路径（触发 REWRITE）

---

## 8. MVP 验收点

- 监督者评分稳定
- 修改建议可执行
- 不出现无限 90 分以下循环
- 用户能理解“为什么不过 90”
