# agent-hitl · Delta

## ADDED Requirements

### Requirement: HITL 决策应用 SHALL 为单一投影实现

approve / reject / respond 决策对消息投影的应用（工具 part 状态翻转、reject 的拒绝 ToolMessage 投影、respond 的应答投影）SHALL 由单一投影实现提供，主聊天、通道与后台子 Agent 复用同一实现，SHALL NOT 在各链路保留各自的决策应用拷贝。后台子 Agent 的审批拒绝/应答 SHALL 与主链路同构地在子会话消息投影中合成工具终态展示（被拒工具在子会话详情中 SHALL 有明确的拒绝态，而非停留在无终态）。

#### Scenario: 子会话被拒工具有终态展示

- **WHEN** 用户拒绝后台子 Agent 的某次工具调用并附说明
- **THEN** 子会话详情的该工具 part SHALL 展示拒绝状态与说明
- **AND** 渲染形态 SHALL 与主聊天被拒工具一致

#### Scenario: 决策应用单一实现

- **WHEN** 代码审查发现主聊天 / 通道 / 子 Agent 任一链路的决策投影逻辑
- **THEN** 其 SHALL 委托同一投影函数实现
- **AND** SHALL NOT 存在语义重复的并行拷贝

### Requirement: HITL 超时语义与文案 SHALL 两侧一致

主聊天 run 与后台子 Agent run 的审批超时 SHALL 语义一致：超时自动按拒绝续跑（同一决策归一化路径）、assistant SHALL NOT 永久停留在 streaming，超时注入的用户可见文案 SHALL 为同一份。

#### Scenario: 子 Agent 审批超时与主链路同文案

- **WHEN** 后台子 Agent awaiting_approval 超过 `hitl.ask_timeout_seconds`
- **THEN** 系统 SHALL 自动按拒绝续跑并注入与主链路相同的超时提示文案
- **AND** 子会话消息 SHALL 到达可观测终态
