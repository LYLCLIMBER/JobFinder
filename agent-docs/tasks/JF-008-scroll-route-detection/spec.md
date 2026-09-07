---
task_id: JF-008
title: Asynchronous Scroll Route Detection
status: done
---

# 异步滚轮路由变化检测

## 目标

当根滚轮手势没有产生原生滚动偏移、但随后触发 SPA URL/route 变化时，将动作识别为有效的滚轮导航，避免过早报告无效果。

## 背景

米哈游首页使用滚轮驱动全屏 SPA 切页。诊断显示滚轮后约 0.67 秒的偏移检查仍无变化，但约 1.48 秒后 URL 从 `/` 变为 `/?page=product`。browser-use 的 `ScrollEvent` 完成只代表手势已发送，既有 0.15 秒等待和单次状态捕获不足以等待异步 route change。

## 范围

- 滚轮前记录当前 URL。
- 保留 JF-007 的 offset 优先成功判据。
- offset 未变化时，在继续 fallback 或报告失败前，以轻量 URL 查询进行受限轮询。
- 默认 URL 观察窗口为 2 秒，轮询间隔约 250 毫秒，并受现有 step deadline 限制。
- path、query、fragment 或 origin 任一变化均视为 route change。
- route 变化后立即停止后续滚动 fallback，并返回明确的滚轮路由变化摘要。
- 增加单元测试覆盖延迟 route change、超时无变化、offset 优先和 deadline 行为。

## 不在范围

- scroll range 变化检测。
- URL 不变的 transform、Canvas、WebGL 或框架 section 状态变化。
- DOM 或截图变化作为成功判据。
- 修改 `../browser-use`。
- 解决模型重复点击、视觉候选质量或提高 `max_steps`。

## 验收标准

- `AC-001`：offset 变化仍立即按原生滚动成功处理，不额外等待 route 窗口。
- `AC-002`：offset 不变但 URL 在观察窗口内变化时，动作成功并报告目标 URL。
- `AC-003`：检测到 route change 后不再执行后续内部滚动 fallback。
- `AC-004`：观察窗口内 URL 不变时，保持现有 fallback 和最终失败行为。
- `AC-005`：URL 轮询使用 BrowserSession 公共 URL 查询能力，不重复构建完整 DOM，且不越过 step deadline。
- `AC-006`：既有滚动、视觉、诊断和 Chromium integration 测试无回归。

## 相关代码

- `src/job_page_finder/finder.py`
- `tests/test_finder.py`
- `tests/test_local_browser.py`
- `tests/test_diagnostics.py`

## 残余风险

- 时间窗口内由其他异步行为造成的 URL 变化可能被归因于滚轮。
- browser-use 尚未为所有 `history.pushState()` 变化提供统一完成事件，轮询仍依赖 CDP target URL 更新。
