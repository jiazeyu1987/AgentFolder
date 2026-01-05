# Unified Deliverables Folder Spec (Per-Plan, Manifest-Driven)

## 目标

把一个 TopTask/Plan 的所有交付物集中到**同一个根目录**下管理，避免按 task 分散到多个目录造成“找交付物困难、路径不一致、依赖难追溯”的问题。

同时，后续节点对上游节点的依赖应当通过 **manifest 中的文件路径引用**自动注入到 prompt（Claude Code 可读本地文件），而不是把上游交付物当成需要人手工提供的 `inputs/upstream:*` 输入项。

## 核心结论（对齐点）

1) **初始节点（入度=0 的 ACTION）不需要任何上游交付物输入**：它负责产生交付物。

2) **后续节点只依赖上游节点的交付物**：依赖关系来自 `DEPENDS_ON`（或等价规则），由系统在 Run 阶段把上游交付物的本地路径注入到下游 prompt。

3) **交付物统一目录**：每个 plan 有且仅有一个交付物根目录；所有节点输出都落在该目录树下；通过 `manifest.json` 做区分与索引。

## 目录约定

### Deliverables 根目录

`workspace/deliverables/<plan_id>/`

该目录必须存在（由系统创建），并包含：

- `manifest.json`：交付物清单（唯一事实源）
- `final.json`：最终交付入口（用户 1 秒定位）
- `tasks/`：（旧方案）按 task 分子目录。**已废弃**：新规则是所有节点交付物文件**全部平铺在 deliverables 根目录**，用 `manifest.json` 做索引区分。

### 节点输出路径（建议）

所有节点输出文件落在：

`workspace/deliverables/<plan_id>/<task_slug>__<artifact_name>_<id8>.<ext>`

说明：
- `task_slug` 由 `task_title` 规范化得到（去特殊字符、空格转下划线、截断长度），用于避免重名与跨平台路径问题。
- 仍然属于同一 plan 根目录，不再出现 `workspace/artifacts/<task_id>/...` 这种分散目录。

## manifest.json（单一入口）

### 作用

- 统一定位每个节点的交付物文件
- 区分 approved/candidate 版本
- 支持下游依赖自动注入（上游文件路径来自 manifest）
- 支持 export/final.json 的最终入口选择

### 最小结构（建议）

```json
{
  "schema_version": "deliverables_manifest_v1",
  "plan": { "plan_id": "...", "title": "...", "created_at": "..." },
  "tasks": [
    {
      "task_title": "Core Game Loop & Structure",
      "node_type": "ACTION",
      "status": "DONE",
      "owner_agent_id": "xiaobo",
      "approved": true,
      "files": [
        { "path": "core_game_loop___structure__game_logic_core_abc12345.js", "format": "js", "sha256": "..." }
      ],
      "acceptance_criteria": [ "..." ]
    }
  ]
}
```

关键点：
- `path` 为相对 `workspace/deliverables/<plan_id>/` 的相对路径
- `approved` 由 CHECK 评审门控决定（v2 workflow）
- `acceptance_criteria` 用于回看“为何通过/如何验收”

## final.json（最终交付定位）

### 作用

让用户不需要翻目录，只看 `final.json` 就知道“最终交付物是哪个文件、如何运行、如何验收”。

### 最小结构（建议）

```json
{
  "schema_version": "final_deliverable_v1",
  "plan_id": "...",
  "final_task_title": "...",
  "final_entrypoint": "<task_slug>__index_<id8>.html",
  "how_to_run": ["..."],
  "acceptance_criteria": ["..."],
  "source_artifacts": [
    { "task_title": "...", "paths": ["..."] }
  ]
}
```

## 上下游依赖注入（禁止 upstream:* 手工输入）

### 需求

- 下游节点不再生成类似 `workspace/inputs/upstream:*` 的缺输入提示。
- Run 阶段根据 `DEPENDS_ON` 找到上游节点的 approved 交付物（优先）或 candidate（按策略），得到本地路径：
  - `workspace/deliverables/<plan_id>/...`
- 把这些路径写入下游 prompt 的 “UPSTREAM_ARTIFACTS (local paths)” 区块，Claude Code 直接读取文件即可。

### 初始节点规则

- 入度=0 的 ACTION：不注入上游交付物，也不要求上游输入文件。

## 兼容与迁移策略（概念层）

为避免破坏现有逻辑，可按阶段迁移：

1) **写入双轨**（过渡）：仍保留旧的 artifacts 路径，但同时把文件复制/落盘到统一 deliverables 目录，并在 manifest 记录新路径。
2) **读优先统一目录**：prompt 注入与 export/final.json 优先使用 manifest 中的统一路径。
3) **最终收敛**：停止写旧 artifacts 分散目录（仅保留兼容读取/清理逻辑）。

## 验收标准

1) 一个 plan 只需要打开 `workspace/deliverables/<plan_id>/final.json` 就能定位最终交付物。
2) `workspace/deliverables/<plan_id>/manifest.json` 能列出所有节点的交付物，并标注 approved/candidate。
3) 任一非初始节点的 prompt 都能看到上游交付物路径（来自 manifest），不再要求手工准备 `inputs/upstream:*`。
4) 初始节点（入度=0）的 prompt 不会出现任何 upstream 输入要求。
