# Agent 文档索引

本目录只做导航，不覆盖任务 `handoff.md` 中的当前事实。实际代码优先；规范冲突应登记在任务文档的问题或残余风险中。

创建、拆分、执行、交接或关闭任务时遵守 [`task-workflow.md`](task-workflow.md)，新文档从 [`templates/`](templates/) 初始化。

## 任务

| Task ID | 主题 | 状态 | 文档 |
| --- | --- | --- | --- |
| JF-001 | Job Page Finder MVP | review | [spec](tasks/JF-001-job-page-finder-mvp/spec.md) · [handoff](tasks/JF-001-job-page-finder-mvp/handoff.md) · [verification](tasks/JF-001-job-page-finder-mvp/verification.md) |
| JF-002 | Conditional Vision Fallback | review | [spec](tasks/JF-002-conditional-vision-fallback/spec.md) · [execution](tasks/JF-002-conditional-vision-fallback/execution.md) · [handoff](tasks/JF-002-conditional-vision-fallback/handoff.md) · [verification](tasks/JF-002-conditional-vision-fallback/verification.md) |
| JF-003 | Unified Task Runtime | review | [spec](tasks/JF-003-unified-task-runtime/spec.md) · [execution](tasks/JF-003-unified-task-runtime/execution.md) · [handoff](tasks/JF-003-unified-task-runtime/handoff.md) · [verification](tasks/JF-003-unified-task-runtime/verification.md) |

## 阅读顺序

先读本索引和任务 `handoff.md`；需要理解目标时读 `spec.md`，需要实现步骤时读 `execution.md`，最后读 `verification.md` 的验收证据和风险。需要创建或变更任务生命周期时先读 [`task-workflow.md`](task-workflow.md)。

## 标识规则

- Task ID 使用 `JF-NNN`，目录名使用 `JF-NNN-short-name`；三位数字一旦分配不得复用。
- 验收项 ID 使用任务内唯一的 `AC-NNN`，从 `AC-001` 顺序编号；文档引用必须保留完整 ID。

## 状态

任务状态必须属于 `draft`、`ready`、`in_progress`、`blocked`、`review`、`done`、`superseded`。索引状态是导航摘要，不替代 handoff。
