---
task_id: JF-007
status: done
---

# 实施方案

## 阶段一：动作与候选

1. 为 `ScrollAction` 增加 `index: int | None`，允许 `0` 表示根页面。
2. 新增滚动模块，封装 DOM 遍历、候选过滤、共享索引分配和模态框优先排序。
3. 根页面由 `page_info` 表示；内部容器由 `EnhancedDOMTreeNode.scroll_info` 表示。
4. 原有 selector 索引保持不变，新增滚动索引从当前最大索引之后分配。

## 阶段二：提示与视觉

1. 在模型输入中增加当前方向可用的滚动目标块。
2. 扩展视觉上下文，将可见滚动容器边界按相同索引绘制到截图。
3. 同一索引若已被视觉候选标注则只绘制一次。

## 阶段三：执行与验证

1. 使用 browser-use `ScrollEvent` 对根页面或指定节点执行一页滚动。
2. 操作后重新获取无截图状态，并按根页面或 `backend_node_id` 读取新偏移量。
3. 仅当新旧偏移量不同时成功，摘要报告实际位移。
4. 根页面没有移动时依次尝试内部候选；模型显式指定内部目标时不扩展 fallback。
5. 全部目标没有移动时返回动作错误，并将事实反馈给下一模型步骤。

## 阶段四：测试与交接

1. 单元测试覆盖索引、过滤、模态框优先、视觉标注、偏移判定和 fallback 停止条件。
2. 本地 Chromium 测试覆盖根页面锁定且内部容器使用 `scroll-snap-type: y mandatory` 的岗位页面。
3. 运行标准 lint、format、unit 和 integration 验证。
4. 更新 handoff、verification 和任务索引状态。

## 实施约束

- 不修改 `../browser-use`。
- 不开放 `send_keys` 或通用 `evaluate`。
- 不增加偏移量之外的成功判据。
- 不加入候选面积、位置或语义评分。
- 任何 fallback 在首个实际移动后立即停止。
