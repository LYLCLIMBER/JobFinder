# 测试风险保障清单

## 文档目的

本文档逐项说明当前 141 个测试函数提供的风险保障，用于决定测试应保留、改写、合并还是删除。参数化展开后，pytest 当前收集 168 个 case。当前系统行为基线见 [`current-behavior.md`](current-behavior.md)。

参数化测试按一个测试函数记录，并在行为栏注明参数分支。`tests/conftest.py` 只有 fixture，不作为独立测试列出。

## 判断口径

第二问使用以下答案：

- **是**：测试主要验证公开结果、安全边界或明确的运维契约；功能相同就应该继续通过。
- **核心应该，当前不一定**：保护的核心行为应保持，但当前断言还绑定调用次数、消息文本、事件顺序、文件布局或具体协作者。
- **否**：测试主要固定源码组织、私有 API 或具体算法；等价实现不应被要求通过原测试。

“删除风险”表示没有其他测试补位时失去的保障，不表示该测试一定应原样保留。

## 阶段 0 契约与依赖测试

### `tests/contracts/test_task_v1.py`

| 测试 | 1. 保护的行为 | 2. 等价重写后还应通过吗？ | 3. 删除后失去的风险保障 |
| --- | --- | --- | --- |
| `test_task_request_schema_preserves_current_v1_payload` | Task v1 当前请求字段和 URL 规范化保持不变，payload 使用独立 `FindJobPageTaskPayload` 而非内部或旧 Finder 输入。 | 是。 | 请求 schema 可能静默变化或重新耦合内部领域类型。 |
| `test_task_request_rejects_unknown_or_invalid_fields`（五种非法输入） | envelope/payload 未知字段、非法 URL 和越界步骤均被拒绝。 | 是。 | 未支持输入可能被静默接受或触发执行副作用。 |
| `test_parse_task_generates_identity_and_exposes_structured_protocol_errors` | 缺省 task ID 被生成，协议错误携带稳定 code、task ID 和 task type。 | 是。 | 前置失败可能失去关联身份或迫使调用者解析异常文案。 |
| `test_task_success_json_contract_keeps_duration_in_metadata` | 成功 JSON 保持当前 v1 字段、空 error 和 `metadata.duration_ms`。 | 是；`DG-01` 决策生效前必须通过。 | 阶段 1 可能未版本化就移动 duration 或改变成功 envelope。 |
| `test_task_failure_json_contract_and_retryability`（全部 11 个错误码） | 失败 JSON、空 output、非负 duration 和 retryable 矩阵保持稳定。 | 是。 | 错误分类或重试策略可能漂移。 |
| `test_task_result_rejects_inconsistent_status_payloads` | 成功/失败状态与 output/error 必须一致。 | 是。 | 可构造自相矛盾的 TaskResult。 |

### `tests/contracts/test_dependencies.py`

| 测试 | 1. 保护的行为 | 2. 等价重写后还应通过吗？ | 3. 删除后失去的风险保障 |
| --- | --- | --- | --- |
| `test_core_modules_do_not_import_browser_use` | 新目标核心模块禁止导入 `browser_use`；旧 `finder.py` 仅作为阶段 5 到期的显式例外。 | 是。 | 后续新核心模块可能重新泄漏具体 SDK 依赖。 |

### `tests/contracts/test_internal_contracts.py`

| 测试 | 1. 保护的行为 | 2. 等价重写后还应通过吗？ | 3. 删除后失去的风险保障 |
| --- | --- | --- | --- |
| `test_task_payload_maps_explicitly_to_internal_and_legacy_requests` | Task payload 先显式映射为内部请求，再由临时 mapper 映射到旧 Finder 输入。 | 是。 | 外部协议与内部领域可能重新直接耦合。 |
| `test_legacy_success_maps_to_complete_discriminated_result` | 旧成功结果转换为字段完整、带 evidence 来源的内部成功分支。 | 是。 | mapper 可能丢失岗位、证据或来源 URL。 |
| `test_legacy_mapper_preserves_root_url_and_unconstrained_steps` | 临时 mapper 保留旧 executor 接受的原始根 URL 和 steps 值。 | 是，直到阶段 5 移除旧 mapper。 | 结构迁移可能静默规范化或拒绝原 Facade 可返回的数据。 |
| `test_legacy_failure_maps_code_retryability_and_steps` | 旧失败结果转换为带稳定 code、retryable 和 steps 的内部失败分支。 | 是。 | 错误映射和重试语义可能漂移。 |
| `test_internal_result_union_rejects_incomplete_success` | 内部判别联合不能表达缺少 evidence 的成功。 | 是。 | 不完整成功可能绕过 Task 边界。 |

### 阶段 2 Core 与 Ports 契约

`test_core_models.py` 固定 adapter-neutral 动作约束；`test_ports.py` 通过内存实现者执行最小 Finder 协作，并分别覆盖成功、动作异常、超时、主异常、调用方取消、cleanup 异常与 cleanup 自身取消。删除这些测试会使 SDK 类型重新进入核心、Ports 签名失配或取消与清理优先级漂移而缺少前置门禁。

### 阶段 3 Browser Adapter 契约

`tests/adapters/browser_use/` 固定 browser-use 状态转换、generation-scoped 元素引用、安全 profile、部分启动失败回收、实时 URL、幂等有界关闭、force fallback 和取消来源。滚动与视觉测试已迁到 adapter 模块路径；Finder 行为测试现在经 `BrowserUseFactory.open()` 执行；本地 Chromium 同时覆盖 adapter 点击、显式内部滚动和 root fallback。删除这些测试会使 SDK 私有结构重新泄漏、旧引用误操作新元素、滚动/视觉行为漂移或 Chromium 资源在异常与取消路径残留。

## 配置测试

### `tests/test_config.py`

| 测试 | 1. 保护的行为 | 2. 等价重写后还应通过吗？ | 3. 删除后失去的风险保障 |
| --- | --- | --- | --- |
| `test_creates_deepseek_client_from_env_file`（第 8 行） | 显式 `.env` 能配置 DeepSeek key、model、base URL，且 temperature 为 0。 | 是；这是配置结果契约。 | `.env` 加载或客户端关键参数映射错误。 |
| `test_process_environment_takes_precedence_over_env_file`（第 25 行） | 进程环境变量优先于 `.env`。 | 是；这是配置优先级契约。 | 部署环境无法覆盖文件配置，可能使用错误凭据。 |
| `test_requires_deepseek_api_key`（第 36 行） | 缺少有效 API key 时在装配阶段明确失败。 | 是；异常类型和配置项提示属于调用契约。 | 缺失凭据被延迟到模型调用时才暴露。 |
| `test_load_environment_rejects_missing_explicit_file`（第 46 行） | 调用方显式指定不存在的环境文件时不静默忽略。 | 是。 | 路径拼写或部署错误被隐藏。 |
| `test_env_file_is_ignored_by_git`（第 54 行） | 仓库 `.gitignore` 包含 `.env`。 | 否；它固定仓库文本，不是运行功能。 | 缺少一条防止凭据文件被误提交的仓库安全检查。 |

## Finder 测试

### `tests/test_finder.py`

| 测试 | 1. 保护的行为 | 2. 等价重写后还应通过吗？ | 3. 删除后失去的风险保障 |
| --- | --- | --- | --- |
| `test_completes_when_home_page_contains_a_job`（第 204 行） | 初始页面的 DOM 岗位可直接完成，返回 URL、标题、evidence 和步数，并清理浏览器。 | 核心应该，当前不一定；截图请求次数、导航记录和精确步数绑定编排。 | 首页直接成功、结果字段或正常清理回归。 |
| `test_sends_annotated_screenshot_when_textless_candidate_exists`（第 232 行） | 视觉开启且有无文本候选时，模型收到标注截图。 | 核心应该，当前不一定；断言固定消息 part 位置和文本。 | 条件视觉链路可能静默失效。 |
| `test_rejects_unverified_done_and_returns_feedback_to_model`（第 284 行） | 拒绝 DOM 中不存在的岗位标题，并允许模型根据反馈改正。 | 核心应该，当前不一定；完整反馈文案和消息位置可变化。 | 幻觉岗位可能被接受，或模型没有纠错信息。 |
| `test_fails_at_max_steps_when_no_specific_job_is_found`（第 309 行） | 达到步骤预算后返回 `MAX_STEPS_REACHED` 并清理浏览器。 | 核心应该，当前不一定；完整错误文案、调用次数和清理观测被固定。 | 任务可能不终止、错误分类漂移或资源泄漏。 |
| `test_root_scroll_falls_back_to_internal_target_and_reports_actual_distance`（第 331 行） | 根滚动无效时尝试内部容器，并报告实际位移。 | 核心应该，当前不一定；固定两次工具调用、索引和反馈文本。 | 内部滚动页面不可探索，或无效滚动被误报成功。 |
| `test_root_gesture_that_moves_internal_target_does_not_scroll_twice`（第 352 行） | 根手势已移动内部容器时避免重复滚动。 | 核心应该，当前不一定；固定工具调用次数和检测策略。 | 重复滚动可能跳过岗位内容。 |
| `test_scroll_detects_delayed_route_change_without_internal_fallback`（第 371 行） | 根滚轮触发延迟 URL 变化时视为成功且不执行内部 fallback。 | 核心应该，当前不一定；固定轮询次数、截图次数和反馈文本。 | SPA 全屏切页被误判为失败并发生额外滚动。 |
| `test_scroll_uses_live_url_as_route_baseline`（第 396 行） | 滚动前以实时 URL 而非旧页面状态 URL 作为 route 基线。 | 核心应该，当前不一定；“如何取得基线”是算法细节。 | 模型思考期间发生的导航可能被错误归因给滚轮。 |
| `test_scroll_without_actual_offset_change_is_an_action_error`（第 416 行） | 显式滚动未产生任何实际位移时返回 `ACTION_ERROR`。 | 核心应该，当前不一定；URL 查询次数和工具索引是内部断言。 | 无效滚动被当成成功，浪费步骤并掩盖不可滚动状态。 |
| `test_scroll_route_polling_stops_at_step_deadline`（第 430 行） | route 观察窗口不能越过步骤 deadline，最终错误为 `STEP_TIMEOUT`。 | 核心应该，当前不一定；轮询次数不是功能契约。 | URL 轮询可能突破任务超时并长期阻塞。 |
| `test_explicit_unavailable_scroll_target_is_rejected` | 不存在的显式滚动索引被拒绝。 | 核心应该，当前不一定；错误文案片段被固定。 | 不存在索引可能被执行。 |
| `test_explicit_scroll_target_rejects_an_unavailable_direction` | 已观察目标在请求方向没有剩余空间时返回动作失败且不执行滚动。 | 是。 | 模型可能对方向不可用的目标执行危险或无效动作。 |
| `test_rejects_an_index_from_an_old_browser_state`（第 464 行） | DOM 更新后拒绝旧点击索引。 | 核心应该，当前不一定；具体工具调用列表可变化。 | 页面变化后可能点击错误甚至危险的控件。 |
| `test_kills_browser_when_startup_fails`（第 486 行） | 浏览器启动失败返回初始化错误，并仍尝试清理会话。 | 核心应该，当前不一定；完整错误文本被固定。 | 启动异常可能泄漏浏览器进程。 |
| `test_enforces_step_timeout`（第 502 行） | 单步超时返回 `STEP_TIMEOUT` 并清理浏览器。 | 核心应该，当前不一定；完整超时文本被固定。 | 模型、状态读取或工具卡死时任务无法受控退出。 |
| `test_enforces_browser_initialization_timeout`（第 519 行；`start`、`navigate`） | 浏览器启动和首次导航两个阶段都受初始化超时约束。 | 核心应该，当前不一定；完整超时文本被固定。 | 任一初始化阶段可能无限占用资源。 |
| `test_classifies_model_errors_from_the_llm_path`（第 539 行） | LLM 异常按来源归类为 `MODEL_ERROR`，不根据异常文案猜测。 | 核心应该，当前不一定；错误包装文本也被断言。 | 模型故障错误码不稳定，误导重试和诊断。 |
| `test_classifies_validation_failures_from_done_rejection`（第 554 行） | 连续 `done` 拒绝达到上限后返回 `VALIDATION_FAILED`。 | 是。 | 无法区分结果校验失败和动作/模型故障。 |
| `test_cleanup_exception_does_not_override_result`（第 574 行） | 浏览器清理异常不覆盖已经得到的成功结果。 | 是。 | 用户成功结果可能被次要清理故障覆盖。 |
| `test_cancellation_cleans_up_browser_and_propagates` | 调用方在页面观察期间取消时仍尝试关闭浏览器，并传播 `CancelledError`。 | 是。 | 取消可能被归一化或遗留浏览器资源。 |

## 滚动模块测试

### `tests/test_scrolling.py`

| 测试 | 1. 保护的行为 | 2. 等价重写后还应通过吗？ | 3. 删除后失去的风险保障 |
| --- | --- | --- | --- |
| `test_discovers_visible_targets_and_filters_by_direction`（第 55 行） | 只发现可见且有剩余空间的原生滚动目标，并按方向过滤。 | 是；这是模块输出契约。 | 无效、不可见或错误方向的容器可能交给模型。 |
| `test_active_modal_targets_are_listed_first`（第 68 行） | 活跃模态框中的滚动容器优先并带正确标记。 | 是；当前这是明确选择策略。 | 页面主体可能抢在模态岗位列表之前被滚动。 |
| `test_reuses_existing_index_and_allocates_after_selector_map`（第 84 行） | 复用已有 selector 索引，并避免给额外滚动目标分配冲突索引。 | 核心应该，当前不一定；具体采用“最大值加一”可替换。 | 点击和滚动索引可能冲突或映射错误。 |

## 视觉模块测试

### `tests/test_vision.py`

| 测试 | 1. 保护的行为 | 2. 等价重写后还应通过吗？ | 3. 删除后失去的风险保障 |
| --- | --- | --- | --- |
| `test_build_visual_context_marks_textless_visible_elements`（第 44 行） | 可见、无文本且坐标可靠的元素得到索引标注和 PNG data URL。 | 是。 | 图片按钮等无文本入口无法被视觉模型定位。 |
| `test_build_visual_context_excludes_accessible_and_invalid_elements`（第 60 行） | 排除已有文本语义、无坐标或视口外元素；无候选时返回 `None`。 | 是。 | 视觉候选噪声、无效坐标或不必要图片请求增加。 |
| `test_build_visual_context_excludes_descendant_image_labels`（第 80 行） | 后代图片已有语义标签时不把父元素当作无文本候选。 | 核心应该，当前不一定；具体后代语义启发式可替换。 | 同一语义元素可能被重复标注并误导模型。 |
| `test_build_visual_context_accounts_for_scroll_position`（第 87 行） | 文档绝对坐标结合滚动位置正确映射到当前视口。 | 是。 | 页面滚动后标注位置可能错位。 |
| `test_build_visual_context_marks_scroll_target_even_when_it_has_text`（第 100 行） | 有文本的内部滚动目标仍进入视觉标注。 | 是。 | 模型可能看不到可滚动岗位容器对应的动作索引。 |

## Runner 测试

### `tests/test_runner.py`

| 测试 | 1. 保护的行为 | 2. 等价重写后还应通过吗？ | 3. 删除后失去的风险保障 |
| --- | --- | --- | --- |
| `test_runner_facade_reexports_task_protocol_dto` | Runner Facade 重导出协议 DTO，旧导入路径保持有效。 | 是，直到阶段 7 的 Facade 决策。 | 阶段 1 会提前破坏旧 Python 导入路径。 |
| `test_run_returns_unified_success_result`（第 32 行） | 合法请求映射为完整统一成功结果，输入 URL 被规范化后传给 Finder。 | 是。 | 成功 envelope、领域字段或输入转换回归。 |
| `test_generates_task_id_when_missing_and_preserves_provided_id`（第 57 行） | 缺省 ID 自动生成，调用方 ID 保留并贯穿日志。 | 核心应该，当前不一定；精确日志前缀和条数可变化。 | 结果与日志无法稳定关联，调用方 ID 被改写。 |
| `test_rejects_invalid_requests_before_calling_finder`（第 85 行） | 未知版本、额外字段和非法 payload 在执行前被拒绝。 | 是；不触发 Finder 是安全副作用契约。 | 非法输入可能启动模型或浏览器，并产生错误副作用。 |
| `test_rejects_unknown_task_type_before_calling_finder`（第 108 行） | 未支持任务类型返回 `UNSUPPORTED_TASK_TYPE` 且不执行 Finder。 | 是。 | 未知任务可能被误路由到现有执行器。 |
| `test_maps_finder_error_code_without_parsing_message`（第 126 行） | Runner 按结构化 Finder 错误码映射失败和 retryable，不解析消息。 | 是。 | 错误文案变化可能改变机器错误分类。 |
| `test_legacy_executor_result_preserves_root_url_and_steps` | 旧自定义 executor 的根 URL 和 steps 在 Task v1 输出中原样保留。 | 是，直到旧 executor Facade 移除。 | mapper 的 URL 规范化或新领域约束可能改变旧输出。 |
| `test_legacy_failure_without_code_keeps_message_as_internal_error` | 缺少 Finder 错误码时仍映射为 `INTERNAL_ERROR` 并保留原错误消息。 | 是。 | 临时 mapper 异常可能覆盖调用者可见的原失败原因。 |
| `test_unhandled_exception_becomes_internal_error_without_traceback`（第 148 行） | 未处理异常变为 `INTERNAL_ERROR`，输出不含 traceback。 | 是。 | 异常穿透或内部堆栈泄露给调用方。 |
| `test_logs_task_metadata_without_sensitive_fields`（第 166 行） | 普通成功路径的日志只观察到 task ID、类型、状态和耗时；测试还做了无密钥/HTML/Base64 字符串检查，但输入没有注入这些带毒内容。 | 核心应该，当前不一定；具体日志文案可变化，敏感字段断言是弱保障。 | 会失去普通路径日志摘要保障；当前测试本就不能可靠证明敏感输入不会泄露。 |
| `test_incomplete_finder_success_is_internal_error`（第 188 行） | Runner 防御 Finder 声称成功但缺少岗位字段的非法结果。 | 核心应该，当前不一定；测试用 `model_construct` 绕过正常模型约束。 | 边界被绕过时下游可能收到不可用的伪成功。 |
| `test_successful_finder_result_requires_job_fields`（第 208 行） | 领域模型禁止构造缺少 URL、标题或 evidence 的成功结果。 | 是。 | 不完整成功状态可进入 Runner 或外部调用方。 |
| `test_accepts_typed_task_request`（第 215 行） | Runner 同时接受已经校验的 `TaskRequest` 对象。 | 是。 | 内部调用方可能被迫退回 dict 或走不一致路径。 |

## Runtime 测试

### `tests/test_runtime.py`

| 测试 | 1. 保护的行为 | 2. 等价重写后还应通过吗？ | 3. 删除后失去的风险保障 |
| --- | --- | --- | --- |
| `test_create_runner_is_the_only_composition_root`（第 13 行） | CLI 和 runtime 不重复装配 Finder、LLM 与 Runner。 | 否；直接检查源码字符串和类构造位置。 | 架构约束可能退化，多个入口形成不同装配逻辑。 |
| `test_create_runner_wires_injected_dependencies`（第 26 行） | 组合根把注入 LLM、Tools、browser factory 和 RuntimeConfig 传给 Finder。 | 核心应该，当前不一定；捕获具体构造 kwargs。 | 注入依赖或运行参数可能被忽略。 |
| `test_default_vision_is_enabled_and_can_be_explicitly_disabled`（第 60 行） | Runtime 和 Finder 默认启用视觉，显式 `False` 能传递到底层。 | 是。 | 默认能力漂移或关闭开关失效。 |
| `test_run_task_uses_create_runner_unless_runner_is_injected`（第 78 行） | 默认自动装配 Runner，显式注入时复用该 Runner。 | 核心应该，当前不一定；固定 `create_runner` 调用次数。 | 注入失效、重复装配或测试路径与生产路径分叉。 |
| `test_run_task_default_diagnostics_root_uses_test_tmp_path`（第 113 行） | pytest 自动 fixture 将隐式诊断根隔离到测试临时目录。 | 核心应该，当前不一定；目录个数是具体布局断言。 | 测试运行可能污染生产默认 `log/diagnostics`。 |
| `test_run_task_maps_assembly_failures_to_configuration_error`（第 139 行） | 依赖装配异常变为不可重试 `CONFIGURATION_ERROR`。 | 是。 | 缺少凭据等配置故障被误报为业务或内部错误。 |
| `test_run_task_rejects_invalid_requests_before_create_runner`（第 163 行） | 请求校验发生在 LLM、Finder 和浏览器装配前。 | 是；无副作用的前置拒绝是安全契约。 | 非法请求也可能读取配置或创建昂贵依赖。 |
| `test_run_task_logs_invalid_requests`（第 231 行） | 无效请求仍产生 started/finished 失败生命周期日志。 | 核心应该，当前不一定；固定精确事件顺序和文本。 | 前置失败任务无法审计或日志不成对。 |
| `test_run_task_logs_unsupported_task_type`（第 260 行） | 未支持任务类型也产生完整失败生命周期日志。 | 核心应该，当前不一定；与上一测试高度相似。 | 未知任务缺少审计记录或错误类型不可见。 |
| `test_run_task_logs_configuration_errors`（第 289 行） | 装配失败前已有 started，之后有带错误码的 finished。 | 核心应该，当前不一定；检查装配发生前的精确日志时序。 | 配置阶段失败在日志中只有半条生命周期。 |
| `test_run_task_does_not_duplicate_lifecycle_logs`（第 324 行） | Runtime 委托 Runner 时只记录一对生命周期日志。 | 核心应该，当前不一定；实现可换成其他去重机制。 | 日志和指标重复计数。 |

## CLI 测试

### `tests/test_cli.py`

| 测试 | 1. 保护的行为 | 2. 等价重写后还应通过吗？ | 3. 删除后失去的风险保障 |
| --- | --- | --- | --- |
| `test_find_job_page_shortcut_prints_json_and_exits_zero`（第 35 行） | 快捷命令成功返回 0，stdout 是统一成功 JSON。 | 是。 | shell 调用方无法稳定解析成功结果。 |
| `test_cli_default_diagnostics_root_uses_test_tmp_path`（第 60 行） | CLI 测试的隐式诊断目录也被隔离。 | 核心应该，当前不一定；目录计数绑定布局。 | CLI 测试污染工作区默认诊断目录。 |
| `test_run_json_file_uses_the_same_result_contract`（第 77 行） | JSON 文件入口与快捷入口使用同一结果 envelope。 | 是。 | 两种 CLI 入口输出不兼容。 |
| `test_invalid_json_file_exits_nonzero_with_structured_error`（第 111 行） | 非法 JSON 返回退出码 2、结构化错误且不泄露 traceback。 | 是。 | 文件解析错误可能产生 argparse 文本或崩溃输出。 |
| `test_unknown_task_type_exits_nonzero`（第 126 行） | 未支持任务类型在 CLI 映射为输入错误和稳定错误码。 | 是。 | 调用方无法区分未知任务和执行失败。 |
| `test_task_failure_uses_exit_code_one`（第 145 行） | Finder 领域失败映射为退出码 1、失败 JSON 和空 output。 | 是。 | 自动化无法根据退出码识别业务执行失败。 |
| `test_configuration_error_uses_exit_code_three`（第 166 行） | LLM/环境/依赖装配失败映射为退出码 3。 | 是。 | 配置故障与输入错误或任务失败混淆。 |
| `test_unreadable_task_file_exits_nonzero_with_structured_error`（第 180 行） | UTF-16/解码错误和读取权限错误均返回结构化输入错误。 | 核心应该，当前不一定；权限分支 monkeypatch `Path.read_text`。 | 文件 I/O 异常可能崩溃或泄露 traceback。 |
| `test_invalid_shortcut_is_not_masked_by_configuration_error`（第 211 行） | 非法 URL 优先返回输入错误，不被缺失配置覆盖。 | 是；错误优先级是公开契约。 | 用户修错方向错误，且非法输入触发不必要装配。 |
| `test_usage_error_prints_json_on_stdout`（第 225 行） | 缺少命令等 argparse 用法错误也输出 JSON。 | 是。 | 机器调用方需要兼容两种错误格式。 |
| `test_evaluate_generate_prints_manifest`（第 235 行） | `evaluate generate` 传递 db、output、sample size、seed 并打印 manifest。 | 核心应该，当前不一定；主要验证参数转发给特定函数。 | CLI 参数可能被忽略，生成结果与请求不一致。 |
| `test_evaluate_run_passes_bounded_execution_options`（第 265 行） | `evaluate run` 传递 workers、case timeout、run ID 并打印汇总。 | 核心应该，当前不一定；通过替换协作者捕获 kwargs。 | 并发和超时选项可能不生效。 |
| `test_evaluate_run_derives_result_and_diagnostics_paths`（第 298 行） | 默认结果和诊断路径由 dataset、run ID、诊断级别稳定推导。 | 是。 | 产物散落、覆盖或无法关联同一次评估。 |
| `test_evaluate_run_creates_default_sidecars`（第 330 行） | 默认评估运行实际生成 results、manifest、lock sidecar 和诊断配置。 | 核心应该，当前不一定；固定 sidecar 文件名和 `_default_run_task` 注入点。 | 缺少恢复、锁或诊断所需的伴随文件。 |
| `test_evaluate_run_rejects_unsafe_run_id`（第 385 行；四种危险值） | 拒绝路径穿越、嵌套和绝对 run ID，且不启动评估。 | 是。 | 结果或诊断可能写出目标目录。 |
| `test_evaluate_reports_invalid_input_as_json`（第 412 行） | `EvaluationError` 映射为退出码 2 和 `INVALID_EVALUATION` JSON。 | 核心应该，当前不一定；替换具体生成函数制造异常。 | 评估错误输出格式不稳定。 |

## 诊断

### `tests/test_diagnostics.py`

| 测试 | 1. 保护的行为 | 2. 等价重写后还应通过吗？ | 3. 删除后失去的风险保障 |
| --- | --- | --- | --- |
| `test_diagnostic_levels_gate_artifacts`（第 130 行；`basic`/`diagnostic`/`raw`） | 三级诊断分别保存允许的产物、禁止越级产物，并生成可关联的 manifest 和事件。 | 核心应该，当前不一定；精确 artifact kind 和事件名属于当前磁盘协议。 | 诊断可能缺关键证据，或低级别意外保存高敏感/高成本数据。 |
| `test_screenshot_capture_does_not_change_nonvision_model_input`（第 157 行；截图关/开） | 诊断截图开关只改变状态采集，不改变非视觉模型输入。 | 核心应该，当前不一定；它比较具体消息对象并检查调用参数。 | 开启诊断可能改变业务决策或模型成本。 |
| `test_raw_records_post_action_snapshot_and_concurrent_runs_are_isolated`（第 179 行） | 并发 raw 运行各用独立目录，并分别保存动作后快照。 | 核心应该，当前不一定；目录枚举和 artifact 名称固定存储实现。 | 并发诊断可能串写、覆盖或丢失动作后状态。 |
| `test_raw_action_snapshot_timeout_does_not_change_action_result`（第 201 行） | raw 补充快照超时不改变业务结果，并留下诊断失败事件。 | 核心应该，当前不一定；固定内部事件名。 | 诊断超时可能误改业务错误码，或故障完全不可见。 |
| `test_step_start_is_recorded_before_browser_state_timeout`（第 221 行） | 状态读取超时时仍有成对的步骤开始/结束事件。 | 核心应该，当前不一定；精确事件序列绑定内部事件模型。 | 超时步骤可能无法从诊断中定位。 |
| `test_action_timeout_has_matched_action_and_step_events`（第 243 行） | 动作超时时，模型、动作和步骤事件完整配对且状态为 timed out。 | 核心应该，当前不一定；固定完整七事件顺序。 | 诊断时间线可能断裂或错误表达超时阶段。 |
| `test_capacity_omits_artifacts_and_preserves_manifest_summary`（第 273 行） | 容量不足时省略普通产物、记录省略原因，但保留最终结果。 | 核心应该，当前不一定；manifest 字段和事件名称是格式细节。 | 容量耗尽可能使任务失败或丢失最终摘要。 |
| `test_tiny_capacity_always_writes_final_result`（第 297 行） | 极小容量下仍写唯一 result，并标记诊断和摘要预算超限。 | 核心应该，当前不一定；精确 artifact 集合和 manifest 字段被固定。 | 最恶劣容量条件下可能连最终状态都不可审计。 |
| `test_total_capacity_is_shared_and_diagnostic_paths_are_private` | 多个 writer 共享总容量；诊断根、运行和产物目录为 `0700`，manifest、events 和产物为 `0600`。 | 是；容量与权限是安全运维契约。 | 总容量可能被绕过，或同机其他用户可读取诊断文件。 |
| `test_serialization_omits_unavailable_sensitive_or_hidden_sdk_fields`（第 342 行） | 诊断序列化排除 thinking、headers 等敏感或隐藏 SDK 字段。 | 是；输出安全边界必须保持。 | 隐藏推理、认证信息或请求头可能进入诊断。 |
| `test_slow_diagnostic_serialization_does_not_consume_step_timeout`（第 362 行） | 慢 artifact 写入不占用 Finder 步骤 deadline。 | 核心应该，当前不一定；通过继承 writer 注入延迟。 | 慢磁盘或序列化可能制造虚假业务超时。 |
| `test_slow_action_selection_event_does_not_consume_step_timeout`（第 386 行） | 慢 action-selected 事件写入不改变业务错误类型。 | 核心应该，当前不一定；依赖具体事件名和可覆盖 writer。 | 诊断事件延迟可能侵入业务时限。 |
| `test_cleanup_failure_is_recorded_in_manifest_and_events`（第 409 行） | 诊断清理失败记录到 manifest/events，而不是静默丢失。 | 否；直接 monkeypatch 私有 `_cleanup`。 | 自动清理失效时缺少磁盘增长告警和追踪证据。 |
| `test_artifact_write_failure_does_not_change_task_result`（第 433 行） | 产物写入失败不覆盖成功任务，并在可用 manifest 中记录故障。 | 核心应该，当前不一定；直接替换私有 `_atomic_write` 并依赖目录名。 | 磁盘故障可能错误导致任务失败，或诊断故障无记录。 |
| `test_capacity_and_writer_creation_failure_do_not_change_task_result`（第 468 行） | max-runs、不可用根目录和过期清理都不阻断业务结果。 | 核心应该，当前不一定；一个测试混合三种存储布局场景。 | 诊断容量或文件系统问题可能阻断任务，保留策略也可能失效。 |
| `test_cleanup_preserves_unmanaged_or_active_directories`（第 517 行） | 清理不删除非受管目录和 `running` 运行。 | 核心应该，当前不一定；手工构造内部目录名和 manifest。 | 自动清理可能误删外部数据或活动运行。 |
| `test_diagnostic_saves_actual_annotated_screenshot_and_coordinates`（第 561 行） | 视觉运行保存标注截图、候选索引和坐标，且模型实际收到图片。 | 核心应该，当前不一定；固定 artifact 文件名、JSON 结构和消息位置。 | 视觉决策无法复盘，或坐标与模型索引不一致。 |
| `test_cli_diagnostic_options_keep_stdout_json`（第 594 行） | CLI 诊断级别、目录和容量参数传入 RuntimeConfig，stdout 仍是业务 JSON。 | 核心应该，当前不一定；通过替换 `run_task` 捕获配置。 | CLI 选项可能失效或诊断输出污染 stdout。 |
| `test_cli_invalid_diagnostic_limits_are_structured_json`（第 638 行；三种非法限制） | 非法运行数、容量和保留期返回退出码 2 及 `INVALID_TASK` JSON。 | 是。 | 非法限制可能进入运行期或产生非结构化错误。 |
| `test_cancelled_run_is_marked_aborted_and_reraises`（第 653 行） | 任务取消继续传播 `CancelledError`，诊断终态为 aborted。 | 核心应该，当前不一定；直接读取 manifest 状态字段。 | 取消可能被吞掉，或诊断永久停留在 running。 |

## 评估测试

### `tests/test_evaluation.py`

| 测试 | 1. 保护的行为 | 2. 等价重写后还应通过吗？ | 3. 删除后失去的风险保障 |
| --- | --- | --- | --- |
| `test_generate_samples_only_valid_sites_with_generic_required_fields`（第 119 行） | CorpWeb 只抽取合法 VALID 站点，按 bucket 分层，输出通用字段和 manifest。 | 核心应该，当前不一定；精确样本数、case ID 和 sidecar 名属于当前数据协议。 | 无效站点、证券专用字段或错误 URL 可能进入评估集。 |
| `test_generate_is_deterministic_and_rejects_oversized_sample`（第 139 行） | 相同 seed 生成相同顺序/fingerprint，超出总体的采样请求被拒绝。 | 核心应该，当前不一定；异常文案正则也被固定。 | 评估无法复现，或超量请求静默得到偏小数据集。 |
| `test_dataset_rejects_non_corpweb_source`（第 156 行） | `read_cases` 拒绝非 CorpWeb source。 | 是。 | 混合来源数据可能被错误当作可比评估集。 |
| `test_run_evaluation_bounds_concurrency_and_resumes`（第 191 行） | worker 并发不超上限，结果逐例保存，再次运行跳过已完成 case。 | 核心应该，当前不一定；调用次数和 JSONL 条数是具体观测。 | 评估可能过载、重复调用或重复记录结果。 |
| `test_run_records_timeout_and_finder_failure_separately`（第 248 行） | case timeout 与 Finder 失败分别统计，并正确归入 bucket 和失败列表。 | 核心应该，当前不一定；失败列表精确顺序可变化。 | 运营汇总可能把基础设施超时误算为 Finder 失败。 |
| `test_executor_cancelled_error_becomes_runner_failure`（第 283 行） | executor 内部产生的非外层取消被记录为 `RUNNER_ERROR`，评估不挂死。 | 是。 | 单个异常取消可能终止或卡住整批评估。 |
| `test_resume_requires_manifest_and_repairs_torn_final_line`（第 308 行） | 有匹配 manifest 时修复尾部半行并恢复；缺 manifest 时拒绝续跑。 | 核心应该，当前不一定；固定 sidecar 路径和换行表示。 | 崩溃后无法续跑，或不可信结果被误当成匹配运行。 |
| `test_run_evaluation_rejects_unsafe_run_id`（第 345 行；四种危险路径） | 公共评估 API 拒绝路径穿越、嵌套和绝对 run ID，且不创建产物。 | 核心应该，当前不一定；固定异常文案和 sidecar 文件名。 | 非 CLI 调用也可能把文件写出目标目录。 |
| `test_results_path_expands_user_before_manifest_and_lock_access`（第 359 行） | `~` 在 results、manifest、lock 操作前统一展开，换行/半行修复不重复执行 case。 | 核心应该，当前不一定；固定三个文件名和 JSONL 尾部实现。 | 产物可能散落在错误路径，恢复时重复执行任务。 |
| `test_missing_manifest_does_not_modify_unknown_results` | 缺 manifest 时拒绝续跑，并保持未知结果文件字节不变。 | 是；只约束公共数据安全结果，不约束内部步骤。 | 未验证归属的结果文件可能被自动修改，掩盖数据来源问题。 |
| `test_concurrent_evaluations_share_results_lock_without_deadlock`（第 446 行） | 两个并发评估只执行一次 case、只写一条结果且无死锁。 | 是。 | 并发运行可能重复执行、覆盖结果或永久阻塞。 |
| `test_cancelling_evaluation_releases_results_for_a_later_run` | 通过公共评估入口取消持锁运行后，后续运行仍能取得所有权并完成。 | 是。 | 外层取消可能遗留锁并阻塞所有后续评估。 |
| `test_lock_body_error_is_not_mislabeled_and_lock_remains_usable`（第 486 行） | 私有锁上下文不掩盖锁体异常，并在异常后可再次使用。 | 否；直接测试私有 `_evaluation_lock`。 | 锁内业务异常可能被错误包装，或锁永久不可用。 |
| `test_lock_setup_errors_are_evaluation_errors`（第 520 行） | 锁目录创建失败被包装为 `EvaluationError`。 | 否；monkeypatch 具体 `Path.mkdir` 调用点。 | 文件系统初始化故障可能泄露底层异常或分类错误。 |
| `test_lock_open_and_acquire_errors_are_evaluation_errors`（第 533 行） | lock 文件打开/flock 失败被包装，短暂竞争会重试。 | 否；直接替换 `Path.open`、`fcntl.flock` 和私有锁。 | 锁故障可能无限重试、错误放行并发写入或无法诊断。 |
| `test_lock_close_error_is_wrapped_without_masking_body_error`（第 569 行） | unlock/close 故障有明确错误，且不掩盖锁体主异常。 | 否；固定私有清理顺序和异常优先级。 | 清理异常可能覆盖真正业务错误或泄漏锁资源。 |
| `test_cancelling_lock_waiter_closes_waiter_and_preserves_holder`（第 601 行） | 取消等待者会关闭其 fd，不释放持有者的锁，最终仍可获取。 | 否；固定文件打开次数和私有句柄生命周期。 | 高频取消可能泄漏 fd、误释放锁或造成死锁。 |
| `test_summary_writes_machine_and_human_readable_reports`（第 658 行） | 汇总同时写 JSON/Markdown，并声明自报成功率不代表准确率或召回率。 | 核心应该，当前不一定；固定英文标题和免责声明原句。 | 报告可能不可机器读取，或误导用户解释评估指标。 |

## 本地浏览器测试

### `tests/test_local_browser.py`

| 测试 | 1. 保护的行为 | 2. 等价重写后还应通过吗？ | 3. 删除后失去的风险保障 |
| --- | --- | --- | --- |
| `test_company_home_to_job_page_with_local_chromium`（第 117 行） | 真实 Chromium 能从本地企业首页点击到岗位页并返回岗位结果。 | 核心应该，当前不一定；`steps == 2` 固定动作编排。 | fake 单测全部通过时，真实浏览器导航/DOM 集成回归仍可能漏检。 |
| `test_internal_css_scroll_snap_reveals_job_with_local_chromium`（第 154 行；显式目标/root fallback） | 真实 CSS scroll-snap 容器经两种滚动路径都能揭示岗位并完成。 | 核心应该，当前不一定；精确步数不应限制等价实现。 | 内部滚动、scroll-snap 或根 fallback 的真实集成可能损坏。 |
| `test_unified_runner_company_home_to_job_page_with_local_chromium`（第 189 行） | 统一 `run_task` 链路配合真实 Chromium 返回正确 task ID、输出和元数据。 | 核心应该，当前不一定；精确步骤数是实现细节。 | Finder 直调可用但生产统一入口可能映射错误。 |

## 使用结论

测试精简时，不应把“当前测试不一定能跨重构通过”直接等同于“可以删除”：

- 标为“是”的测试优先保留其行为覆盖，可合并重复入口场景。
- 标为“核心应该，当前不一定”的测试优先改写为结果、错误码、安全副作用或稳定产物契约断言。
- 标为“否”的测试只有在对应内部机制确实承担独立风险时保留；否则用公开入口行为测试替代。
- 删除任何测试前，应先确认同一风险已由另一条更高层测试覆盖，而不只是同一成功路径被重复执行。
