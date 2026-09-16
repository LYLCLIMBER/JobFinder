# JobFinder 模块化重构设计

## 文档定位

本文档描述 JobFinder 的建议目标架构、模块职责和模块间接口，目标是减少跨模块信息泄露，隔离 browser-use、LLM、诊断文件和 CorpWeb SQLite 等易变实现，并形成接口简单、隐藏复杂度的深模块。

本文档是未来重构提案，不描述已经实现的结构，也不表示重构已经开始。当前实现和稳定行为以 [`current-behavior.md`](current-behavior.md) 为准，现有测试的风险保障以 [`test-protection-map.md`](test-protection-map.md) 为准。

## 设计原则

模块划分围绕以下变化边界进行，而不是按类或函数数量机械拆分：

- 任务协议边界：JSON、Pydantic、协议版本和错误码。
- 核心用例边界：寻找招聘页面的步骤循环和完成条件。
- 浏览器边界：browser-use、DOM、元素引用、滚动和截图。
- 模型边界：prompt、模型消息类型和结构化输出解析。
- 诊断边界：事件、脱敏、文件格式、容量限制和保留策略。
- 评估边界：数据集、并发执行、持久化、报告和 CorpWeb 数据源。

主要依赖规则：

```text
finder.py 不得导入 browser_use
application.py 不得导入 browser_use
completion.py 不得导入 browser_use
contracts.py 不得导入 browser_use
core_models.py 不得导入 browser_use
```

只有 `adapters/` 和组合根 `runtime.py` 可以了解具体使用了 browser-use、DeepSeek、文件系统和 SQLite。

## 总体调用关系

```text
Python caller -> api.py ---------+
                                |
cli.py -------------------------+
                                v
runtime.py ----------------------+
  |                              |
  | 创建                         | 创建具体适配器
  v                              v
application.py              adapters/*
  |
  v
finder.py
  |
  +--> completion.py
  |
  +--> ports.py <-------------- adapters/*
          |
          v
      core_models.py

task_protocol.py
  ^
  |
application.py

evaluation/*
  |
  +--> runtime.py / application.py
  +--> evaluation/adapters/corpweb_sqlite.py
```

目标依赖方向为：

```text
Python API
  -> Public API
      -> Runtime

CLI run
  -> Runtime

Runtime
  -> Task Application
      -> Finder Core
          -> Ports
              <- Browser/LLM/Diagnostics Adapters

Evaluation
  -> Task Application
  -> Evaluation Ports
      <- CorpWeb SQLite Adapter
```

## 建议目录

```text
src/job_page_finder/
├── __init__.py
├── api.py
├── contracts.py
├── core_models.py
├── ports.py
├── finder.py
├── completion.py
├── task_protocol.py
├── application.py
├── settings.py
├── runtime.py
├── cli.py
│
├── adapters/
│   ├── __init__.py
│   ├── diagnostics.py
│   ├── models/
│   │   ├── __init__.py
│   │   └── browser_use_chat.py
│   └── browser_use/
│       ├── __init__.py
│       ├── gateway.py
│       ├── observation.py
│       ├── scroll_targets.py
│       ├── scrolling.py
│       └── vision.py
│
└── evaluation/
    ├── __init__.py
    ├── contracts.py
    ├── dataset.py
    ├── campaign.py
    ├── store.py
    ├── report.py
    └── adapters/
        ├── __init__.py
        └── corpweb_sqlite.py
```

该目录是目标结构，不要求第一步就创建全部文件。迁移期间可以先保持扁平目录，只要依赖方向和接口边界已经形成。

## 核心模块

### `contracts.py`

#### 作用

定义 Finder 核心内部使用的业务输入、成功结果和失败结果。它们是 Application、Finder 和 Task 协议映射之间的内部契约，不属于包根 Public API。

#### 不负责

- 不定义 browser-use 类型。
- 不定义模型动作。
- 不处理 Task v1 envelope。
- 不负责执行逻辑。
- 不访问文件或数据库。

#### 提供给其他模块的接口

```python
class FindJobPageRequest(BaseModel):
    company_url: HttpUrl
    max_steps: int = 8


class JobEvidence(BaseModel):
    quote: str
    source_url: HttpUrl


class FindJobPageSuccess(BaseModel):
    status: Literal["succeeded"]
    job_page_url: HttpUrl
    job_title: str
    evidence: JobEvidence
    steps: int


class FindJobPageFailure(BaseModel):
    status: Literal["failed"]
    code: FinderFailureCode
    message: str
    retryable: bool
    steps: int


FindJobPageResult = FindJobPageSuccess | FindJobPageFailure
```

建议使用成功/失败判别联合替代当前 `success: bool` 加多个可空字段的结果结构，从类型上排除“成功但缺少岗位字段”等无效状态。

`task_protocol.py` 负责将这些内部领域结果映射为对外承诺的 Task v1 JSON。领域类型可以随内部设计演进，不形成包根兼容承诺。

#### 调用者

- `finder.py`
- `completion.py`
- `task_protocol.py`

#### 依赖

只依赖 Pydantic 和标准库类型。

### `core_models.py`

#### 作用

定义 Finder 核心、浏览器适配器和模型适配器之间使用的内部稳定数据结构。

#### 不负责

- 不执行浏览器操作。
- 不调用模型。
- 不构建 prompt。
- 不做完成验证。
- 不包含 browser-use 对象。

#### 提供给其他模块的接口

页面状态模型：

```python
@dataclass(frozen=True)
class ElementRef:
    value: str


@dataclass(frozen=True)
class InteractiveElement:
    ref: ElementRef
    text: str
    role: str | None
    href: str | None


@dataclass(frozen=True)
class ScrollTarget:
    ref: ElementRef | None
    description: str
    can_scroll_up: bool
    can_scroll_down: bool


@dataclass(frozen=True)
class VisualCandidate:
    ref: ElementRef
    box: tuple[int, int, int, int]


@dataclass(frozen=True)
class PageObservation:
    url: str
    title: str
    visible_text: str
    elements: tuple[InteractiveElement, ...]
    scroll_targets: tuple[ScrollTarget, ...]
    screenshot: bytes | None
    visual_candidates: tuple[VisualCandidate, ...]
```

动作模型：

```python
@dataclass(frozen=True)
class Click:
    element: ElementRef


@dataclass(frozen=True)
class Scroll:
    direction: Literal["up", "down"]
    target: ElementRef | None = None


@dataclass(frozen=True)
class Wait:
    seconds: int


@dataclass(frozen=True)
class Complete:
    job_title: str
    evidence_quote: str


AgentAction = Click | Scroll | Wait | Complete
```

决策上下文和动作结果：

```python
@dataclass(frozen=True)
class DecisionContext:
    observation: PageObservation
    previous_outcome: str
    step: int
    max_steps: int


@dataclass(frozen=True)
class ActionOutcome:
    changed: bool
    current_url: str
    description: str


@dataclass(frozen=True)
class ObservationOptions:
    include_image: bool = False
```

`ElementRef` 是不透明标识符。它可以在 browser-use 适配器内部映射到 selector index 或 `backend_node_id`，但核心代码不应了解映射方式。`VisualCandidate` 是 adapter-neutral 的诊断和模型输入事实，只表达候选引用及截图坐标；browser-use adapter 内部的 `VisualSnapshot` 不得离开 adapter。

#### 调用者

- `ports.py`
- `finder.py`
- `completion.py`
- `adapters/models/browser_use_chat.py`
- `adapters/browser_use/*`
- `adapters/diagnostics.py`

#### 依赖

只依赖 Python 标准库。

### `ports.py`

#### 作用

定义核心业务需要的外部能力，只表达能力，不提供具体实现。

相关 Protocol 数量较少且共同服务于 Finder，应放在一个模块中，避免为每个十几行的接口创建浅模块。

#### 不负责

- 不创建 browser-use Session。
- 不构造模型客户端。
- 不写诊断文件。
- 不包含步骤循环。

#### 提供给其他模块的接口

浏览器接口：

```python
class ExplorationBrowser(Protocol):
    async def navigate(self, url: str) -> None:
        ...

    async def observe(
        self,
        options: ObservationOptions = ObservationOptions(),
    ) -> PageObservation:
        ...

    async def click(
        self,
        element: ElementRef,
    ) -> ActionOutcome:
        ...

    async def scroll(
        self,
        *,
        target: ElementRef | None,
        direction: Literal["up", "down"],
    ) -> ActionOutcome:
        ...

    async def wait(self, seconds: int) -> ActionOutcome:
        ...

    async def close(self) -> None:
        ...


class BrowserFactory(Protocol):
    async def open(self) -> ExplorationBrowser:
        ...
```

`ExplorationBrowser` 表达 JobFinder 所需的页面探索能力，避免与 browser-use 自身的 `BrowserSession` 类型混淆。`observe()` 表达“观察当前页面”的业务语义；是否需要图像通过本地 `ObservationOptions` 描述，而不是暴露具体 SDK 的截图参数。

浏览器端口采用简单的 `open() + close()` 生命周期，不额外引入 Provider 或异步上下文管理器，但必须满足以下强制契约：

- `BrowserFactory.open()` 成功返回时，浏览器已经可用；此时资源所有权转交给 Finder。
- `open()` 在部分初始化后发生异常或取消时，由 Factory 清理已经创建的 Session 和浏览器进程，不得返回半初始化对象。
- `open()` 成功后，无论 Finder 正常返回、抛出异常还是被取消，Finder 都必须在 `finally` 中调用 `close()`。
- `close()` 应当幂等，并负责清理页面、Session 和浏览器进程。
- `close()` 必须具有关闭超时和最终强制终止策略，不能无限等待。
- 调用方取消具有最高优先级；没有调用方取消时，主执行异常或成功结果都高于 cleanup 异常、超时或自身取消。cleanup 故障只作为次级故障写入日志或诊断。
- 任务取消时必须保护已启动的清理不被同一次取消中断，清理结束或达到关闭上限后重新抛出原始 `CancelledError`。
- 必须区分调用方取消与 cleanup Task 自身取消。前者是最高优先级控制信号；后者是 cleanup 故障，不能被传播成整个任务被调用方取消。

资源责任边界为：

```text
open() 返回之前失败或取消
    -> BrowserFactory 清理部分创建的资源

open() 成功返回之后
    -> JobPageFinder 在 finally 中调用 ExplorationBrowser.close()

close() 内部
    -> browser adapter 负责关闭超时、取消保护和强制终止细节
```

Finder 的生命周期结构应保持为：

```python
browser: ExplorationBrowser | None = None

try:
    browser = await browser_factory.open()
    await browser.navigate(str(request.company_url))
    return await self._run_steps(browser, request, events)
finally:
    if browser is not None:
        await close_browser_safely(browser)
```

异常优先级必须满足：

| 主执行状态 | cleanup 状态 | 对外结果 |
| --- | --- | --- |
| 成功返回 Finder 结果 | 成功 | 返回原 Finder 结果 |
| 成功返回 Finder 结果 | 异常、超时或自身取消 | 记录 cleanup 故障，仍返回原 Finder 结果 |
| 抛出业务或基础设施异常 | 成功 | 保留原异常，由上层分类或映射 |
| 抛出业务或基础设施异常 | 异常、超时或自身取消 | 原异常保持主异常，cleanup 故障只记录为次级诊断 |
| 抛出 `CancelledError` | 成功 | 清理后重新抛出原 `CancelledError` |
| 抛出 `CancelledError` | 异常、超时或自身取消 | 记录 cleanup 故障，仍重新抛出原 `CancelledError` |
| 任意非取消主状态 | cleanup 期间收到调用方取消 | 取消升级为最高优先级；清理后传播 `CancelledError`，原结果或异常只保留为诊断上下文 |

`close_browser_safely()` 必须吸收并记录普通 cleanup 异常、超时和自身取消，使它们不能从 `finally` 覆盖主结果或主异常；但调用方取消不能被吸收。该契约不规定具体 asyncio 算法：实现可以使用 `asyncio.shield()`、独立 cleanup Task 或其他机制，但不能仅根据捕获到 `CancelledError` 就判断取消来源，必须可靠地区分调用方取消与 cleanup Task 自身取消。

`ExplorationBrowser.close()` 自身负责有界等待和强制终止，因此 cleanup 最终必须结束或达到明确的关闭上限。若调用方取消和 cleanup 自身取消同时发生，调用方取消优先。Factory 在 `open()` 尚未成功返回时遵循同一优先级：初始化期间收到调用方取消时取消最高；否则初始化异常为主故障；部分资源清理的异常、超时或自身取消始终只作为次级诊断。

模型接口：

```python
class ActionModel(Protocol):
    async def decide(
        self,
        context: DecisionContext,
    ) -> AgentAction:
        ...
```

Finder 事件接收接口：

```python
class FinderEventSink(Protocol):
    @property
    def requires_image(self) -> bool:
        ...

    def step_started(self, step: int) -> None:
        ...

    def page_observed(
        self,
        step: int,
        observation: PageObservation,
    ) -> None:
        ...

    def decision_made(
        self,
        step: int,
        action: AgentAction,
    ) -> None:
        ...

    def action_finished(
        self,
        step: int,
        outcome: ActionOutcome,
    ) -> None:
        ...
```

任务级诊断通过组合提供 Finder 事件接收器，两种接口不建立继承关系：

```python
class RunDiagnostics(Protocol):
    @property
    def finder_events(self) -> FinderEventSink:
        ...

    def task_started(self) -> None:
        ...

    def task_finished(self, result: TaskResult) -> None:
        ...

    def abort(self) -> None:
        ...


class DiagnosticsFactory(Protocol):
    def create(
        self,
        *,
        task_id: str,
        task_type: str,
    ) -> RunDiagnostics:
        ...
```

Application 为每次任务创建独立的 `RunDiagnostics`，并将其中的 `finder_events` 显式传给 Finder。Finder 使用 `settings.use_vision or events.requires_image` 决定 `ObservationOptions.include_image`，因此模型视觉和诊断截图彼此独立，也不会在并发任务之间共享事件接收器状态。

#### 接口实现者

| 接口 | 实现模块 |
| --- | --- |
| `BrowserFactory` | `adapters/browser_use/gateway.py` |
| `ExplorationBrowser` | `adapters/browser_use/gateway.py` |
| `ActionModel` | `adapters/models/browser_use_chat.py` |
| `FinderEventSink`、`RunDiagnostics` | `adapters/diagnostics.py` |
| `DiagnosticsFactory` | `adapters/diagnostics.py` |

#### 调用者

- `finder.py` 使用浏览器、模型和 Finder 事件接收接口。
- `application.py` 使用 `DiagnosticsFactory` 和 `RunDiagnostics`。

### `completion.py`

#### 作用

判断模型报告的职位是否真的能够被当前页面证明。这是领域规则，不是浏览器规则。

#### 不负责

- 不调用浏览器。
- 不调用模型。
- 不读取 DOM 私有结构。
- 不记录诊断文件。
- 不管理步骤循环。

#### 提供给其他模块的接口

```python
def validate_completion(
    action: Complete,
    observation: PageObservation,
    *,
    step: int,
) -> FindJobPageResult:
    ...
```

该函数至少验证：

- `job_title` 非空且出现在 `observation.visible_text`。
- `evidence_quote` 非空且出现在 `observation.visible_text`。
- 当前 URL 是合法 HTTP(S) URL。
- 标题不是单独的 `Careers`、`Jobs`、`Join Us` 等泛化文本。

成功时返回 `FindJobPageSuccess`，失败时返回错误码为 `VALIDATION_FAILED` 的 `FindJobPageFailure`。

当前只有一套完成规则，不建议引入 Validator Factory 或 Strategy 层次；一个纯函数是更简单且足够深的接口。

#### 调用者

只有 `finder.py`。

#### 依赖

- `contracts.py`
- `core_models.py`

### `finder.py`

#### 作用

实现“寻找招聘页面”这一完整核心用例。

#### 负责

- 打开和关闭浏览器。
- 导航到企业官网。
- 执行步骤循环。
- 获取 `PageObservation`。
- 请求模型选择动作。
- 调用浏览器执行动作。
- 管理连续失败次数和步骤 timeout。
- 调用完成条件验证。
- 返回领域结果。

#### 不负责

- 不解析 Task v1。
- 不构造 DeepSeek 客户端。
- 不构造 browser-use 消息。
- 不访问 browser-use DOM。
- 不写具体诊断文件。
- 不读环境变量。
- 不了解 CLI 参数。
- 不读取 CorpWeb 数据库。

#### 提供给其他模块的接口

```python
class JobPageFinder:
    def __init__(
        self,
        browser_factory: BrowserFactory,
        action_model: ActionModel,
        *,
        settings: FinderSettings,
    ) -> None:
        ...

    async def find(
        self,
        request: FindJobPageRequest,
        *,
        events: FinderEventSink,
    ) -> FindJobPageResult:
        ...
```

建议只保留少量紧密相关的内部方法：

```python
async def _run_steps(
    self,
    browser: ExplorationBrowser,
    request: FindJobPageRequest,
    events: FinderEventSink,
) -> FindJobPageResult:
    ...


async def _perform_action(
    self,
    browser: ExplorationBrowser,
    action: AgentAction,
) -> ActionOutcome:
    ...


def _failure(
    self,
    code: FinderFailureCode,
    message: str,
    *,
    steps: int,
) -> FindJobPageFailure:
    ...
```

Click、Scroll 和 Wait 不需要分别建立 Handler 类；这些分支与步骤循环紧密相关，拆开只会增加接口数量。

#### 调用者

- `application.py`
- 核心测试

#### 依赖

- `contracts.py`
- `core_models.py`
- `ports.py`
- `completion.py`
- `settings.py`

## 任务协议与应用层

### `task_protocol.py`

#### 作用

定义当前 `version=v1` 的外部任务 envelope。这是任务系统协议，不是 Finder 领域模型。

#### 不负责

- 不运行 Finder。
- 不创建诊断对象。
- 不创建浏览器。
- 不处理任务生命周期。
- 不导入 browser-use。

#### 提供给其他模块的接口

```python
class FindJobPageTaskPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_url: HttpUrl
    max_steps: int = Field(default=8, ge=1, le=50)


class TaskRequest(BaseModel):
    version: Literal["v1"]
    task_id: str | None
    type: Literal["find_job_page"]
    payload: FindJobPageTaskPayload


class TaskError(BaseModel):
    code: TaskErrorCode
    message: str
    retryable: bool


class FindJobPageTaskOutput(BaseModel):
    job_page_url: str
    job_title: str
    evidence: str
    steps: int


class TaskResult(BaseModel):
    version: Literal["v1"]
    task_id: str
    type: str
    status: Literal["succeeded", "failed"]
    output: FindJobPageTaskOutput | None
    error: TaskError | None
    duration_ms: int
```

`duration_ms` 直接属于任务结果。没有独立兼容需求时，不为它引入只有一个字段且不隐藏复杂度的包装模型。

提供四个协议函数：

```python
def parse_task(
    raw: TaskRequest | Mapping[str, object],
) -> TaskRequest:
    """提取任务身份，并验证版本、类型和 payload。"""


def to_finder_request(
    payload: FindJobPageTaskPayload,
) -> FindJobPageRequest:
    """把稳定的 Task v1 payload 映射为内部 Finder 请求。"""


def build_task_result(
    request: TaskRequest,
    result: FindJobPageResult,
    *,
    duration_ms: int,
) -> TaskResult:
    """把 Finder 领域结果映射为 Task v1 结果。"""


def build_task_failure(
    *,
    task_id: str,
    task_type: str,
    code: TaskErrorCode,
    message: str,
    duration_ms: int,
) -> TaskResult:
    """把协议、配置或未预期故障映射为 Task v1 失败结果。"""
```

Task v1 的 `FindJobPageTaskPayload` 和 `FindJobPageTaskOutput` 属于外部协议模型；内部 `FindJobPageRequest` 和 `FindJobPageResult` 不直接出现在公共 Task schema 中。即使当前字段相同，也必须通过显式映射隔离外部协议兼容性和内部领域演进。

`build_task_failure()` 是 `INVALID_TASK`、`UNSUPPORTED_TASK_TYPE`、`CONFIGURATION_ERROR` 和 `INTERNAL_ERROR` 等应用级失败的唯一 TaskResult 构造入口。错误码到 `retryable` 的映射集中在 `task_protocol.py`；`CONFIGURATION_ERROR` 必须为不可重试。Application 不自行拼装 Task v1 envelope。

协议异常使用公开类型，而不是跨模块导入私有异常：

```python
class TaskProtocolError(ValueError):
    code: Literal["INVALID_TASK", "UNSUPPORTED_TASK_TYPE"]
    task_id: str
    task_type: str
    public_message: str
```

`parse_task()` 在完整验证前通过模块私有辅助函数提取或生成任务身份。解析失败时，`TaskProtocolError` 携带构造失败结果和诊断上下文所需的 `task_id`、`task_type`，因此其他模块不需要单独调用一次身份识别接口：

```python
def _extract_identity(raw: object) -> tuple[str, str]:
    ...
```

`_extract_identity()` 只供 `parse_task()` 使用，不属于模块公开接口。

#### 调用者

- 只有 `application.py` 调用 `parse_task()`、`to_finder_request()`、`build_task_result()` 和 `build_task_failure()`。
- `api.py`、`cli.py` 和外部任务调用者只使用 Task DTO，不直接调用协议编排函数。

#### 依赖

- `contracts.py`
- Pydantic
- 标准库

### `application.py`

#### 作用

管理一次完整 Task 调用的应用级生命周期，替代当前职责重叠的 `TaskRunner`。

#### 负责

- 通过 `parse_task()` 解析 TaskRequest；解析失败时从 `TaskProtocolError` 取得任务身份。
- 通过 `DiagnosticsFactory` 创建本次任务独有的 `RunDiagnostics`。
- 通过 `to_finder_request()` 将 Task v1 payload 转换为内部 Finder 请求。
- 只在请求成功解析后，通过 `FinderProvider` 取得已装配的 Finder。
- 将该记录器的 `finder_events` 作为 `FinderEventSink` 传给 `JobPageFinder.find()`。
- 计算运行时间。
- 将领域结果转换为 TaskResult。
- 通过 `build_task_failure()` 将最后一道未预期异常转换为 `INTERNAL_ERROR`。
- 通过 `build_task_failure()` 将 `FinderProvider.get()` 的装配异常转换为不可重试的 `CONFIGURATION_ERROR`。
- 完成或中止诊断记录。
- 记录 task started/finished。

#### 不负责

- 不创建具体浏览器。
- 不创建具体 LLM。
- 不构建 prompt。
- 不实现页面探索步骤。
- 不处理 CLI 输出。

#### 提供给其他模块的接口

```python
class FinderProvider(Protocol):
    def get(self) -> JobPageFinder:
        ...


class TaskApplication:
    def __init__(
        self,
        finder_provider: FinderProvider,
        diagnostics_factory: DiagnosticsFactory,
    ) -> None:
        ...

    async def run(
        self,
        request: TaskRequest | Mapping[str, object],
    ) -> TaskResult:
        ...
```

`FinderProvider` 是为满足错误优先级而存在的惰性装配边界，不是通用 Service Locator。`TaskApplication.run()` 必须先调用 `parse_task()`；只有解析成功后才能调用 `finder_provider.get()`。Provider 在首次成功装配后复用同一个 `JobPageFinder`，使批量调用不会重复创建模型和 adapters；装配失败可以由后续任务重试，不缓存失败结果。

`FinderProvider.get()` 抛出的普通异常按明确来源映射为 `CONFIGURATION_ERROR`，调用方取消仍直接传播。这样非法 Task 不会读取 LLM 配置或创建 browser-use 依赖，同时 API、CLI 和 Evaluation 都不需要重复解析 Task。

如果确实需要稳定测试 duration，可以额外注入 `Clock`；在没有具体需求前，直接使用 `time.perf_counter()` 更简单。

#### 调用者

- `api.py`
- `evaluation/campaign.py`
- 应用层测试

#### 依赖

- `finder.py`
- `task_protocol.py`
- `contracts.py`
- 诊断工厂接口
- 标准库

## 配置与组合

### `settings.py`

#### 作用

定义运行参数，不读取环境变量，也不创建具体对象。

#### 提供给其他模块的接口

```python
class FinderSettings(BaseModel):
    step_timeout: float = 30
    startup_timeout: float = 30
    max_consecutive_failures: int = 2
    use_vision: bool = True


class BrowserSettings(BaseModel):
    max_dom_characters: int = 40_000
    max_visual_candidates: int = 20
    scroll_route_timeout: float = 2.0
    scroll_route_poll_interval: float = 0.25


class DiagnosticsStoragePolicy(BaseModel):
    max_runs: int = 100
    retention_days: int = 7
    max_run_bytes: int = 256 * 1024 * 1024
    max_total_bytes: int = 5 * 1024 * 1024 * 1024


class DiagnosticsSettings(BaseModel):
    enabled: bool = True
    root: Path = Path("log/diagnostics")
    save_screenshots: bool = True
    include_raw: bool = False
    storage: DiagnosticsStoragePolicy = Field(default_factory=DiagnosticsStoragePolicy)


class RuntimeSettings(BaseModel):
    finder: FinderSettings = FinderSettings()
    browser: BrowserSettings = BrowserSettings()
    diagnostics: DiagnosticsSettings = DiagnosticsSettings()
```

诊断不再使用 `basic`、`diagnostic`、`raw` 级别，而是由 `enabled` 和 `include_raw` 两个直接开关表达行为：

| 配置 | 行为 |
| --- | --- |
| `enabled=False` | 不创建诊断运行或产物 |
| `enabled=True, include_raw=False` | 保存默认诊断内容和截图 |
| `enabled=True, include_raw=True` | 在默认诊断基础上增加高体积原始材料 |

默认诊断保存 manifest、任务和步骤事件、最终结果、当前 URL 和标题、截断后的页面文本、模型决策、动作结果、页面截图、标注截图、视觉候选，以及经过安全处理的模型输入和输出。`save_screenshots=False` 只关闭诊断截图落盘，不改变模型是否使用视觉输入。

`include_raw=True` 额外保存未截断 DOM、允许记录的更多 SDK 页面元数据和动作后的额外原始观察。即使启用 raw，也不得记录 API key、Authorization、cookies、环境变量、请求 headers 或隐藏推理字段。

四项存储限制收进 `DiagnosticsStoragePolicy`，避免占据常用配置的主层级。普通调用者只需要使用 `DiagnosticsSettings()`；需要调整容量和保留策略时才设置 `storage`。

#### 调用者

- `runtime.py`
- `finder.py`
- browser adapter
- diagnostics adapter
- `cli.py`

#### 依赖

- Pydantic
- `pathlib.Path`

### `runtime.py`

#### 作用

作为组合根，将核心接口与具体实现连接起来。

#### 负责

- 加载必要环境配置。
- 创建惰性 `FinderProvider`，由它在首个合法任务到达后创建并缓存默认 DeepSeek 模型、`BrowserUseFactory`、`BrowserUseChatActionModel` 和 `JobPageFinder`。
- 创建诊断工厂。
- 创建 `TaskApplication`。

#### 不负责

- 不解析 TaskRequest。
- 不运行任务生命周期。
- 不构造 TaskResult。
- 不捕获业务异常。
- 不记录 task started/finished。

#### 提供给其他模块的接口

```python
def build_application(
    settings: RuntimeSettings | None = None,
    *,
    browser_factory: BrowserFactory | None = None,
    action_model: ActionModel | None = None,
    diagnostics_factory: DiagnosticsFactory | None = None,
    env_file: str | Path | None = None,
) -> TaskApplication:
    ...
```

Runtime 只提供这一条标准装配路径，是唯一允许同时依赖核心模块、具体 adapter 和环境配置的模块。`build_application()` 本身不得读取 LLM 凭据或创建 Finder 的具体依赖；这些可能失败的操作封装在 Runtime 内部的 `FinderProvider` 中，并由 `TaskApplication.run()` 在 Task 解析成功后触发。Provider 只缓存成功结果，不缓存装配异常。

#### 调用者

- `api.py`
- `cli.py`
- 高级 Python 调用者
- 集成测试

### `api.py`

#### 作用

提供单一、稳定的 Task 协议公共入口。

#### 提供给其他模块的接口

Task 协议入口：

```python
async def run_task(
    request: TaskRequest | Mapping[str, object],
    *,
    settings: RuntimeSettings | None = None,
) -> TaskResult:
    application = build_application(settings)
    return await application.run(request)
```

`api.run_task()` 不调用 `parse_task()`，也不构造协议失败结果。Task 解析、身份提取以及无效请求和配置错误的优先级统一由 `TaskApplication.run()` 负责。

该便利函数面向 Python 单次调用。批量调用者应复用 `build_application()` 的结果，避免每个任务重新装配 Application。

#### 调用者

- 外部 Python 用户
- 集成测试

#### 依赖

- `task_protocol.py`
- `runtime.py`
- `settings.py`

### `__init__.py`

#### 作用

定义包根的稳定公共 API。

#### 推荐导出

```python
from .api import run_task
from .settings import DiagnosticsSettings, RuntimeSettings
from .task_protocol import TaskRequest, TaskResult
```

包根只承诺 `run_task()`、Task v1 请求和结果，以及 Runtime/Diagnostics 设置。`FindJobPageRequest`、`FindJobPageResult`、`FindJobPageSuccess`、`FindJobPageFailure` 和 `JobEvidence` 保留为内部类型，不从包根导出。诊断相关的包根公共接口只有 `DiagnosticsSettings`，调用者不需要了解诊断 writer、factory、run 或 Finder event sink。

#### 不推荐从包根导出

- `JobPageFinder`
- `TaskApplication`
- `BrowserUseFactory`
- `BrowserUseChatActionModel`
- `DiagnosticWriter`
- `create_deepseek_llm`
- `load_environment`

这些类型仍然可以从具体子模块导入，但不作为包根的稳定兼容承诺。

## 具体适配器

### `adapters/browser_use/gateway.py`

#### 作用

实现 `BrowserFactory` 和 `ExplorationBrowser`，是核心代码与 browser-use 浏览器能力之间的唯一主要入口。

#### 负责

- 创建和配置 browser-use `BrowserSession`。
- 启动和关闭浏览器。
- 在启动异常或取消时清理部分创建的资源。
- 实现幂等、有超时、取消安全并带强制终止 fallback 的关闭操作。
- 导航、点击、滚动和等待。
- 获取 browser-use state。
- 调用 `observation.py` 构建本地页面观察。
- 将 browser-use 异常转换为本地异常。

#### 提供给其他模块的接口

```python
class BrowserUseFactory:
    def __init__(
        self,
        *,
        settings: BrowserSettings,
        browser_factory: Callable[[], browser_use.BrowserSession] | None = None,
        tools: browser_use.Tools | None = None,
    ) -> None:
        ...

    async def open(self) -> ExplorationBrowser:
        ...
```

`BrowserUseSessionAdapter` 作为实现细节实现 `ports.ExplorationBrowser`：

```python
class BrowserUseSessionAdapter:
    async def navigate(self, url: str) -> None: ...
    async def observe(self, options: ObservationOptions = ObservationOptions()) -> PageObservation: ...
    async def click(self, element: ElementRef) -> ActionOutcome: ...
    async def scroll(...) -> ActionOutcome: ...
    async def wait(self, seconds: int) -> ActionOutcome: ...
    async def close(self) -> None: ...
```

#### 调用者

- `runtime.py` 创建 `BrowserUseFactory`。
- `finder.py` 仅通过 `BrowserFactory` 接口间接调用。

#### 依赖

- browser-use
- `ports.py`
- `core_models.py`
- `settings.py`
- 同目录的 `observation.py`
- 同目录的 `scroll_targets.py`
- 同目录的 `scrolling.py`

### `adapters/browser_use/observation.py`

#### 作用

将 browser-use 页面状态转换为本地 `PageObservation`。

#### 负责

- 提取 URL、标题和可见文本。
- 截断过长 DOM 文本。
- 提取交互元素。
- 为元素创建不透明 `ElementRef`。
- 建立 `ElementRef` 到 browser-use node/index 的内部映射。
- 调用 `scroll_targets.py` 分析滚动目标。
- 调用 `vision.py` 构建可选视觉观察。

#### 提供给 adapter 内部的接口

```python
@dataclass
class ObservationConversion:
    observation: PageObservation
    element_map: dict[ElementRef, BrowserUseElement]
    scroll_index: ScrollTargetIndex


def build_observation(
    state: BrowserUseState,
    *,
    settings: BrowserSettings,
    include_image: bool,
) -> ObservationConversion:
    ...
```

`ObservationConversion` 不得离开 browser-use adapter。核心模块只接收 `conversion.observation`。

`observation.py` 将 adapter 内部 `VisualSnapshot.screenshot` 映射到 `PageObservation.screenshot`，并将 `candidate_boxes` 映射为 `PageObservation.visual_candidates`。因此模型和诊断都能使用同一次视觉观察，而 `VisualSnapshot` 本身仍只存在于 `vision.py` 和 `observation.py` 之间。

#### 调用者

只有 `gateway.py`。

### `adapters/browser_use/scroll_targets.py`

#### 作用

分析 browser-use 页面状态中的滚动能力，维护当前观察内的滚动目标引用，并在执行动作前根据最新页面状态重新解析目标。该模块回答“哪里可以滚动”，不执行滚动动作。

#### 负责

- 遍历 browser-use DOM 并识别可见的可滚动容器。
- 计算根页面和内部容器的上下剩余滚动距离。
- 识别活动弹窗内的滚动区域。
- 为滚动目标分配 `ElementRef`。
- 将内部目标转换为 `PageObservation.scroll_targets`。
- 在新页面状态中重新解析目标，避免直接复用旧 DOM 节点。

#### 提供给 adapter 内部的接口

```python
@dataclass
class ScrollTargetIndex:
    public_targets: tuple[ScrollTarget, ...]
    bindings: dict[ElementRef, BrowserUseScrollTarget]


def discover_scroll_targets(
    state: BrowserUseState,
) -> ScrollTargetIndex:
    ...


def resolve_scroll_target(
    state: BrowserUseState,
    ref: ElementRef,
) -> BrowserUseScrollTarget | None:
    ...
```

#### 调用者

- `observation.py` 调用 `discover_scroll_targets()` 构建页面观察。
- `gateway.py` 调用 `resolve_scroll_target()`，再把有效目标交给滚动执行模块。

Finder、Application、API 和 Completion 不得调用该模块。

### `adapters/browser_use/scrolling.py`

#### 作用

执行 browser-use 滚动动作并验证动作结果。该模块回答“如何滚动以及滚动是否生效”，不负责发现目标。

#### 负责

- 执行根页面或内部容器滚动。
- 比较滚动前后的实际 offset。
- 检测滚动触发的 SPA 路由变化。
- 管理 route polling 的时间边界。
- 将执行结果转换为本地 `ActionOutcome`。

#### 提供给 adapter 内部的接口

```python
async def perform_scroll(
    browser: BrowserUseBrowserSession,
    target: BrowserUseScrollTarget,
    direction: Literal["up", "down"],
) -> ActionOutcome:
    ...
```

#### 调用者

- 只有 `gateway.py`。Gateway 先通过 `scroll_targets.py` 解析目标，再调用 `perform_scroll()`。

Finder、Application、API 和 Completion 不得调用该模块。

### `adapters/browser_use/vision.py`

#### 作用

封装视觉候选发现和截图标注，由当前 `vision.py` 迁移。

#### 提供给 adapter 内部的接口

```python
@dataclass(frozen=True)
class VisualSnapshot:
    screenshot: bytes
    candidate_boxes: dict[ElementRef, tuple[int, int, int, int]]


def build_visual_snapshot(
    state: BrowserUseState,
    *,
    element_refs: Mapping[int, ElementRef],
    max_candidates: int,
) -> VisualSnapshot | None:
    ...
```

该模块不创建 LLM 图片消息。图片如何交给模型属于 `adapters/models/browser_use_chat.py` 的职责。

#### 调用者

只有 `observation.py`。

### `adapters/models/browser_use_chat.py`

#### 作用

基于 browser-use 的聊天模型抽象实现 `ActionModel`，隐藏 LLM SDK、prompt 和结构化输出细节。该模块位于独立的模型 adapter 目录，不属于 browser-use 浏览器 adapter，因此浏览器实现与模型实现可以分别替换。

#### 负责

- 保存系统 prompt。
- 将 `DecisionContext` 格式化为模型输入。
- 将截图 bytes 转换为模型图片消息。
- 调用 `BaseChatModel`。
- 使用 Pydantic 解析模型决策。
- 将模型返回的动作索引转换为 `ElementRef`。
- 规范化模型异常。

#### 提供给其他模块的接口

```python
class BrowserUseChatActionModel:
    def __init__(
        self,
        llm: BaseChatModel,
        *,
        max_dom_characters: int,
    ) -> None:
        ...

    async def decide(
        self,
        context: DecisionContext,
    ) -> AgentAction:
        ...
```

尽管具体实现内部使用 browser-use 的 `BaseChatModel` 和消息类型，它对 Finder 只表现为 `ActionModel.decide(context) -> AgentAction`。未来可以并列增加 `DeepSeekActionModel`、`OpenAIActionModel` 或本地模型 adapter，而不修改浏览器 adapter。

#### 调用者

- `runtime.py` 创建具体实现。
- `finder.py` 通过 `ActionModel` 接口调用。

### `adapters/diagnostics.py`

#### 作用

隐藏所有具体诊断产物和文件管理机制。

#### 负责

- 创建运行目录。
- 写事件 JSONL。
- 写 screenshot 和 artifact。
- 管理 manifest。
- 执行容量限制和 retention。
- 处理文件锁。
- 执行数据脱敏。
- 记录完成和中止状态。

#### 提供给其他模块的接口

本节实现 `ports.py` 中的内部诊断接口，不属于包根公共 API。`FileRunDiagnostics` 记录任务生命周期，并组合一个共享底层 writer 的 `FileFinderEventSink` 来记录 Finder 事件。

具体实现：

```python
class FileDiagnosticsFactory:
    def __init__(self, settings: DiagnosticsSettings) -> None:
        ...

    def create(
        self,
        *,
        task_id: str,
        task_type: str,
    ) -> RunDiagnostics:
        ...


class FileRunDiagnostics:
    @property
    def finder_events(self) -> FinderEventSink: ...
    def task_started(self) -> None: ...
    def task_finished(self, result: TaskResult) -> None: ...
    def abort(self) -> None: ...


class FileFinderEventSink:
    @property
    def requires_image(self) -> bool: ...
    def step_started(self, step: int) -> None: ...
    def page_observed(self, step: int, observation: PageObservation) -> None: ...
    def decision_made(self, step: int, action: AgentAction) -> None: ...
    def action_finished(self, step: int, outcome: ActionOutcome) -> None: ...
```

诊断接口的调用对象和时机固定如下：

| 接口 | 调用对象 | 调用时机 |
| --- | --- | --- |
| `DiagnosticsFactory.create()` | `TaskApplication` | 请求身份可用后，为本次任务创建独立记录器 |
| `RunDiagnostics.task_started()` | `TaskApplication` | 开始执行已识别的任务时 |
| `RunDiagnostics.finder_events` | `TaskApplication` | 调用 Finder 时取得本次运行专用的事件接收器 |
| `FinderEventSink.requires_image` | `JobPageFinder` | 每步调用 `browser.observe()` 前决定是否采集图像 |
| `FinderEventSink.step_started()` | `JobPageFinder` | 每个探索步骤开始时 |
| `FinderEventSink.page_observed()` | `JobPageFinder` | 获得当前 `PageObservation` 后 |
| `FinderEventSink.decision_made()` | `JobPageFinder` | 模型动作完成解析后 |
| `FinderEventSink.action_finished()` | `JobPageFinder` | click、scroll 或 wait 执行结束后 |
| `RunDiagnostics.task_finished()` | `TaskApplication` | Finder 结果已映射为最终 `TaskResult` 后 |
| `RunDiagnostics.abort()` | `TaskApplication` | 捕获任务取消并重新抛出 `CancelledError` 前 |

不再定义 `finder_finished()`。Finder 的领域结果最终会由 Application 映射进 `TaskResult` 并通过 `task_finished()` 记录，保留两个结束事件只会重复表达同一次完成。

同时提供 `NullDiagnosticsFactory`、`NullRunDiagnostics` 和 `NullFinderEventSink`。空实现遵守相同接口但不写任何产物，且 `NullFinderEventSink.requires_image=False`。`application.py` 只使用 `RunDiagnostics`，`finder.py` 只使用 `FinderEventSink`，两者都不应知道文件和目录结构。

当 `DiagnosticsSettings.enabled=False` 时，Runtime 装配 `NullDiagnosticsFactory`；否则装配 `FileDiagnosticsFactory`。`FileFinderEventSink.requires_image` 在 `save_screenshots=True` 时返回 true，使 Finder 请求带图像的页面观察；是否落盘和是否记录 raw 材料仍由诊断 adapter 决定，不进入 Finder 的公开接口。

## CLI

### `cli.py`

#### 作用

只负责命令行交互。

#### 负责

- 定义 argparse 参数。
- 将参数转换为 `RuntimeSettings`。
- 通过同一个 `run` 子命令逐行读取 TaskRequest JSON 对象。
- 调用 `build_application()` 一次，并为每个输入调用 `TaskApplication.run()`。
- 每完成一个任务立即输出一行 TaskResult JSON。
- 根据 `TaskResult` 决定退出码。

诊断相关 CLI 只保留常用选项：

```text
--diagnostics-dir PATH
--no-diagnostics
--diagnostics-raw
--no-diagnostics-screenshots
```

不再提供 `--diagnostics-level`。容量和保留策略属于高级配置，不要求全部暴露为常用 CLI 参数。

#### 不负责

- 不创建 browser-use 对象。
- 不调用 Finder 内部方法。
- 不构造失败结果。
- 不处理页面逻辑。
- 不写诊断 artifact。

#### 统一 `run` 入口

CLI 始终使用 JSONL 流协议，不区分单任务和多任务模式。每个非空输入行是一个 TaskRequest，每个结果占一个输出行；单任务只是只有一行的任务流。

```python
application = build_application(settings)

for line in input_stream(input_path):
    if not line.strip():
        continue

    request = parse_json_object(line)
    result = await application.run(request)
    write_json_line(result)
```

文件输入和标准输入使用相同协议：

```bash
job-page-finder run task.jsonl
job-page-finder run tasks.jsonl
job-page-finder run -
some-agent | job-page-finder run
```

CLI 不根据文件扩展名切换格式，不支持 JSON 数组批量格式，也不提供 `--format`。PATH 为 `-` 或未提供 PATH 时读取 stdin；每个非空输入行处理完成后立即向 stdout 写一行结果，不需要把全部请求或结果保存在内存中。

`TaskApplication` 仍然只提供单任务 `run()`，CLI 不增加 `run_batch()`；并发、resume、抽样和汇总等评估语义继续由独立的 `evaluate` 命令和 `EvaluationCampaign` 负责。

## Evaluation 模块

### `evaluation/contracts.py`

#### 作用

定义评估系统自己的数据模型和端口。

#### 提供给其他模块的接口

```python
@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    company_name: str
    company_url: str


@dataclass(frozen=True)
class EvaluationRecord:
    case: EvaluationCase
    result: TaskResult
    started_at: datetime
    finished_at: datetime


@dataclass(frozen=True)
class EvaluationSummary:
    total: int
    succeeded: int
    failed: int
    success_rate: float
```

数据源和存储接口：

```python
class EvaluationCaseSource(Protocol):
    def load(self) -> Iterable[EvaluationCase]:
        ...


class EvaluationStoreSession(Protocol):
    def completed_case_ids(self) -> set[str]:
        ...

    def append(self, record: EvaluationRecord) -> None:
        ...

    def finish(self, summary: EvaluationSummary) -> None:
        ...


class EvaluationResultStore(Protocol):
    def open_campaign(
        self,
    ) -> AsyncContextManager[EvaluationStoreSession]:
        ...
```

`EvaluationCase` 只保留所有网站数据源都能提供的通用字段。交易所、证券代码和板块等 CorpWeb 专有信息不得进入该契约，也不得传给 Finder 或模型。

`EvaluationResultStore` 表达可打开的持久化目标，`EvaluationStoreSession` 表达一次 campaign 对该目标的排他使用期。进入 `open_campaign()` 返回的异步上下文时取得所有权，退出上下文时释放所有权；`completed_case_ids()`、`append()` 和 `finish()` 只能在有效 session 内调用。`finish()` 写入 campaign 完成状态，但不负责释放所有权，释放统一由异步上下文退出处理。

session 必须覆盖“读取已完成 case、执行剩余 case、逐条追加结果、写最终 summary”的完整区间，避免两个并发 campaign 在 check-then-append 之间重复执行同一个 case。该接口只暴露通用的 campaign 生命周期，不暴露文件锁、锁文件、`fcntl` 或文件描述符。

等待取得 session 所有权不得阻塞 asyncio 事件循环。成功、异常或取消退出都必须尝试释放所有权：调用方取消优先级最高；没有调用方取消时，campaign 主结果或主异常高于 release cleanup 故障，release 故障只作为次级诊断记录。

session 进入和退出必须遵守以下状态契约：

- 进入上下文前，Store 拥有 acquisition 期间创建的目录、锁句柄和其他部分资源。
- acquisition 失败时，原 acquisition 异常是主异常；部分资源清理故障只作为次级诊断。
- 调用方在等待或部分 acquisition 期间取消时，Store 必须清理已创建的资源后重新传播调用方取消。
- 若“取得所有权”和“调用方取消”发生竞态，只要底层所有权已经取得，Store 就必须先释放它，再传播取消；不得返回一个调用方已经无法进入的泄漏 session。
- `__aenter__` 只有在 manifest 验证、可恢复尾行修复、resume 状态加载和所有权取得全部成功后才能返回有效 session。
- `__aexit__` 必须对释放设置明确上限，并保护已开始的释放不被同一次调用方取消中断；达到上限后执行底层强制关闭。
- 没有调用方取消时，release 自身取消按 cleanup 故障处理，不能伪装成 campaign 被调用方取消。

异常优先级必须满足：

| Campaign/session 主状态 | acquisition/release 状态 | 对外结果 |
| --- | --- | --- |
| acquisition 失败 | 部分资源清理成功或失败 | 传播 acquisition 异常；清理失败只记录为次级诊断 |
| acquisition 期间调用方取消 | 尚未取得或刚取得所有权 | 清理部分资源或释放已取得所有权后传播调用方取消 |
| Campaign 成功 | release 成功 | 返回原 Summary |
| Campaign 成功 | release 异常、超时或自身取消 | 强制关闭并记录 cleanup 故障，仍返回原 Summary |
| Campaign 抛出异常 | release 成功 | 传播原异常 |
| Campaign 抛出异常 | release 异常、超时或自身取消 | 原异常保持主异常；强制关闭并记录 cleanup 故障 |
| Campaign 被调用方取消 | 任意 release 结果 | 完成有界释放或强制关闭后传播原调用方取消 |
| 任意非取消主状态 | release 期间收到调用方取消 | 调用方取消升级为最高优先级；完成有界释放或强制关闭后传播取消 |

### `evaluation/dataset.py`

#### 作用

负责数据集加载、校验、去重、确定性抽样和 fingerprint，不假设数据源能够提供交易所或其他专有分类字段。

#### 提供给其他模块的接口

```python
def build_dataset(
    source: EvaluationCaseSource,
    *,
    sample_size: int | None,
    seed: int,
) -> list[EvaluationCase]:
    ...
```

#### 调用者

- `evaluation/campaign.py`
- Evaluation CLI

### `evaluation/adapters/corpweb_sqlite.py`

#### 作用

将 CorpWeb SQLite 数据转换为 `EvaluationCase`，是唯一知道 CorpWeb 表名、列名和持久化状态值的模块。它可以在查询和筛选时使用 CorpWeb 专有字段，但输出只包含 `case_id`、`company_name` 和 `company_url`。

#### 提供给其他模块的接口

```python
class CorpWebSqliteCaseSource:
    def __init__(self, database: Path) -> None:
        ...

    def load(self) -> Iterable[EvaluationCase]:
        ...
```

#### 调用者

- `evaluation/dataset.py`
- Evaluation CLI 的装配逻辑

### `evaluation/campaign.py`

#### 作用

执行一轮完整评估。

#### 负责

- 管理并发 worker。
- 管理单 case timeout。
- 在 `EvaluationStoreSession` 内支持 resume。
- 调用 `TaskApplication.run()`。
- 将结果写入 `EvaluationStoreSession`。
- 生成最终 Summary。

#### 提供给其他模块的接口

```python
class EvaluationCampaign:
    def __init__(
        self,
        application: TaskApplication,
        store: EvaluationResultStore,
        *,
        concurrency: int,
        case_timeout: float,
    ) -> None:
        ...

    async def run(
        self,
        cases: Iterable[EvaluationCase],
    ) -> EvaluationSummary:
        ...
```

`run()` 在启动 worker 前进入 `self._store.open_campaign()`，并在 session 有效期内读取已完成 case、运行剩余 case、追加记录和完成 summary。Campaign 只知道 session 的所有权边界，不知道具体实现使用进程内锁、文件锁还是其他并发控制机制。

#### 调用者

- Evaluation CLI
- 评估测试

### `evaluation/store.py`

#### 作用

隐藏所有评估持久化细节。

#### 负责

- manifest。
- JSONL 追加。
- 文件锁。
- resume 状态。
- 损坏尾行恢复。
- campaign 状态。

#### 提供给其他模块的接口

```python
class JsonlEvaluationResultStore:
    def __init__(
        self,
        output_directory: Path,
        *,
        campaign_id: str,
    ) -> None:
        ...

    def open_campaign(
        self,
    ) -> AsyncContextManager[EvaluationStoreSession]:
        ...
```

`JsonlEvaluationResultStore` 返回的内部 session 实现 `EvaluationStoreSession`。它在取得排他所有权后验证 manifest、修复可恢复的 JSONL 尾行并加载 resume 状态；session 退出时释放锁和文件资源。等待锁、取消等待者以及退出清理都必须遵守上述异步生命周期和异常优先级契约。

`campaign.py` 不应了解文件锁、JSONL 尾行和 manifest 格式。

### `evaluation/report.py`

#### 作用

将评估记录转换为统计和人类可读报告。

#### 提供给其他模块的接口

```python
def summarize(
    records: Iterable[EvaluationRecord],
) -> EvaluationSummary:
    ...


def render_markdown(
    summary: EvaluationSummary,
) -> str:
    ...
```

只要报告逻辑仍然简单，就保持为函数，不创建额外 Builder 或 Factory。

## 模块接口总表

| 模块 | 提供给其他模块的接口 | 主要调用者 |
| --- | --- | --- |
| `contracts.py` | 内部 `FindJobPageRequest`、`FindJobPageResult` | Finder、Completion、Task 协议 |
| `core_models.py` | `PageObservation`、`VisualCandidate`、`AgentAction`、`ActionOutcome` | Finder、Ports、Adapters |
| `ports.py` | `BrowserFactory`、`ExplorationBrowser`、`ActionModel`、`FinderEventSink`、`RunDiagnostics`、`DiagnosticsFactory` | Finder、Application、Adapters |
| `completion.py` | `validate_completion()` | Finder |
| `finder.py` | `JobPageFinder.find()` | Application |
| `task_protocol.py` | `TaskRequest`、`TaskResult`、`parse_task()`、`to_finder_request()`、`build_task_result()`、`build_task_failure()` | Application；CLI 只使用 DTO |
| `application.py` | `FinderProvider`、`TaskApplication.run()` | Runtime、API、Evaluation |
| `settings.py` | `RuntimeSettings`、公开的 `DiagnosticsSettings` 及内部配置 | Runtime、CLI、Adapters、外部调用者 |
| `runtime.py` | `build_application()` | API、CLI、集成测试 |
| `api.py` | `run_task()` | 外部调用者 |
| `browser_use/gateway.py` | `BrowserUseFactory` | Runtime |
| `browser_use/observation.py` | `build_observation()`，仅 adapter 内部 | Gateway |
| `browser_use/scroll_targets.py` | `discover_scroll_targets()`、`resolve_scroll_target()`，仅 adapter 内部 | Gateway、Observation |
| `browser_use/scrolling.py` | `perform_scroll()`，仅 adapter 内部 | Gateway |
| `browser_use/vision.py` | `build_visual_snapshot()`，仅 adapter 内部 | Observation |
| `adapters/models/browser_use_chat.py` | `BrowserUseChatActionModel` | Runtime |
| `adapters/diagnostics.py` | `FileDiagnosticsFactory` | Runtime、Application |
| `evaluation/contracts.py` | `EvaluationCase`、`EvaluationRecord`、`EvaluationSummary`、`EvaluationCaseSource`、`EvaluationResultStore`、`EvaluationStoreSession` | Evaluation 模块与 adapters |
| `evaluation/dataset.py` | `build_dataset()` | Evaluation CLI、Campaign |
| `evaluation/campaign.py` | `EvaluationCampaign.run()` | Evaluation CLI |
| `evaluation/store.py` | `JsonlEvaluationResultStore` | Campaign |
| `evaluation/report.py` | `summarize()`、`render_markdown()` | Evaluation CLI |
| `corpweb_sqlite.py` | `CorpWebSqliteCaseSource` | Dataset |

## 一次任务的完整调用过程

外部调用：

```python
result = await run_task(raw_request, settings=settings)
```

内部流程：

```text
api.run_task()
  -> runtime.build_application()
      -> 创建惰性 FinderProvider
      -> 创建 FileDiagnosticsFactory
      -> 创建 TaskApplication
  -> TaskApplication.run()
      -> task_protocol.parse_task()
          -> 解析失败时 task_protocol.build_task_failure()
      -> diagnostics_factory.create()
      -> task_protocol.to_finder_request()
      -> finder_provider.get()
          -> 装配失败时 task_protocol.build_task_failure()
          -> 首次成功时创建 BrowserUseFactory
          -> 首次成功时创建 BrowserUseChatActionModel
          -> 首次成功时创建并缓存 JobPageFinder
      -> JobPageFinder.find(..., events=diagnostics.finder_events)
          -> browser_factory.open()
          -> browser.navigate()
          -> browser.observe()
          -> action_model.decide()
          -> browser.click()/scroll()/wait()
          -> completion.validate_completion()
          -> browser.close()
      -> task_protocol.build_task_result()
      -> diagnostics.task_finished()
  -> 返回 TaskResult
```

## 类型选型

- 外部 JSON、API 和 CLI 边界使用 Pydantic。
- 内部不可变快照和值对象使用 `@dataclass(frozen=True)`。
- 外部设施边界使用 Protocol。
- 单一领域规则使用普通函数。
- 具体适配器使用普通 class。

不应把所有数据都转换成 Pydantic，也不应把每个函数都包装成 Service 类。

## 测试边界

建议将重构后的测试分为以下四类。

### 契约测试

目录建议：

```text
tests/contracts/
```

验证 Task v1、Finder 输入输出和错误码兼容性，包括：

- `TaskRequest` schema 使用 `FindJobPageTaskPayload`，不引用内部 `FindJobPageRequest`。
- `to_finder_request()` 将 Task v1 字段和默认值正确映射为内部请求。
- `build_task_result()` 将内部成功和失败结果映射为稳定的 Task v1 output/error。

### 核心测试

目录建议：

```text
tests/core/
```

使用 Fake Browser 和 Fake ActionModel 测试完整步骤循环，包括：

- 点击后找到职位。
- 多次滚动。
- 模型错误。
- 浏览器错误。
- timeout。
- 最大步骤。
- evidence 不在页面中。

### Adapter 测试

目录建议：

```text
tests/adapters/
```

验证 browser-use 状态转换、元素引用映射、内部滚动、视觉标注和 prompt 构造。浏览器 adapter 契约测试还必须覆盖：

- 启动中途失败后不残留浏览器资源。
- 初始导航或 Finder 步骤异常后调用 `close()`。
- 任务取消后完成清理并重新抛出 `CancelledError`。
- cleanup Task 自身取消时将其记录为 cleanup 故障，不把整个任务报告为调用方取消。
- cleanup 期间发生调用方取消时，将其与 cleanup 自身取消区分，并在有界清理后传播调用方取消。
- `close()` 重复调用安全。
- `close()` 异常不覆盖已有业务结果或原始异常。

### 集成测试

目录建议：

```text
tests/integration/
```

保留少量真实 Chromium、CLI 和可选模型集成测试。核心测试不应继续 monkeypatch browser-use 的 `_root`，也不应断言其事件总线调用次数。

## 应避免的重构方式

- 不要因为文件长就拆成大量一两个函数的类。
- 不要给每个现有类机械添加同名 Protocol。
- 不要为 Click、Scroll、Wait 分别创建 Handler 和 Factory。
- 不要让本地页面模型复制 browser-use 的完整 DOM 状态树。
- 不要同时重写 Finder、Evaluation 和 Task v1 协议。
- 不要为了“看起来分层”保留两层职责相同的 Runtime 和 Runner。
- 不要把尚未证明存在替换需求的规则设计成 Strategy 层次。

## 目标检查清单

重构完成后应满足：

```text
finder.py 只知道浏览器能做什么，不知道 browser-use 如何实现
finder.py 只知道模型能选择动作，不知道 prompt 如何构造
application.py 只知道 Finder 能执行任务，不知道网页如何操作
evaluation 只知道 Application 能运行 Task，不知道 Finder 内部循环
CorpWeb SQLite 只有一个 adapter 知道表结构
诊断接口只表达事件，不暴露文件和目录结构
```

核心 Finder 的认知负担最终应收敛到以下概念：

```text
FindJobPageRequest
PageObservation
AgentAction
ExplorationBrowser
ActionModel
FinderEventSink
FindJobPageResult
```

理想的核心构造和调用方式为：

```python
finder = JobPageFinder(
    browser_factory=browser_factory,
    action_model=action_model,
    settings=settings,
)

result = await finder.find(request, events=events)
```

各主要深模块分别隐藏一套完整复杂度：

- `JobPageFinder` 隐藏探索流程。
- `BrowserUseFactory` 隐藏浏览器 SDK 和 DOM 私有结构。
- `BrowserUseChatActionModel` 隐藏 prompt 和模型 SDK，并可独立于浏览器实现替换。
- `TaskApplication` 隐藏任务生命周期和错误映射。
- `JsonlEvaluationResultStore` 隐藏锁、恢复和文件格式。
- `CorpWebSqliteCaseSource` 隐藏跨项目数据库 schema。

最终目标不是增加目录数量，而是让上层模块使用少量稳定概念完成完整工作，并使外部实现变化不会穿透核心业务。
