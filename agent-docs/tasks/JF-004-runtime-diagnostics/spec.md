---
task_id: JF-004
title: Runtime Diagnostics
status: review
---

# 任务规格

## 目标

为每次 `find_job_page` 运行提供独立、可关联、可分级的运行诊断：标准日志写入 stderr，并在每个运行目录写入 `events.jsonl`、manifest 和结果产物，以支持问题复现而不改变任务业务请求/结果 schema。

## 背景

JF-003 已建立 `TaskRunner`、`RuntimeConfig` 和 CLI，但其任务级日志刻意不持久化完整 DOM、截图或模型消息。用户已批准建立独立的诊断产物系统，以在需要时保留实际运行证据。

## 范围

- 提供 `basic`、`diagnostic`、`raw` 三个诊断级别；每次运行创建隔离目录，标准 logging 仍写 stderr。
- 每个级别都记录核心生命周期与步骤事件到 `events.jsonl`，并以 `run_id`、`task_id`、step/call ID 关联事件、产物和结果。
- 写入 manifest 与最终 result 产物，记录诊断级别、`redaction_applied: false`、产物关系、状态和故障信息。
- `diagnostic` 保存实际模型消息、响应、解析结果、页面输入、原始截图，以及实际标注截图与候选/坐标映射。
- `raw` 在 `diagnostic` 基础上保存未截断 DOM、允许的页面/响应元数据，以及每次补充动作后的页面快照。
- `diagnostic` 和 `raw` 默认采集截图；截图采集与 `use_vision` 解耦，截图开关不得改变模型输入。
- 通过 `RuntimeConfig` 和 CLI 暴露诊断配置，不改变任务请求 payload 或 `TaskResult` 的业务 schema。
- 定义容量上限、保留/清理策略、写入失败降级和并发运行隔离，并补充自动化测试。

## 不在范围

- 修改 `JobPageFinder` 的四动作语义、任务路由、业务请求 schema 或业务结果 schema。
- 修改 `.env`、相邻 `../browser-use`、认证头、cookie、环境变量、完整运行配置或客户端对象。
- 本任务内实现内容脱敏、加密、远程上传、集中式日志服务或历史查询 UI。

## 安全边界

- 不读取、输出或持久化 `.env`、密钥、headers、cookies、完整环境配置或客户端对象。
- 当前版本不脱敏；所有诊断 manifest 必须显式写入 `redaction_applied: false`，以提示受控存储责任。
- `raw` 仅保留本规格允许的页面/响应元数据，不将排除内容作为“原始”数据采集。
- 诊断写入失败、容量耗尽或清理失败不得泄露排除内容，且不得覆盖主任务结果。

## 验收标准

- `AC-001`：每次运行生成唯一 `run_id` 和独立运行目录；标准日志写 stderr，运行目录包含可逐行解析的 `events.jsonl`、manifest 和最终 result，且均可关联 `run_id`、`task_id` 与最终状态。
- `AC-002`：`basic`、`diagnostic`、`raw` 都记录相同的核心生命周期和每一步核心事件；事件可通过 step ID、模型 call ID 或动作 ID 关联到相应产物。
- `AC-003`：`basic` 不保存完整模型消息/响应、解析内容、页面输入、截图、完整 DOM 或补充动作快照；其事件和 manifest 仅保留该级别允许的摘要与引用。
- `AC-004`：`diagnostic` 保存实际模型消息、响应、解析结果、页面输入、原始截图，以及实际标注截图与候选/坐标映射，并由事件引用对应产物。
- `AC-005`：`raw` 额外保存未截断 DOM、允许的页面/响应元数据和每次补充动作后的页面快照；不得保存 headers、cookies、环境变量、完整配置或客户端对象。
- `AC-006`：`diagnostic` 和 `raw` 默认启用截图采集；显式截图开关独立于 `use_vision`，切换截图设置不会增删或改变发给模型的输入。
- `AC-007`：manifest 与 result 完整记录运行级别、产物索引/关系、结果或失败信息及 `redaction_applied: false`；result 不改变既有 `TaskResult` 业务 schema。
- `AC-008`：诊断级别、根目录、截图开关和容量/保留参数可由 `RuntimeConfig` 与 CLI 设置，且 CLI JSON stdout 契约和现有任务 payload 保持不变。
- `AC-009`：并发运行互不写入对方目录或覆盖彼此产物；容量限制和保留清理在创建/完成路径受控执行。
- `AC-010`：诊断创建、序列化或清理失败时记录 stderr/可用事件并受控降级，主任务仍返回其原有成功或失败结果；诊断故障明确出现在 manifest 或事件中。
- `AC-011`：自动化测试覆盖三级产物边界、事件关联、截图与视觉解耦、CLI/RuntimeConfig 兼容性、容量保留/故障降级和并发隔离；既有测试继续通过。

## 相关代码

- `src/job_page_finder/runtime.py`：`RuntimeConfig`、运行装配与 `run_task()`。
- `src/job_page_finder/runner.py`：任务生命周期、`TaskResult` 与日志上下文。
- `src/job_page_finder/finder.py`：步骤、模型调用、DOM、截图和浏览器动作边界。
- `src/job_page_finder/vision.py`：视觉候选与标注图映射。
- `src/job_page_finder/cli.py`：CLI 参数、stderr 与 JSON stdout 分离。
- `tests/test_runtime.py`、`tests/test_runner.py`、`tests/test_finder.py`、`tests/test_cli.py`：现有测试模式。

## 外部依赖

- Python 标准 `logging` 和文件系统；除非实现证明必要，不新增遥测/日志服务依赖。
- 继续使用本地 editable `browser-use` 依赖但不修改其源码。

## 未决问题

- 无阻塞性产品问题；实现阶段应将默认容量、保留期限/数量和允许元数据字段固化为显式、安全的配置默认值并在交接中记录。
