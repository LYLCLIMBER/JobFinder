---
task_id: JF-011
status: done
verified_at: 2026-09-16
---

# 验证记录

## 验证范围

- `docs/tasks.md` 阶段 0 的行为门禁、依赖规则和退出条件。

## 验收矩阵

| ID | 验收项 | 结果 | 证据/说明 |
| --- | --- | --- | --- |
| AC-001 | Task v1 契约门禁 | Passed | `tests/contracts/test_task_v1.py` 固定当前请求、成功/失败 JSON、11 个错误码、retryable 和状态互斥。 |
| AC-002 | Finder cleanup 与动作安全门禁 | Passed | 新增调用方取消 cleanup 和方向不可用测试；既有正常、启动失败和超时测试继续通过。 |
| AC-003 | Diagnostics 稳定边界 | Passed | 三级产物、best-effort、隔离、容量、取消及目录/文件权限测试通过。 |
| AC-004 | Evaluation 数据完整性 | Passed | 公共入口覆盖并发锁、持有者取消释放、resume、manifest 和 torn-tail 历史保留。 |
| AC-005 | 项目 AC-001 至 AC-018 证据映射 | Passed | 本文“项目行为证据”逐项列出证据；AC-003 的 prompt 语义缺少独立自动化证据已记录为残余风险。 |
| AC-006 | 核心模块依赖规则 | Passed | `tests/contracts/test_dependencies.py` 检查目标核心模块；旧 Finder 例外标明阶段 5 到期。 |
| AC-007 | 无生产路径或外部行为变更 | Passed | `src/` 无修改，阶段 1 生产模块未创建，`DG-01` 至 `DG-06` 未处理。 |
| AC-008 | 标准检查与相关集成测试 | Passed | 静态检查、144 个完整测试和 4 个 Chromium 集成测试通过。 |

结果状态只能使用 `Passed`、`Failed`、`Partial`、`Not run`、`Not applicable`。

## 项目行为证据

| 项目 AC | 主要测试证据 |
| --- | --- |
| AC-001 | `test_completes_when_home_page_contains_a_job`；`test_task_success_json_contract_keeps_duration_in_metadata` |
| AC-002 | `test_company_home_to_job_page_with_local_chromium` |
| AC-003 | `test_fails_at_max_steps_when_no_specific_job_is_found`；允许动作由 Pydantic 判别联合和 Finder 行为测试覆盖；不绑定 prompt 原文的独立提示语义测试仍是记录缺口 |
| AC-004 | `test_rejects_unverified_done_and_returns_feedback_to_model`；`test_classifies_validation_failures_from_done_rejection` |
| AC-005 | `test_rejects_an_index_from_an_old_browser_state`；`test_explicit_unavailable_scroll_target_is_rejected`；`test_explicit_scroll_target_rejects_an_unavailable_direction` |
| AC-006 | `test_internal_css_scroll_snap_reveals_job_with_local_chromium` 的显式目标和 root fallback 分支 |
| AC-007 | `test_scroll_detects_delayed_route_change_without_internal_fallback` |
| AC-008 | `test_sends_annotated_screenshot_when_textless_candidate_exists`；`tests/test_vision.py` |
| AC-009 | `test_rejects_invalid_requests_before_calling_finder`；`test_run_task_rejects_invalid_requests_before_create_runner` |
| AC-010 | `test_run_returns_unified_success_result`；CLI shortcut/JSON 文件契约测试；统一 Chromium Runner 测试 |
| AC-011 | Finder 初始化、步骤超时、模型、动作、校验和预算错误测试；Task 错误码/retryable 参数化测试 |
| AC-012 | Finder 启动失败、超时、cleanup 异常及 `test_cancellation_cleans_up_browser_and_propagates` |
| AC-013 | `test_diagnostic_levels_gate_artifacts`；`test_screenshot_capture_does_not_change_nonvision_model_input` |
| AC-014 | 诊断写入、容量、cleanup 和 writer 创建失败测试 |
| AC-015 | 并发运行隔离测试；`test_total_capacity_is_shared_and_diagnostic_paths_are_private` |
| AC-016 | Evaluation 合法站点、确定性、超量拒绝和来源校验测试 |
| AC-017 | Evaluation 并发/resume/timeout/torn-tail/公共取消释放及锁故障测试 |
| AC-018 | `test_summary_writes_machine_and_human_readable_reports` |

## 自动化测试

- `git diff --check`：Passed，无输出。
- `uv run ruff check .`：Passed，`All checks passed!`。
- `uv run ruff format --check .`：Passed，`25 files already formatted`。
- `uv run pytest --collect-only -q`：Passed，`144 tests collected`。
- `uv run pytest -q`：Passed，`144 passed in 15.77s`。
- `uv run pytest -q -m integration`：Passed，`4 passed, 140 deselected in 6.27s`。

## 手工/真实网站验证

- 本地静态站点 Chromium 集成场景通过；未访问外部网站或模型服务。

## 环境

- 工作区：`main`，开始前已有未提交的 JF-010/设计文档变更；本任务不回退这些变更。

## 产物

- `tests/contracts/test_task_v1.py`
- `tests/contracts/test_dependencies.py`
- 更新后的 Finder、Diagnostics 和 Evaluation 行为测试。
- `docs/tasks.md` 阶段 0 完成标记和 `docs/test-protection-map.md`。

## 未验证项

- 无。

## 残余风险

- 通用招聘标题的拒绝仍主要依赖 prompt；按 `DG-02` 留到阶段 4 决策，本阶段不改变当前行为。
- 旧 `finder.py` 的 `browser_use` 导入保留为阶段 5 到期的依赖规则例外。
- 部分 Evaluation 锁故障测试仍直接测试私有边界，待阶段 11 Store 公共契约覆盖相同风险后再删除。
