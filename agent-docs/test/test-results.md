# 测试结果与代码审查记录

## 1. 测试范围

本次验证覆盖以下内容：

- JobPageFinder 的普通招聘页面发现流程。
- 开启视觉输入后的真实网站导航流程。
- 校园招聘专项目标下的导航行为和安全失败行为。
- 岗位标题必须存在于当前 DOM 后才能返回 `done`。
- 浏览器启动、导航、动作执行、超时和退出清理。

验证对象为 `https://www.kurogames.com/`，使用真实 Chromium 和视觉模型 `deepseek-v4-flash-vision-exp`。

API Key 通过 `.env` 的显式值加载，日志不记录密钥；截图未嵌入日志，日志只保存对应截图路径。

## 2. 代码审查结果

### 2.1 已确认通过的部分

- Agent loop 每一步只选择并执行一个动作，页面变化后重新获取浏览器状态。
- `click` 会校验索引是否属于当前 `selector_map`，避免使用旧页面索引。
- `done` 会校验岗位标题、证据、HTTP 页面、可用 DOM，以及岗位标题是否实际出现在当前 DOM 文本中。
- 浏览器启动失败、导航失败、步骤超时和正常结束路径都会执行浏览器清理。
- 视觉回退只在存在无文本且有可靠坐标的交互元素时附加截图；无效截图或坐标不会触发猜测点击。
- 默认浏览器关闭截图高亮、下载和默认扩展，未修改 browser-use 源码。
- 校园专项失败时没有误报社会招聘岗位，符合“只在校园招聘页面且有具体岗位名称时完成”的约束。

### 2.2 发现的问题和残余风险

- 低优先级文档问题：`README.md` 当前引用的是 `docs/conditional-vision-fallback.md`，实际实现文档位于 `agent-docs/task/conditional-vision-fallback.md`。
- `JobPageFinderInput` 只有企业 URL 和最大步数，没有正式的任务目标字段。校园专项验证因此通过临时 LLM wrapper 注入目标，不能视为生产接口已经支持任意专项目标。
- `max_steps=8` 对动态加载、全屏滚动和跨域招聘平台导航较紧。真实网站可能在页面尚未稳定时消耗多个等待步骤。
- Kuro 页面报告的 `pixels_above/pixels_below` 在滚动后仍为 0，但截图和 DOM 内容发生变化，说明内部滚动容器或全屏滚动会使滚动元数据不完全可靠。
- 真实网站和第三方招聘系统内容会变化，Kuro 集成结果应作为当前环境下的验证记录，不应替代稳定单元测试。

## 3. 自动化测试结果

执行命令：

```text
uv run pytest
```

结果：`19 passed in 4.35s`

静态检查命令：

```text
uv run ruff check .
```

结果：通过。

## 4. 验证案例一：普通招聘页面

### 输入和环境

- URL：`https://www.kurogames.com/`
- 模型：`deepseek-v4-flash-vision-exp`
- 最大步骤：`8`
- 视觉输入：开启

### 结果

- 状态：成功
- 步骤：`8`
- 岗位页面：`https://kurogame.jobs.feishu.cn/index/position/list?functionCategory=7456715028863912201`
- 岗位名称：`NAMI-分镜编导`
- 证据：当前 DOM 中包含岗位名称、城市、职位类别和岗位描述。

该案例验证了从企业官网进入招聘系统、等待动态内容、选择职位分类并在岗位列表页面完成的完整链路。该页面属于普通/社会招聘验证，不是校园招聘验证。

### 日志摘录

以下摘录保留了该案例的关键动作和最终返回：

```text
STEP 1: click(index=11)                 # 加入我们
STEP 2: scroll(direction=down)
STEP 3: click(index=509)                # 进入招聘系统
STEP 4: wait(seconds=2)
STEP 5: scroll(direction=down)
STEP 6: click(index=1128)               # 选择职位分类
STEP 7: wait(seconds=3)
STEP 8: done(job_title="NAMI-分镜编导")

FINAL RESULT:
success=true
job_page_url=https://kurogame.jobs.feishu.cn/index/position/list?functionCategory=7456715028863912201
job_title=NAMI-分镜编导
steps=8
error=null
```

完整原始日志及截图作为补充材料保存在 [`log/kuro-vision-review/`](../../log/kuro-vision-review/)。

## 5. 验证案例二：校园招聘专项

### 输入和环境

- URL：`https://www.kurogames.com/`
- 模型：`deepseek-v4-flash-vision-exp`
- 最大步骤：`8`
- 视觉输入：开启
- 专项要求：只能在校园招聘岗位列表页面且当前 DOM 有具体岗位名称时返回 `done`。

### 结果

- 状态：失败，但属于符合约束的安全失败
- 步骤：`8`
- 最终 URL：`https://www.kurogames.com/join`
- 最终错误：`Maximum steps reached without finding a specific job`
- 未返回岗位名称，也未误报社会招聘岗位。

### 行为分析

- 第 1 步点击“加入我们”，成功进入 `/join`。
- 第 2 至第 4 步连续等待 3 秒，页面仍主要显示动态加载状态和宣传内容。
- 第 5 至第 6 步向下滚动，看到的是公司文化、办公环境、薪酬待遇和员工福利，不是校园岗位列表。
- 第 7 步继续向上滚动，第 8 步再次向下滚动，仍未进入校园招聘列表。
- 日志中的截图标注索引与传给模型的候选索引一致；未发现默认浏览器高亮污染截图的问题。

### 失败归因

主要原因是动态全屏页面的入口发现和步骤预算不足，而不是 API、浏览器启动、视觉调用或 `done` 校验异常。当前日志不足以确认具体校园入口是否在该次页面状态中暴露，因此不能将失败归因到某一个确定的 DOM 元素。

### 日志摘录

以下摘录保留了该案例的关键动作和最终返回：

```text
STEP 1: click(index=10)                 # 加入我们
STEP 2: wait(seconds=3)
STEP 3: wait(seconds=3)
STEP 4: wait(seconds=3)
STEP 5: scroll(direction=down)
STEP 6: scroll(direction=down)
STEP 7: scroll(direction=up)
STEP 8: scroll(direction=down)

FINAL RESULT:
success=false
job_page_url=null
job_title=null
steps=8
error=Maximum steps reached without finding a specific job
```

完整原始日志及截图作为补充材料保存在 [`log/kuro-vision-review-campus/`](../../log/kuro-vision-review-campus/)。

## 6. 结论

普通招聘链路验证成功，代码级自动化测试和静态检查均通过。校园专项验证未完成，但程序正确拒绝了没有校园岗位 DOM 证据的完成请求，避免了误报。当前主要剩余风险集中在动态网站导航、专项任务输入表达能力和有限步骤预算。
