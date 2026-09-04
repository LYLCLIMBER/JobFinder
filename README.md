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

默认浏览器配置为无头模式，关闭截图、下载和默认扩展，并在所有退出路径终止浏览器进程。MVP 不处理登录、验证码、申请表单或搜索引擎查找官网。
