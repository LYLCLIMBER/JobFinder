# JobFinder 当前架构

## 文档定位

本文档描述 JobFinder 当前已经实现的模块边界、依赖方向、主要接口和资源生命周期。外部可观察行为、稳定协议和已知限制以 [`current-behavior.md`](current-behavior.md) 为准；代码与本文不一致时，以代码和测试事实为准并修正文档。

本架构的目标是把 Task 协议、Finder 核心、browser-use、模型 SDK、诊断文件和 CorpWeb SQLite 分隔在各自变化边界内。上层模块通过少量本地契约使用完整能力，不直接依赖下层实现细节。

## 总体结构

```text
Python caller -> api.py ---------+
                                 |
CLI ----------> cli.py ----------+
                                 v
                              runtime.py
                                 |
                                 v
                           application.py
                                 |
                                 v
                              finder.py
                              /   |   \
                             v    v    v
                    completion  ports  contracts
                                  ^
                                  |
                             adapters/*

Evaluation CLI -> evaluation/_impl.py
                    |          |
                    v          v
              application   evaluation ports
                               ^          ^
                               |          |
                     CorpWeb adapter   JSONL store
```

主要依赖规则：

- `contracts.py`、`core_models.py`、`ports.py`、`completion.py`、`finder.py` 和 `application.py` 不导入 `browser_use`。
- Finder 核心不读取环境变量，不创建模型或浏览器，不访问文件系统和 SQLite。
- `runtime.py` 是任务执行的组合根，负责连接核心接口与具体 adapters。
- 只有 browser adapter 了解 browser-use Session、DOM 私有结构、selector index、滚动事件和截图实现。
- 只有 model adapter 了解 prompt、browser-use 模型消息和结构化响应。
- 只有 diagnostics adapter 及其底层 writer 了解诊断目录、事件 JSONL、artifact、容量和保留策略。
- 只有 CorpWeb adapter 了解 CorpWeb 表、列、状态和证券分类字段。
- Evaluation Campaign 只依赖任务应用和 Evaluation ports，不了解 Finder 循环、SQLite、JSONL 或文件锁。

## 目录职责

```text
src/job_page_finder/
├── __init__.py                 # 稳定包根导出
├── api.py                      # Python 单任务便利入口
├── task_protocol.py            # Task v1 DTO、解析和结果映射
├── contracts.py                # Finder 内部请求与领域结果
├── core_models.py              # 页面观察、动作和值对象
├── ports.py                    # Browser、Model、Diagnostics 接口
├── completion.py               # 确定性完成校验
├── finder.py                   # 页面探索循环
├── application.py              # 单任务生命周期
├── settings.py                 # 运行设置模型
├── runtime.py                  # 唯一组合根
├── cli.py                      # JSONL CLI 与 Evaluation CLI
├── adapters/
│   ├── diagnostics.py
│   ├── models/browser_use_chat.py
│   └── browser_use/
│       ├── gateway.py
│       ├── observation.py
│       ├── scroll_targets.py
│       ├── scrolling.py
│       └── vision.py
└── evaluation/
    ├── contracts.py
    ├── dataset.py
    ├── campaign.py
    ├── store.py
    ├── report.py
    ├── _impl.py
    └── adapters/corpweb_sqlite.py
```

`runner.py`、`models.py`、`config.py` 和具体 `DiagnosticWriter` 仍用于子模块兼容或 adapter 实现，但不属于包根稳定 API。

## 核心契约

### Task 协议

`task_protocol.py` 定义外部 Task v1：

- `FindJobPageTaskPayload`
- `TaskRequest`
- `FindJobPageTaskOutput`
- `TaskError`
- `TaskResult`
- `parse_task()`
- `to_finder_request()`
- `build_task_result()`
- `build_task_failure()`

Task DTO 与 Finder 内部领域模型始终通过显式 mapper 隔离。只有 `TaskApplication` 调用协议编排函数；API、CLI 和 Evaluation 只提交请求或消费结果。

`parse_task()` 在完整验证前提取或生成任务身份。协议失败通过公开的 `TaskProtocolError` 携带 task ID、task type、错误码和安全消息，不要求调用者解析异常文本。

### Finder 领域契约

`contracts.py` 定义：

```python
FindJobPageRequest
JobEvidence
FindJobPageSuccess
FindJobPageFailure
FindJobPageResult = FindJobPageSuccess | FindJobPageFailure
```

成功和失败使用 `status` 判别，成功分支必须具有 URL、岗位标题、evidence 和步数，失败分支必须具有错误码、消息、retryable 和步数。

### 核心模型

`core_models.py` 提供 adapter-neutral 的稳定数据：

- `PageObservation`、`InteractiveElement`、`ScrollTarget` 和 `VisualCandidate`。
- 不透明的 `ElementRef`。
- `Click`、`Scroll`、`Wait`、`Complete` 和 `AgentAction`。
- `DecisionContext`、`ActionOutcome` 和 `ObservationOptions`。

`ElementRef.value` 的编码只由 browser adapter 解释。模型 adapter 为每次观察建立展示 index 与引用的本地映射，不反解析引用字符串。

### Ports

`ports.py` 定义核心所需的外部能力：

| Port | 使用者 | 当前实现 |
| --- | --- | --- |
| `BrowserFactory` | Finder | `BrowserUseFactory` |
| `ExplorationBrowser` | Finder | `BrowserUseSessionAdapter` |
| `ActionModel` | Finder | `BrowserUseChatActionModel` |
| `FinderEventSink` | Finder | File/Null Finder event sink |
| `RunDiagnostics` | Application | File/Null run diagnostics |
| `DiagnosticsFactory` | Application | File/Null diagnostics factory |

Ports 不包含 browser-use、文件路径、SQLite 连接或具体消息类型。

## 任务执行

一次 Python API 调用的主流程：

```text
api.run_task(request, settings)
  -> runtime.build_application(settings)
  -> TaskApplication.run(request)
       -> parse_task()
       -> DiagnosticsFactory.create()
       -> RunDiagnostics.task_started()
       -> to_finder_request()
       -> FinderProvider.get()
       -> JobPageFinder.find(..., events=run.finder_events)
       -> build_task_result()
       -> RunDiagnostics.task_finished()
  -> TaskResult
```

Runtime 使用惰性 `FinderProvider`。`build_application()` 不读取 LLM 凭据或创建浏览器；只有合法 Task 到达后才装配并缓存 Finder。失败的装配不缓存，后续任务可以重试。

错误优先级：

- Task 解析先于 LLM 和浏览器装配。
- Provider 普通异常映射为不可重试的 `CONFIGURATION_ERROR`。
- 未预期异常映射为不包含 traceback 的 `INTERNAL_ERROR`。
- `CancelledError` 不映射为普通失败。
- 诊断创建、事件、写入、结束或清理故障均为 best-effort，不改变 Task 结果。

## Finder Core

`JobPageFinder` 只编排以下概念：

```text
FindJobPageRequest
PageObservation
AgentAction
ExplorationBrowser
ActionModel
FinderEventSink
FindJobPageResult
```

步骤循环为：

```text
open -> navigate -> observe -> decide -> act/validate -> repeat -> close
```

每步使用独立 deadline。模型或动作失败、完成校验拒绝和步骤超时计入连续失败；达到上限或步骤预算后返回领域失败。

`completion.validate_completion()` 是唯一确定性完成规则。Task v1 当前验证岗位标题存在于完整可见 DOM、evidence 非空、页面可用且 URL 为 HTTP(S)。evidence 原文包含校验和通用标题黑名单不属于当前 v1 行为。

## Browser Adapter

`adapters/browser_use/gateway.py` 实现浏览器创建、导航、观察、点击、滚动、等待和关闭。它维护当前观察的 `ElementRef` 到 browser-use 节点或 selector index 的内部映射。

子模块职责：

- `observation.py` 把 browser-use state 转换为 `PageObservation`。
- `scroll_targets.py` 发现、排序并重新解析滚动目标。
- `scrolling.py` 执行滚动，验证偏移变化并观察 SPA route。
- `vision.py` 发现候选并生成内存中的标注 PNG。

`VisualSnapshot` 和 browser-use DOM 对象不离开 browser adapter。模型只看到页面观察和本次观察的展示 index。

浏览器资源所有权：

- `BrowserFactory.open()` 返回前失败或取消时，由 Factory 清理部分资源。
- `open()` 成功后，Finder 在所有退出路径调用 `close()`。
- `close()` 幂等、有界，并在必要时强制终止底层资源。
- 调用方取消优先于业务结果和 cleanup 故障。
- 没有调用方取消时，cleanup 异常、超时或自身取消不能覆盖主结果或主异常。

## Model Adapter

`BrowserUseChatActionModel` 把 `DecisionContext` 转换为模型消息，调用 `BaseChatModel`，解析结构化结果并映射为 `AgentAction`。

模型输入的 DOM 按 `BrowserSettings.max_dom_characters` 截断；`PageObservation.visible_text` 保留完整可见文本供确定性完成校验。视觉关闭时，截图和候选不会进入模型上下文，即使诊断为了落盘而请求了图像。

模型只能产生 click、scroll、wait 或 complete。未知字段、未知动作和越界等待时间由结构化 schema 拒绝。

## Diagnostics Adapter

Application 为每个任务创建独立 `RunDiagnostics`，并把其中的 Finder event sink 显式传给 Finder。任务级接口与 Finder 事件接口通过组合关联，不建立继承关系。

当前公共配置保留 `basic`、`diagnostic` 和 `raw` 三级，并允许以 `capture_screenshots` 覆盖默认截图行为。截图诊断与模型视觉相互独立。

文件 adapter 隐藏：

- manifest、事件 JSONL 和 artifact。
- 权限、容量、保留和目录清理。
- 诊断文件锁与原子写入。
- 完成和中止状态。

所有诊断调用都遵循 best-effort。诊断钩子自身产生的取消与调用方取消通过当前任务取消状态区分。

## CLI

CLI 只负责参数、JSONL 输入输出和进程退出码。`run` 对文件、`-` 和缺省 stdin 使用同一逐行协议：

```text
每个非空输入行 -> 一个 TaskRequest -> 一个 TaskResult 输出行
```

Application 在一次 CLI 运行中只构建一次。非法 JSON、非对象、非法 Task 和任务失败不会停止后续行。总退出码按 `CONFIGURATION_ERROR`、输入错误、任务失败、成功的优先级聚合。

CLI 不创建 Finder、browser-use Session、LLM 或诊断 writer，也不提供批处理领域接口。

## Evaluation

Evaluation 分为五个边界：

- `dataset.py`：数据集读取、验证、fingerprint 和通用处理。
- `adapters/corpweb_sqlite.py`：CorpWeb 查询、合法站点筛选和分层抽样。
- `campaign.py`：worker、case timeout、resume、Application 调用和逐条追加。
- `store.py`：manifest、JSONL、排他锁、尾行恢复和 campaign 完成状态。
- `report.py`：统计与 Markdown/JSON 输出。

`EvaluationCampaign` 复用一个 Application，接受任意 iterable case，在 worker 启动前进入 Store session。session 覆盖读取已完成 case、执行剩余 case、追加记录和写入 Summary 的完整临界区。

Store 的 acquisition 和 release 不阻塞 asyncio 事件循环。取得所有权与取消发生竞态时，Store 先回收已取得的文件或锁再传播取消。release 故障是次级 cleanup 故障，不能覆盖 campaign 主结果或主异常。

CorpWeb 专有字段只用于 adapter 内部筛选与抽样。当前 v1 数据集为兼容已有文件保留 `source`、`sample_bucket` 和 `max_steps`，但这些字段不会进入 Finder 或模型上下文。

Evaluation 报告中的成功率是 self-reported completion rate，不代表准确率或召回率。

## 稳定公共 API

包根只导出：

```python
run_task
TaskRequest
TaskResult
RuntimeSettings
DiagnosticsSettings
```

`JobPageFinder`、`TaskApplication`、具体 adapters、`TaskRunner`、`RuntimeConfig`、`create_runner()` 和 `DiagnosticWriter` 可以从子模块用于内部集成或兼容，但不属于包根跨版本承诺。

## 已批准的兼容决策

以下决策定义当前 v1，不应在未升级协议或格式时静默改变：

| 编号 | 当前决策 |
| --- | --- |
| `DG-01` | Task v1 继续使用 `metadata.duration_ms`；顶层 duration 需要新协议版本。 |
| `DG-02` | 只确定性验证 `job_title` 出现在可见 DOM；evidence 仅要求非空，通用标题仍由 prompt 约束。 |
| `DG-03` | 公共诊断配置继续使用 `basic`、`diagnostic`、`raw` 三级，并保留显式截图覆盖。 |
| `DG-04` | CLI `run` 使用 JSONL，支持文件和 stdin；逐行错误后继续。总退出码优先级为配置错误 3、输入错误 2、任务失败 1、成功 0。 |
| `DG-05` | 包根稳定承诺仅为 `run_task`、Task DTO 和 Runtime/Diagnostics 设置。 |
| `DG-06` | Evaluation v1 保留现有 dataset/manifest、`source`、`sample_bucket`、`max_steps` 和 CorpWeb 分层抽样格式。 |

## 测试边界

- `tests/contracts/` 保护 Task v1、内部模型、Ports 和依赖方向。
- `tests/core/` 使用 Fake Ports 保护 Finder 循环和完成校验。
- `tests/adapters/` 保护 browser-use 状态转换、资源生命周期、滚动、视觉和模型消息映射。
- 顶层测试保护 API、Application、CLI、Diagnostics、Evaluation 和兼容入口。
- 标记为 `integration` 的测试使用本地 Chromium 验证真实点击和 CSS Scroll Snap 链路。

标准门禁：

```bash
git diff --check
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv run pytest -q -m integration
```
