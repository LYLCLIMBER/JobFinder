---
task_id: JF-004
title: Runtime Diagnostics Execution
status: review
---

# 运行时诊断实施方案

## 实施约束

- 在 JF-003 的 Runner/Finder 边界增加可注入诊断组件，不重写任务路由或四动作循环。
- 所有结构化诊断事件经单一路径写入 `events.jsonl`；标准 logging 保持 stderr，CLI stdout 只输出最终 JSON。
- 按最低权限采集：basic 只含摘要；diagnostic/raw 才保存用户批准的原始证据，始终排除 headers、cookies、环境变量、完整配置和客户端对象。
- 将采集截图与向模型附加视觉输入拆为独立判断；测试应直接断言模型调用参数不随截图开关改变。

## 建议实施顺序

1. 定义诊断级别、运行 ID、事件/manifest 记录、产物索引、容量/保留配置和失败状态的内部模型；将新选项加入 `RuntimeConfig`，不加入任务 payload。
2. 实现每运行唯一目录的诊断 writer：原子/安全命名、JSONL 追加、manifest/result 写入、stderr 故障日志和无 writer 降级路径。
3. 在 `TaskRunner` 生命周期接入 run/task 关联、开始/结束事件和最终 result；在 Finder/视觉边界接入步骤、模型 call、解析、页面输入、截图及标注映射事件。
4. 实现 basic/diagnostic/raw 采集门控：raw 才保存未截断 DOM、允许元数据和补充动作后快照；逐项审查写入字段的排除边界。
5. 接入 CLI 选项与 RuntimeConfig，保持 stdout JSON、已有 CLI 调用方式和业务 schema 不变。
6. 实现容量预约、完成后保留清理、并发目录隔离及 writer 异常降级；清理或诊断失败不得改变任务主结果。
7. 添加 fake/临时目录测试，再运行现有静态检查和测试套件。

## 测试重点

- 使用固定 fake 模型、浏览器和临时诊断根目录断言 JSONL、manifest、result 和产物引用。
- 参数化三级模式，验证允许与禁止的产物；覆盖实际标注图与映射、raw 快照及排除字段。
- 覆盖默认截图、显式关闭截图和 `use_vision` 的交叉组合，比较模型实际输入。
- 覆盖 CLI stdout/stderr 分离、RuntimeConfig 传递、容量/保留、写入/清理失败和并发运行。

## 预期验证命令

```bash
git diff --check
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv run pytest -q -m integration
```

## 交接要求

- 将每个 `AC-001` 至 `AC-011` 的实际命令、结果和持久证据写入 `verification.md`；未运行项保持 `Not run`。
- 在 handoff 记录确定的容量/保留默认值、允许元数据字段、任何实现与规格差异及诊断故障降级行为。

## 实施结果（2026-09-07）

- 已按上述边界实现独立 `DiagnosticWriter`，并在 Runtime、Runner、Finder、Vision 和 CLI 接入；未改任务 payload、`TaskResult` 业务 schema 或四动作语义。
- 独立 review 后补齐/修正了清理失败记录、跨进程并发容量预约、取消时标记 `aborted`、诊断写入不消耗步骤超时，以及步骤/动作超时事件成对记录。
- 实际诊断根目录默认是相对路径 `log/diagnostics`，不是初始建议中的 XDG 路径；容量/保留默认值和严格采集边界见 `handoff.md` 与 `verification.md`。
