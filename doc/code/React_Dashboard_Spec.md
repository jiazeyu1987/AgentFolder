# React Dashboard UI（Windows）设计文档

## 1. 背景与目标
你希望新增一个增强 UI（React）作为“看板”，但系统仍以 CLI 为主驱动：
- 前端只是看板：UI 关了，CLI 继续跑，不影响任务执行。
- 不卡电脑：避免频繁启动 subprocess；看板刷新要轻量。
- 只在 Windows 跑。
- 先不做 LLM Explorer（右侧调试面板暂缓），但整体布局预留。

## 2. 关键约束与结论
### 2.1 React 不能直接读 SQLite / 跑本机命令
浏览器环境无法直接访问本机文件系统/SQLite，也无法安全稳定地执行 `subprocess`。
因此必须引入一个**本地轻量后端**（Local Daemon），负责：
- 读 `state/state.db`（只读查询为主）
- 读 `runtime_config.json`（显示/复制路径）
- 启动/停止/单步运行 CLI（可选）
- 提供任务图数据（Graph JSON）

> 结论：架构为 **React 前端 + Python 本地后端 + CLI 独立进程**。  
> CLI 的 `run` 仍是主驱动，后端只是“控制/查询”，不参与调度逻辑。

## 3. UI 布局与交互
整体布局：三栏
- 左：控制面板（TopTask 输入 + 6 按钮 + 路径显示/复制 + Plan 选择）
- 中：任务图（DAG）
- 右：调试面板（预留区域；LLM Explorer 后续实现）

### 3.1 左侧控制面板（必须）
控件：
1) `TopTask` 文本输入框
2) 按钮（6 个）：
   - `Create Plan`
   - `Run`（启动后台 run 进程）
   - `Status`（刷新数据）
   - `Reset DB`（危险操作：二次确认；行为与现有 Tk UI 一致）
   - `Export`（导出 deliverables）
   - `TopTask`（可选：等价于“写入 TopTask 输入并复制到剪贴板/持久化”，也可仅作为输入框不单独按钮）
3) 目录路径显示（只读 + 一键复制）：
   - 输入文件夹：`workspace/inputs/`
   - 输出文件夹：`workspace/deliverables/`（以及 artifacts 可作为辅助显示）
   - DB 文件：`state/state.db`
4) Plan 选择：
   - 下拉列表（按 created_at 倒序）
   - 展示：`plan_title` + `plan_id`（截断显示）
   - 选中后：中间任务图/右侧详情都切换到该 plan

### 3.2 中间任务图（必须）
图形内容：
- 展示某个 plan 的所有节点（task_nodes）与边（task_edges）。
- 节点颜色/形态表示状态：
  - `READY`：绿色
  - `PENDING`：灰色
  - `BLOCKED`：橙色（可在节点右上角标小图标）
  - `READY_TO_CHECK`：蓝色
  - `TO_BE_MODIFY`：紫色
  - `DONE`：深灰或带勾
  - `FAILED`：红色
  - `IN_PROGRESS`（若存在）：高亮/闪烁边框
- 边类型展示：
  - `DEPENDS_ON`：实线箭头
  - `DECOMPOSE`：虚线或不同颜色
  - `ALTERNATIVE`：点划线（可选）

交互：
- 点击节点：打开“节点详情抽屉/弹窗”
  - 基本信息：title、node_type、status、owner_agent_id、attempt_count、blocked_reason
  - 若 `BLOCKED(WAITING_INPUT)`：显示缺少哪些输入、需要去哪个 required_docs 文件补、每项建议放到哪个 suggested_path
  - 显示各 Agent 输入/输出（第一期可只展示 DB 里能查到的 artifacts/reviews/events；LLM 原始输入输出由右侧调试面板后续补）
- 图上标识“当前正在执行”：
  - 优先：DB 中存在 `status=IN_PROGRESS` 的节点
  - 兜底：最近的 `task_events`（如 `STATUS_CHANGED` 到 RUNNING/IN_PROGRESS）或最近一条 `llm_calls` 的 task_id（需要后端做轻推断）

### 3.3 右侧调试面板（预留）
第一期先放占位（“Coming soon: LLM Explorer”）。
后续对齐现有 Tk 版 LLM Explorer：
- 筛选：plan-title contains、plan-id、task-id、scope、agent、errors only
- 列表 + 选中显示 prompt/raw/parsed/normalized/meta

## 4. 后端（Local Daemon）职责与接口（推荐 FastAPI）
目标：只做轻查询与进程控制，不做调度逻辑。

### 4.1 核心 API（MVP）
- `GET /api/config`
  - 返回 `runtime_config.json` + 派生路径（inputs/artifacts/deliverables/db）
  - UI 仅展示与复制

- `GET /api/plans`
  - 返回 plans 列表：`plan_id/title/created_at/root_task_id`

- `GET /api/plan/{plan_id}/graph`
  - 返回任务图 JSON（见第 5 节契约）

- `POST /api/plan/create`
  - body：`top_task` + 其他可选参数（max_attempts/keep_trying/...）
  - 内部调用：`agent_cli.py create-plan ...`
  - 返回：stdout/stderr + exit_code + plan_id（若可解析）

- `POST /api/run/start`
  - body：`plan_id?`、`max_iterations?`
  - 行为：用 Windows `Start-Process` 启动 `agent_cli.py run ...`，并记录 PID 到 `state/run_process.json`

- `POST /api/run/stop`
  - 行为：根据 PID `Stop-Process`（或 python terminate），并清理 `run_process.json`

- `POST /api/run/once`
  - 行为：执行一次“单步”循环（建议：`agent_cli.py run --max-iterations 1`）

- `POST /api/reset-db`
  - body：`purge_workspace/purge_tasks/purge_logs`（需要二次确认由 UI 实现）
  - 行为：调用 `agent_cli.py reset-db ...`

- `POST /api/export`
  - body：`plan_id`、`include_reviews?`
  - 行为：调用 `agent_cli.py export --plan-id ...`

### 4.2 轮询策略（不卡）
- 前端以 1–2s 轮询 `GET /api/plan/{plan_id}/graph`（或更慢，如 3–5s）。
- 后端每次请求只做少量 SQL 查询（读 task_nodes/task_edges + 读取 required_docs 文件头部），不启动 CLI。

> 禁止：前端每秒 `subprocess agent_cli.py status`（频繁启动进程会卡）。

## 5. `agent_cli graph --json` 输出契约（新增命令）
目的：让 UI 中间“任务图”无需解析表格输出，直接拿结构化 JSON。

命令：
`agent_cli.py graph --plan-id <PLAN_ID> --json`

输出（单一 JSON）：
```json
{
  "schema_version": "graph_v1",
  "plan": {
    "plan_id": "...",
    "title": "...",
    "root_task_id": "...",
    "created_at": "..."
  },
  "running": {
    "task_id": null,
    "since": null,
    "source": "status|event|llm_calls"
  },
  "nodes": [
    {
      "task_id": "...",
      "title": "...",
      "node_type": "ACTION|GOAL|CHECK",
      "status": "PENDING|READY|IN_PROGRESS|BLOCKED|READY_TO_CHECK|TO_BE_MODIFY|DONE|FAILED",
      "owner_agent_id": "xiaobo|xiaojing|xiaoxie",
      "priority": 0,
      "blocked_reason": "WAITING_INPUT|WAITING_EXTERNAL|WAITING_SKILL|null",
      "attempt_count": 0,
      "active_artifact": {
        "artifact_id": "...",
        "format": "md|txt|json|html|css|js",
        "path": "..."
      },
      "missing_inputs": [
        {
          "name": "product_spec",
          "accepted_types": ["md","txt","pdf"],
          "suggested_path": "workspace/inputs/product_spec/xxx.md"
        }
      ],
      "required_docs_path": "workspace/required_docs/<task_id>.md"
    }
  ],
  "edges": [
    {
      "edge_id": "...",
      "from_task_id": "...",
      "to_task_id": "...",
      "edge_type": "DEPENDS_ON|DECOMPOSE|ALTERNATIVE",
      "metadata": {}
    }
  ],
  "ts": "..."
}
```

说明：
- `missing_inputs` 来自 DB 的 `input_requirements/evidences` 统计 + `workspace/required_docs/<task_id>.md`（若存在则优先展示 suggested_path）。
- `running.task_id` 若 DB 中没有显式 `IN_PROGRESS`，可用“最近事件/最近 llm_calls”推断。

## 6. Reset DB（与现有 Tk UI 一致）
UI 需要二次确认：
1) “确认删除所有 DB 数据吗？（会删除 state.db）”
2) “是否同时清理 workspace/tasks/logs 文件内容？”

行为等价于：
`agent_cli.py reset-db [--purge-workspace] [--purge-tasks] [--purge-logs]`

## 7. 性能与大数据量注意事项
- Graph API/graph CLI 必须分页/限量读取“详情”字段：
  - 任务图只需要 `task_nodes/task_edges` 的关键字段；大文本（artifact content、review JSON、LLM prompt/raw）不要在图接口里返回。
- required_docs 文件读取：只读取并解析前几百行（足够展示 suggested_path）。
- 未来 LLM Explorer 必须分页（按 created_at desc + limit + filters）。

## 8. 交付物与“最终可交付文件”规则（建议写入计划模板）
为避免出现“最终产出是 md 检查报告而不是可交付 html”的情况，建议在计划模板中强制：
- 最后一个 ACTION 节点必须产出 `artifact.format=html`（或 zip），并明确文件名（如 `index.html`）。
- `Validate` 类节点产出 md 属于“验收报告”，不应被当作最终交付物。

## 9. 实现里程碑（建议）
MVP（优先满足你列的需求）：
1) 后端：FastAPI（只读 DB + 调用 create-plan/run/reset-db/export + plans 列表）
2) CLI：新增 `agent_cli graph --json`
3) React：三栏布局 + 左侧控制面板 + 中间任务图（点击节点看缺输入/状态）
4) 进程控制：run start/stop/once（UI 关了也不影响 run）

下一步：
- 右侧 LLM Explorer（对齐现有 Tk 版）
- 更稳定的“当前执行节点”判定（显式 `IN_PROGRESS` + `TASK_STARTED/TASK_FINISHED` 事件）

## 10. 本地运行（开发模式）
后端（本地 API）：
1) 安装依赖：`D:\miniconda3\python.exe -m pip install -r dashboard_backend/requirements.txt`
2) 启动：`D:\miniconda3\python.exe -m uvicorn dashboard_backend.app:app --host 127.0.0.1 --port 8000`

前端（React）：
1) `cd dashboard_ui`
2) `npm install`
3) `npm run dev`

访问：`http://127.0.0.1:5173/`
