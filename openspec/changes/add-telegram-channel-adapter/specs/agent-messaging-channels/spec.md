## ADDED Requirements

### Requirement: telegram ChannelAdapter SHALL 可真收发（非永久 stub）

当 Telegram 运行时启用时，`channel_type=telegram` 的注册实现 SHALL 支持 normalize 入站 Update 与出站 `sendMessage` 投影；**SHALL NOT** 仅停留在无副作用的 stub（测试可用 stub 替换）。

#### Scenario: Registry 解析到可运行 adapter

- **WHEN** 运行时已启动且存在启用中的 telegram 通道
- **THEN** ChannelRegistry 对 `telegram` 的解析结果 SHALL 能执行入站规范化与出站发送接口
