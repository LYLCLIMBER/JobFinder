---
task_id: JF-002
status: review
verified_at: 2026-09-04
---

# 验证记录

## 验证范围

拆分自历史测试报告：覆盖普通招聘链路、视觉候选和消息、浏览器生命周期、Kuro 真实网站，以及校园专项的安全失败。历史 README 链接问题已在本次迁移中修正，不再列为当前问题。

## 验收矩阵

| ID | 验收项 | 结果 | 证据/说明 |
| --- | --- | --- | --- |
| AC-001 | 默认关闭 | Passed | `use_vision=False`，自动化覆盖关闭截图请求。 |
| AC-002 | 候选语义筛选 | Passed | `vision.py` 排除文本、value、ARIA、title、placeholder、alt 和 AX 语义。 |
| AC-003 | 坐标与安全布局 | Partial | 代码支持按截图实际尺寸缩放，单测覆盖滚动、无效和视口外矩形；当前单测未独立覆盖高 DPR/截图尺寸变化。 |
| AC-004 | 截图 selector_map 标注 | Passed | 单测确认 PNG 标注和 index。 |
| AC-005 | 多模态消息 | Passed | 单测确认 DOM、index 上下文和一个图片块，继续 `AgentDecision`。 |
| AC-006 | selector_map/done 安全校验 | Passed | 历史审查确认 click 索引校验；done 不能仅凭截图通过。 |
| AC-007 | 图片失败降级 | Passed | 图片解码、坐标和标注失败返回纯文本上下文，不猜测点击。 |
| AC-008 | 多模态失败后纯文本重试 | Partial | 当前期望是失败后重试纯文本请求；实际调用失败直接计为步骤失败，没有纯文本重试，未改代码。 |
| AC-009 | 无企业专用逻辑 | Passed | 代码和历史 Kuro 验证未将企业 class/URL 写入生产逻辑。 |

## 自动化测试

- 历史 `uv run pytest`：`19 passed in 4.35s`；历史 `uv run ruff check .`：通过。
- 本次本地自动化重跑（2026-09-04）：`uv run pytest -q` 为 `19 passed in 4.33s`；`uv run pytest -q -m integration` 为 `1 passed, 18 deselected in 4.24s`；`uv run ruff check .` 通过；`uv run ruff format --check .` 为 `9 files already formatted`；`git diff --check` 通过。
- 本次未重跑真实视觉 API；也未将本次本地重跑表述为真实网站验证。

## 手工/真实网站验证

普通 Kuro 链路成功：8 步进入 `kurogame.jobs.feishu.cn` 岗位列表，岗位为 `NAMI-分镜编导`；日志显示候选 index 与传给模型的 index 一致。[普通链路日志](../../../log/kuro-vision-review/)。

校园专项安全失败：8 步后仍在 `https://www.kurogames.com/join`，返回 `Maximum steps reached without finding a specific job`，没有误报社会招聘岗位。[校园链路日志](../../../log/kuro-vision-review-campus/)。

## 环境

历史报告记录真实 Chromium、Kuro 官网和 `deepseek-v4-flash-vision-exp`；未提供时间、commit 或版本，因此不伪造这些字段。密钥来自 `.env`，本文不读取或记录其值。

## 产物

- [Kuro 普通链路日志/截图](../../../log/kuro-vision-review/)
- [Kuro 校园专项日志/截图](../../../log/kuro-vision-review-campus/)
- `tests/test_vision.py`、`tests/test_finder.py`

## 未验证项

- 本次未重新调用真实视觉 API。
- 当前单测未独立覆盖高 DPR/截图尺寸变化。
- 多模态调用异常后的纯文本重试不存在，故无法标 Passed。
- 完整 evidence 是否逐字存在于 DOM 未由当前代码验证。

## 残余风险

动态全屏页面的入口发现、内部滚动元数据和 8 步预算可能造成安全失败；真实网站内容会变化，历史 Kuro 结果不能替代稳定单元测试。
