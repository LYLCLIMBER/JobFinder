---
task_id: JF-001
status: review
verified_at: 2026-09-04
---

# 验证记录

## 验证范围

覆盖 MVP 循环、普通招聘页面、动作和浏览器清理；以下自动化和真实网站证据来自旧报告，属于历史验证范围，不代表本次重新运行。

## 验收矩阵

| ID | 验收项 | 结果 | 证据/说明 |
| --- | --- | --- | --- |
| AC-001 | 当前页面出现具体岗位 | Partial | 历史 Kuro 普通链路返回 `NAMI-分镜编导`；代码只校验标题在 DOM。 |
| AC-002 | 招聘入口不能算成功 | Passed | 提示词、schema 约束和历史行为均排除通用入口。 |
| AC-003 | 成功结果字段完整 | Partial | 字段和非空校验存在；完整 evidence 未做 DOM 校验。 |
| AC-004 | 步数限制与浏览器关闭 | Passed | 历史审查确认启动、导航、超时和正常结束均清理。 |
| AC-005 | 只执行四种动作 | Passed | 动作 schema 和执行分支限定四种动作。 |

## 自动化测试

- 历史 `uv run pytest`：`19 passed in 4.35s`；历史 `uv run ruff check .`：通过。
- 本次本地重跑（2026-09-04）：`uv run pytest -q` 为 `19 passed in 4.33s`；`uv run pytest -q -m integration` 为 `1 passed, 18 deselected in 4.24s`；`uv run ruff check .` 通过；`uv run ruff format --check .` 为 `9 files already formatted`；`git diff --check` 通过。
- 本次未重跑真实视觉 API。

## 手工/真实网站验证

Kuro 普通链路：视觉开启，最多 8 步，从官网进入飞书招聘列表，返回 `NAMI-分镜编导`，步骤 8。原始材料：[log/kuro-vision-review](../../../log/kuro-vision-review/)。

## 环境

历史记录使用真实 Chromium、Kuro 官网和视觉模型 `deepseek-v4-flash-vision-exp`；报告没有提供时间、commit 或版本信息，本文不补写。

## 产物

- [普通链路日志和截图](../../../log/kuro-vision-review/)
- `tests/test_finder.py`、`tests/test_local_browser.py`

## 未验证项

- 本次未重新进行真实网站/API 验证。
- 完整 evidence 原文是否存在于 DOM 未被当前代码验证。

## 残余风险

动态页面、跨域招聘系统和 8 步预算可能导致安全失败；规范与实际 evidence 校验仍有差异。
