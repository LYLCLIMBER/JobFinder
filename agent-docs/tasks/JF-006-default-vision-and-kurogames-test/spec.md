---
task_id: JF-006
title: Default Vision and Kurogames Test
status: review
---

# 任务规格

## 目标

将公开运行时和 Finder 的视觉功能默认值统一为启用，并对 Kuro Games 网站执行一次真实 CLI 验证。

## 背景

现有 `RuntimeConfig.use_vision` 与 `JobPageFinder` 构造参数默认关闭，CLI 未提供视觉参数，导致默认 CLI 调用不发送视觉上下文。

## 范围

- 将公开默认值改为 `True`，保持显式 `False` 的文本模式。
- 补充默认及显式关闭的回归测试。
- 以 raw diagnostics 对 `https://www.kurogames.com/` 执行一次真实 CLI 调用，并据产物记录图片请求、标注和候选证据。

## 不在范围

- 不改变视觉候选、标注或失败处理策略。
- 不修改 `.env`、`../browser-use` 或 `.opencode/`，不更换模型。

## 安全边界

- 不读取、展示或记录密钥。
- 真实调用仅使用现有自动加载的配置。

## 验收标准

- `AC-001`：`RuntimeConfig` 和 `JobPageFinder` 的公开默认构造均启用视觉，运行时装配传递该默认值。
- `AC-002`：显式 `use_vision=False` 保持文本模型输入；截图诊断开关不改变该输入。
- `AC-003`：完成一次指定 Kuro Games CLI 真实调用，并如实记录任务结果、视觉请求/产物证据及模型视觉兼容性。

## 相关代码

- `src/job_page_finder/runtime.py`
- `src/job_page_finder/finder.py`
- `tests/test_runtime.py`
- `tests/test_finder.py`
- `tests/test_diagnostics.py`

## 外部依赖

- 可用的既有模型配置、网络、浏览器和 Kuro Games 网站。

## 未决问题

- 当前已配置模型是否接受图片输入，仅可由真实调用确认。
