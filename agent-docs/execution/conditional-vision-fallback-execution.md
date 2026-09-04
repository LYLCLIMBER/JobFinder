# 条件视觉回退执行文档

## 执行目标

在 JobFinder 中实现通用的条件视觉输入能力，解决文本 DOM 无法表达图片按钮、CSS 背景按钮等交互元素的问题。

JobFinder 使用单个支持视觉的 LLM。普通状态只发送文本 DOM；当前状态存在可可靠标注的无文本交互元素时，才将带 selector index 标注的完整视口截图附加到同一次模型请求中。

## 工作范围

需要完成：

1. 增加视觉能力开关，默认关闭。
2. 视觉开启时获取当前浏览器状态截图。
3. 从现有 `selector_map` 筛选无文本交互元素。
4. 检查元素是否具有可靠的可见布局区域。
5. 将元素区域映射到截图像素坐标。
6. 在截图上标注对应的 selector index。
7. 有成功标注候选时发送文本 DOM和标注截图。
8. 继续使用现有 `AgentDecision` 和四种动作。
9. 在截图、坐标或图片处理失败时回退到纯文本消息。
10. 保持现有 `done` DOM 证据校验和 click index 校验。

## 不要实现

- 不加入企业或域名专用规则。
- 不解析 CSS `background-image`。
- 不解析外部 JavaScript 或事件监听器目标。
- 不执行任意 JavaScript。
- 不开放新的浏览器动作。
- 不裁剪候选区域；第一版使用完整当前视口截图。
- 不使用独立视觉识别模型或二次决策模型。
- 不修改相邻的 browser-use 项目。

## 执行顺序

### 1. 检查基线

在项目根目录执行：

```bash
uv sync --dev
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

如果基线失败，先记录失败原因，不要将无关修复混入本任务。

### 2. 实现视觉辅助逻辑

在 `src/job_page_finder/vision.py` 中集中处理：

- 无文本语义判断。
- AX name 和 description 判断。
- 后代图片 `alt`、`title`、`aria-label` 判断。
- `selector_map` 候选遍历。
- `absolute_position` 和页面滚动位置转换。
- 视口边界求交。
- 截图尺寸与 CSS 视口尺寸的缩放。
- 截图边框和 index 标签绘制。
- Base64 PNG 输出。

无布局信息、零尺寸、非有限坐标、完全位于视口外或可见区域过小的候选必须跳过，不能猜测位置。

### 3. 接入 JobPageFinder

在 `JobPageFinder` 中增加：

```python
use_vision: bool = False
```

可选地限制本次最多处理的视觉候选数量，默认值应避免页面大量图标造成截图混乱。

视觉开启时调用：

```python
get_browser_state_summary(include_screenshot=True)
```

视觉关闭时保持：

```python
get_browser_state_summary(include_screenshot=False)
```

模型调用仍然只发生一次，并继续使用：

```python
output_format=AgentDecision
```

没有成功标注候选时发送原来的纯文本 `UserMessage`。有候选时发送：

1. 当前任务、URL、标题、滚动信息和 DOM 文本。
2. 成功标注的 index 列表。
3. 一张带边框和 index 标签的完整视口 PNG 截图。

### 4. 保持安全校验

视觉模型返回的 `click(index)` 必须继续通过当前 `selector_map` 存在性校验。

视觉模型不能通过截图绕过 `done` 校验。岗位标题和证据仍必须来自当前可见 DOM。

图片处理和视觉输入失败时：

- 不执行程序猜测的点击。
- 优先退回纯文本模型请求。
- 继续沿用现有步骤超时和错误处理。

### 5. 更新依赖和文档

如果使用 Pillow 进行内存图片标注，将 Pillow 声明为 JobFinder 的直接依赖并更新 `uv.lock`。

更新 `README.md`，说明：

- 默认视觉关闭。
- 使用视觉模型时需要显式传入 `use_vision=True`。
- 截图只在存在可靠无文本候选时附加。

## 测试要求

### 单元测试

至少覆盖：

- 空交互元素被识别为视觉候选。
- 有文本的元素被排除。
- 有 `aria-label`、`title`、`alt` 或 AX 语义的元素被排除。
- 没有布局区域的元素被排除。
- 页面滚动后坐标映射正确。
- 高 DPR 或截图尺寸变化时像素映射正确。
- 完全位于视口外的元素被排除。
- 截图标注包含正确 index。
- 没有候选时只发送纯文本消息。
- 有候选时发送文本和一个图片内容块。
- 截图解码或标注失败时安全降级。
- 现有 click 和 done 校验不回归。

### 浏览器集成测试

使用本地静态页面验证：


```text
企业首页
→ 无文本交互按钮
→ 岗位列表页
→ 当前 DOM 出现具体岗位
```

Fake LLM 只验证图片内容块存在、index 标注上下文存在以及最终动作仍使用现有 action schema，不需要在测试中调用真实视觉 API。

Kuro 只作为真实网站验证案例，不作为稳定单元测试依赖。验证时不能将 Kuro 的 class、URL 或页面结构写入生产逻辑。

## 验证命令

完成实现后在项目根目录执行：

```bash
uv run pytest -q
uv run pytest -q -m integration
uv run ruff check .
uv run ruff format --check .
git diff --check
```

如使用真实视觉 API 验证 Kuro，应显式指定视觉模型，不要输出或记录 API Key：

```python
llm = create_deepseek_llm(model="deepseek-v4-flash-vision-exp")
finder = JobPageFinder(llm=llm, use_vision=True)
```

## 完成标准

- 默认配置的现有行为不变。
- 视觉开启时，截图仅在存在可靠无文本候选时进入模型消息。
- 截图中的 index 能对应当前 `selector_map`。
- 模型可以使用标注截图选择图片交互元素。
- 无法标注时不会发生猜测点击。
- `done` 仍要求岗位标题存在于当前 DOM。
- 所有测试、格式和静态检查通过。
- 代码和文档不包含企业专用逻辑或敏感配置。
