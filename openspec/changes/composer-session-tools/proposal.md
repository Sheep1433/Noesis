## Why

聊天 Composer 只能切模型与知识库，MCP 仍绑死在部署级 `mcp.json` + `FAULT_OPERATION_QA`，用户无法按会话启用自定义 MCP，也无法在输入栏看到 Skills/MCP 目录。需要对齐 Cursor 式 `+` 菜单：按会话开关 Models / Skills / MCP Servers，且所有 `qa_type` 均可挂载已启用的自定义 MCP。

## What Changes

- 新增用户级 MCP 配置（`.data/users/{uid}/mcp.json`），与平台 `extensions/mcp/mcp.json` 合并目录；用户 server 仅允许 `streamable_http` / `sse`（禁止 stdio）。
- 新增 `/api/mcp/*`：列出平台+用户 server、用户 CRUD、可选连通探测（不在打开菜单时探测）。
- 会话 `extra.mcp_servers` / `extra.enabled_skills` 持久化勾选；`qa_service` 在发问前解析并注入工具。
- **BREAKING（行为）**：`COMMON_QA` / `SUPER_AGENT_QA` / `TEST_CASE_QA` 在会话勾选 MCP 后 SHALL 挂载对应 MCP 工具（此前 COMMON 明确禁止 MCP）。
- `FAULT_OPERATION_QA`：若会话未写 `mcp_servers`，回退平台 profile `fault_operation`；一旦写入则以勾选为准。
- 前端 Composer `+` 菜单改为层级：附件 / Models / Skills / MCP Servers（+ COMMON 下 KB）；Models 迁入子菜单，保留触发器展示当前模型名。
- Skills 勾选仅对挂载 `SkillsMiddleware` 的 Agent（当前 `SUPER_AGENT_QA`）生效；其它 `qa_type` 仍可展示列表并写入 extra，运行时忽略。

## Capabilities

### New Capabilities

- `composer-session-tools`：Composer 会话级 Models/Skills/MCP 目录、勾选持久化与加载契约
- `user-mcp-config`：用户 MCP 配置存储、合并目录、API、安全约束

### Modified Capabilities

- `agent-common-qa`：允许按会话挂载 MCP 工具
- `agent-fault-operation`：MCP 来源改为会话勾选（缺省回退 profile）
- `agent-super-agent`：可按会话挂载 MCP；Skills 可按会话过滤
- `platform-chat`：会话 `extra` 增加 `mcp_servers` / `enabled_skills`

## Impact

- 后端：`config/mcp_config.py`、`agent/mcp/loader.py`、新 `api/mcp_api.py` / `services/mcp_service.py`、`qa_service`、各 Agent `run_agent` 签名
- 前端：`ChatComposerToolbar`、新 API 客户端、会话 extra 读写
- 规格：COMMON_QA「不得挂 MCP」条款修订；无新外部依赖（沿用 `langchain-mcp-adapters`）
