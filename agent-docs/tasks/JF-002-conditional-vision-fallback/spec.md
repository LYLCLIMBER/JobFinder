---
task_id: JF-002
title: Conditional Vision Fallback
status: review
---

# 条件视觉回退

## 目标

视觉显式开启且 DOM 无法表达可靠交互语义时，为同一次模型请求附加带 `selector_map` index 标注的当前视口截图；普通状态保持纯文本。

## 背景

图片按钮或 CSS 背景按钮可能没有文字或无障碍名称。候选只来自 browser-use 已识别的交互元素，不重复实现元素检测。截图只增加观察信息，不改变岗位成功标准。

## 范围

默认 `use_vision=False`；开启后获取 DOM、页面信息、`selector_map` 和截图，筛选无文本候选，依据 `absolute_position`、滚动位置、视口和实际图片尺寸映射，在内存 PNG 副本标注 index，继续使用四种动作和现有安全校验。

## 不在范围

企业、域名、CSS class 或招聘 URL 特殊规则，CSS background-image、外部脚本、任意 JavaScript、独立视觉模型、二次决策、裁剪候选、额外浏览器动作或修改 `../browser-use`。

## 安全边界

只把成功标注的当前 `selector_map` index 交给模型；click 仍做索引存在性校验，done 不能仅凭截图通过。截图不落盘、不写入 Base64 日志；处理失败不得猜测点击。不读取或记录密钥。

## 验收标准

- `AC-001`：视觉默认关闭，关闭时不请求截图。
- `AC-002`：只把无文本、无 value/ARIA/title/placeholder/alt 及元素自身 AX name/description 的交互元素作为候选；后代当前仅检查 `aria-label`、`title`、`alt`。
- `AC-003`：可靠布局、滚动、视口边界和 DPR/图片尺寸映射正确，无效候选跳过。
- `AC-004`：成功候选在完整当前视口 PNG 上标注对应 selector index。
- `AC-005`：截图请求包含 DOM、候选 index 上下文和一个图片块，并继续使用 `AgentDecision`。
- `AC-006`：selector_map 安全校验和 done 的 DOM 岗位标题校验不被截图绕过。
- `AC-007`：图片获取、解码、坐标或标注失败时降级到纯文本，不猜测点击。
- `AC-008`：视觉模型多模态调用失败后应重试一次纯文本请求；当前实现没有纯文本重试，验收保持未完成。
- `AC-009`：不包含企业专用逻辑；Kuro 仅作真实网站案例。

## 相关代码

- `src/job_page_finder/vision.py`：候选、坐标、标注和 PNG。
- `src/job_page_finder/finder.py`：开关、消息、动作和安全校验。
- `tests/test_vision.py`、`tests/test_finder.py`：视觉单元测试和消息断言。

## 外部依赖

Pillow、browser-use、支持图片内容的 LLM、Chromium；Kuro 真实验证依赖外网和模型配置。

## 未决问题

多模态调用失败在 `finder.py` 的 `_choose_action` 调用链中直接进入步骤错误处理，没有纯文本重试。另有 evidence 文档描述强于实现的问题：截图不能替代 DOM，但实际仅校验 `job_title`，不校验完整 evidence。
