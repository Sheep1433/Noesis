## ADDED Requirements

### Requirement: 飞书 HITL 操作 SHALL 映射到统一决策模型
需要审批时，系统 SHALL 向原飞书会话发送只含安全摘要的交互卡片；approve/reject action SHALL 映射到网页和 Telegram 共用的 decision 模型。回调 token SHALL 为短期、不透明、单次使用，并校验用户、通道、session 与 pending 状态。

#### Scenario: 已配对用户批准有效卡片
- **WHEN** 已配对用户点击未过期审批卡片的批准按钮
- **THEN** 系统 SHALL 以 `approve` resume 原 Agent run
- **AND** 后续输出 SHALL 继续投递到同一飞书会话

#### Scenario: 其他用户点击审批卡片
- **WHEN** 非 pending 所属用户提交相同 callback token
- **THEN** 系统 SHALL 拒绝 resume 且 SHALL NOT 执行待审批工具

### Requirement: 飞书 clarification SHALL 接受下一条已配对文本
当 pending decision 要求补充信息时，系统 SHALL 将同一绑定用户的下一条文本映射为 `respond`，并 SHALL 避免同时创建新的独立 Agent run。

#### Scenario: 用户补充缺失信息
- **WHEN** 会话存在有效 clarification pending 且所属用户发送文本
- **THEN** 系统 SHALL 使用该文本 resume 原 run
- **AND** SHALL NOT 为该文本新建第二个 run
