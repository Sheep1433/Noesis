Delta: platform-chat — 子会话抽屉与主会话 UI 同构

## MODIFIED Requirements

### Requirement: 子 Agent（task）展示

chat 页 SHALL 对 `task` 工具 parts 渲染折叠 UI；子 Agent 内部 tool/text/reasoning parts SHALL 嵌套展示。流式帧与 parts MAY 含 `parentTaskCallId`。非法 input/output SHALL 防御性处理。子会话详情抽屉 SHALL 与主会话界面同构复用：消息渲染、统计条与 composer（模型选择、推理档位选择、单按钮发送/停止、待发队列）均 SHALL 使用与主 Agent 相同的组件或同一共享实现；两侧 UI 行为差异 SHALL 仅源于会话上下文（父/子），不源于重复实现。

#### Scenario: 嵌套 tool

- **WHEN** 子 Agent 产生工具调用
- **THEN** UI SHALL 在父 task 折叠块内展示，而非与顶层工具平铺混淆

#### Scenario: 推理档位选择同构

- **WHEN** 用户在子会话抽屉 composer 打开推理档位选择器
- **THEN** 交互与展示 SHALL 与主 Agent composer 的同一选择器一致
- **AND** 选择结果 SHALL 随该条 followup 消息提交

#### Scenario: 领域事件消费单点

- **WHEN** 前端新增对某领域事件（如 usage 终态）的 UI 反应
- **THEN** 主会话与子会话消费路径 SHALL 经同一 reducer 生效，无需分别实现解析逻辑
