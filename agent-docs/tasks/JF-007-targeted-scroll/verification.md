---
task_id: JF-007
status: done
verified_at: 2026-09-07
---

# 验证记录

## 验证范围

- 目标化滚动动作、候选发现、视觉标注、偏移量验证、根页面 fallback 和 CSS Scroll Snap。

## 验收矩阵

| ID | 验收项 | 结果 | 证据/说明 |
| --- | --- | --- | --- |
| AC-001 | 目标索引和过期索引校验 | Passed | `tests/test_scrolling.py` 验证索引复用/分配；`tests/test_finder.py` 验证非法目标被拒绝。 |
| AC-002 | 候选过滤和模态框优先 | Passed | `tests/test_scrolling.py` 覆盖可见性、方向剩余空间和活跃模态框优先。 |
| AC-003 | 视觉与提示索引一致 | Passed | `tests/test_vision.py` 验证滚动目标使用动作索引标注；finder 提示公开相同目标。 |
| AC-004 | 实际偏移量验证和摘要 | Passed | finder 单元测试覆盖无位移失败和 `100px` 实际位移摘要。 |
| AC-005 | 根页面无效后的内部 fallback | Passed | 单元测试覆盖 fallback 和根手势已移动内部容器时停止，避免重复滚动。 |
| AC-006 | CSS Scroll Snap 本地 Chromium 测试 | Passed | 显式内部目标与根 fallback 两种 integration 参数均发现第二屏岗位。 |
| AC-007 | 既有行为无回归 | Passed | 完整测试 86 passed；独立 reviewer 复核无 blocking/important 问题。 |

结果状态只能使用 `Passed`、`Failed`、`Partial`、`Not run`、`Not applicable`。

## 自动化测试

- `git diff --check`：通过。
- `uv run ruff check .`：通过。
- `uv run ruff format --check .`：通过，21 个文件已格式化。
- `uv run pytest -q`：通过，86 passed。
- `uv run pytest -q -m integration`：通过，4 passed、82 deselected。
- 工作区：未提交修改。

## 手工/真实网站验证

- 不要求重新运行外部真实网站；原始米哈游诊断作为问题背景，不作为修复通过证据。

## 环境

- 本地 Linux；项目 uv 环境；本地 Chromium integration 测试。

## 产物

- `src/job_page_finder/scrolling.py`：候选发现、描述、匹配和方向过滤。
- `tests/test_local_browser.py`：真实本地 Chromium CSS Scroll Snap 验证。
- 独立 reviewer 在修复根手势重复滚动问题后复核，无 blocking 或 important 发现。

## 未验证项

- 无。

## 残余风险

- 非原生虚拟滚动不在本任务范围内。
- 完整 DOM 遍历依赖 browser-use 私有 `SerializedDOMState._root`，依赖升级时需复核。
- 按任务范围未增加平滑滚动等待、epsilon 或偏移量之外的成功判据。
