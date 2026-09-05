---
task_id: JF-003
status: review
verified_at: 2026-09-05
---

# 验证记录

## 验证范围

- 统一任务请求和结果契约。
- Runner、composition root、Python API 和 CLI。
- Finder 错误类型化、浏览器生命周期、日志安全和回归测试。

## 验收矩阵

| ID | 验收项 | 结果 | 证据/说明 |
| --- | --- | --- | --- |
| AC-001 | 合法请求经统一 Python API 返回统一结果 | Passed | `tests/test_runner.py::test_run_returns_unified_success_result`；`tests/test_local_browser.py::test_unified_runner_company_home_to_job_page_with_local_chromium` 从 `run_task()` 返回 `status=succeeded` 且含岗位字段 |
| AC-002 | 自动生成或保留任务 ID 并贯穿日志 | Passed | `test_generates_task_id_when_missing_and_preserves_provided_id` 断言 started、finished 与结果使用同一 `task_id`；调用方 ID 原样保留 |
| AC-003 | 非法请求在浏览器启动前返回结构化失败 | Passed | Runner 在调用 finder 前拒绝非法请求；`test_run_task_rejects_invalid_requests_before_create_runner` 在 `create_runner` 必失败时仍返回 `INVALID_TASK` / `UNSUPPORTED_TASK_TYPE` |
| AC-004 | Finder 失败映射为稳定错误码且不解析消息 | Passed | Finder 路径测试覆盖 `BROWSER_INITIALIZATION_*`、`STEP_TIMEOUT`、`MODEL_ERROR`、`ACTION_ERROR`、`VALIDATION_FAILED`、`MAX_STEPS_REACHED`；`test_maps_finder_error_code_without_parsing_message` 用误导性消息证明 Runner 使用 `error_code` |
| AC-005 | composition root 集中装配且入口不重复装配 | Passed | `tests/test_runtime.py` 检查 CLI 源码不装配 Finder，`create_runner()` 注入 LLM/Tools/browser factory，`run_task()` 默认调用 `create_runner()` |
| AC-006 | CLI 两种输入均通过同一 Runner 输出统一 JSON | Passed | `tests/test_cli.py` 覆盖 `find-job-page` 与 `run <task.json>`，stdout 可 `json.loads` |
| AC-007 | CLI 退出码及 stdout/stderr 契约正确 | Passed | 成功=0、任务失败=1、非法输入=2、配置错误=3；UTF-16/权限错误与非法 URL 在装配失败时仍为退出码 2 且 stdout 为 JSON |
| AC-008 | 各结果路径保持独立浏览器生命周期和清理语义 | Passed | 现有 Finder 成功/失败/初始化失败均 `killed is True`；`test_cleanup_exception_does_not_override_result` 证明清理异常不覆盖主结果 |
| AC-009 | 结果和安全日志包含规定元数据且无敏感大字段 | Passed | 成功路径见 `test_logs_task_metadata_without_sensitive_fields`；`run_task` 在校验/装配前写 started、失败后只写 finished，见 `test_run_task_logs_*` 与 `test_run_task_does_not_duplicate_lifecycle_logs` |
| AC-010 | 既有 API 无回归且统一入口有完整自动化覆盖 | Passed | `uv run pytest -q` 为 `51 passed in 6.32s` |

结果状态只能使用 `Passed`、`Failed`、`Partial`、`Not run`、`Not applicable`。

## 自动化测试

- 命令：`git diff --check`
- 结果：2026-09-05 通过。

- 命令：`uv run ruff check .`
- 结果：2026-09-05 `All checks passed!`

- 命令：`uv run ruff format --check .`
- 结果：2026-09-05 `16 files already formatted`

- 命令：`uv run pytest -q`
- 结果：2026-09-05 生命周期日志顺序修复后 `51 passed in 6.32s`

- 命令：`uv run pytest -q -m integration`
- 结果：2026-09-05 审查修复后 `2 passed, 45 deselected in 4.56s`。本次仅为日志路径修复，未重跑集成测试。

## 手工/真实网站验证

- 2026-09-05 `uv run jobfinder run /tmp/jobfinder-missing.json`：stdout 为 `INVALID_TASK` JSON，退出码 2。
- 2026-09-05 `uv run python -m job_page_finder`：stdout 为 `INVALID_TASK` JSON（缺少 command），退出码 2。
- 成功路径由 `tests/test_cli.py` 与本地 Chromium 集成测试覆盖；未对真实外网网站做手工跑通。

## 环境

- Python `>=3.11,<4.0`。
- 本地 editable `../browser-use`。
- 工作区 revision `d9137e9` 加未提交实现。
- 真实运行需要有效的模型配置和 Chromium；测试使用 fake 依赖与本地静态站点。

## 产物

- `src/job_page_finder/runner.py`、`runtime.py`、`cli.py`、`__main__.py`
- Finder `error_code` 与 Runner 错误映射
- `jobfinder` 命令入口
- README 统一入口说明
- `tests/test_runner.py`、`tests/test_runtime.py`、`tests/test_cli.py` 及扩展的 Finder/集成测试

## 未验证项

- 无必需验收项未验证。未做真实外网网站手工 CLI 成功案例。

## 残余风险

- Finder 异常捕获边界已按模型/动作拆分，既有测试通过，但真实网站上的超时与模型失败仍取决于外部服务。
- LLM 和 Tools 的并发共享语义尚未验证，首版维持单任务独立浏览器且不承诺并发安全。
- 真实网站仍可能受到验证码、登录、动态加载和第三方招聘系统影响；统一 Runner 不改变 Finder 的能力边界。
