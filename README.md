# Job Page Finder

一个基于本地 `browser-use` 的轻量招聘岗位页面发现器。输入企业官网 URL，最多执行有限次点击、滚动或等待，直到当前页面出现具体岗位名称。

## 安装

```bash
uv sync --dev
```

项目通过 `pyproject.toml` 使用相邻目录中的 `../browser-use` 源码。

## DeepSeek 配置

在 `JobFinder/.env` 或工作区父目录的 `.env` 中配置：

```env
DEEPSEEK_API_KEY=your-key
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
```

`.env` 已加入 Git 忽略规则。模型和 API 地址可省略，以上值是默认值。

## 使用

统一入口会校验任务、装配依赖并返回稳定的 JSON 结果：

```python
import asyncio

from job_page_finder import run_task


async def main() -> None:
    result = await run_task(
        {
            "version": "v1",
            "type": "find_job_page",
            "payload": {"company_url": "https://example.com", "max_steps": 8},
        }
    )
    print(result.model_dump_json(indent=2))


asyncio.run(main())
```

```bash
jobfinder find-job-page https://example.com --max-steps 8
jobfinder run task.json
```

`jobfinder` 成功时退出码为 0；非法输入为 2；配置错误为 3；任务执行失败为 1。标准输出只包含最终 JSON，诊断日志写入标准错误。

### 运行时诊断

每个 `run_task()` 运行默认在 `log/diagnostics/` 创建一个独立的 `basic` 诊断目录，包含
`events.jsonl`、`manifest.json` 和最终 `result` 产物；业务 JSON schema 不会变化。可用 CLI 控制：

```bash
jobfinder find-job-page https://example.com --diagnostics-level diagnostic
jobfinder run task.json --diagnostics-level raw --diagnostics-root /controlled/diagnostics \
  --diagnostics-screenshots --diagnostics-max-runs 100 --diagnostics-retention-days 7 \
  --diagnostics-max-run-bytes 268435456 --diagnostics-max-total-bytes 5368709120
```

`diagnostic` 和 `raw` 默认采集截图，`--no-diagnostics-screenshots` 可关闭它；此开关不影响
`use_vision` 或发送给模型的输入。`diagnostic` 保存可从 SDK 实际取得的模型消息、完成响应、解析
结果、页面输入和截图；`raw` 还保存完整 DOM 和页面位置元数据。诊断当前**不脱敏**
（manifest 标记 `redaction_applied: false`），不得上传或共享到不受控位置；不会保存 headers、cookies、
环境变量、完整配置或客户端对象。SDK 暴露的是完成响应，不保证或声称保留供应商原始 HTTP 响应。

默认最多保留 100 个目录并清理超过 7 天的**已完成**受控目录；每个运行最多 256 MiB，受控目录
合计最多 5 GiB。容量检查在跨进程锁内进行；达到任一容量时会跳过普通产物，并在 manifest/events
标记诊断不完整。最终 `result` 是必要摘要，即使超过配置容量仍会写入；manifest 会同时标记
`diagnostic_incomplete: true` 和 `summary_budget_exceeded: true`。因此这些容量对最终摘要（以及活动运行）
是软上限；活动运行不因总容量自动删除，
崩溃遗留的 `running` 目录须人工审查和清理。创建、写入或清理诊断失败不会改变任务结果；可用的
manifest/events 也会记录该故障。

SDK 只能保存实际可取得的完成响应；不会采集不可得的原始 SDK 数据或隐藏推理（thinking）。运行目录
和产物分别以 0700、0600 权限创建，并以原子替换写入产物。

也可以继续直接调用领域执行器：

```python
import asyncio

from job_page_finder import (
    JobPageFinder,
    JobPageFinderInput,
    create_deepseek_llm,
)


async def main() -> None:
    finder = JobPageFinder(llm=create_deepseek_llm())
    result = await finder.find(
        JobPageFinderInput(company_url="https://example.com", max_steps=8)
    )
    print(result.model_dump_json(indent=2))


asyncio.run(main())
```

默认浏览器配置为无头模式，关闭下载和默认扩展，并在所有退出路径终止浏览器进程。页面状态截图按视觉和诊断配置获取。MVP 不处理登录、验证码、申请表单或搜索引擎查找官网。

条件视觉默认开启，需要使用支持图片输入的模型；可通过 `RuntimeConfig(use_vision=False)` 或 `JobPageFinder(use_vision=False)` 显式关闭。以下示例显式指定视觉配置：

```python
finder = JobPageFinder(
    llm=create_deepseek_llm(model="deepseek-v4-flash-vision-exp"),
    use_vision=True,
)
```

只有页面存在没有文本语义但有可靠布局位置的交互元素时，当前视口截图才会附加到模型请求中。实现说明见 [`agent-docs/tasks/JF-002-conditional-vision-fallback/spec.md`](agent-docs/tasks/JF-002-conditional-vision-fallback/spec.md)。
