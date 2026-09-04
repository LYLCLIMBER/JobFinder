---
task_id: JF-002
title: Conditional Vision Fallback Execution
status: review
---

# 条件视觉回退执行记录

## 执行目标

实现通用的条件视觉输入：普通状态只发送文本 DOM，有可靠标注的无文本交互元素时才发送同一次请求的完整视口截图。

## 执行要点

1. `JobPageFinder(use_vision=False)` 保持默认纯文本行为。
2. `vision.py` 负责语义排除、候选遍历、`absolute_position` 减滚动位置、视口求交、图片缩放和 index 标注。
3. 无布局、零尺寸、非有限坐标、完全视口外或不可见区域不足的候选必须跳过。
4. 有成功标注候选才发送一个 PNG 图片块；没有候选或图片处理失败则发送纯文本。
5. click 必须存在于当前 `selector_map`；done 仍要求岗位标题在当前 DOM。

候选语义判断检查元素自身的 meaningful text、`value`、`aria-label`、`title`、`placeholder`、`alt` 及自身 AX name/description；后代当前仅检查 `aria-label`、`title`、`alt`。规范化后完全为空才是无文本候选，不采用少于字符数的近似规则。优先使用已含 frame 偏移的 `absolute_position`，减去页面滚动位置后按 CSS 视口到截图实际尺寸缩放，并与截图边界求交。候选按可见面积排序并限制数量，标注只发生在内存副本。

有候选时用户消息包含任务、URL、标题、滚动信息、DOM、index 列表和完整 PNG；系统提示要求标签对应当前 DOM index，且截图文字不能作为 `done` evidence。没有候选时发送原来的纯文本 `UserMessage`。

## 不要实现

不要加入企业专用逻辑、解析 CSS/外部脚本、执行任意 JavaScript、开放新动作、裁剪候选、使用独立视觉模型或修改相邻 `browser-use`。

## 测试要求与命令

单元测试覆盖文本/ARIA/AX/alt 排除、坐标滚动和 DPR、视口外候选、标注 index、无候选纯文本、图片块、图片解码/标注失败降级、click/done 校验；本地静态页面应验证“企业首页 → 无文本交互按钮 → 岗位列表页 → 当前 DOM 有具体岗位”的闭环。Fake LLM 只检查图片块、index 上下文和既有 action schema；Kuro 不作为稳定单元测试依赖。

```bash
uv run pytest -q
uv run pytest -q -m integration
uv run ruff check .
uv run ruff format --check .
git diff --check
```

## 实现事实登记

当前代码已将 Pillow 作为直接依赖，并实现上述视觉辅助逻辑。截图/解码/坐标/标注失败由 `build_visual_context` 返回空并走纯文本；但多模态 LLM 调用异常不会触发纯文本重试，而是进入循环的错误处理。规范中“岗位标题和证据来自 DOM”的表述也强于当前 `_validate_done` 实现。
