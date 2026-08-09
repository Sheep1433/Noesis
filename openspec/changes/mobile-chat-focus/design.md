## Context

`frontend/src/views/chat.vue` 当前在 Composer 上方常驻三个 `qa_type` 按钮，切换时会清空本地消息、重置 Composer 配置并创建新的 UUID。移动端外壳 `frontend/src/components/Layout/SlotCenterPanel.vue` 还固定展示底部全局导航；`DefaultPage.vue` 展示品牌与能力卡片。这些元素叠加后明显压缩消息区。

`qa_type` 同时影响 Agent 路由、会话历史、附件、Skills、MCP 与 mentions，不能仅在视觉层把类型合成一个运行时。改动应保留内部类型，只调整用户入口和移动端布局。

## Goals / Non-Goals

**Goals:**

- 将 `COMMON_QA`、`SUPER_AGENT_QA` 呈现为“聊天”“任务”。
- 新建会话时选择模式；已有消息时切换模式必须创建新会话，不破坏当前历史。
- 移动端聊天路由常态只展示极简顶栏、消息区和 Composer。
- 保留历史、全局导航、会话文件、模型、附件、知识库、MCP 与 Skills 的可达入口。
- 桌面端采用相同模式语义，同时保持现有宽屏结构。

**Non-Goals:**

- 不修改 `/api/chat`、SSE、数据库或 Agent profile。
- 不将 `COMMON_QA` 与 `SUPER_AGENT_QA` 合为同一个后端类型。
- 不改变故障运维与测试用例 Agent 的执行行为。
- 不重做消息卡片、Markdown、Reasoning 或 Tool 展示。

## Decisions

### 1. 模式是会话属性，不是消息级开关

新增前端模式元数据，把“聊天”映射到 `COMMON_QA`，“任务”映射到 `SUPER_AGENT_QA`。顶栏展示当前模式；模式选择组件只调用已有的类型切换入口。

如果当前会话已有消息，选择另一模式将保留当前会话并进入该模式的空白 COMPOSING 表面。历史会话从 `extra.qa_type` 恢复原模式。这样与当前一类会话对应一个 `qa_type` 的数据模型一致。

备选方案是在发送每条消息时自动判断类型。该方案会造成不可预测的工具权限与会话上下文变化，本次不采用。

### 2. 以共享模式选择器替代 Composer 常驻按钮

新增独立 `ChatModeSelector`，负责紧凑触发器和模式面板：

- “聊天”：`COMMON_QA`
- “任务”：`SUPER_AGENT_QA`
- “故障排查”：`FAULT_OPERATION_QA`，作为任务类专项入口展示

移动端触发器放在极简顶栏；桌面端可放在顶栏同一位置。Composer 上方删除 `qa-type-tabs`。选择器只发出目标 `qa_type`，会话生命周期仍由 `chat.vue` 管理。

### 3. 聊天路由启用移动端沉浸式外壳

`SlotCenterPanel.vue` 根据当前路由判断移动端聊天页：

- 不渲染 `MobileBottomNav`；
- 移除外壳四周 padding 和圆角；
- 内容占满安全区域以内的可用高度。

左上角菜单继续打开历史抽屉。历史抽屉增加产品导航入口，确保隐藏底部导航后知识库、扩展、测试和设置仍可访问。

会话文件入口从顶栏移入 Composer `+` 菜单或顶栏溢出菜单，避免常驻占位。

### 4. 移动端空白会话不展示营销式欢迎卡片

移动端 `DefaultPage.vue` 只展示与当前模式相关的一行短提示，消息区仍保留充足留白。桌面端继续显示完整欢迎内容。

### 5. 执行状态按需出现

重连提醒、HITL 和 Todo 仅在相关状态存在时展示在 Composer 上方。它们属于当前执行必须处理的信息，不作为常驻导航。

### 6. API 与 SSE 数据流不变

选择模式 → `chat.vue` 更新 `qa_type` 与 COMPOSING UUID → 首次发送时按现有流程 ensure session / 上传 → `POST /api/chat/sessions/stream`。本变更不新增 SSE 事件，也不改变 assistant 落库状态机。

## Risks / Trade-offs

- [风险] 隐藏底部导航后用户找不到其它模块 → 历史抽屉加入明确的产品导航，并保留桌面端导航。
- [风险] 用户误以为可在同一会话中切换执行能力 → 模式变化创建新的空白会话，顶栏持续显示当前模式。
- [风险] “任务”含义仍较宽 → 模式面板用一行说明“调研、分析与多步骤执行”，故障排查作为专项入口。
- [风险] 移动端键盘和 safe-area 造成 Composer 遮挡 → 继续使用应用高度 token，并把底部 padding 改为 safe-area，而不是固定导航高度。
- [风险] 现有深链 `?qa_type=` 行为变化 → 保留 query 映射和历史加载逻辑，只替换展示文案与入口。

## Migration Plan

1. 增加模式元数据和选择器，并接入现有切换函数。
2. 删除常驻类型按钮，调整空态、顶栏和移动外壳。
3. 给历史抽屉补充隐藏导航后的入口。
4. 运行 lint、build 和相关测试；在窄屏验证新建、历史恢复、模式切换、抽屉导航与 Composer。
5. 若需回滚，可恢复原类型按钮与移动底栏；后端和历史数据无需迁移。

## Open Questions

无。首期“任务”下保留故障排查入口；后续是否增加任务模板不在本次范围。
