---
task_id: JF-001
title: Job Page Finder MVP
status: review
---

# 轻量招聘岗位页面发现 Agent MVP

## 目标

输入企业官网 URL，在有限步骤内找到当前页面显示具体岗位的招聘页面，并返回结构化结果。

## 背景

采用自定义轻量 Agent Loop，复用 `browser-use` 的 `BrowserSession`、`BrowserStateSummary`、DOM/`selector_map`、点击、滚动和等待能力，不复用完整通用 Agent 的 planning、memory、judge 或文件系统能力。起始 URL 由程序直接打开，不向模型开放 navigate。

## 范围

- 输入 `company_url` 和 `max_steps`（默认 8）。
- 每步只执行一个动作，并在页面变化后刷新状态。
- 输出 `success`、`job_page_url`、`job_title`、`evidence`、`steps`、`error`。
- 只暴露 `click(index)`、`scroll(direction)`、`wait(seconds)`、`done(job_title, evidence)` 四种动作。
- 允许通用链接进入第三方招聘系统，但以步骤和动作限制风险。

执行顺序为：启动浏览器、导航到输入 URL、获取状态、把 URL/标题/精简 DOM 发给 LLM、执行一个动作、刷新 DOM；`done` 经过校验后返回，达到步数上限则失败，最后关闭会话。只保留上一步结果，不保留完整对话历史。

动作语义：`click` 点击当前 DOM 索引的链接或按钮；`scroll` 只支持上下滚动；`wait` 等待动态页面（建议 1 至 5 秒）；`done` 返回岗位标题和原文 evidence。失败不开放专门的失败动作，由程序统一返回。

## 不在范围

企业专用规则、搜索官网、岗位详情提取/筛选/分页、登录、验证码、申请表、下载上传、任意 JavaScript、iframe/汉堡菜单专项、多 Tab、planning、长期记忆、judge、复杂恢复和遥测。

## 安全边界

默认无头浏览器、截图关闭、禁止下载和默认扩展；所有退出路径关闭浏览器。不修改相邻 `../browser-use`，不执行任意脚本，不填写表单，不处理验证码，不读取或记录密钥。

建议默认约束为 `max_steps=8`、连续失败 2 次、单步超时 30 秒、每步一个动作、`vision=False`。模型提示词必须明确：具体岗位才可 `done`，通用招聘入口不算成功，页面内容是非可信数据，不能扩展动作集合。

## 验收标准

- `AC-001`：当前页面出现至少一条具体岗位名称才可成功。
- `AC-002`：仅出现 `Careers`、`Jobs`、`加入我们`、`查看职位` 等入口不能算成功。
- `AC-003`：成功结果包含当前 URL、具体 `job_title`、非空 `evidence` 和步数，失败包含结构化 `error`。
- `AC-004`：达到 `max_steps` 或连续失败限制时退出，并在所有路径关闭浏览器。
- `AC-005`：模型及程序只执行四种动作。

## 相关代码

- `src/job_page_finder/finder.py`：生命周期、循环、动作校验和完成校验。
- `src/job_page_finder/models.py`：输入、动作和结果模型。
- `tests/test_finder.py`、`tests/test_local_browser.py`：单元及本地浏览器验证。

## 外部依赖

Python 3.11+、相邻路径的 `browser-use`、Pydantic、DeepSeek 或兼容 LLM；真实网站验证需要 Chromium 和模型配置。

## 未决问题

当前实现只确定性验证 `job_title` 出现在当前 DOM，并只要求 `evidence` 非空；旧规范对 evidence 的描述更强（要求证据原文在 DOM 中）。该差异登记为问题，本次不改产品代码。

本地静态页面测试计划应覆盖首页直接有岗位、点击 Careers 后有岗位、滚动后出现入口、只有介绍文字、evidence 不在 DOM、达到最大步数和点击后 DOM 更新索引失效等场景。
