# Plan Definition Spec (plan.json)
## 任务树定义态文件规范 + 示例（Fruit 二类证）

> 本文档定义 `tasks/plan.json` 的**强约束结构**与字段语义，确保：
> - 小波生成的任务树结构稳定
> - 工程可直接导入 state.db
> - 监督者审查“有依据”
>
> 定义态 = 计划与结构，不包含运行态（状态/证据/审查结果）。
> 运行态只能进 state.db。

---

## 1. 文件结构（顶层）

`plan.json` 必须包含四个顶层字段：

- `plan`
- `nodes`
- `edges`
- `requirements`

```json
{
  "plan": {...},
  "nodes": [...],
  "edges": [...],
  "requirements": [...]
}
```

---

## 2. plan 字段（必填）

```json
{
  "plan_id": "uuid",
  "title": "Fruit 国内二类证注册",
  "owner_agent_id": "xiaobo",
  "root_task_id": "uuid",
  "created_at": "ISO8601",
  "constraints": {
    "deadline": "ISO8601|null",
    "priority": "LOW|MED|HIGH"
  }
}
```

约束：
- plan_id/root_task_id 必须是 UUID 字符串
- created_at 必须可解析 ISO8601

---

## 3. nodes 字段（必填）

每个 node 必须包含：
- task_id（uuid）
- plan_id（uuid，与 plan.plan_id 相同）
- node_type（GOAL|ACTION|CHECK）
- title（非空）
- owner_agent_id（xiaobo/xiaojing/xiaoxie 之一；MVP 建议都归 xiaobo）
- priority（整数）
- tags（数组，可空）

可选：goal_statement / rationale

```json
{
  "task_id": "uuid",
  "plan_id": "uuid",
  "node_type": "ACTION",
  "title": "收集产品技术资料",
  "owner_agent_id": "xiaobo",
  "priority": 10,
  "tags": ["input", "device"]
}
```

---

## 4. edges 字段（必填）

每条边必须包含：
- edge_id（uuid）
- plan_id（uuid）
- from_task_id（uuid）
- to_task_id（uuid）
- edge_type（DECOMPOSE|DEPENDS_ON|ALTERNATIVE）
- metadata（对象，可空但建议）

### 4.1 DECOMPOSE 元数据（父子聚合）
- `and_or`: AND|OR（默认 AND）

### 4.2 ALTERNATIVE 元数据（替代组）
- `group_id`: string（同一替代组相同）

示例：
```json
{
  "edge_id": "uuid",
  "plan_id": "uuid",
  "from_task_id": "uuid",
  "to_task_id": "uuid",
  "edge_type": "DECOMPOSE",
  "metadata": {"and_or": "AND"}
}
```

---

## 5. requirements 字段（必填）

每个 requirement 必须包含：
- requirement_id（uuid）
- task_id（uuid）
- name（string）
- kind（FILE|CONFIRMATION|SKILL_OUTPUT）
- required（0/1）
- min_count（>=1）
- allowed_types（数组，如 ["pdf","docx"]）
- source（USER|AGENT|ANY）
- validation（对象，可空）

示例：
```json
{
  "requirement_id": "uuid",
  "task_id": "uuid",
  "name": "product_spec",
  "kind": "FILE",
  "required": 1,
  "min_count": 1,
  "allowed_types": ["pdf","docx"],
  "source": "USER",
  "validation": {"filename_keywords": ["spec", "规格"]}
}
```

---

## 6. Fruit 国内二类证（MVP 示例任务树）

> 说明：以下是**最小可运行**的示例（节点数控制在 14 个左右）。
> 真实落地可继续细分，但先跑通闭环。

### 6.1 示例 JSON（可直接保存为 tasks/plan.json）

```json
{
  "plan": {
    "plan_id": "PLAN_FRUIT_CLASS2_V1",
    "title": "Fruit 国内二类证注册",
    "owner_agent_id": "xiaobo",
    "root_task_id": "T0_ROOT",
    "created_at": "2025-12-30T00:00:00Z",
    "constraints": {"deadline": null, "priority": "HIGH"}
  },
  "nodes": [
    {"task_id":"T0_ROOT","plan_id":"PLAN_FRUIT_CLASS2_V1","node_type":"GOAL","title":"完成 Fruit 国内二类证注册","owner_agent_id":"xiaobo","priority":100,"tags":["top"]},

    {"task_id":"T1_SCOPE","plan_id":"PLAN_FRUIT_CLASS2_V1","node_type":"ACTION","title":"明确产品分类与注册路径（II类判定）","owner_agent_id":"xiaobo","priority":90,"tags":["reg"]},
    {"task_id":"T2_MATERIALS","plan_id":"PLAN_FRUIT_CLASS2_V1","node_type":"ACTION","title":"收集产品技术资料（说明书/规格/图纸）","owner_agent_id":"xiaobo","priority":80,"tags":["input"]},
    {"task_id":"T3_QMS","plan_id":"PLAN_FRUIT_CLASS2_V1","node_type":"ACTION","title":"准备质量管理体系与生产相关材料","owner_agent_id":"xiaobo","priority":75,"tags":["qms"]},
    {"task_id":"T4_RISK","plan_id":"PLAN_FRUIT_CLASS2_V1","node_type":"ACTION","title":"编制风险管理文件（初稿）","owner_agent_id":"xiaobo","priority":70,"tags":["risk","doc"]},
    {"task_id":"T5_TEST_PLAN","plan_id":"PLAN_FRUIT_CLASS2_V1","node_type":"ACTION","title":"制定检验/测试计划与差距分析","owner_agent_id":"xiaobo","priority":68,"tags":["test","plan"]},
    {"task_id":"T6_TEST_REPORT","plan_id":"PLAN_FRUIT_CLASS2_V1","node_type":"ACTION","title":"汇总/生成测试报告包（或列出缺口）","owner_agent_id":"xiaobo","priority":66,"tags":["test","report"]},
    {"task_id":"T7_CLINICAL","plan_id":"PLAN_FRUIT_CLASS2_V1","node_type":"ACTION","title":"临床评价资料准备（或临床豁免论证）","owner_agent_id":"xiaobo","priority":64,"tags":["clinical"]},
    {"task_id":"T8_PRD","plan_id":"PLAN_FRUIT_CLASS2_V1","node_type":"ACTION","title":"生成注册资料清单与任务分解（可提交版）","owner_agent_id":"xiaobo","priority":62,"tags":["doc","bundle"]},
    {"task_id":"T9_APPLICATION","plan_id":"PLAN_FRUIT_CLASS2_V1","node_type":"ACTION","title":"生成申报表单草稿与提交检查清单","owner_agent_id":"xiaobo","priority":60,"tags":["forms"]},

    {"task_id":"T10_REVIEW_PLAN","plan_id":"PLAN_FRUIT_CLASS2_V1","node_type":"CHECK","title":"任务树拆解审查（小京）","owner_agent_id":"xiaojing","priority":95,"tags":["review","plan"]},

    {"task_id":"T11_REVIEW_NODE","plan_id":"PLAN_FRUIT_CLASS2_V1","node_type":"CHECK","title":"节点产出质量审查（小京）","owner_agent_id":"xiaojing","priority":50,"tags":["review","node"]},

    {"task_id":"T12_PACKAGE","plan_id":"PLAN_FRUIT_CLASS2_V1","node_type":"ACTION","title":"汇总最终提交包（目录+版本）","owner_agent_id":"xiaobo","priority":55,"tags":["final"]},
    {"task_id":"T13_FINAL_CHECK","plan_id":"PLAN_FRUIT_CLASS2_V1","node_type":"CHECK","title":"最终提交包完整性检查","owner_agent_id":"xiaojing","priority":54,"tags":["final","check"]}
  ],
  "edges": [
    {"edge_id":"E0","plan_id":"PLAN_FRUIT_CLASS2_V1","from_task_id":"T0_ROOT","to_task_id":"T1_SCOPE","edge_type":"DECOMPOSE","metadata":{"and_or":"AND"}},
    {"edge_id":"E1","plan_id":"PLAN_FRUIT_CLASS2_V1","from_task_id":"T0_ROOT","to_task_id":"T2_MATERIALS","edge_type":"DECOMPOSE","metadata":{"and_or":"AND"}},
    {"edge_id":"E2","plan_id":"PLAN_FRUIT_CLASS2_V1","from_task_id":"T0_ROOT","to_task_id":"T3_QMS","edge_type":"DECOMPOSE","metadata":{"and_or":"AND"}},
    {"edge_id":"E3","plan_id":"PLAN_FRUIT_CLASS2_V1","from_task_id":"T0_ROOT","to_task_id":"T4_RISK","edge_type":"DECOMPOSE","metadata":{"and_or":"AND"}},
    {"edge_id":"E4","plan_id":"PLAN_FRUIT_CLASS2_V1","from_task_id":"T0_ROOT","to_task_id":"T5_TEST_PLAN","edge_type":"DECOMPOSE","metadata":{"and_or":"AND"}},
    {"edge_id":"E5","plan_id":"PLAN_FRUIT_CLASS2_V1","from_task_id":"T0_ROOT","to_task_id":"T6_TEST_REPORT","edge_type":"DECOMPOSE","metadata":{"and_or":"AND"}},
    {"edge_id":"E6","plan_id":"PLAN_FRUIT_CLASS2_V1","from_task_id":"T0_ROOT","to_task_id":"T7_CLINICAL","edge_type":"DECOMPOSE","metadata":{"and_or":"AND"}},
    {"edge_id":"E7","plan_id":"PLAN_FRUIT_CLASS2_V1","from_task_id":"T0_ROOT","to_task_id":"T8_PRD","edge_type":"DECOMPOSE","metadata":{"and_or":"AND"}},
    {"edge_id":"E8","plan_id":"PLAN_FRUIT_CLASS2_V1","from_task_id":"T0_ROOT","to_task_id":"T9_APPLICATION","edge_type":"DECOMPOSE","metadata":{"and_or":"AND"}},
    {"edge_id":"E9","plan_id":"PLAN_FRUIT_CLASS2_V1","from_task_id":"T0_ROOT","to_task_id":"T12_PACKAGE","edge_type":"DECOMPOSE","metadata":{"and_or":"AND"}},
    {"edge_id":"E10","plan_id":"PLAN_FRUIT_CLASS2_V1","from_task_id":"T0_ROOT","to_task_id":"T13_FINAL_CHECK","edge_type":"DECOMPOSE","metadata":{"and_or":"AND"}},

    {"edge_id":"D0","plan_id":"PLAN_FRUIT_CLASS2_V1","from_task_id":"T2_MATERIALS","to_task_id":"T4_RISK","edge_type":"DEPENDS_ON","metadata":{}},
    {"edge_id":"D1","plan_id":"PLAN_FRUIT_CLASS2_V1","from_task_id":"T2_MATERIALS","to_task_id":"T5_TEST_PLAN","edge_type":"DEPENDS_ON","metadata":{}},
    {"edge_id":"D2","plan_id":"PLAN_FRUIT_CLASS2_V1","from_task_id":"T5_TEST_PLAN","to_task_id":"T6_TEST_REPORT","edge_type":"DEPENDS_ON","metadata":{}},
    {"edge_id":"D3","plan_id":"PLAN_FRUIT_CLASS2_V1","from_task_id":"T6_TEST_REPORT","to_task_id":"T12_PACKAGE","edge_type":"DEPENDS_ON","metadata":{}},
    {"edge_id":"D4","plan_id":"PLAN_FRUIT_CLASS2_V1","from_task_id":"T4_RISK","to_task_id":"T12_PACKAGE","edge_type":"DEPENDS_ON","metadata":{}},
    {"edge_id":"D5","plan_id":"PLAN_FRUIT_CLASS2_V1","from_task_id":"T7_CLINICAL","to_task_id":"T12_PACKAGE","edge_type":"DEPENDS_ON","metadata":{}},
    {"edge_id":"D6","plan_id":"PLAN_FRUIT_CLASS2_V1","from_task_id":"T9_APPLICATION","to_task_id":"T12_PACKAGE","edge_type":"DEPENDS_ON","metadata":{}},
    {"edge_id":"D7","plan_id":"PLAN_FRUIT_CLASS2_V1","from_task_id":"T12_PACKAGE","to_task_id":"T13_FINAL_CHECK","edge_type":"DEPENDS_ON","metadata":{}}
  ],
  "requirements": [
    {"requirement_id":"R1","task_id":"T2_MATERIALS","name":"product_spec","kind":"FILE","required":1,"min_count":1,"allowed_types":["pdf","docx"],"source":"USER","validation":{"filename_keywords":["规格","spec"]}},
    {"requirement_id":"R2","task_id":"T2_MATERIALS","name":"ifu_manual","kind":"FILE","required":1,"min_count":1,"allowed_types":["pdf","docx"],"source":"USER","validation":{"filename_keywords":["说明书","IFU","manual"]}},
    {"requirement_id":"R3","task_id":"T3_QMS","name":"qms_docs","kind":"FILE","required":0,"min_count":1,"allowed_types":["pdf","docx","xlsx"],"source":"USER","validation":{"filename_keywords":["QMS","体系","程序文件"]}},
    {"requirement_id":"R4","task_id":"T6_TEST_REPORT","name":"test_reports","kind":"FILE","required":0,"min_count":1,"allowed_types":["pdf"],"source":"USER","validation":{"filename_keywords":["测试","检验","report"]}}
  ]
}
```

---

## 7. MVP 导入规则（plan.json -> state.db）
- plan: upsert plans
- nodes: upsert task_nodes（status 初始化为 PENDING，active_branch=1）
- edges: upsert task_edges
- requirements: upsert input_requirements
