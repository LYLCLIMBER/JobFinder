---
task_id: JF-008
status: done
---

# 实施方案

1. 在滚动执行前保存 URL，并保留现有 offset 快速成功路径。
2. offset 不变时通过 `BrowserSession.get_current_page_url()` 轮询，默认最长 2 秒、间隔 250 毫秒。
3. 轮询使用现有 step deadline 计算剩余时间，不使用完整 DOM 状态作为每次探针。
4. route 变化立即返回独立摘要并停止 fallback；超时后继续现有内部目标 fallback 或失败。
5. 增加延迟变化、无变化、offset 优先、停止 fallback 和 deadline 单元测试。
6. 运行标准 lint、format、unit 和 integration 验证，并完成独立 review。

## 实施约束

- 不修改 `../browser-use`。
- 不加入 scroll range、DOM 或截图成功判据。
- 不把 route change 描述为像素滚动。
- 不用固定单次长 sleep 替代有界轮询。
