# Agent 工作协议

## 项目边界

- 本仓库是 JobFinder；普通产品任务可按用户要求修改 `src/`、`tests/` 等文件；文档系统改造才限制为根目录 `AGENTS.md`、`README.md` 和 `agent-docs/` 下的文档。
- `../browser-use` 是相邻依赖，严禁修改；未跟踪的 `.opencode/` 也不得修改或删除，除非用户明确要求目标就是它们。
- 不读取、不复制、不记录 `.env` 或任何密钥。

## 事实来源

冲突时按以下优先级判断：实际代码，其次任务 `handoff.md`，其次 `verification.md`，其次 `spec.md`，其次 `execution.md`，最后才是索引或其他说明。规范与实现不一致时登记问题，不在文档迁移中声称已解决。

## 会话协议

启动时先查看 `git status`、`AGENTS.md`、`agent-docs/README.md` 和当前任务文档，确认边界及已有修改；不得回退无关改动。创建、拆分、执行或关闭任务时必须遵守 [`agent-docs/task-workflow.md`](agent-docs/task-workflow.md)。工作中保持单一任务范围，优先引用代码事实，敏感值只说明存在，不展示内容。结束前更新对应任务的 handoff、verification 和索引状态，记录实际修改文件、验证命令、结果、阻塞和下一步；不得把未运行的检查写成通过。纯只读审查或探索任务不应为了更新状态而违反用户的只读要求，只有实际改变任务状态、代码或验证事实时才更新持久文档。

状态只能使用：`draft`、`ready`、`in_progress`、`blocked`、`review`、`done`、`superseded`。

## 标准验证

```bash
git diff --check
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv run pytest -q -m integration
```

集成测试按任务范围执行；纯文档或只读任务无需强制运行集成测试，也无需为此修改 handoff。文档变更还要手工检查所有 Markdown 相对链接并搜索旧路径引用。若命令因环境失败，记录原始事实，不修改产品源代码来掩盖失败。
