# Delta: user-platform — MCP 合并场景措辞收窄

## MODIFIED Requirements

### Requirement: 用户 MCP 配置

系统 SHALL 持久化用户 MCP server 配置，并在 Agent 装配时与平台默认合并；用户配置 **SHALL NOT** 覆盖平台安全禁止项（若有黑名单）。CRUD API SHALL 需登录。

#### Scenario: 合并可见

- **WHEN** 用户添加自定义 MCP server 后在启用该 server 的会话（含重档 SuperAgent）启动
- **THEN** 工具列表 SHALL 含合并后的可用 server（在连接成功前提下）
