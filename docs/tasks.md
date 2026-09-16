# JobFinder 分阶段重构任务

## 文档定位

本文档将 [`modular-refactoring-design.md`](modular-refactoring-design.md) 中的目标架构拆解为可独立实施、验证和停止的重构阶段。当前行为以 [`current-behavior.md`](current-behavior.md) 为基线，测试取舍以 [`test-protection-map.md`](test-protection-map.md) 为依据。

本文档只规划结构迁移，不表示任何阶段已经开始。每个阶段完成后，仓库都必须保持可运行、可测试，并且可以在不依赖下一阶段的情况下接受评审。

## 迁移方法

每个阶段内部使用 Parallel Change / Expand-and-Contract：

1. **Facade**：先确定迁移期间保持稳定的入口。旧调用者只能通过该入口访问正在替换的能力。
2. **Expand**：并列增加目标接口、模型或实现，不立即删除旧路径，也不同时改变业务语义。
3. **Migrate**：通过显式 adapter 或 mapper 逐个迁移调用者和测试。每迁移一组调用者就运行对应验证，不等到整个阶段结束才集成。
4. **Contract**：确认旧路径没有剩余调用者且替代测试已存在后，删除旧实现、临时 adapter 和重复测试。

这里的 Parallel Change 表示新旧结构在迁移窗口内可以同时存在，不表示同一真实任务要在新旧实现上执行两次。浏览器操作、模型请求、诊断写入和评估结果写入都有副作用，禁止用双执行或双写来比较实现。

Facade 是临时迁移设施，不是默认永久兼容层。每个 Facade 必须指定移除阶段；如果存在已发布的外部调用者，移除前必须完成单独的兼容性决策，不能在结构重构中静默破坏。

## 全局约束

- 不修改相邻依赖 `../browser-use`。
- 不把目标设计描述成当前已实现能力。
- 每个阶段只迁移一个主要变化边界，不同时重写 Task、Finder、Diagnostics 和 Evaluation。
- 结构迁移期间保持当前 Task v1 JSON、错误码、CLI 退出码、浏览器动作集合和安全限制，除非通过本文档中的契约决策门。
- 新核心模块不得导入 browser-use；具体 SDK、文件系统和 SQLite 细节只能进入 adapter 或组合根。
- 诊断故障不得改变业务结果，取消不得被归一化为普通失败。
- 删除旧测试之前，必须先在稳定边界建立覆盖同一风险的替代测试。
- 不以精确 prompt 文本、私有方法名、内部事件完整顺序或协作者调用次数作为结构重构的主要验收标准。

## 契约决策门

目标设计与当前行为存在以下差异。它们不是纯重构，必须在相关 Contract 步骤前单独决定并记录兼容策略。

| 编号 | 当前行为 | 目标设计 | 最晚决策阶段 |
| --- | --- | --- | --- |
| `DG-01` | Task v1 使用 `metadata.duration_ms` | `duration_ms` 位于 `TaskResult` 顶层 | 阶段 1 |
| `DG-02` | evidence 只要求非空，泛化标题主要由 prompt 约束 | evidence 必须出现在可见文本中，并确定性拒绝泛化标题 | 阶段 4 |
| `DG-03` | 诊断配置使用 `basic`、`diagnostic`、`raw` | 使用 `enabled`、`save_screenshots`、`include_raw` | 阶段 6 |
| `DG-04` | CLI 有快捷命令和单 JSON 文件输入 | `run` 统一使用逐行 JSONL，支持 stdin 和多任务 | 阶段 9；同时决定逐行失败处理和总退出码 |
| `DG-05` | 包根导出 Finder、Runner、配置和构造函数等实现类型 | 包根只承诺 `run_task`、Task v1 和 Runtime/Diagnostics 设置 | 阶段 8 |
| `DG-06` | Evaluation case、分层采样、报告和持久化格式包含 CorpWeb 与运行专用字段 | 通用 `EvaluationCase` 只含跨数据源字段，持久化由独立 Store 隐藏 | 阶段 10；同时决定采样和报告兼容性 |

已批准决策：

- `DG-01`（2026-09-16）：保持现有 Task v1 `metadata.duration_ms`，本轮只隔离协议结构。目标设计中的顶层 `duration_ms` 不在 v1 中实施；未来如需采用，必须发布新协议版本。
- `DG-02`（2026-09-16）：保持现有完成校验语义。只确定性验证 `job_title` 出现在可见 DOM，`evidence` 仅要求非空，泛化标题仍由 prompt 约束。目标设计中的 evidence 原文校验和泛化标题黑名单不在 v1 中实施；未来如需采用，必须作为独立行为变更提交并更新验收场景。
- `DG-03`（2026-09-16）：保持现有 `basic`/`diagnostic`/`raw` 三级诊断行为。目标设计中的 `enabled`/`save_screenshots`/`include_raw` 不在本轮公共配置中实施；未来如需采用，必须提供兼容映射或作为明确变更获批。
- `DG-05`（2026-09-16）：包根稳定承诺为 `run_task`、`TaskRequest`、`TaskResult`、`RuntimeSettings` 和 `DiagnosticsSettings`。阶段 12 Contract 已删除旧包根导出；`create_runner()` / `TaskRunner` / `RuntimeConfig` 仅保留为子模块内部入口。
- `DG-04`（2026-09-16）：`run` 使用 JSONL（每个非空行一个 TaskRequest，立即输出一行 TaskResult）。遇错继续处理后续行。总退出码优先级为 `CONFIGURATION_ERROR`(3) > 输入/`INVALID_TASK`(2) > 任务失败(1) > 成功(0)。阶段 12 Contract 已删除 `find-job-page`；`--diagnostics-level` 按 `DG-03` 作为公共配置保留。不按扩展名猜格式，不支持 JSON 数组批量。
- `DG-06`（2026-09-16）：保持现有 dataset/manifest/`source`/`sample_bucket`/`max_steps` 与 CorpWeb 分层抽样为 v1。通用三字段 case 不在本轮替换持久化格式；分层抽样留在 CorpWeb adapter 内部。

决策只能选择以下一种方式：保持现有 v1 行为、提供有明确截止点的兼容读取/别名，或发布新的协议/文件格式版本。不能在仍称为同一个稳定 v1 的同时直接改变字段位置或语义。

在决策完成前，相关阶段先完成结构隔离，并由 Facade 保持当前外部行为。例如阶段 1 可以建立独立 Task payload 和内部请求映射，但不能自动把 `metadata.duration_ms` 移到顶层。

## 阶段依赖

```text
0 行为门禁
  -> 1 Task 协议与领域契约
      -> 2 核心模型与 Ports
          -> 3 Browser Adapter --------+
          -> 4 Model Adapter/Completion +-> 5 Finder Core
                                                -> 6 Diagnostics Adapter
                                                    -> 7 Task Application
                                                        -> 8 Settings/Runtime/API
                                                            -> 9 CLI
                                                            -> 10 Evaluation Contracts/Source
                                                                -> 11 Evaluation Store
                                                                    -> 12 Campaign/Report/收口
```

阶段 3 和阶段 4 可以在 Ports 稳定后并行开发，但必须分别提交、分别验证。阶段 11 的纯 Store 工作可以提前探索，但在 TaskResult 和 Evaluation 格式决策稳定前不得完成 Contract。

## 阶段 0：建立行为门禁

### 目标

在移动职责前建立稳定、面向行为的验证门禁，明确哪些现有测试保留、改写、合并或由更高层测试替代。

### Facade

保持所有当前入口不变：`JobPageFinder.find()`、`TaskRunner.run()`、`run_task()`、包根导出、CLI 子命令和 Evaluation 命令。

### Expand

- [x] 按 `tests/contracts/`、`tests/core/`、`tests/adapters/` 和 `tests/integration/` 的目标边界补充或整理测试，不要求立即搬动所有文件。
- [x] 为 Task v1 请求、结果、错误码和无副作用前置拒绝建立协议快照或等价 schema 断言。
- [x] 为浏览器正常、失败、超时和取消路径建立资源清理行为测试。
- [x] 为诊断 best-effort、Evaluation 锁、resume 和损坏尾行恢复建立稳定边界测试。
- [x] 建立依赖规则检查，至少保证目标核心模块出现后不能导入 `browser_use`。

### Migrate

- [x] 将绑定完整 prompt、精确事件顺序、私有方法或调用次数的测试改写为结果、错误码、安全副作用或稳定产物断言。
- [x] 将重复验证同一成功路径的测试收敛到各公共边界的一条代表性测试。
- [x] 为 `test-protection-map.md` 中标记为“核心应该，当前不一定”的风险记录替代测试位置。

### Contract

- [x] 确认本阶段未删除生产路径，并且只删除已有同等风险替代证据的脆弱测试。

### 验证与退出条件

- [x] `current-behavior.md` 的 `AC-001` 至 `AC-018` 均有明确测试证据或记录的测试缺口。
- [x] Task v1、错误映射、cleanup、cancellation、诊断和 Evaluation 数据完整性均有稳定门禁。
- [x] 完整测试套件通过，且没有为迁移预先放宽外部行为断言。

## 阶段 1：隔离 Task 协议与领域契约

### 目标

创建 `contracts.py` 和 `task_protocol.py`，把 Task v1 DTO 与 Finder 内部契约分开，消除 `TaskRequest.payload` 对内部 Finder 输入模型的引用。

### Facade

保留当前 `runner.py` 中的 Task 类型和 `TaskRunner` 入口。迁移期间它们转发到新协议函数或重导出协议 DTO，外部序列化结果保持不变。

### Expand

- [x] 在 `contracts.py` 增加内部 `FindJobPageRequest`、`JobEvidence`、`FindJobPageSuccess`、`FindJobPageFailure` 和 `FindJobPageResult`。
- [x] 在 `task_protocol.py` 增加 `FindJobPageTaskPayload`、`FindJobPageTaskOutput`、`TaskRequest`、`TaskError` 和 `TaskResult`。
- [x] 增加 `parse_task()`、`to_finder_request()`、`build_task_result()` 和应用级失败专用的 `build_task_failure()`。
- [x] 增加旧 `JobPageFinderInput`/`JobPageFinderResult` 与新内部契约之间的临时 mapper。
- [x] 保留 URL 校验、`max_steps=8`、范围 1 至 50、额外字段拒绝和 task ID 规则。

### Migrate

- [x] 先把 Runner 的解析逻辑迁到 `parse_task()`，保持 Finder 调用不变。
- [x] 再把 payload 到 Finder 输入的转换迁到 `to_finder_request()`。
- [x] 最后把结果和错误码映射迁到 `build_task_result()`。
- [x] 把协议、配置和未预期故障的 TaskResult 构造迁到 `build_task_failure()`，集中维护 retryable 映射。
- [x] 将 Runner 测试拆为 Task 协议契约测试和暂时保留的 Runner Facade 测试。

### Contract

- [x] 删除 Runner 内重复的 Task DTO、探测模型和结果构造逻辑。
- [x] 保留 `TaskRunner` Facade 到阶段 7；保留旧领域模型 mapper 到阶段 5。
- [x] 按 `DG-01` 决定 duration 字段策略。在决策前保持 `metadata.duration_ms`；若采用顶层字段，必须版本化或提供已批准的兼容窗口。

### 验证与退出条件

- [x] `TaskRequest` schema 使用 `FindJobPageTaskPayload`，不引用内部 `FindJobPageRequest`。
- [x] 非法请求在 Finder、LLM 和浏览器调用前失败。
- [x] 新旧入口产生相同的 Task v1 JSON 和错误码。
- [x] 内部成功/失败联合不能表达“成功但缺字段”的状态。
- [x] 协议契约测试和 Runner 测试通过。
- [x] `CONFIGURATION_ERROR` 由 `build_task_failure()` 构造且不可重试，Application 不自行拼装失败 envelope。

## 阶段 2：引入核心模型与 Ports

### 目标

创建 `core_models.py` 和 `ports.py`，先定义 Finder 所需能力，再迁移具体实现。

### Facade

当前 Finder 仍使用现有 browser-use、LLM 和诊断对象。新增临时 adapter 将旧对象转换为 Ports，避免要求所有调用者同时变化。

### Expand

- [x] 增加 `PageObservation`、`InteractiveElement`、`ScrollTarget`、`ElementRef` 和 adapter-neutral 的 `VisualCandidate`。
- [x] 增加 `Click`、`Scroll`、`Wait`、`Complete`、`AgentAction`、`DecisionContext`、`ActionOutcome` 和 `ObservationOptions`。
- [x] 定义 `BrowserFactory`、`ExplorationBrowser`、`ActionModel`、`FinderEventSink`、`RunDiagnostics` 和 `DiagnosticsFactory`。
- [x] 增加 Null diagnostics 和内存 Fake Browser/Fake ActionModel，用于后续核心测试。
- [x] 记录 `open()`、`close()`、超时、异常优先级和取消来源的行为契约，不规定具体 `asyncio.shield()` 算法。

### Migrate

- [x] 先迁移纯模型和动作 schema 测试。
- [x] 再使用 mapper 把当前 browser-use 状态转换为 `PageObservation`。
- [x] 用 Fake Ports 建立最小 Finder 场景，但暂不切换生产 Finder。
- [x] 为每个 Protocol 建立实现者契约测试，而不是测试 Protocol 本身。

### Contract

本阶段不删除现有 browser-use 调用。冻结 Ports 后才允许阶段 3 和阶段 4 并行推进；任何 SDK 类型进入 Ports 都必须在本阶段移除。

### 验证与退出条件

- [x] `contracts.py`、`core_models.py` 和 `ports.py` 不导入 browser-use。
- [x] Core 模型不复制 browser-use 完整 DOM 树，只保留业务所需信息。
- [x] Fake Ports 能表达成功、动作失败、超时、主异常、调用方取消和 cleanup 自身取消。
- [x] 调用方取消与 cleanup Task 自身取消有独立测试场景。

## 阶段 3：迁移 Browser Adapter

### 目标

把 BrowserSession、DOM 私有结构、滚动、截图和 browser-use 异常集中到 `adapters/browser_use/`。

### Facade

提供 `BrowserUseFactory` 和实现 `ExplorationBrowser` 的 adapter。现有 Finder 的 `browser_factory` 仍可由临时桥接器提供，直到阶段 5 切换核心循环。

### Expand

- [x] 在 `gateway.py` 实现浏览器创建、导航、观察、点击、滚动、等待和关闭。
- [x] 在 `observation.py` 实现 browser-use 状态到 `PageObservation` 的转换，包括把内部 `VisualSnapshot` 映射为 `screenshot` 和 `visual_candidates`。
- [x] 在 `scroll_targets.py` 迁移目标发现、排序、临时索引分配和解析。
- [x] 在 `scrolling.py` 迁移偏移验证、root fallback、SPA route 观察和 deadline 约束。
- [x] 在 `vision.py` 定义 adapter 内部的 `VisualSnapshot`，并实现 `build_visual_snapshot()`，封装候选发现、坐标映射和内存 PNG 标注。
- [x] 原样迁移无头模式、下载/PDF 限制、默认扩展和元素高亮开关，以及 IP 地址拦截等浏览器安全设置。
- [x] 实现启动部分失败的资源回收、幂等且有界的 `close()` 和最终强制终止策略。

### Migrate

- [x] 先迁移 scrolling 和 vision 单元测试到 adapter 边界。
- [x] 再迁移 observation 转换和元素引用测试。
- [x] 然后让本地 Chromium 测试通过 `BrowserUseFactory` 运行。
- [x] 最后让旧 Finder 的临时桥接器使用新 Browser Adapter，保持旧 Finder 接口不变。

### Contract

- [x] 删除旧 `scrolling.py` 和 `vision.py` 前，确认所有调用者已迁移且本地 Chromium 测试通过。
- [x] 删除核心测试对 browser-use `_root`、事件总线和具体消息类型的 monkeypatch。
- [x] Browser Facade 在阶段 5 Finder 切换完成后移除。

### 验证与退出条件

- [x] 滚动目标过滤、模态优先级、索引复用和冲突避免保持不变。
- [x] root fallback、避免重复滚动、延迟 route、实时 URL 基线和步骤 deadline 保持不变。
- [x] 视觉候选、滚动后坐标和无候选降级保持不变。
- [x] `VisualSnapshot` 只存在于 `vision.py` 与 `observation.py` 之间；Finder、Model Adapter 和 Diagnostics 只接收 `PageObservation` 中的 adapter-neutral 数据。
- [x] Browser Factory 契约测试覆盖无头模式、下载/PDF 限制、扩展、高亮和 IP 地址拦截；本地测试 profile 的例外必须显式配置，不能改变生产默认值。
- [x] 启动失败不残留资源，所有退出路径尝试关闭浏览器。
- [x] cleanup 普通异常、超时或自身取消不覆盖主结果；调用方取消在有界清理后传播。
- [x] 本地 Chromium 点击和 CSS Scroll Snap 场景通过。

## 阶段 4：迁移 Model Adapter 与完成规则

### 目标

把 prompt、模型消息、结构化输出和动作索引转换移到 `adapters/models/browser_use_chat.py`，把完成验证移到纯函数 `completion.py`。

### Facade

保留当前传入 `BaseChatModel` 的构造路径。临时 `ActionModel` adapter 包装该模型，旧 Finder 在阶段 5 前仍可通过桥接器调用。

### Expand

- [x] 实现 `BrowserUseChatActionModel.decide(DecisionContext) -> AgentAction`。
- [x] 将 system prompt、文本上下文、图片消息和 Pydantic 解析移入 adapter。
- [x] 将模型动作索引转换为当前观察中的 `ElementRef`。
- [x] 规范化模型调用和解析异常，但不通过异常文案猜测错误来源。
- [x] 在 `completion.py` 提取 `validate_completion()`，先完整复制当前确定性验证语义。

### Migrate

- [x] 先迁移动作 schema、未知字段和动作范围测试。
- [x] 再迁移纯文本、视觉消息和无对话历史测试。
- [x] 然后迁移完成校验、拒绝反馈和连续验证失败测试。
- [x] 最后让旧 Finder 桥接到 `ActionModel` 和 `validate_completion()`。

### Contract

- [x] 删除 Finder 内的 prompt、browser-use message 和结构化输出模型。
- [x] 删除 Finder 内重复完成校验后，保留 `completion.py` 为唯一完成规则。
- [x] 按 `DG-02` 决定是否加强 evidence 和泛化标题校验。未决前必须保持当前语义；采用新语义时作为独立行为变更提交并更新验收场景。

### 验证与退出条件

- [x] Finder 面向的模型接口只有 `decide(context)`。
- [x] 模型只能产生 click、scroll、wait 或 complete，不能扩大动作集合。
- [x] 旧观察索引不能在新观察中使用。
- [x] 诊断截图采集不改变关闭视觉时的模型输入。
- [x] 模型错误继续映射为 `MODEL_ERROR`，完成拒绝继续映射为 `VALIDATION_FAILED` 或允许后续探索。

## 阶段 5：切换 Finder Core

### 目标

让 `JobPageFinder` 只编排探索用例，并且只依赖内部契约、核心模型、Ports、完成规则和 Finder 设置。

### Facade

保留当前 `JobPageFinder.find()` 名称和不带 `events` 的旧调用方式。迁移窗口内由核心外部的构造/调用 Facade 接受旧依赖、组装 Ports，并把当前 diagnostics 包装为临时 `FinderEventSink`；没有 diagnostics 时显式传入 Null sink。新调用者直接注入 `BrowserFactory`、`ActionModel` 和 `FinderSettings`。

### Expand

- [x] 以新依赖实现 `open -> navigate -> observe/decide/act -> validate -> close` 循环。
- [x] 把每步 deadline、连续失败次数和最大步骤限制迁入新循环。
- [x] 通过显式 `FinderEventSink` 发出事件，不在核心 Finder 中访问诊断文件或 ContextVar；旧签名的 Facade 负责在核心外解析当前 diagnostics 并传入临时 sink。
- [x] 使用内部 `FindJobPageResult`，在 Facade 边界映射旧领域结果。
- [x] 实现设计文档规定的 browser ownership 和异常优先级表。

### Migrate

- [x] 先用 Fake Browser/Fake ActionModel 迁移首页成功、点击、滚动和等待测试。
- [x] 再迁移模型错误、动作错误、校验失败、步骤超时和最大步骤测试。
- [x] 然后迁移 stale reference、诊断 deadline 隔离和所有 cleanup/cancellation 测试。
- [x] 最后把 Runtime 的 Finder 创建切换到新构造方式，通过临时 event bridge 保持现有诊断事件，并运行本地 Chromium 链路。

### Contract

- [x] 删除 Finder 对 browser-use、模型消息、DOM 私有结构、文件系统和环境变量的所有导入。
- [x] 删除旧循环和旧依赖构造 Facade，前提是 Runtime、Runner 和测试均已迁移。
- [x] 删除阶段 1 的旧领域结果 mapper；如存在外部子模块调用者，先按 `DG-05` 完成兼容决策。mapper 保留为 Finder 内部契约，不再从包根导出。

### 验证与退出条件

- [x] 核心测试完全使用 Fake Ports，不 monkeypatch browser-use。
- [x] `finder.py` 只认知目标设计列出的七个核心概念。
- [x] 当前 URL、步骤数、错误码、反馈和 timeout 行为保持基线一致。
- [x] 旧 `find()` 调用者不传 `events` 时仍得到 Null 或当前运行对应的临时 sink，不能静默丢失已有 Finder 诊断。
- [x] 正常、异常、超时和取消路径全部满足 browser cleanup 契约。
- [x] Finder 单元测试与本地 Chromium 集成测试通过。

## 阶段 6：迁移 Diagnostics Adapter

### 目标

通过 `DiagnosticsFactory`、`RunDiagnostics` 和组合式 `FinderEventSink` 隐藏诊断文件、序列化、容量、保留和锁。

### Facade

保留当前 `DiagnosticWriter` 和 ContextVar 入口作为临时桥接。新 `FileRunDiagnostics` 可以复用现有底层 writer，但 TaskRunner 和 Finder 不再直接了解它。

### Expand

- [x] 实现 `FileDiagnosticsFactory`、`FileRunDiagnostics` 和 `FileFinderEventSink`。
- [x] 实现 `NullDiagnosticsFactory`、`NullRunDiagnostics` 和 `NullFinderEventSink`。
- [x] 让 `FileRunDiagnostics.finder_events` 组合共享 writer，而不是继承 Finder sink。
- [x] 将 manifest、事件 JSONL、artifact、权限、容量、retention、锁和脱敏保留在 adapter 内。
- [x] 将 `requires_image` 作为 Finder 请求观察图像的唯一诊断信号。

### Migrate

- [x] 先用 `FileFinderEventSink`/`NullFinderEventSink` 替换阶段 5 的临时 event bridge，不改变 Finder 的显式事件接口。
- [x] 再让当前 `TaskRunner` 临时通过 `DiagnosticsFactory`/`RunDiagnostics` 管理 task started/finished/abort；该所有权将在阶段 7 原样迁给 Application。
- [x] 然后迁移 Runtime 的 writer 创建到 `DiagnosticsFactory`。
- [x] 最后将依赖私有 writer 方法的测试改成 adapter 契约或稳定产物测试。剩余 `DiagnosticWriter` 测试覆盖文件 adapter 的容量、cleanup 和产物契约，不再作为 Task 生命周期测试。

### Contract

- [x] 删除 Finder 和 Runner 对 `current_diagnostics()`、set/reset ContextVar 和 `DiagnosticWriter` 的直接依赖；Runner 暂时只依赖诊断 Ports。
- [x] 删除重复的 `finder_finished()`，最终结果只由 `task_finished()` 记录。
- [x] 按 `DG-03` 决定配置迁移。结构迁移先保持现有三级行为；新设置只有在兼容映射或明确变更获批后成为公共配置。

### 验证与退出条件

- [x] `TaskRunner` 暂时只依赖 `DiagnosticsFactory`/`RunDiagnostics`，Finder 只依赖 `FinderEventSink`；本阶段不依赖尚未创建的 `TaskApplication`。
- [x] 诊断关闭时使用 Null 对象，不在业务代码散布条件分支。
- [x] writer、序列化、容量、retention 或 cleanup 故障均不改变 Task 结果。
- [x] 取消运行标记 aborted 并重新传播 `CancelledError`。
- [x] 三级产物边界、并发隔离、容量、权限和敏感字段排除保持当前契约，直到 `DG-03` 决策生效。

## 阶段 7：引入 Task Application

### 目标

用 `TaskApplication.run()` 统一请求解析、诊断生命周期、Finder 调用、计时和错误归一化，替代职责重叠的 `TaskRunner`。

### Facade

保留 `TaskRunner`，但将其变成调用 `TaskApplication` 的薄 Facade。禁止两层同时记录生命周期或重复映射结果。

### Expand

- [x] 实现只提供 `run()` 的 `TaskApplication`。
- [x] 定义只提供 `get() -> JobPageFinder` 的 `FinderProvider`，让 Application 能把可能失败的具体装配延迟到请求解析之后。
- [x] 按顺序调用 `parse_task()`、diagnostics create/start、`to_finder_request()`、`finder_provider.get()`、Finder、`build_task_result()` 和 diagnostics finish。
- [x] 只在 `parse_task()` 成功后调用 provider；通过 `build_task_failure()` 把 provider 的普通装配异常映射为不可重试的 `CONFIGURATION_ERROR`，调用方取消继续传播。
- [x] 通过 `build_task_failure()` 将未预期异常映射为不含 traceback 的 `INTERNAL_ERROR`。
- [x] 在取消路径调用 diagnostics abort 后重新抛出 `CancelledError`。
- [x] 保证请求身份在完整 Pydantic 验证失败前仍可用于结果、日志和诊断关联。

### Migrate

- [x] 先迁移 Runner 单元测试到 Application 和 Task 协议边界。
- [x] 再让 `TaskRunner` Facade 委托 Application，并将阶段 6 的诊断 Ports 所有权从 Runner 原样迁入 Application，保留一条等价结果测试。
- [x] 让 `TaskRunner` Facade 在现有 Runtime 路径内调用 Application；Runtime 的前置解析和急切装配暂时保留到阶段 8，作为明确的迁移桥接。
- [x] 最后消除 Runtime 与 Runner 之间的重复 started/finished 日志。

### Contract

- [x] 所有仓库内调用者迁移后，先完成 Facade 与 Application 的等价验证，再删除 `TaskRunner` Facade。包根不再导出 `TaskRunner`；子模块薄委托与等价测试按 `DG-05` 保留为内部入口。
- [x] 若存在外部 `TaskRunner` 调用者，移除必须纳入 `DG-05`，不能无限保留双层 Application/Runner。
- [x] 删除 Runner 私有异常和重复结果 builder，只保留 `TaskProtocolError` 与协议 mapper。

### 验证与退出条件

- [x] API、CLI 暂存入口和 Evaluation 均可通过 Application 完成单任务。
- [x] Application 单元测试证明无效请求不调用 Provider；生产入口在本阶段仍由旧 Runtime Facade 保持“先校验、后装配”。
- [x] task ID、错误码、retryable、日志和诊断生命周期只产生一次。
- [x] 未预期异常不泄露 traceback，调用方取消不变成 `INTERNAL_ERROR`。
- [x] Contract 前必须证明 TaskRunner Facade 与 Application 的 Task v1 结果等价；Contract 后若 Facade 已删除则验证不存在剩余调用者，若 `DG-05` 要求暂时保留则继续保留等价测试。

## 阶段 8：收敛 Settings、Runtime 与 Python API

### 目标

建立 `settings.py`、唯一组合根 `build_application()` 和稳定公共入口 `api.run_task()`。

### Facade

暂时保留 `RuntimeConfig`、`create_runner()` 和旧 `run_task()` 注入参数。它们只负责映射到 `RuntimeSettings` 或委托 `build_application()`，不得形成第二套装配路径。

### Expand

- [x] 增加 `FinderSettings`、`BrowserSettings`、`DiagnosticsStoragePolicy`、`DiagnosticsSettings` 和 `RuntimeSettings`。
- [x] 在 `runtime.py` 集中环境加载、LLM、Browser Adapter、Model Adapter、Diagnostics Adapter、Finder 和 Application 装配。
- [x] 实现可注入 Ports 的 `build_application()`；它只创建 Application、Diagnostics Factory 和惰性 Finder Provider，不读取 LLM 凭据或立即创建 Finder 依赖。
- [x] 让 Runtime 内部 Provider 在首个合法任务到达时创建并缓存 LLM、Browser Adapter、Model Adapter 和 Finder，只缓存成功结果。
- [x] 在 `api.py` 提供只接受 Task 请求和 Runtime 设置的 `run_task()`。
- [x] 保持 API 只执行 `build_application(settings)` 和 `application.run(request)`，不调用 `parse_task()`、不提取 task ID，也不构造失败结果。
- [x] 保持显式参数、进程环境和 `.env` 的当前优先级与密钥安全规则。

### Migrate

- [x] 先为 Application 解析所有权、无效请求不调用 Provider 和 Provider 装配异常映射增加回归测试。
- [x] 再让集成测试直接调用 `build_application()`。
- [x] 然后把 Python API 切换到新 `api.run_task()`。
- [x] 随后把 Evaluation 和 CLI 的装配入口切换到同一个 Application，但暂不改变 CLI 输入格式。
- [x] 删除阶段 7 保留的 Runtime 前置解析桥接，使 `TaskApplication` 成为唯一生产解析者。
- [x] 最后迁移包内 import，停止从包根导入内部实现。

### Contract

- [x] 删除 `create_runner()` 和旧 Runtime 参数前完成 `DG-05`。
- [x] 包根最终只导出 `run_task`、`TaskRequest`、`TaskResult`、`RuntimeSettings` 和 `DiagnosticsSettings`。
- [x] `JobPageFinder`、`TaskApplication` 和具体 adapters 可以从子模块使用，但不属于包根兼容承诺。
- [x] 删除旧设置 Facade 后确认只有 `runtime.py` 同时依赖核心、具体 adapter 和环境配置。`RuntimeConfig`/`create_runner` 仅作为子模块内部入口保留。

### 验证与退出条件

- [x] API 与 CLI 使用同一个组合根。
- [x] `TaskApplication` 是唯一调用 `parse_task()` 的模块，请求校验优先于默认 LLM 和浏览器装配。
- [x] Provider 装配错误继续由 Application 映射为不可重试 `CONFIGURATION_ERROR`。
- [x] 无效请求和装配失败都返回可关联 task ID 的 TaskResult，且 API 不参与两类失败结果的构造。
- [x] 依赖注入、默认视觉、显式关闭视觉和测试诊断目录隔离有效。
- [x] 包根导出符合已批准的 `DG-05` 策略。

## 阶段 9：迁移 CLI 到统一任务流

### 目标

让 `cli.py` 只负责参数、输入输出和退出码，并根据 `DG-04` 收敛到目标 JSONL `run` 协议。

### Facade

在格式迁移窗口内保留当前 `find-job-page` 和单 JSON 文件命令。它们必须转换为 `TaskRequest` 后调用同一个 Application，不得直接调用 Finder。

### Expand

- [x] 增加逐行读取非空 TaskRequest、逐行立即输出 TaskResult 的流式路径。
- [x] 支持文件、`-` 和缺省 stdin 输入。
- [x] Application 只保持单任务 `run()`，CLI 不增加批处理领域接口。
- [x] 将诊断 CLI 参数映射到 `RuntimeSettings`，不在 CLI 创建 writer。
- [x] 保持 stdout 只含机器可读结果，logging 写 stderr。

### Migrate

- [x] 先让单行 JSONL 通过现有 `run` 命令端到端运行。
- [x] 再加入多行和 stdin 场景，验证逐条输出和有界内存。
- [x] 然后把快捷命令改成构造一条 TaskRequest 并复用同一执行函数。
- [x] 最后按 `DG-04` 移除或保留兼容命令，确定行级失败后的继续/停止策略、输出顺序和混合结果的总退出码优先级，并按 `DG-03` 处理旧诊断参数。

### Contract

- [x] 只有在输入格式和命令兼容策略获批后，才删除单 JSON 解析和 `find-job-page` Facade。阶段 12 已删除 `find-job-page`。
- [x] 删除 `--diagnostics-level` 前必须完成诊断配置决策。按 `DG-03` 作为公共 CLI 配置保留。
- [x] 不支持按扩展名猜格式、JSON 数组批量格式或 CLI `run_batch()`。

### 验证与退出条件

- [x] 每个已处理的非空输入行对应且只对应一个输出行；若 `DG-04` 批准遇错停止，则已处理前缀和剩余输入的行为必须符合该决策。
- [x] 单任务、多任务、文件和 stdin 使用同一协议。
- [x] 成功、任务失败、输入错误和配置错误的退出码符合已批准契约。
- [x] 混合成功/失败、流中非法 JSON、流中非法 Task 和中途配置故障均有测试，输出数量、继续策略和总退出码符合 `DG-04`。
- [x] 非 UTF-8、非法 JSON、非对象和未知任务返回结构化错误且无 traceback。
- [x] CLI 不导入或构造 browser-use、Finder、LLM 或诊断 writer。

## 阶段 10：拆分 Evaluation 契约、数据集与数据源

### 目标

创建 `evaluation/contracts.py`、`dataset.py` 和 `adapters/corpweb_sqlite.py`，让 CorpWeb schema 只存在于一个 adapter。

### Facade

保留当前 `evaluation.py` 的公共函数和 CLI 入口。它们委托新模块，并在边界映射当前 Evaluation case/manifest 格式。

### Expand

- [x] 定义通用 `EvaluationCase`、`EvaluationRecord`、`EvaluationSummary`、`EvaluationCaseSource`、`EvaluationResultStore` 和 `EvaluationStoreSession`。
- [x] 实现 `CorpWebSqliteCaseSource.load()`，将 CorpWeb 记录转换为通用 case。
- [x] 在 `dataset.py` 实现 URL 校验、去重、确定性抽样和 fingerprint；在 `DG-06` 决策前，当前 CorpWeb 分层比例和行业轮询仍由兼容 Facade 保持。
- [x] 把交易所、证券代码、板块、表名、列名和状态值限制在 CorpWeb adapter 内。
- [x] 保持同输入、样本数和 seed 的确定性。

### Migrate

- [x] 先迁移 SQLite 查询和合法站点过滤测试。
- [x] 再迁移数据集校验、去重、采样和 fingerprint 测试。
- [x] 然后让旧 Evaluation Facade 使用新 source/dataset。
- [x] 最后迁移 Evaluation CLI 的 generate 装配。

### Contract

- [x] 按 `DG-06` 决定已有 dataset、manifest、resume、分层抽样和 bucket 报告的兼容方式。
- [x] 若删除 `source`、`sample_bucket` 或 `max_steps` 等现有字段会改变持久化格式，必须版本化，而不是用同一 fingerprint 静默解释为新 schema。
- [x] 若继续保留 CorpWeb 分层抽样，则把 source port 扩展为按 `sample_size`/`seed` 选择并只返回通用 case 的能力，由 CorpWeb adapter 在内部使用专有 bucket；若采用目标通用抽样，则把算法变化作为版本化行为变更。
- [x] 决策完成后删除 `evaluation.py` 中的直接 SQLite 和 CorpWeb schema 逻辑。

### 验证与退出条件

- [x] 只有 `corpweb_sqlite.py` 知道 CorpWeb schema。
- [x] 通用 case 不向 Finder 或模型传递证券专有字段。
- [x] 合法站点过滤、超量样本拒绝、确定性顺序和 fingerprint 通过；当前分层比例和行业多样性在 `DG-06` 改变前保持回归覆盖。
- [x] 已有数据集的读取行为符合 `DG-06` 决策。

## 阶段 11：拆分 Evaluation Result Store

### 目标

创建 `evaluation/store.py`，用 `JsonlEvaluationResultStore` 隐藏 JSONL、manifest、锁、resume 和损坏尾行恢复。

### Facade

当前 Evaluation 运行函数仍可接受原路径参数，但立即构造 `EvaluationResultStore`；Campaign 只通过 `open_campaign()` 取得 `EvaluationStoreSession`，上层调用者不再直接操作文件或锁。

### Expand

- [x] 按目标设计实现 `EvaluationResultStore.open_campaign() -> AsyncContextManager[EvaluationStoreSession]`，由 session 提供 `completed_case_ids()`、`append()` 和 `finish()`。
- [x] 让 `open_campaign()` 在进入上下文时取得本次 campaign 的排他所有权，并在成功、异常和取消退出时释放；接口不暴露 `fcntl` 或文件描述符。
- [x] 迁移安全 run ID、路径展开、manifest 匹配和 campaign 状态。
- [x] 迁移 JSONL 原子追加、同步、尾行修复和结果归属检查。
- [x] 按目标设计迁移 acquisition、等待竞态、部分初始化清理、本地文件锁及取消时的 fd/锁释放。
- [x] 实现目标设计的 session 进入/退出状态契约和异常优先级表，不在本阶段另行选择 cleanup 语义。

### Migrate

- [x] 先迁移纯路径、manifest 和尾行恢复测试。
- [x] 再迁移 session 生命周期、append/resume 和重复 case 跳过测试。
- [x] 然后迁移并发 session 所有权、部分 acquisition 失败、取得所有权时取消竞态、等待者取消、持有者取消、release 自身取消和锁错误测试。
- [x] 最后让当前 Evaluation Facade 的所有持久化经过 `EvaluationStoreSession`。

### Contract

- [x] 删除 campaign、dataset 和 CLI 中的 JSONL、manifest、`fcntl` 和尾行修复逻辑。
- [x] 私有锁测试只有在 Store 公共契约覆盖同一风险后才能删除。现有 `_evaluation_lock` 测试仍覆盖同一风险，作为 Store 内部契约测试保留。
- [x] Contract 必须遵守 `DG-06` 的文件格式和 resume 兼容决策。

### 验证与退出条件

- [x] 同一结果路径的并发运行不重复执行、不重复写入且无死锁。
- [x] 并发去重由一次 campaign session 覆盖“检查已完成 case、执行和追加”的完整临界区，不使用可竞争的 check-then-append。
- [x] 缺少匹配 manifest 时不修改未知结果文件。
- [x] 合法损坏尾行可恢复，完整历史记录不被截断。
- [x] 取消持有者或等待者后锁和 fd 均可释放。
- [x] cleanup 错误不覆盖主要存储或 campaign 异常。

## 阶段 12：拆分 Campaign/Report 并完成收口

### 目标

创建 `evaluation/campaign.py` 和 `report.py`，迁移 Evaluation orchestration，随后删除剩余临时 Facade，完成目标依赖方向。

### Facade

`evaluation.py` 在本阶段开始时只做参数映射和委托。Evaluation CLI 仍保持批准后的命令与输出契约。

### Expand

- [x] 实现 `EvaluationCampaign.run()`，只依赖 `TaskApplication`、Evaluation contracts 和 Store，并在 worker 启动前进入 `open_campaign()` 上下文。
- [x] 迁移 worker 并发、单 case timeout、resume 和逐 case append。
- [x] 把 executor 内部取消与外层 campaign 取消区分。
- [x] 在 `report.py` 实现 `summarize()` 和 `render_markdown()`，同时保留机器可读 Summary 输出。
- [x] 保持完成率、错误分布、耗时统计、成功步骤统计，以及 `DG-06` 决策要求保留的 bucket 汇总。
- [x] 保持 timeout、Finder failure 和 runner exception 的统计分类。

### Migrate

- [x] 先迁移 campaign 并发、timeout 和 resume 测试。
- [x] 再迁移汇总与自报成功率免责声明测试。
- [x] 然后迁移 Evaluation CLI generate/run/summarize。
- [x] 最后搜索并迁移所有旧模块、旧 Facade 和旧包根导入。

### Contract

- [x] 删除单体 `evaluation.py` Facade 及其重复实现。公共入口改为 `evaluation/` 包；`_impl.py` 仅做 generate/run 参数映射。
- [x] 删除已到移除阶段且无调用者的包根旧导出和 `find-job-page`。`--diagnostics-level` 按 `DG-03` 作为公共配置保留。`TaskRunner`、`create_runner`、`RuntimeConfig` 和 `JobPageFinderResult` mapper 不再属于包根承诺，支持范围见 `current-behavior.md`。
- [x] 删除兼容测试前保留每个最终公共边界的一条行为测试。
- [x] 不删除已批准需要跨版本保留的兼容读取器；这类读取器必须有明确支持范围，而不是重新暴露内部模块。

### 验证与退出条件

- [x] Evaluation 只通过 `TaskApplication` 执行任务，不导入 Finder 内部循环。
- [x] Campaign 不知道 SQLite、JSONL、manifest 或文件锁细节。
- [x] Store 不知道 worker、模型或 Finder 语义。
- [x] JSON 和 Markdown 报告保留已批准的完成率、错误分布、耗时、成功步骤和 bucket 汇总。
- [x] 报告明确说明 self-reported success 不代表准确率或召回率。
- [x] 目标模块接口表、包根导出和依赖规则全部满足设计文档。
- [x] 完整单元、契约、Adapter、CLI、Evaluation 和本地 Chromium 测试通过。

## 每阶段统一完成定义

每个阶段只有同时满足以下条件才可进入下一阶段。下列条目是跨阶段闸门，已在各阶段退出时满足；阶段 12 收尾后再次确认：

- [x] Expand、Migrate 和本阶段允许的 Contract 均已完成，不留下没有所有者的临时路径。
- [x] Facade 仍存在时，文档明确它的剩余调用者和计划移除阶段。
- [x] 新增模块拥有面向其稳定接口的测试，旧风险已有替代证据。
- [x] 当前阶段没有引入未批准的 Task、CLI、Diagnostics 或 Evaluation 格式变化。
- [x] 依赖方向检查通过，核心模块没有新增 browser-use、文件系统或 SQLite 泄漏。
- [x] `git diff --check`、Ruff 检查、格式检查和非集成测试通过。
- [x] 涉及 Browser Adapter、CLI 或 Evaluation 外部链路时，对应集成测试通过。
- [x] 阶段变更可以单独评审和回退，不依赖未合并的下一阶段代码才能运行。

标准验证命令为：

```bash
git diff --check
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```

涉及真实 Chromium 或完整外部链路的阶段额外运行：

```bash
uv run pytest -q -m integration
```

模型服务需要凭据或网络时，相关测试应保持为显式可选集成测试；不能读取或输出 `.env` 内容，也不能因为外部服务不可用而削弱本地契约测试。

## 最终验收

全部阶段完成后，系统应满足：

- [x] Task v1 DTO 与 Finder 内部契约通过显式 mapper 隔离。
- [x] `JobPageFinder` 只依赖内部模型、Ports、完成规则和设置。
- [x] browser-use、模型 SDK、诊断文件和 CorpWeb SQLite 分别封装在具体 adapter 中。
- [x] `TaskApplication` 是唯一任务生命周期编排者，Runtime 是唯一组合根。
- [x] Python API、CLI 和 Evaluation 复用同一个 Application 行为。
- [x] 每个 Facade 已删除，或因明确版本兼容承诺而保留并记录支持范围。
- [x] 所有契约决策门已有结论，行为文档和测试与最终实现一致。
