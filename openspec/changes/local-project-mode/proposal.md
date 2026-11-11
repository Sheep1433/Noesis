# 提案：本地项目目录模式——`uv run noesis --project <dir>` 直接干活

## Why

Noesis 当前是「部署在服务器上的多用户平台」：要 PG / Redis / Qdrant / Docker 沙箱四件套才跑得起来，日常自用（打开真实项目目录处理文件、改代码、写文档）的门槛过高。目标形态是「打开我的真实项目目录、直接干活、越界先问」的日常工具——对齐 dsh / Claude Code 的工作模型，产品形态（浏览器 / CLI）不是重点，工作模型才是。

代码核验后路线已经清晰：`noesis-cli` 包已存在且 `uv run noesis chat` 今天就能跑（`MemorySaver` 组合根不碰 Postgres），`agents/backends/` 三种文件后端齐备，`AgentPathBackend.strip_root` 就是 `/workspace` 剥离机制——**CLI 路线离目标只差「接真实目录 + 权限 + 持久化」三步，完全绕开平台存储的 156 处 pg_manager 引用**。TODO 起草时对两个事实的判断需要修正：P0.4（一条命令启动）完成度远高于预期（CLI 已存在）；P0.4 提到的 `agents/runtime/tool_registry.py` 不存在，权限层挂载点另定（见 design）。

Redis / Qdrant 两项在 CLI 路线下零改造（memory 总线是本地默认主路径；Qdrant 连接失败已是优雅降级，工具面全部短路）。

## What Changes

- **CLI 主命令升级**：`noesis chat --project <dir>`（缺省取当前目录）——文件后端从 memory/docker 换 `local_shell`，workspace 根从沙箱 `/workspace` 换本地项目根，Agent 看到的绝对路径与宿主机一致。
- **权限层（碰真实项目的前提）**：读自动放行；写项目内可配置；项目外路径 / 执行命令 / 网络默认询问。复用既有 HITL 中断机制（按工具 + 参数模式扩展），路径边界拦截放 `AgentPathBackend` 层；与 ReadBeforeWrite 的「没读过的文件拒绝改」衔接。
- **本地持久化**：`langgraph-checkpoint-sqlite`（新增依赖）做 checkpointer；会话/消息/待办落项目根 `.noesis/local.db`（新建轻存储，不经 ChatService / pg_manager）。
- **不做**：平台形态不动（app.py / 三角色 / PG 栈照旧）；不迁移 agent loop 与框架底座；第一版砍知识库、插件、多用户（见 TODO.local.md 明确不做清单）。

## Capabilities

### New Capabilities

- `local-project-mode`：本地 CLI 会话的文件边界（项目根内/外路径语义）、权限分级与授权记忆、本地持久化（SQLite）、`--project` 启动参数——验收语义见 specs/。

### Modified Capabilities

（无——平台形态 `platform-chat` 等规格不动）

## Impact

- 新增：`noesis-cli` 的 `--project` 参数与本地组合根；权限中间件/包装（挂 `AgentPathBackend` 外层与 HITL interrupt_on）；`.noesis/local.db` 轻存储（会话、消息、授权记录、待办）。
- 依赖：新增 `langgraph-checkpoint-sqlite`（CLI 可选依赖组）。
- 兼容：`uv run noesis chat` 现有行为不回归（无 `--project` 时维持现状）；平台部署零影响。
- 验收（来自 TODO）：裸 git 仓库零配置跑通对话 + 文件读写；项目外路径必弹确认、拒绝后 Agent 能继续工作不卡死；最终标准「我愿意拿它处理真实文件」。
