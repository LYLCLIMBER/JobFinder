---
task_id: JF-004
status: review
verified_at: 2026-09-07
---

# 验证记录

## 验证范围

- JF-004 运行时诊断分级、产物、关联、安全排除、配置、降级、容量和并发隔离。

## 验收矩阵

| ID | 验收项 | 结果 | 证据/说明 |
| --- | --- | --- | --- |
| AC-001 | 运行 ID、隔离目录、stderr、events、manifest 和 result | Passed | `DiagnosticWriter` 创建 UUID 运行目录、JSONL、manifest/result（`diagnostics.py:63-88,102-115,203-220,306-315`）；CLI logging 明确为 stderr（`cli.py:54-60`）；三级测试验证关联（`test_diagnostics.py:124-147`）。 |
| AC-002 | 三级共同核心事件及 step/call/action 关联 | Passed | Runner/Finder 记录 task、step、call、action 生命周期并带关联 ID（`runner.py:200-278`、`finder.py:127-253,317-397`）；参数化三级测试验证核心事件（`test_diagnostics.py:111-147`）。 |
| AC-003 | basic 产物边界 | Passed | `is_diagnostic`/`is_raw` 门控模型、页面、截图与 raw 产物（`diagnostics.py:94-100`、`finder.py:399-435`）；三级边界测试（`test_diagnostics.py:111-147`）。 |
| AC-004 | diagnostic 实际消息、响应、解析、页面输入和截图/标注映射 | Partial | 保存实际传入的模型消息、SDK 可取得的 completion、parsed decision、页面输入和截图；视觉时保存实际标注图和候选坐标（`finder.py:317-397,399-435`，`test_diagnostics.py:532-564`）。严格限定：SDK 层没有原始 HTTP 或解析前响应，产物明确 `pre_parse_response_available: false`，且排除隐藏推理；不得称为完整原始响应。 |
| AC-005 | raw DOM、允许元数据和补充动作后快照及排除边界 | Passed | raw 保存 BrowserSession 暴露且未再按 `max_dom_characters` 截断的 DOM、页面位置元数据和动作后快照（`finder.py:414-474`）；序列化排除 headers/cookies/认证及 thinking（`diagnostics.py:30-32,334-349`；`test_diagnostics.py:314-317`）。响应元数据同样仅限 SDK 应用层可取得字段。 |
| AC-006 | 默认截图及与 use_vision 解耦 | Passed | diagnostic/raw 默认截图，显式设置覆盖（`runtime.py:87-101`）；模型视觉输入只由 `use_vision` 决定（`finder.py:136-147,293-315`）；独立性测试（`test_diagnostics.py:150-169`）。 |
| AC-007 | manifest/result 关联、失败信息和 redaction 标记 | Passed | manifest 含级别、运行关联、产物索引、状态、`redaction_applied: false` 和诊断故障，result 是独立产物（`diagnostics.py:76-87,222-235,306-323`）；既有 `TaskResult` 未扩展（`runtime.py:73-153`）。 |
| AC-008 | RuntimeConfig/CLI 配置且业务契约不变 | Passed | 配置默认和类型在 `runtime.py:28-43`，CLI 参数/传递在 `cli.py:44-51,149-169`；stdout JSON 测试（`test_diagnostics.py:566-621`）。实际根目录是相对 `log/diagnostics`。 |
| AC-009 | 容量保留控制与并发隔离 | Passed | 跨进程锁、受控完成目录清理及容量预约/软上限（`diagnostics.py:102-182,256-288,310-327`）；普通产物在容量耗尽时省略，最终 `result` 必要摘要始终写入；若其超限，manifest 记录 `diagnostic_incomplete: true` 和 `summary_budget_exceeded: true`，不伪记 result 已省略。并发、容量、极小预算最终摘要与活动目录保护测试（`test_diagnostics.py:172-191,267-329,457-548`）。`running`/崩溃遗留目录不自动删除，须人工管理。 |
| AC-010 | 诊断故障受控降级且主结果不变 | Passed | writer 异常只警告/记录，不向任务传播（`diagnostics.py:184-201,271-273,316-323`）；取消标记 `aborted` 后重抛（`runner.py:273-278`）；写入、创建/容量、清理和取消测试（`test_diagnostics.py:381-485,624-649`）。 |
| AC-011 | 自动化覆盖和既有回归 | Passed | 专项、全量和集成测试通过，含三级边界、关联、截图/视觉、CLI、容量/保留（包括极小预算最终摘要）、故障降级、并发、取消与超时覆盖。 |

## 自动化测试

- 2026-09-07 收尾修复：`uv run ruff check .` 通过；`uv run ruff format --check .` 为 19 files already formatted；`uv run pytest -q` 为 76 passed in 5.90s；`uv run pytest -q -m integration` 为 2 passed, 74 deselected in 5.79s；`git diff --check` 通过。新增 `test_tiny_capacity_always_writes_final_result` 验证 `max_run_bytes=1`、`max_total_bytes=1`。
- `uv run ruff check .`：通过。
- `uv run ruff format --check .`：通过（18 files）。
- `uv run pytest -q`：通过（72 passed）。
- `uv run pytest -q -m integration`：通过（2 passed, 70 deselected）。
- 专项 `tests/test_diagnostics.py`：21 passed。
- `git diff --check`：通过。

## 手工/真实网站验证

- 2026-09-07，在仓库根目录以真实模型和浏览器顺序执行同一目标 `https://www.kurogames.com/`，三次均固定 `--max-steps 8`，诊断根目录为 `log/jf004-kurogames-real-test`。运行前已确认 `log/` 存在，专用根目录由诊断系统创建。

| 模式 | 实际命令 | 耗时 / CLI 退出码 | 业务结果 | 运行目录 | 产物 / 截图 |
| --- | --- | --- | --- | --- | --- |
| basic | `uv run jobfinder find-job-page https://www.kurogames.com/ --max-steps 8 --diagnostics-level basic --diagnostics-root log/jf004-kurogames-real-test` | runner 12,945 ms（CLI 墙钟 15 s）/ 1 | `failed`，`MAX_STEPS_REACHED` | `20260907T033302932457Z_887ec70b7cd0472f83ee6b47ab54ab81` | 1 / 0 |
| diagnostic | `uv run jobfinder find-job-page https://www.kurogames.com/ --max-steps 8 --diagnostics-level diagnostic --diagnostics-root log/jf004-kurogames-real-test` | runner 20,818 ms（CLI 墙钟 23 s）/ 1 | `failed`，`MAX_STEPS_REACHED` | `20260907T033322826485Z_eef76f11a47f4e388f7555e867998a10` | 41 / 8 |
| raw | `uv run jobfinder find-job-page https://www.kurogames.com/ --max-steps 8 --diagnostics-level raw --diagnostics-root log/jf004-kurogames-real-test` | runner 30,907 ms（CLI 墙钟 33 s）/ 1 | `failed`，`MAX_STEPS_REACHED` | `20260907T033350012872Z_17dd153ab1144bc4a2fbb33727360baf` | 73 / 16（8 张原图、8 张动作后图） |

- 三个 `events.jsonl` 均逐行 JSON 解析成功，且全部事件的 `run_id`/`task_id` 与 manifest 一致；每个运行均有 manifest、唯一 result、8 个 `step_started`/`step_finished`、8 个 `model_call_started`/`model_call_finished`、8 个 `action_selected`/`action_finished` 和最终 `run_finished`。manifest 均为 `failed`、`redaction_applied: false`，且 `diagnostic_failures` 为空。
- basic 仅有 result 产物，没有截图、模型消息/响应、页面输入、DOM 或动作快照，符合 AC-003。diagnostic 每步各保存页面输入、原图、模型消息、SDK 应用层响应及解析决策（各 8）；raw 另有 8 个未截断页面快照、8 个响应元数据、8 个动作后快照和 8 张动作后图，符合实际触发的补充动作采集。
- 两个截图默认开启的模式均保存了截图；对保存的模型消息结构检查未发现图片输入，因此本次截图采集未启用或改变模型视觉输入。未触发视觉候选/标注图，故不将其记为产物缺失。
- 网站业务目标在八步内未完成（日志显示先点击“加入我们”，其后滚动），这是任务结果，不是诊断故障。真实运行未见配置或网络失败，未重试。
- AC-004 仍为 **Partial**：本次仅验证保存 SDK 应用层实际可取得的响应，不能将其误记为原始 HTTP 或解析前响应。

## 环境

- 未读取 `.env`。诊断不脱敏，受控目录外不得共享或上传。

## 产物

- 自动化测试在临时诊断根目录验证 `events.jsonl`、`manifest.json`、result 和分级 artifacts；测试结束后临时目录清理。

## 未验证项

- 无未运行 AC；`AC-004` 为 Partial，原因是 SDK 可见性不提供且实现不采集原始 HTTP/解析前响应或隐藏推理。

## 残余风险

- 本版本不脱敏（`redaction_applied: false`）；诊断含页面和模型内容，必须受控存储。
- `log/diagnostics` 是相对默认路径，不是 XDG 位置；部署时应显式指定受控的 `--diagnostics-root`。
- 达到总容量时不自动删除活动或崩溃遗留的 `running` 目录，总容量对活动运行是软上限，需人工审查/清理。
