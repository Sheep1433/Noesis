## ADDED Requirements

### Requirement: feishu ChannelAdapter SHALL 可真实收发
`channel_type=feishu` SHALL 注册可执行 Adapter，而非 Stub 或仅可保存的配置类型。入站 SHALL 规范化为统一 InboundMessage 并进入 ChannelRunService；出站 SHALL 消费同一次 run 的 RunEvent，且 SHALL NOT 经过浏览器 SSE 字符串转发。

#### Scenario: Registry 解析飞书 Adapter
- **WHEN** 飞书运行时启动且存在有效启用配置
- **THEN** ChannelRegistry SHALL 解析到支持入站规范化与出站投影的 `feishu` Adapter

### Requirement: 飞书绑定 SHALL 分离授权主体与回复目标
飞书入站授权 SHALL 绑定发送者 open_id；群聊 chat_id 或 message_id SHALL 只作为会话线程与回复目标，SHALL NOT 单独授予群内所有成员调用 Agent 的权限。

#### Scenario: 同群未配对成员提及机器人
- **WHEN** 未配对成员在已存在目标 chat_id 的群中 @机器人
- **THEN** 系统 SHALL 拒绝触发 Agent

### Requirement: 飞书消息 SHALL 共用消息 SSOT 与 delivery 终态
飞书入站用户消息与 assistant 结果 SHALL 写入网页使用的同一消息 SSOT并记录 `origin=feishu`。飞书发送结果 SHALL 与 run 终态分离，断开浏览器或飞书发送失败 SHALL NOT 阻止 PersistSink 完成终态落库。

#### Scenario: 网页查看飞书会话
- **WHEN** 已配对用户通过飞书完成一轮对话
- **THEN** 对应 session 的 messages API SHALL 返回该轮 user 与 assistant 消息
- **AND** 消息来源 SHALL 可审计为 `feishu`
