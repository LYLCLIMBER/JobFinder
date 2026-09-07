---
task_id: JF-008
status: done
verified_at: 2026-09-07
---

# 验证记录

## 验收矩阵

| ID | 验收项 | 结果 | 证据/说明 |
| --- | --- | --- | --- |
| AC-001 | offset 成功路径不额外等待 | Passed | `test_root_gesture_that_moves_internal_target_does_not_scroll_twice` 验证 offset 变化后仅执行一次实时 URL 基线读取，不进入 route 轮询。 |
| AC-002 | 延迟 URL/route 变化被识别 | Passed | `test_scroll_detects_delayed_route_change_without_internal_fallback` 验证轮询后识别 query 变化并返回目标 URL。 |
| AC-003 | route change 后停止 fallback | Passed | 同一测试断言只发送根滚轮，不滚动内部目标。 |
| AC-004 | URL 不变时保留 fallback/失败 | Passed | 根 fallback、实时 URL 基线和显式目标无效果测试分别覆盖继续 fallback 与最终失败。 |
| AC-005 | 轻量轮询且受 step deadline 限制 | Passed | 轮询只调用 `get_current_page_url()`；deadline 测试验证返回 `STEP_TIMEOUT` 且不执行内部 fallback。 |
| AC-006 | 既有行为无回归 | Passed | 完整测试 89 passed；Chromium integration 4 passed。 |

结果状态只能使用 `Passed`、`Failed`、`Partial`、`Not run`、`Not applicable`。

## 自动化测试

- `git diff --check`：Passed。
- `uv run ruff check .`：Passed。
- `uv run ruff format --check .`：Passed，21 files already formatted。
- `uv run pytest -q`：Passed，89 passed。
- `uv run pytest -q -m integration`：Passed，4 passed、85 deselected。
- 独立 reviewer：发现的 deadline 传播、root-only 轮询、实时 URL 基线和 diagnostics fake 问题均已修复；完整测试通过。

## 残余风险

- 时间窗口只能建立有限因果关系，不能证明 route change 一定由滚轮产生。
