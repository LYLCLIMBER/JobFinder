# JobFinder 当前系统行为基线

## 文档定位

本文档记录从 JobFinder 当前实现、测试和既有验收事实中归纳出的系统行为与稳定边界，是精简测试、修改实现和评审回归影响时的现状基线；它不等同于面向未来的产品规格。

事实来源按优先级为实际代码、已完成任务的交接与验证记录、任务规格和 README。本文档描述当前 `v1` 行为，不把尚未实现的提案写成现有能力。具体功能的设计历史和验收证据仍保留在 [`agent-docs/`](../agent-docs/README.md) 中。

## 当前能力概述

JobFinder 接收企业官网 HTTP(S) URL，在有限步骤和受限浏览器动作内，寻找一个当前可访问且可见 DOM 中包含具体招聘岗位名称的页面，并返回该页面 URL、岗位名称、证据和执行信息。

系统同时提供：

- 版本化任务请求和稳定结果 envelope。
- Python 异步入口 `run_task()` 和命令行入口 `jobfinder run`。
- 分级、本地、按运行隔离的诊断产物。
- 基于 CorpWeb 数据库的无真值运行评估工具。
- 子模块中的领域执行器 `JobPageFinder.find()`，不属于包根兼容承诺。

## 非目标

当前版本不负责：

- 搜索企业官网或调用搜索引擎。
- 枚举企业的全部招聘类别、全部岗位、分页结果或岗位详情。
- 判断企业是否不存在招聘岗位。
- 主动提交申请、填写表单、登录、处理验证码、上传或下载文件；当前没有这些专用动作。
- 向模型开放任意 URL 导航、任意 JavaScript、键盘输入或通用浏览器控制。
- 提供 HTTP API、任务队列、后台 worker、数据库任务历史或管理 UI。
- 提供基于人工真值的准确率、召回率或成功率评估。
- 修改或封装相邻的 `../browser-use` 源码。

## 系统边界

```text
Python API / CLI
        |
        v
build_application() 组合根
        |
        v
TaskApplication 校验、诊断生命周期、错误归一化
        |
        v
JobPageFinder 浏览器循环
        |
        +--> browser-use / Chromium
        +--> DeepSeek 或兼容结构化输出模型
        +--> 本地诊断目录
```

当前仅支持任务版本 `v1` 和任务类型 `find_job_page`。

## 核心术语

- **企业官网 URL**：调用方提供的探索起点，必须是合法 HTTP(S) URL。
- **招聘岗位页面**：当前可访问页面的可见 DOM 中至少包含一个具体岗位名称的页面。
- **具体岗位名称**：例如“Senior Backend Engineer”。产品意图不把 `Careers`、`Jobs`、`招聘`、`加入我们`、`查看职位` 等通用入口视为具体岗位；当前主要由模型提示约束这一点，程序没有通用标题黑名单。
- **步骤**：获取当前状态、让模型选择一个动作并处理该动作的一轮执行。
- **当前浏览器状态**：本步骤取得的 URL、标题、DOM、selector map、页面滚动信息及按配置获取的截图。
- **诊断**：独立于业务结果的本地运行事件和产物；诊断故障不得改变业务结果。

## 功能需求

### 输入和启动

- `FR-001`：领域输入包含 `company_url` 和 `max_steps`。
- `FR-002`：`company_url` 必须是合法 HTTP(S) URL。
- `FR-003`：`max_steps` 默认值为 8，允许范围为 1 至 50。
- `FR-004`：系统直接导航到调用方提供的 URL，不把任意导航动作交给模型。
- `FR-005`：浏览器启动和首次导航共享可配置的初始化超时，默认均受 30 秒上限约束。
- `FR-006`：默认浏览器以无头模式运行，关闭下载、PDF 自动下载、默认扩展和元素高亮，并启用 browser-use 的 IP 地址拦截。

### 探索循环

- `FR-010`：每个步骤最多选择并执行一个 `click`、`scroll`、`wait` 或 `done` 动作。
- `FR-011`：每一步都使用最新浏览器状态；旧状态中的元素或滚动索引不得直接用于新状态。
- `FR-012`：发送给模型的文本上下文包含原始企业 URL、当前 URL、页面标题、滚动位置、步骤进度、上一步结果、受长度限制的 DOM 和当前滚动目标。
- `FR-013`：发送给模型的 DOM 默认最多 40,000 个字符。
- `FR-014`：系统不保留完整模型对话历史，只向下一步提供上一次动作结果摘要。
- `FR-015`：页面内容必须被视为不可信数据。动作 schema 不能据此扩展到四类动作之外；忽略页面内指令当前由系统提示约束模型，不是程序对点击目标的确定性内容校验。
- `FR-016`：每一步有独立的可配置 deadline，默认 30 秒；诊断序列化和写入时间不计入该业务 deadline。
- `FR-017`：模型调用、浏览器状态读取、动作执行和滚动路由观察均受当前步骤 deadline 约束。
- `FR-018`：连续失败达到配置上限时提前结束，默认上限为 2。
- `FR-019`：未提前结束且达到 `max_steps` 时返回 `MAX_STEPS_REACHED`。

### 动作契约

- `FR-020`：`click` 必须引用当前状态 `selector_map` 中存在的正整数索引。
- `FR-021`：`scroll` 方向只能是 `up` 或 `down`；省略 index 或使用 `0` 表示根页面，正整数表示当前步骤发现的内部滚动目标。
- `FR-022`：`wait.seconds` 必须为 1 至 5 的整数。
- `FR-023`：`done` 必须提供非空 `job_title` 和 `evidence`。
- `FR-024`：动作模型拒绝未知字段和未知动作类型。
- `FR-025`：点击和滚动只使用当前状态公开的索引；不存在、方向不可用或已经失效的目标返回动作失败。

### 完成判定

- `FR-030`：只有 `done.job_title` 经 HTML 解码、空白归一化和大小写折叠后出现在当前可见 DOM 文本中，任务才能成功。
- `FR-031`：当前状态有错误、没有可用 DOM 或不是 HTTP(S) 页面时，不接受 `done`。
- `FR-032`：截图中的文字不能单独作为成功证据。
- `FR-033`：成功结果返回当前浏览器状态的实际 URL，而不是必然返回输入 URL。
- `FR-034`：当前实现只要求 `evidence` 非空，不验证完整 evidence 文本是否出现在 DOM；调用方不得把它解释为独立验证过的原文引用。
- `FR-035`：完成校验失败会把原因反馈给下一次模型决策，并计为验证失败。
- `FR-036`：系统提示明确要求模型只报告具体岗位，不把通用招聘入口作为 `done`；当前程序仅校验标题存在于 DOM，不能确定性排除通用标题误报。

### 滚动发现和验证

- `FR-040`：系统从当前 DOM 状态发现可见、位于视口内且具有剩余滚动空间的原生内部滚动容器。
- `FR-041`：已有 selector 索引应复用；额外滚动容器使用当前步骤内临时索引，不修改 browser-use 的 selector map。
- `FR-042`：活跃模态框中的滚动目标排在普通目标之前。
- `FR-043`：滚动只有在根页面或某个已观察目标的实际偏移量发生变化时才按原生滚动成功处理，结果摘要报告实际移动距离。
- `FR-044`：模型请求滚动根页面且根页面没有移动时，系统依次尝试当前方向可用的内部滚动目标，在首个实际移动目标处停止。
- `FR-045`：如果根滚轮已间接移动内部容器，不得再次滚动该容器。
- `FR-046`：根滚轮没有产生偏移变化时，系统以滚动前的实时 URL 为基线，在最多 2 秒、默认每 250 毫秒一次且不超过步骤 deadline 的窗口内观察 URL。
- `FR-047`：origin、path、query 或 fragment 的任何变化均可作为根滚轮触发 SPA route 的成功信号；检测到变化后不再执行内部滚动 fallback。
- `FR-048`：偏移和 URL 均未变化，且所有可用 fallback 均无效果时返回动作失败。
- `FR-049`：当前版本不支持以 CSS transform、Canvas、WebGL、截图变化或 URL 不变的框架状态作为滚动成功判据。

### 条件视觉

- `FR-050`：视觉默认开启，可通过 `RuntimeConfig(use_vision=False)` 或 `JobPageFinder(use_vision=False)` 显式关闭。
- `FR-051`：视觉开启时可以获取当前视口截图，但只有存在可靠候选时才把标注截图加入模型请求。
- `FR-052`：普通视觉候选来自 selector map 中可见、有可靠布局位置但缺少文本、语义属性、可访问名称和已检查后代图片标签的元素。
- `FR-053`：当前可滚动目标即使有文本，也可作为视觉标注候选。
- `FR-054`：候选坐标根据页面滚动位置、视口尺寸和实际截图尺寸映射，只标注达到最小可见比例的区域。
- `FR-055`：标注只发生在内存中的 PNG 副本，模型看到的索引必须与当前动作索引一致。
- `FR-056`：截图缺失、解码失败、坐标无效或绘制失败时退回纯文本上下文，不猜测点击。
- `FR-057`：当前实现没有在多模态模型调用失败后自动重试纯文本请求；该能力不是当前契约。

## 任务运行契约

### 领域结果

直接调用 `JobPageFinder.find()` 返回 `JobPageFinderResult`：

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `success` | boolean | 表示 Finder 是否接受一个 `done` 结果 |
| `job_page_url` | string 或 null | 成功时必须非空，并取自完成时的当前 URL |
| `job_title` | string 或 null | 成功时必须非空 |
| `evidence` | string 或 null | 成功时必须非空，但当前不保证完整文本出现在 DOM |
| `steps` | integer | 已执行或尝试的步骤数 |
| `error` | string 或 null | 失败时的人类可读说明 |
| `error_code` | FinderErrorCode 或 null | 失败时的机器可读分类 |

成功领域结果必须同时具有 `job_page_url`、`job_title` 和 `evidence`。领域错误码范围为 `BROWSER_INITIALIZATION_FAILED`、`BROWSER_INITIALIZATION_TIMEOUT`、`STEP_TIMEOUT`、`MODEL_ERROR`、`ACTION_ERROR`、`VALIDATION_FAILED` 和 `MAX_STEPS_REACHED`。`TaskApplication` 将这些字段映射到统一任务结果；请求、配置和未预期异常产生的顶层错误码只存在于任务运行契约中。

### 请求

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

- `task_id` 缺省时由系统生成；调用方提供非空值时原样保留。
- 请求 envelope 和 payload 都拒绝额外字段。
- 未知版本、未知任务类型和非法 payload 必须在 LLM、Finder 或浏览器装配前失败。

### 成功结果

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

### 失败结果

失败结果必须满足：

- `status` 为 `failed`。
- `output` 为 `null`。
- `error` 包含稳定的 `code`、人类可读 `message` 和 `retryable`。
- `metadata.duration_ms` 是非负整数。
- 不向结果暴露 traceback。

稳定错误码为：

| 错误码 | 含义 | 默认可重试 |
| --- | --- | --- |
| `INVALID_TASK` | 请求版本、结构或 payload 非法 | 否 |
| `UNSUPPORTED_TASK_TYPE` | 任务类型不受支持 | 否 |
| `CONFIGURATION_ERROR` | LLM、环境或运行依赖无法装配 | 否 |
| `BROWSER_INITIALIZATION_FAILED` | 浏览器启动或首次导航失败 | 是 |
| `BROWSER_INITIALIZATION_TIMEOUT` | 浏览器启动或首次导航超时 | 是 |
| `STEP_TIMEOUT` | 当前步骤超过 deadline | 是 |
| `MODEL_ERROR` | 模型调用或结构化响应失败 | 是 |
| `ACTION_ERROR` | 点击、滚动、等待或浏览器动作失败 | 是 |
| `VALIDATION_FAILED` | 连续完成校验失败 | 否 |
| `MAX_STEPS_REACHED` | 步骤预算耗尽 | 否 |
| `INTERNAL_ERROR` | 未预期异常或内部结果不一致 | 是 |

## Python API

- 包根稳定承诺只导出 `run_task`、`TaskRequest`、`TaskResult`、`RuntimeSettings` 和 `DiagnosticsSettings`。
- `run_task()` 是统一异步任务入口，调用 `build_application()` 后委托 `TaskApplication.run()`。
- `build_application()` 是 LLM、Tools、Browser factory、Finder 和诊断的唯一组合根。
- `TaskApplication.run()` 负责请求解析、诊断生命周期、计时、错误归一化和 Finder 调用。
- `JobPageFinder.find()` 可从 `job_page_finder.finder` 直接调用，不属于包根兼容承诺。

## CLI 契约

当前命令：

```text
jobfinder run [TASK_FILE|-] [诊断选项]
jobfinder evaluate generate ...
jobfinder evaluate run ...
jobfinder evaluate summarize ...
```

- `run` 读取 JSONL：每个非空行一个 TaskRequest，立即输出一行 TaskResult。支持文件、`-` 和缺省 stdin。
- 不支持 `find-job-page` 快捷命令、按扩展名猜格式、JSON 数组批量或 CLI `run_batch()`。
- 标准输出只包含机器可读 JSONL；Python logging 写入标准错误。
- 对 `run`：成功退出码为 `0`，任务执行失败为 `1`，非法输入或非法 CLI/Runtime 参数为 `2`，LLM、环境或运行依赖装配产生的 `CONFIGURATION_ERROR` 为 `3`。多行时总退出码优先级为 `3 > 2 > 1 > 0`，遇错继续处理后续行。
- 对 `evaluate generate`、`evaluate run` 和 `evaluate summarize`：评估命令本身正常完成时退出码为 `0`；被归一化为 `EvaluationError` 或 `ValidationError` 的输入及评估错误返回 `2`。`evaluate run` 中单个乃至全部 Finder case 失败会记录在结果和汇总中，但只要评估流程正常完成，CLI 仍返回 `0`。
- 文件不存在、不是 UTF-8、不是合法 JSON 对象行时返回结构化 `INVALID_TASK`，不输出 traceback。
- `--diagnostics-level {basic,diagnostic,raw}` 是公共诊断配置（`DG-03`），不是临时 Facade。

## 配置需求

- 默认 LLM 是 `browser-use` 提供的 `ChatDeepSeek`。
- 显式函数参数优先于环境变量；进程环境变量优先于 `.env` 文件。
- 未显式指定环境文件时，依次检查项目根目录和项目父目录的 `.env`。
- `DEEPSEEK_API_KEY` 必须存在且去除空白后非空。
- 默认模型为 `deepseek-chat`，默认 base URL 为 `https://api.deepseek.com/v1`，默认 temperature 为 `0`。
- 系统不得在日志、结果、诊断或文档中输出 API key 或 `.env` 内容。

## 诊断需求

### 通用行为

- 每次 `run_task()` 默认在 `log/diagnostics/` 下尝试创建独立运行目录。
- 诊断级别为 `basic`、`diagnostic` 或 `raw`，默认 `basic`。
- 正常进入 `finish()` 的可用运行目录会尝试写 manifest、逐行 JSON 事件和最终 result 产物，并通过 `run_id`、`task_id`、步骤、调用或动作 ID 关联。取消路径只保证尝试更新 manifest 和事件，不写 result；所有诊断写入仍是 best-effort。
- `diagnostic` 和 `raw` 默认采集截图；显式截图开关与模型视觉开关相互独立。
- 诊断创建、序列化、写入、容量检查或清理失败不得改变任务原本的成功或失败结果。
- 任务取消必须重新抛出 `CancelledError`，并在可用诊断中标记运行 `aborted`。

### 级别边界

- `basic`：核心生命周期、步骤事件、manifest 和最终结果。
- `diagnostic`：增加 SDK 应用层可取得的模型消息、完成响应、解析决策、页面输入、截图、标注截图和视觉候选。
- `raw`：增加未按模型输入上限截断的 DOM、允许的页面/响应元数据和动作后页面快照。

`raw` 不表示网络层原始数据。系统不采集 headers、cookies、环境变量、完整配置、客户端对象或隐藏推理字段。

### 存储和安全

- 当前诊断不脱敏，manifest 必须标记 `redaction_applied: false`。
- 诊断根目录和运行/产物目录使用受限本地权限；运行目录目标权限为 `0700`，文件目标权限为 `0600`。
- 默认最多保留 100 个受控运行目录，清理超过 7 天的已完成运行。
- 默认单运行普通产物上限为 256 MiB，受控目录总上限为 5 GiB。
- 最终 result 是必要摘要，即使超过普通容量也尝试写入，并标记诊断不完整和摘要预算超限。
- 活动运行、无法识别的目录和不符合受控 manifest 的目录不得被自动清理。

## 运行评估需求

- 数据集生成只读取 CorpWeb 中 `websites.status = VALID` 且 `final_url` 为合法 HTTP(S) URL 的记录。
- 样本按交易所和板块组成的 bucket 比例分层，并在 bucket 内轮询行业以增加多样性。
- 相同输入、样本数和 seed 必须产生确定性样本及稳定数据集 fingerprint。
- 评估数据集只包含通用企业信息、实际 URL、来源、抽样 bucket 和步骤预算，不把证券市场明细传给 Finder。
- 评估执行支持正数 worker 并发和正数 case timeout，每个完成记录立即追加并同步到结果 JSONL。
- 相同数据集 fingerprint、run ID 和运行配置可以断点续跑，并跳过已有 case。
- run ID 必须是单个安全路径组件，禁止绝对路径和目录穿越。
- 同一路径的并发评估通过本地文件锁串行保护，避免重复写入。
- 结果尾部的崩溃残片可以在 manifest 校验后修复；非空结果缺少匹配 manifest 时拒绝继续。
- 汇总区分 Finder 成功、Finder 失败、case 超时和 runner 异常，并提供完成率、错误分布、耗时、成功步骤和 bucket 汇总。
- 评估没有人工真值；`self_reported_success_rate` 只表示 JobFinder 自报成功，不得解释为准确率或召回率。

## 安全与可靠性需求

- `NFR-001`：任何成功、失败、初始化异常或超时路径都必须尝试关闭浏览器；清理异常不得覆盖主结果。
- `NFR-002`：取消信号不得被归一化为普通失败，资源和诊断完成清理后必须继续传播。
- `NFR-003`：任务日志只记录 task ID、类型、状态、耗时和错误码等摘要，不记录密钥、完整 DOM、截图 Base64 或完整模型上下文。
- `NFR-004`：页面内容不能扩大允许动作；系统不提供表单填写、登录、文件操作、键盘输入或任意脚本动作。模型提示禁止通过普通点击推进这些流程，但当前点击执行器没有按控件用途进行确定性拦截。
- `NFR-005`：未知请求字段必须被拒绝，避免调用方误以为未支持配置已经生效。
- `NFR-006`：诊断属于 best-effort 辅助能力，业务结果优先于诊断故障。
- `NFR-007`：生产代码不得依赖企业、域名、招聘系统或网站 CSS 的专用规则。
- `NFR-008`：项目支持 Python 3.11 至 3.x，当前构建约束为 `>=3.11,<4.0`。

## 验收场景

以下场景构成项目级最小行为基线，测试精简后仍应有对应证据：

- `AC-001`：当前页面直接包含具体岗位时，任务成功并返回当前 URL、岗位名称、非空 evidence 和步骤数。
- `AC-002`：从企业首页点击招聘入口后，使用真实本地 Chromium 到达岗位页并成功。
- `AC-003`：系统提示明确禁止把通用招聘文字作为岗位；页面没有具体岗位且模型遵守动作契约时，达到步骤预算后返回 `MAX_STEPS_REACHED`。
- `AC-004`：模型报告的岗位名称不在当前可见 DOM 时拒绝完成，并最终使用 `VALIDATION_FAILED` 或继续探索。
- `AC-005`：过期点击索引和不可用滚动索引不会被执行为有效动作。
- `AC-006`：根页面不可滚动时，可发现并滚动真实内部 CSS Scroll Snap 容器，随后发现岗位。
- `AC-007`：根滚轮不改变偏移但触发延迟 SPA URL 变化时，动作被识别为成功且不执行内部 fallback。
- `AC-008`：视觉候选存在时模型收到带当前索引的标注截图；候选不存在或视觉关闭时使用纯文本输入。
- `AC-009`：非法任务在依赖装配和浏览器启动前返回结构化错误。
- `AC-010`：Python API、CLI 快捷命令和 JSON 文件入口遵守同一结果及错误码契约。
- `AC-011`：浏览器启动失败、步骤超时、模型失败、动作失败、校验失败和预算耗尽映射为稳定错误码。
- `AC-012`：所有正常和异常退出路径尝试关闭浏览器，清理失败不覆盖主结果。
- `AC-013`：三级诊断遵守各自产物边界，截图采集不改变非视觉模型输入。
- `AC-014`：诊断写入、容量或清理失败不改变任务结果，并在可用 manifest 或事件中留下故障摘要。
- `AC-015`：并发诊断运行相互隔离，受控目录容量和文件权限符合配置。
- `AC-016`：评估样本生成确定、只选合法站点，并保持通用数据集 schema。
- `AC-017`：评估支持受限并发、case 超时、断点续跑、并发锁和中断尾行恢复。
- `AC-018`：评估汇总明确标注成功率为自报运行指标，不声称准确率或召回率。

## 测试保留原则

精简测试时按以下优先级保留证据：

1. 保留覆盖上述验收场景的系统行为测试。
2. 为请求/结果 schema、错误码、动作约束、诊断级别和评估文件格式保留模块契约测试。
3. 同一行为跨 Runner、Runtime 和 CLI 重复时，每个公开边界只保留证明该边界职责的一条测试。
4. 除非内部机制承担明确的安全、并发或数据完整性风险，不保留源码文本、私有方法、精确调用次数、完整 prompt 文本或内部事件完整顺序测试。
5. 实现重构后只要外部契约和验收场景不变，测试不应因内部调用顺序变化而失败。

## 已知限制

- 完整 `evidence` 没有经过 DOM 包含校验，只有 `job_title` 被确定性验证。
- 程序没有判断 DOM 中的标题是否只是 `Careers`、`Jobs`、`招聘` 等通用入口；“必须是具体岗位”目前依赖模型遵守系统提示。
- 普通 `click` 只校验当前 DOM 索引是否存在，不判断目标是否为登录、表单提交、下载或页面诱导控件；避免这些操作目前依赖模型遵守系统提示和浏览器下载配置。
- 多模态模型调用失败后不会自动降级重试纯文本。
- 内部滚动容器发现只读依赖 browser-use 的私有 DOM `_root`，依赖升级时需要复核。
- URL 不变且偏移不变的全屏切页、虚拟滚动和 Canvas/WebGL 交互无法确认成功。
- 诊断不脱敏，必须保存在受控位置。
- 默认 `max_steps=8` 对复杂、动态或第三方招聘系统可能不足。
- `block_ip_addresses=True` 的默认浏览器安全配置可能限制内网或本地地址；本地 Chromium 测试使用单独 profile 明确关闭该限制。
- 部分评估文件系统操作产生的非 `EvaluationError`/`ValidationError` 异常没有被 CLI 统一包装，可能直接退出并产生不同于结构化错误码 2 的进程结果。

## 未来提案

分类发现、同时返回校招/社招/实习列表、`complete`/`partial`/`not_found` 等行为目前仍属于草案，不是本 `v1/find_job_page` 契约。若该提案进入实施，应先决定新增任务类型还是升级现有 schema，并更新本文档、任务结果模型和项目级验收场景。

## 剩余内部入口

下列类型可从子模块导入，不属于包根稳定承诺，也不是跨版本兼容 Facade：

- `job_page_finder.runtime.create_runner()` / `RuntimeConfig`：测试与旧组合参数的内部桥接，生产入口使用 `build_application()` 和 `RuntimeSettings`。
- `job_page_finder.runner.TaskRunner`：薄委托，生产生命周期由 `TaskApplication` 拥有。
- `job_page_finder.models.JobPageFinderInput` / `JobPageFinderResult`：Finder 内部契约；Task v1 通过显式 mapper 隔离。
- `job_page_finder.diagnostics.DiagnosticWriter`：文件诊断 adapter 实现细节。

## 代码映射

| 领域 | 主要代码 |
| --- | --- |
| 输入、动作、领域结果 | `src/job_page_finder/models.py` |
| 浏览器循环和完成校验 | `src/job_page_finder/finder.py` |
| 内部滚动目标 | `src/job_page_finder/scrolling.py` |
| 条件视觉 | `src/job_page_finder/vision.py` |
| Task v1 协议 | `src/job_page_finder/task_protocol.py` |
| 任务生命周期 | `src/job_page_finder/application.py` |
| 组合根 | `src/job_page_finder/runtime.py` |
| 公共 Python API | `src/job_page_finder/api.py` |
| 环境和 LLM | `src/job_page_finder/config.py` |
| CLI | `src/job_page_finder/cli.py` |
| 诊断 | `src/job_page_finder/diagnostics.py` |
| 运行评估 | `src/job_page_finder/evaluation/` |

## 外部依赖

- Python `>=3.11,<4.0`。
- 相邻只读 editable 依赖 `../browser-use`。
- Pydantic v2。
- Pillow。
- `python-dotenv`。
- DeepSeek 或接口兼容且支持结构化输出的模型；视觉开启时需要图片输入能力。
- Chromium；本地并发锁和诊断锁依赖支持 `fcntl` 的运行环境。
