# user-mcp-config Specification

## Purpose
TBD - created by archiving change composer-session-tools. Update Purpose after archive.
## Requirements
### Requirement: 用户 MCP 配置 SHALL 与平台配置合并

系统 SHALL 在 `.data/users/{user_id}/mcp.json` 存储用户 MCP；顶层形状为 `{ "mcpServers": { "<id>": { ... } } }`。目录合并时同名 server SHALL 以用户配置覆盖平台 `extensions/mcp/mcp.json`。

用户 server 的 `transport` SHALL 仅为 `streamable_http` 或 `sse`；SHALL NOT 接受 `stdio` / `command`。

#### Scenario: 用户覆盖同名平台 server

- **WHEN** 平台与用户均定义 id `ssh`
- **THEN** `resolve` 结果 SHALL 使用用户配置

### Requirement: 系统 SHALL 提供 MCP 目录与用户 CRUD API

系统 SHALL 提供（前缀 `/api/mcp`，需登录）：

- `GET /servers` — 合并目录（id、source=platform|user、transport、展示名；密钥字段脱敏）
- `PUT /servers/{id}` — 创建或更新当前用户 server
- `DELETE /servers/{id}` — 删除当前用户 server（SHALL NOT 删除平台 server）
- `POST /servers/{id}/probe` — 可选连通探测（显式触发）

#### Scenario: 列出目录不含密钥明文

- **WHEN** 用户调用 `GET /api/mcp/servers`
- **THEN** 响应中 header/env 类敏感值 SHALL 被脱敏或省略

#### Scenario: 禁止用户 stdio

- **WHEN** 用户 PUT 的 body 含 `transport=stdio` 或 `command`
- **THEN** 系统 SHALL 拒绝（4xx）且不写入磁盘

