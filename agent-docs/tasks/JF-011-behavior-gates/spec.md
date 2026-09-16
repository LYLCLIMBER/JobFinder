---
task_id: JF-011
title: Modular Refactoring Phase 0 Behavior Gates
status: done
---

# 任务规格

## 目标

完成 `docs/tasks.md` 的阶段 0，在不改变生产行为或进入阶段 1 的前提下，为模块化重构建立稳定行为门禁。

## 背景

当前 119 个测试 case 已覆盖大部分行为，但 Task v1 契约矩阵、Finder 取消清理、滚动方向安全、诊断权限和 Evaluation 数据完整性仍缺少稳定证据；部分测试还绑定私有方法、精确文案、事件顺序或调用次数。

## 范围

- 补充 Task v1 请求、结果、错误码和无副作用前置拒绝测试。
- 补充 Finder 正常、失败、超时和取消清理测试，以及动作引用安全测试。
- 补充诊断 best-effort、权限和隔离测试。
- 补充 Evaluation 锁、resume 和损坏尾行恢复的公共行为证据。
- 建立目标核心模块禁止导入 `browser_use` 的依赖规则检查。
- 记录 `AC-001` 至 `AC-018` 的测试证据或明确缺口。
- 只在已有同等风险替代证据后改写或删除脆弱测试。

## 不在范围

- 不创建 `contracts.py`、`task_protocol.py`、Ports、Adapters 或其他阶段 1 及以后生产模块。
- 不改变 Task v1、CLI、Diagnostics 或 Evaluation 格式和语义。
- 不处理 `DG-01` 至 `DG-06` 的契约决策。
- 不重构生产职责或修改相邻 `../browser-use`。

## 安全边界

- 不读取或记录 `.env`、API key 或其他密钥。
- 非法请求不得触发 Finder、LLM 或浏览器装配。
- 诊断故障不得改变业务结果，取消不得归一化为普通失败。
- 不因测试发现相邻设计问题而修改后续阶段生产代码。

## 验收标准

- `AC-001`：当前 Task v1 请求、成功/失败 JSON、错误码、retryable 和前置拒绝具有稳定契约测试。
- `AC-002`：Finder 正常、失败、超时和调用方取消路径具有资源清理证据，动作引用安全缺口得到覆盖。
- `AC-003`：三级诊断、best-effort、并发隔离、容量和权限具有稳定边界证据。
- `AC-004`：Evaluation 锁、resume、manifest 和损坏尾行恢复具有公共行为或明确记录的替代证据。
- `AC-005`：`current-behavior.md` 的 `AC-001` 至 `AC-018` 均映射到测试证据或明确测试缺口。
- `AC-006`：依赖规则检查保证目标核心模块一旦存在便不能导入 `browser_use`。
- `AC-007`：未删除生产路径，未改变已批准外部行为，未实施阶段 1 内容。
- `AC-008`：`git diff --check`、Ruff、格式检查、完整测试和相关集成测试通过，或如实记录环境阻塞。

## 相关代码

- `src/job_page_finder/`
- `tests/`
- `docs/tasks.md`
- `docs/current-behavior.md`
- `docs/test-protection-map.md`

## 外部依赖

- 相邻只读 editable 依赖 `../browser-use`。
- 本地 Chromium 集成测试环境。

## 未决问题

- 无。`docs/tasks.md` 与 `docs/modular-refactoring-design.md` 对阶段 0 无阻塞冲突；契约差异由 `DG-01` 至 `DG-06` 延后。
