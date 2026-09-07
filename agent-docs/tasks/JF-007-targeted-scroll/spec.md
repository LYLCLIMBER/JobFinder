---
task_id: JF-007
title: Targeted Scroll Discovery and Verification
status: done
---

# 目标化滚动发现与验证

## 目标

让 Job Page Finder 能发现并选择页面内部的原生可滚动容器，按实际滚动偏移量验证动作结果，并在根页面没有移动时尝试有效的内部容器。

## 背景

当前 `scroll` 只能滚动根页面，且底层工具根据请求距离返回成功摘要，不验证页面是否实际移动。在使用内部滚动容器的网站上，这会把无效动作误报为成功并导致模型重复滚动。

## 范围

- 为 `scroll` 增加可选目标索引；`None` 或 `0` 表示根页面，正整数表示当前步骤中的滚动容器。
- 点击元素与滚动容器共享当前步骤的数字索引空间，分别由动作类型校验。
- 从当前 browser-use DOM 状态发现原生可滚动容器并分配步骤内索引。
- 只保留当前方向有剩余空间且当前可见的候选；活跃模态框中的候选优先。
- 在视觉截图中标注滚动候选，并向模型提供方向、偏移量和剩余空间。
- 仅以滚动前后目标偏移量是否变化判断动作是否有效。
- 根页面未移动时按候选顺序尝试内部容器，在第一个实际移动的目标处停止。
- 覆盖普通内部滚动和原生 CSS Scroll Snap/分页吸附滚动。

## 不在范围

- CSS transform、Canvas、WebGL 或仅修改框架页码状态的虚拟滚动。
- DOM、URL 或截图变化作为滚动成功判据。
- 到顶/到底、懒加载、平滑滚动等待、亚像素容差等边缘处理。
- 面积、位置、任务语义等候选评分。
- 内部目标失败后自动尝试其他内部目标。
- `send_keys`、通用 `evaluate` 或任意 JavaScript 动作。
- 修改相邻 `../browser-use`。

## 安全边界

- 不读取或记录密钥。
- 不向模型开放脚本执行或任意坐标滚动。
- 只读使用 browser-use 当前状态中的 DOM 节点，并复用其 `ScrollEvent`。
- 滚动目标索引只在生成它的当前浏览器状态中有效。

## 验收标准

- `AC-001`：模型可用 `scroll.index` 选择当前提示中列出的根页面或内部滚动容器，非法或过期索引被拒绝。
- `AC-002`：候选发现只保留当前可见且在请求方向有剩余空间的原生滚动容器，并优先列出活跃模态框中的容器。
- `AC-003`：视觉输入使用与动作相同的索引标注滚动容器，并向模型提供可选择的滚动目标信息。
- `AC-004`：滚动仅在目标操作前后偏移量不同时报告成功，成功摘要包含实际位移而非请求位移。
- `AC-005`：根页面偏移量未变化时自动尝试内部候选，并在第一个偏移量变化的候选处停止；全部不动时返回动作失败。
- `AC-006`：本地 Chromium 集成测试证明根页面不可滚动时可滚动内部 CSS Scroll Snap 容器并发现第二屏具体岗位。
- `AC-007`：既有点击、等待、完成校验、诊断和默认视觉行为无回归。

## 相关代码

- `src/job_page_finder/models.py`
- `src/job_page_finder/finder.py`
- `src/job_page_finder/vision.py`
- `src/job_page_finder/scrolling.py`
- `tests/test_finder.py`
- `tests/test_vision.py`
- `tests/test_scrolling.py`
- `tests/test_local_browser.py`
- `tests/test_diagnostics.py`

## 外部依赖

- browser-use 的 `BrowserStateSummary`、DOM 节点滚动信息和 `ScrollEvent`。
- Pillow 截图标注。
- Chromium 仅用于 integration 标记测试。

## 未决问题

- browser-use 没有公开完整 DOM 遍历接口；本任务将对 `SerializedDOMState._root` 的只读访问隔离在滚动模块中，后续依赖升级时需复核。
- 非原生虚拟滚动按本任务定义会被判定为未移动，遇到真实需求后另行扩展。
