---
task_id: JF-003
title: Unified Task Runtime
status: review
---

# 任务规格

## 目标

建立一条统一、可测试的同步任务运行链路：调用方提交结构化任务后，由唯一的 Runner 完成校验、依赖装配、任务路由、执行、异常归一化和结果序列化，并返回稳定的结构化输出。首个任务类型为 `find_job_page`，复用现有 `JobPageFinder.find()` 作为领域执行器。

目标链路为：

```text
Python API / CLI
        ↓
TaskRequest 校验
        ↓
TaskRunner 路由、计时和异常归一化
        ↓
JobPageFinder.find()
        ↓
TaskResult
        ↓
Python 对象 / JSON
```

## 背景

当前 `JobPageFinder.find()` 已实现从企业 URL 到岗位页面结果的浏览器闭环，但调用方仍需自行加载环境、创建 LLM、创建 Finder、管理异步入口并序列化结果。仓库没有统一任务模型、composition root、CLI、稳定错误码或任务级运行元数据，因此不同入口容易重复装配和错误处理逻辑。

## 范围

- 定义版本化的统一任务请求，包含 `version`、`task_id`、`type` 和强类型 `payload`。
- 首版只支持 `find_job_page`，其 payload 复用 `JobPageFinderInput`。
- 定义统一任务结果，包含状态、领域输出、结构化错误和运行元数据。
- 实现单一 `TaskRunner`，负责请求校验后的路由、调用、计时和未处理异常归一化。
- 建立唯一 composition root，集中装配环境配置、LLM、Tools、Finder 和 Runner。
- 提供异步 Python API `run_task()` 和可复用的 `create_runner()`。
- 提供正式 CLI，支持 JSON 任务文件和 `find-job-page` 快捷参数，两者调用同一 Runner。
- 为 Finder 失败结果增加机器可读分类，使 Runner 不依赖错误消息字符串判断失败原因。
- 增加任务级日志上下文和耗时元数据，同时保持敏感内容和大体积页面数据不进入日志。
- 补充 Runner、装配、CLI 和本地浏览器端到端测试。
- 更新包导出、项目命令入口和 README 使用说明。

## 不在范围

- HTTP API、任务队列、后台 worker、数据库或任务历史持久化。
- 接收无结构的自然语言任务并自动推断任务类型。
- 搜索企业官网、抓取全部职位、分页、职位详情解析、筛选或排序。
- 重写现有四动作 Agent 循环，或替换为完整的 `browser-use.Agent`。
- 修改相邻的 `../browser-use` 项目。
- 解决 JF-001 中 evidence 未完整校验 DOM 的问题。
- 解决 JF-002 中多模态调用失败后未进行纯文本重试的问题。
- 为既有外部调用方增加未经确认的兼容层；现有 `JobPageFinder.find()` Python API应继续可直接使用。

## 请求与结果契约

首版请求示例：

```json
{
  "version": "v1",
  "task_id": "optional-caller-id",
  "type": "find_job_page",
  "payload": {
    "company_url": "https://example.com",
    "max_steps": 8
  }
}
```

`task_id` 缺省时由运行时生成。所有请求模型继续使用 `extra="forbid"`，未知版本、未知任务类型和非法 payload 必须在浏览器启动前失败。

成功结果示例：

```json
{
  "version": "v1",
  "task_id": "generated-or-provided-id",
  "type": "find_job_page",
  "status": "succeeded",
  "output": {
    "job_page_url": "https://example.com/careers",
    "job_title": "Senior Backend Engineer",
    "evidence": "Senior Backend Engineer",
    "steps": 3
  },
  "error": null,
  "metadata": {
    "duration_ms": 4215
  }
}
```

失败结果使用 `status="failed"`、`output=null` 和结构化 `error`。错误至少包含稳定的 `code`、人类可读 `message` 和 `retryable`。首版错误分类至少覆盖：

- `INVALID_TASK`
- `UNSUPPORTED_TASK_TYPE`
- `CONFIGURATION_ERROR`
- `BROWSER_INITIALIZATION_FAILED`
- `BROWSER_INITIALIZATION_TIMEOUT`
- `STEP_TIMEOUT`
- `MODEL_ERROR`
- `ACTION_ERROR`
- `VALIDATION_FAILED`
- `MAX_STEPS_REACHED`
- `INTERNAL_ERROR`

## 行为约束

- 顶层运行状态首版只使用 `succeeded` 和 `failed`，具体失败类别由 `error.code` 表达。
- `TaskRunner` 不操作浏览器、不构造 prompt、不解释 DOM，也不决定 Agent 动作。
- CLI、Python API 和未来入口不得各自实现 Finder 装配、错误映射或结果格式。
- 标准输出只包含最终 JSON；运行日志写入标准错误或日志系统。
- 每个任务继续创建并清理独立的 `BrowserSession`，清理失败不得覆盖主任务结果。
- `task_id` 必须贯穿运行日志和结果；日志不得包含 API key、完整 DOM、截图 Base64 或模型消息全文。
- 现有 `JobPageFinder.find()` 直接调用方式保持可用，现有成功语义不因 Runner 引入而改变。

## 安全边界

- 不读取、输出或记录 `.env` 内容及任何密钥。
- 不将用户页面内容作为运行时配置或可执行指令。
- 不扩大 Finder 已有的 `click`、`scroll`、`wait`、`done` 动作集合。
- 保持下载、PDF 自动下载、默认扩展和非 HTTP 页面等既有浏览器限制。
- 不在任务结果或日志中持久化截图、完整 DOM 或模型上下文。

## 验收标准

- `AC-001`：合法的 `v1/find_job_page` 请求可通过统一 Python API 完成执行，并返回包含原有岗位结果的统一 `TaskResult`。
- `AC-002`：缺失 `task_id` 时系统生成非空 ID；调用方提供 ID 时结果和该次运行日志保留原值。
- `AC-003`：未知版本、未知任务类型、额外字段或非法 payload 在浏览器启动前被拒绝，并得到稳定的结构化失败结果。
- `AC-004`：Finder 的浏览器初始化失败、初始化超时、步骤超时、动作/模型失败和达到最大步数均映射为稳定错误码，Runner 不解析错误消息文本来确定类别。
- `AC-005`：`create_runner()` 集中装配 LLM、Tools、Finder 和 Runner；CLI 与 `run_task()` 不重复实现该装配逻辑。
- `AC-006`：正式 `jobfinder` 命令同时支持 JSON 任务文件与 `find-job-page` 快捷参数，两种方式通过同一 Runner 输出符合统一契约的 JSON。
- `AC-007`：CLI 成功时退出码为 0；输入或任务执行失败时为非零；标准输出保持可单独解析的 JSON，诊断日志不写入标准输出。
- `AC-008`：每个任务保持独立浏览器生命周期，成功、执行失败和初始化失败路径均尝试清理浏览器，清理异常不覆盖主结果。
- `AC-009`：任务结果包含耗时元数据；任务级日志包含 `task_id`、任务类型和最终状态，但不包含密钥、完整 DOM、截图 Base64 或模型消息全文。
- `AC-010`：现有 `JobPageFinder.find()` 调用和测试继续通过，且统一入口具有 Runner、CLI 和本地 Chromium 端到端覆盖。

## 相关代码

- `src/job_page_finder/finder.py`：现有领域执行器、浏览器生命周期和失败结果来源。
- `src/job_page_finder/models.py`：现有 Finder 输入、动作和结果模型。
- `src/job_page_finder/config.py`：环境加载和 DeepSeek 客户端创建。
- `src/job_page_finder/__init__.py`：公共 Python API 导出。
- `tests/test_finder.py`：现有 Finder 行为和 fake 依赖模式。
- `tests/test_local_browser.py`：本地 Chromium 闭环测试基础。
- `pyproject.toml`：正式 CLI 命令入口。
- `README.md`：用户入口和输出契约说明。

## 外部依赖

- 继续使用 Pydantic v2 建模请求和结果。
- 继续使用本地 editable `browser-use` 依赖，不修改其源码。
- CLI 优先使用 Python 标准库实现；除非实现时出现明确需求，否则不新增 CLI 框架依赖。
- 真实执行仍依赖有效的 DeepSeek 配置和可启动的 Chromium 环境。

## 关联任务

- JF-001 定义并实现现有 Job Page Finder MVP；本任务在其外层建立应用运行链路。
- JF-002 定义条件视觉输入；本任务必须保持其当前行为，但不解决其待确认差异。

## 未决问题

- 无阻塞性未决问题。HTTP 服务、异步任务队列和完整职位处理流水线应在出现独立需求后另建任务。
