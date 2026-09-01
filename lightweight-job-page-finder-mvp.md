# 轻量招聘岗位页面发现 Agent MVP 方案

## MVP 目标

输入一个企业官网 URL，自动浏览页面并找到招聘岗位列表页面。

**成功标准：**

> 当前网页中能够看到至少一条具体岗位信息。

例如 `Senior Backend Engineer`、`Product Manager, Payments`。仅看到 `Careers`、`Jobs`、`加入我们`、`查看职位` 等招聘入口不算成功。

## 技术方案

采用“自定义轻量 Agent Loop + 复用 browser-use 浏览器能力”的方式。

复用 browser-use：

- `BrowserSession`：启动、导航、关闭浏览器
- `BrowserStateSummary`：获取当前 URL、标题和页面状态
- DOM 序列化与 `selector_map`：向模型提供精简页面内容
- `Tools` 的点击、滚动和等待实现
- 元素索引点击及页面变化后的 DOM 更新机制

不复用完整的通用 `Agent` 主循环，避免引入 planning、memory、judge、文件系统等当前不需要的能力。

## 输入输出

### 输入模型

```python
class JobPageFinderInput(BaseModel):
    company_url: HttpUrl
    max_steps: int = 8
```

第一版假设输入一定是企业官网 URL，不负责通过搜索引擎寻找官网。

### 输出模型

```python
class JobPageFinderResult(BaseModel):
    success: bool
    job_page_url: str | None
    job_title: str | None
    evidence: str | None
    steps: int
    error: str | None
```

示例：

```json
{
  "success": true,
  "job_page_url": "https://example.com/careers/open-positions",
  "job_title": "Senior Backend Engineer",
  "evidence": "Senior Backend Engineer - Remote - Engineering",
  "steps": 3,
  "error": null
}
```

## 动作集合

只向模型暴露以下四个动作：

```text
click(index)
scroll(direction)
wait(seconds)
done(job_title, evidence)
```

起始 URL 由程序直接打开，因此不向模型开放 `navigate`。

### `click`

点击 DOM 中指定索引的链接或按钮。

主要用于：

- 点击 `Careers`
- 点击 `Jobs`
- 点击招聘入口
- 展开基础导航菜单

### `scroll`

仅支持向上或向下滚动，用于查找当前视口之外的招聘入口或岗位列表。

### `wait`

等待动态页面加载。

建议限制为较短时间，例如 1 至 5 秒。

### `done`

模型确认当前页面出现至少一条具体岗位后调用，并返回：

- `job_title`：看到的具体岗位名称
- `evidence`：包含岗位信息的原文片段

失败不需要专门的 `done(success=False)`；达到最大步数后由程序统一返回失败。

## 执行流程

```text
1. 创建并启动 BrowserSession
2. 直接导航到 company_url
3. 获取当前页面 BrowserStateSummary
4. 将 URL、标题和精简 DOM 发送给 LLM
5. LLM 只选择一个动作
6. 执行 click、scroll、wait 或 done
7. 页面变化后重新获取 DOM
8. 如果是 done，校验岗位证据
9. 校验成功则返回结果
10. 达到 max_steps 后返回失败
11. 关闭 BrowserSession
```

建议每一步只执行一个动作。这样页面变化后会立即刷新 DOM，避免模型继续使用已经失效的元素索引。

## 完成校验

不针对不同企业编写 URL、CSS selector 或招聘关键词规则。

模型调用 `done` 时，程序进行最低限度的确定性校验：

1. `job_title` 不为空。
2. `evidence` 不为空。
3. `job_title` 或完整 `evidence` 确实存在于当前 DOM 文本中。
4. 当前页面不是加载失败页或空白页。

这里采用：

```text
模型负责语义判断：这是不是一个具体岗位
程序负责事实校验：模型提供的岗位文字是否真的出现在页面中
```

校验失败时，不立即结束，将错误反馈给模型并继续下一步。

MVP 不要求岗位必须是可点击链接，因为有些招聘页面使用不可点击的卡片或先加载列表再绑定交互。

## 模型提示词

System Prompt 只描述单一目标和约束：

```text
你需要从企业官网找到显示具体招聘岗位的页面。

每一步只能选择一个动作：
- click：点击页面元素
- scroll：滚动页面
- wait：等待加载
- done：确认找到具体岗位

只有在当前页面明确显示至少一个具体岗位名称时才能调用 done。
仅看到 Careers、Jobs、Join Us、招聘或查看职位等入口不算完成。
调用 done 时，必须原样返回页面上可见的岗位名称和证据文本。
不要填写表单、申请岗位、登录、下载文件或访问无关页面。
```

每一步提供给模型的上下文仅包括：

- 原始任务
- 当前 URL
- 页面标题
- 精简 DOM
- 上一步动作及执行结果
- 当前步数和剩余步数

不保留完整对话历史，只保留上一步结果即可。

LLM 默认建议使用 `ChatBrowserUse`。

## 限制与安全边界

MVP 建议使用以下默认配置：

```text
max_steps = 8
max_consecutive_failures = 2
step_timeout = 30 秒
vision = False
headless = True
每步最多一个动作
```

同时限制：

- 不执行任意 JavaScript
- 不允许文件上传或下载
- 不填写申请表
- 不处理验证码
- 不跳转到与企业招聘无关的网站
- 可通过 `allowed_domains` 限制在企业域名及其招聘系统跳转范围，但跨域招聘平台的精确限制可留到后续版本

不少企业会跳转到 Greenhouse、Lever、Workday 等第三方招聘域名。第一版如果严格限制为企业域名，可能找不到这些岗位；建议 MVP 暂时允许普通链接跳转，但限制最大步骤和动作类型。

## 明确不做

第一版不实现：

- 针对企业页面的特殊规则
- 截图和视觉识别
- 搜索引擎搜索
- 岗位详情提取
- 岗位筛选、分页和搜索
- iframe 专项处理
- 汉堡菜单专项逻辑
- 多 Tab 协作
- 登录和验证码
- planning 和长期记忆
- judge 模型
- 消息压缩
- 失败后的复杂恢复
- GIF、录屏、文件系统和遥测集成

## 代码组织建议

保持实现紧凑，预计只需要以下职责：

```text
JobPageFinder
├── 输入校验
├── 浏览器生命周期
├── 最小 Agent Loop
├── 模型动作调用
├── 动作执行
└── 完成证据校验
```

Pydantic 模型集中定义：

```text
JobPageFinderInput
ClickAction
ScrollAction
WaitAction
DoneAction
JobPageFinderResult
```

不为每个动作创建独立服务类，动作调度保留在一个函数中，避免 MVP 过度抽象。

## 测试计划

先使用本地静态页面，不依赖真实网站和真实岗位变化。

至少覆盖：

1. 首页直接显示岗位，第一步完成。
2. 首页包含 Careers 链接，点击后出现岗位。
3. 招聘入口需要滚动后才能看到。
4. 招聘页面只有介绍文字，没有具体岗位，不能成功。
5. 模型提交的岗位证据不在 DOM 中，拒绝完成。
6. 达到最大步数，返回结构化失败结果。
7. 点击后 DOM 更新，不能继续使用旧元素索引。

## MVP 验收标准

- 能在简单静态测试网站上完成“官网 → 招聘入口 → 岗位列表”闭环。
- 成功结果必须包含当前页面真实可见的岗位名称。
- 失败时能够在步数限制内退出并关闭浏览器。
- 不执行四种动作之外的任何操作。
