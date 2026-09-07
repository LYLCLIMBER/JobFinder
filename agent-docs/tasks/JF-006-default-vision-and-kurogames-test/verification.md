---
task_id: JF-006
status: review
verified_at: 2026-09-07
---

# 验证记录

## 验证范围

- 默认视觉配置、显式文本模式和一次 Kuro Games 真实 CLI 调用。

## 验收矩阵

| ID | 验收项 | 结果 | 证据/说明 |
| --- | --- | --- | --- |
| AC-001 | 公开默认构造和运行时装配启用视觉 | Passed | `tests/test_runtime.py::test_default_vision_is_enabled_and_can_be_explicitly_disabled`；全量测试通过。 |
| AC-002 | 显式关闭保持文本输入 | Passed | 同一运行时测试验证传递 `False`；`tests/test_diagnostics.py::test_screenshot_capture_does_not_change_nonvision_model_input` 显式使用 `use_vision=False` 并通过。 |
| AC-003 | Kuro Games 真实调用及视觉证据 | Passed | 指定 CLI 成功，raw run `20260907T041423123427Z_71031b8fd7d24a9cb3389ee6baaea78f` 保存了请求与视觉产物。 |

## 自动化测试

- 命令：`uv run pytest -q tests/test_runtime.py tests/test_finder.py tests/test_diagnostics.py`
- 结果：44 passed in 0.57s。
- 命令：`uv run ruff check .`；`uv run ruff format --check .`；`uv run pytest -q`；`uv run pytest -q -m integration`；`git diff --check`
- 结果：分别通过、19 files already formatted、75 passed in 5.92s、2 passed/73 deselected in 5.83s、通过。

## 手工/真实网站验证

- 已先确认父目录 `log/` 存在、目标根目录尚不存在，然后仅执行一次：`uv run jobfinder find-job-page "https://www.kurogames.com/" --max-steps 8 --diagnostics-level raw --diagnostics-root "log/jf006-kurogames-vision-test"`。
- 结果：成功（task ID `a1a0399f6e7e484385ee4937a77bfe21`，7 步，32,713 ms），页面为 `https://kurogame.jobs.feishu.cn/index/position/list?functionCategory=7456715028863912201`，职位为 `NAMI-分镜编导`。
- 视觉：7 次模型调用均成功；其中步骤 1、2、3、5、6、7 的 `model_messages` 含 `image_url`，所以模型实际接受了 6 张请求图片。步骤 4 没有生成视觉上下文，因而是文本请求；未改变策略。没有模型接口报出图片或视觉不支持。

## 环境

- 不主动读取 `.env`；真实调用由程序自动加载既有配置，未修改或更换模型。

## 产物

- `log/jf006-kurogames-vision-test/20260907T041423123427Z_71031b8fd7d24a9cb3389ee6baaea78f/manifest.json`：`status: succeeded`、`capture_screenshots: true`。
- 同目录 `events.jsonl`：步骤 1、2、3、5、6、7 记录 `annotated_screenshot_saved` 和 `visual_candidates_saved`，以及成功的 `model_call_finished`。
- `artifacts/006_model_messages.json`（以及 017、028、048、059、070）含 `type: image_url`；对应 6 个标注截图和候选 JSON。步骤 4 的 `037_model_messages.json` 不含图片。

## 未验证项

- 无。

## 残余风险

- 真实网站和模型行为随时间可能变化；此次仅执行一次，不能代表所有页面或模型配置。第 4 步无视觉候选，因此该单步未发送图片，但没有回退或接口失败。
