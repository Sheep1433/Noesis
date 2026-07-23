## MODIFIED Requirements

### Requirement: MCP 工具连接与工具集边界

`FaultOperationAgent` SHALL 使用 `qa_service` 解析后的 MCP 工具列表（来自会话 `extra.mcp_servers`，缺省回退平台 profile `fault_operation`），通过 `MultiServerMCPClient` 以 `streamable_http` 传输连接对应端点，在每次 `run_agent` 调用时动态加载工具列表并注入主 Agent 与 `general-purpose` 子 Agent。SHALL NOT 在忽略会话勾选的情况下始终硬绑单一 profile（回退仅适用于键缺失）。

MCP 工具集 SHALL 至少覆盖以下语义分层（具体工具名以实现注册为准）：

| 层级 | 工具 | 行为边界 |
|------|------|----------|
| L1 配置 | `setup_passwordless_login` | 一次性密码引导，将本机公钥写入远程 `authorized_keys` 并验证免密登录 |
| L1 原子 | `read` | 经 SSH 读取远程**文本文件**（绝对路径）；支持 `offset`/`limit` 行范围；拒绝二进制/图片 |
| L1 原子 | `grep` | 经 SSH 在远程路径正则搜索；支持 `output_mode`、`glob`、`ignore_case`、`head_limit` 等 |
| L1 原子 | `glob` | 经 SSH 按 glob 模式匹配远程文件列表（最多 100 个） |
| L1 原子 | `bash` | 经 SSH 执行 shell 命令；须携带 `ip`；`timeout_ms` 默认 120000；仅用于诊断类命令 |
| L2 场景 | `system_info` | 组合诊断命令输出主机资源概览（CPU/内存/磁盘/进程等） |
| L2 场景 | `playbook_log` | 检索并读取 Ansible playbook 日志，可过滤错误行 |

所有 MCP 工具调用 SHALL 要求显式 `ip`（目标主机）；Agent 系统提示 SHALL 规定：只使用提供的 MCP 工具操作远程环境，不编造命令或执行结果，日志分析须给出明确结论，修复建议须具体可操作。

#### Scenario: 会话显式选择用户 MCP

- **WHEN** 故障运维会话 `extra.mcp_servers` 为用户自定义 server id 列表
- **THEN** Agent SHALL 仅加载这些 server 的工具

#### Scenario: MCP 端点可达时加载工具

- **WHEN** `FaultOperationAgent.run_agent` 开始且解析出的 MCP server 可用
- **THEN** 系统 SHALL 成功 `get_tools()` 并将非空工具列表传入 Agent，且工具调用经 SSE `tool-*` 帧对外可见（平台桥接规则见 `platform-chat`）

#### Scenario: read 行范围与截断

- **WHEN** Agent 调用 `read` 且指定 `offset` 与 `limit`
- **THEN** MCP 实现 SHALL 仅返回该范围内的远程文件内容（或约定 JSON 中的 `truncated` 标识），不得返回未请求路径的内容

#### Scenario: bash 超时

- **WHEN** Agent 调用 `bash` 且远程命令超过配置的 `timeout`
- **THEN** MCP 实现 SHALL 终止等待并返回可辨失败结果，Agent SHALL 在回复中如实反映失败而非伪造输出
