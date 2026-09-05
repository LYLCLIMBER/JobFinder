---
task_id: JF-003
title: Unified Task Runtime Execution
status: review
---

# 统一任务运行链路实施方案

## 实施原则

- 在现有 Finder 外增加薄应用层，不重写稳定的浏览器 Agent 循环。
- 先稳定同步请求/响应契约，再考虑 HTTP、队列或持久化。
- 使用一个 composition root；所有入口共享 Runner。
- 保持每任务一个浏览器会话，避免在首版引入未验证的并发共享。
- 以类型字段传递失败原因，不通过错误消息匹配实现控制流。

## 建议文件布局

```text
src/job_page_finder/
├── __init__.py
├── config.py
├── finder.py
├── models.py
├── vision.py
├── runner.py
├── runtime.py
├── cli.py
└── __main__.py
```

- `models.py` 保留领域模型，并增加 Finder 层机器可读错误分类。
- `runner.py` 包含统一请求、结果、公共错误模型以及 `TaskRunner`。
- `runtime.py` 包含运行配置、`create_runner()` 和 `run_task()`。
- `cli.py` 只负责输入适配、调用 Runner、JSON 输出和退出码。
- `__main__.py` 复用 `cli.main()`，支持 `python -m job_page_finder`。

如果实现后 `runner.py` 模型过多，可再按实际复杂度拆分；首版不预先建立多层 handler 包或插件框架。

## 阶段 1：领域失败类型化

1. 为 `JobPageFinderResult` 增加可选的机器可读失败分类，保留 `error` 作为人类可读消息。
2. 将初始化失败、初始化超时、步骤超时、模型/动作异常、done 校验失败和最大步数退出写入明确分类。
3. 如同一步无法准确区分模型和动作错误，在现有 try 范围内增加最小必要边界，不改变 Agent 的动作语义和失败预算。
4. 保持 `find()` 返回结果而不是向外抛出运行失败；构造参数错误仍可在装配阶段作为配置错误处理。
5. 扩展 `test_finder.py`，证明错误分类来自执行路径而不是消息字符串。

## 阶段 2：请求、结果与 Runner

1. 定义严格的 `TaskRequest`，首版使用 `v1` 和 `find_job_page` 字面量，并复用 `JobPageFinderInput`。
2. 定义 `TaskError`、`TaskMetadata` 和 `TaskResult`；强制成功时有 output 且无 error，失败时无 output 且有 error。
3. `TaskRunner` 接收已装配的 Finder 或执行 callable，便于单元测试注入 fake。
4. Runner 在调用前确定 `task_id` 和开始时间，在完成后统一生成结果及毫秒耗时。
5. Runner 映射 Finder 的类型化失败；未预期异常统一为 `INTERNAL_ERROR`，不得向 JSON 暴露 traceback。
6. 首版只需显式处理一个任务类型，但未知类型仍应得到 `UNSUPPORTED_TASK_TYPE`。

## 阶段 3：唯一装配入口

1. 定义显式运行配置，收敛 `step_timeout`、`startup_timeout`、`max_consecutive_failures`、`max_dom_characters`、`use_vision` 和 `max_visual_candidates`。
2. `create_runner()` 负责加载环境、创建 LLM、Tools、Finder 和 Runner。
3. 保留依赖注入参数，使测试可绕过真实 `.env`、网络、LLM 和浏览器。
4. `run_task()` 作为轻量异步便利 API，内部只创建 Runner 并委托执行。
5. 从包根导出稳定的任务模型和运行 API，同时保留原有导出。

## 阶段 4：CLI

1. 在 `pyproject.toml` 注册 `jobfinder` 命令。
2. 支持 `jobfinder run <task.json>`，读取完整任务 envelope。
3. 支持 `jobfinder find-job-page <company_url> [--max-steps N]`，只负责生成同一 envelope。
4. 最终 `TaskResult` JSON 写 stdout；日志和参数诊断写 stderr。
5. 为成功、无效输入、配置错误和任务执行失败定义稳定的非交互退出码。
6. `__main__.py` 调用同一个 CLI main，不建立第二套入口逻辑。

## 阶段 5：可观测性

1. 使用标准日志设施记录任务开始和结束。
2. 日志字段至少包含 `task_id`、`task_type`、`status`、`duration_ms`，失败时可包含稳定错误码。
3. Finder 步骤日志若纳入本任务，只记录步骤号、当前 URL、动作类型和耗时，不记录 prompt、完整 DOM、图片数据或模型原始响应。
4. 浏览器清理异常记录 warning，但保留原始任务结果。

## 阶段 6：测试闭环

1. 新增 Runner 单元测试，覆盖成功、自动/显式任务 ID、非法请求、未知类型、领域失败和未处理异常。
2. 新增 runtime 装配测试，使用 fake LLM、browser factory 和 tools，证明入口共享 composition root。
3. 新增 CLI 测试，覆盖两种输入、JSON 可解析性、stdout/stderr 隔离和退出码。
4. 扩展本地浏览器集成测试，使请求从 Runner 进入真实 Chromium 和本地静态站点，再返回统一结果。
5. 运行现有全部 Finder 和视觉测试，确认底层行为没有回归。

## 实施顺序

```text
Finder 错误类型化
        ↓
TaskRequest / TaskResult
        ↓
TaskRunner
        ↓
runtime composition root
        ↓
Python API
        ↓
CLI
        ↓
日志和端到端验证
```

该顺序保证每一层都能用 fake 依赖独立测试，并避免 CLI 先于公共运行接口形成临时实现。

## 预期验证命令

```bash
git diff --check
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv run pytest -q -m integration
```

还需手工执行至少一个 CLI 成功案例和一个非法输入案例，确认 stdout 可独立解析且 stderr 不混入结果。

## 后续扩展边界

未来若新增 `extract_jobs`、`filter_jobs` 或 `rank_jobs`，应增加各自强类型 payload 和 handler，再由 Runner 路由或由单独 pipeline 编排。不得把这些职责继续累积到 `JobPageFinder.find()`。
