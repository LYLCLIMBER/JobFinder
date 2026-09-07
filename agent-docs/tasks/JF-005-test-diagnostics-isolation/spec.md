---
task_id: JF-005
title: Test Diagnostics Isolation
status: done
---

# 任务规格

## 目标

隔离 pytest 中未显式指定的诊断输出，避免自动化测试写入仓库默认 `log/diagnostics`。

## 背景

`RuntimeConfig` 和 CLI 都使用相对默认诊断根目录；测试调用这些默认值时会污染工作区。

## 范围

- 为 pytest 建立函数级、自动使用的诊断根目录隔离。
- 保持产品运行时和 CLI 的默认路径为 `log/diagnostics`。
- 为 runtime 和 CLI 默认路径增加回归测试。

## 不在范围

- 修改产品诊断路径、浏览器依赖或既有诊断产物。

## 安全边界

- 不读取或记录密钥。
- 不读取、删除或修改既有诊断内容。

## 验收标准

- `AC-001`：未显式设置 `RuntimeConfig.diagnostics_root` 的 pytest runtime 调用只写入该测试的 `tmp_path`。
- `AC-002`：未传递 `--diagnostics-root` 的 pytest CLI 调用只写入该测试的 `tmp_path`。
- `AC-003`：显式传递的诊断根目录及产品默认 CLI 参数含义保持不变，测试间诊断根目录隔离。
- `AC-004`：标准静态检查、全量和集成测试及默认目录元数据前后对比无新增或修改。

## 相关代码

- `src/job_page_finder/runtime.py`
- `src/job_page_finder/cli.py`
- `tests/conftest.py`
- `tests/test_runtime.py`
- `tests/test_cli.py`

## 外部依赖

- pytest 的 `tmp_path` 和 `monkeypatch` fixture。

## 未决问题

- 无。
