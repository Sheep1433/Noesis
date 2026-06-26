## MODIFIED Requirements

### Requirement: AioSandboxBackend SHALL 经 agent_sandbox 远程执行

`AioSandboxBackend` **SHALL** 经 `agent_sandbox.Sandbox(base_url=...)` 实现 `execute`、`upload_files`、`download_files`。**SHALL NOT** 在 API 进程本地 shell 执行 Agent 命令。

`execute` 的调用失败与执行结果语义 **SHALL** 符合 `agent-tool-failure-handling` 双层模型：

- **超时**：`shell.exec_command` 超时 **SHALL** 抛出 `ToolTimeoutError`（调用失败），**SHALL NOT** 返回 `exit_code=0` 或空 stdout 的 success 响应；
- **沙箱不可达 / runner 错误**：**SHALL** 抛出 `ToolInfrastructureError`，**SHALL NOT** 将异常文本写入 `ExecuteResponse.output` 后作为 success 返回；
- **命令正常结束**：**SHALL** 返回 `ExecuteResponse`，含 `output`、`exit_code`、`truncated`；由上层 `execute` 工具序列化为含 `exit_code` / `timed_out` 的 JSON 供 bridge 解析。

#### Scenario: execute 不经 API 进程 shell

- **WHEN** Agent 调用 `execute`
- **THEN** 命令 **SHALL** 在用户 AIO 容器内执行，**SHALL NOT** 在 FastAPI 进程 `subprocess` 执行

#### Scenario: execute 超时时抛出 ToolTimeoutError

- **WHEN** `shell.exec_command` 因超出 `timeout` 失败
- **THEN** `AioSandboxBackend.execute` **SHALL** 抛出 `ToolTimeoutError`
- **AND** **SHALL NOT** 返回空 `output` 且 `exit_code=0` 的 `ExecuteResponse`

#### Scenario: 沙箱基础设施错误抛出 ToolInfrastructureError

- **WHEN** 沙箱 client 连接失败或容器不存在
- **THEN** `execute` **SHALL** 抛出 `ToolInfrastructureError`
- **AND** **SHALL NOT** 返回 `ExecuteResponse(output="AIO sandbox execute failed: ...", exit_code=1)` 作为唯一手段
