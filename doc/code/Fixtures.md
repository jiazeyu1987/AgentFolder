# Fixtures（P0.4 可复现样例集）

目标：提供固定的 S/M/L 三个回归样例，在任何机器上无需手工补文件即可复现 baseline 输入与目录结构。

## 目录结构
- 样例定义：`tests/fixtures/cases/<CASE_ID>/`
  - `case.json`：`case_id/top_task/expected_outcome/notes/recommended_commands`
  - `baseline_inputs/`：最小输入文件集合（总大小建议 < 100KB）

当前内置样例：
- `S_2048`：小型单文件 Web 交付
- `M_doudizhu`：中型多文件 Web 交付（manifest 导出）
- `L_3d_shooter`：大型任务（预期触发 `REQUEST_EXTERNAL_INPUT` 或更细拆分）

## 一键安装到 baseline_inputs
安装脚本：`tools/install_fixtures.py`

列出可用样例：
- `python tools/install_fixtures.py --list`

安装单个样例到默认目录 `workspace/baseline_inputs/`：
- `python tools/install_fixtures.py --case S_2048`

安装全部样例：
- `python tools/install_fixtures.py --all`

指定安装目录（例如临时目录）：
- `python tools/install_fixtures.py --all --dest D:\\temp\\baseline_inputs`

安装后的落盘位置：
- `workspace/baseline_inputs/<CASE_ID>/...`

说明：baseline_inputs 的匹配策略会优先使用“文件名包含 requirement 名称”的启发式（例如 `product_spec_*.md`），所以 fixture 文件名刻意包含 `product_spec/requirements/constraints` 等关键词。

