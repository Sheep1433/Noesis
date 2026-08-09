# Noesis 后端开发指南

FastAPI + LangGraph 后端：多场景 Agent、知识库 RAG、SSE 投递与会话持久化。仓库级约定见 [../AGENTS.md](../AGENTS.md)。

## 技术栈

- **框架**：FastAPI
- **Agent**：LangGraph，统一工厂 `noesis.factory.create_noesis_agent`
- **数据库**：PostgreSQL + SQLAlchemy async
- **向量库**：Qdrant
- **认证**：Cookie Session + CSRF

## 两层结构

Noesis 采用两层后端：`server` 是 HTTP/进程边缘，`packages/noesis-core` 是可独立安装的核心后端包。物理打包保留 DeerFlow 的 workspace package 优点，包内职责采用 YuXi 的完整核心后端方式。

```text
backend/
├── server/                         # 进程与 HTTP 边缘
│   ├── api/                        # FastAPI routers
│   ├── bootstrap/                  # 启动组装
│   ├── middleware/                 # HTTP middleware
│   ├── main.py                     # app + lifespan
│   ├── db.py                       # request-scoped DB dependency
│   ├── response.py                 # ResponseUtil
│   └── exception_handlers.py       # HTTP 异常翻译
├── packages/noesis-core/src/noesis/ # 可安装的核心后端包（import noesis）
│   ├── agents/                     # Agent、tools、skills、MCP、middleware、backend
│   ├── services/                   # 应用服务与 QA/channel 编排
│   ├── domain/                     # auth/chat 领域模型与交付运行实现
│   ├── knowledge/                  # parser/chunk/retrieval/Qdrant
│   ├── repositories/               # 共享查询语义
│   ├── storage/                    # engine、ORM、Alembic
│   ├── schemas/                    # Pydantic schemas
│   ├── runtime/                    # stream、HITL、日志、观测、附件
│   ├── config/                     # env/yaml/path/checkpointer
│   ├── llm/
│   └── errors/
├── packages/noesis-cli/
├── evals/
├── sql/
├── tests/
└── app.py
```

### 依赖边界（强制）

```text
server  ──▶  noesis
evals   ──▶  noesis
```

- `noesis` 禁止 import `server`；不存在反向 wiring 或兼容 shim。
- `server` 只负责 FastAPI、middleware、请求依赖、lifespan 和 HTTP 异常翻译，不放业务 service、ORM 或 repository。
- `noesis.domain` 禁止 import `noesis.services`、`noesis.agents`；`storage`、`repositories`、`knowledge` 禁止 import `server`。
- API 必须经 Service；API 禁止直接查询 ORM。
- Service 可以在单事务、单用例内直接写 SQLAlchemy 查询；多个调用方共享的查询、锁和持久化语义必须进入 repository。
- `noesis.config.checkpointer` 管 LangGraph checkpoint；`noesis.storage.pg_manager` 管业务库。两者连接池和数据库职责独立。

边界守卫见 `tests/test_harness_package_boundary.py`。

## 目录判断

| 放哪里 | 判断标准 |
|--------|----------|
| `server/api/` | HTTP 路由、认证依赖、输入输出翻译 |
| `server/middleware/` | FastAPI / Starlette 请求响应链 |
| `server/bootstrap/` | 进程启动时的外部资源和默认数据组装 |
| `noesis/services/` | 应用用例、事务和跨领域编排 |
| `noesis/domain/` | 与 HTTP、ORM 无关的领域语义；chat delivery/run runtime 也在此 |
| `noesis/repositories/` | 被多个用例共享的查询和持久化规则 |
| `noesis/storage/` | PostgreSQL manager、ORM、Alembic |
| `noesis/knowledge/` | 知识库生命周期、解析、检索和 Qdrant 实现 |
| `noesis/agents/` | Agent 入口及其 tools、skills、MCP、middleware、backend |
| `noesis/runtime/` | 跨 Agent/Service 的运行时基础能力 |

## 核心规范

### API (`server/api/*.py`)

- 单文件一个 `APIRouter`，通过 `prefix` 归类 URI。
- 通过 `Depends(get_db)` 注入 `AsyncSession`。
- 禁止手写裸 JSON，使用 `server.response.ResponseUtil`。
- 未预期异常交给 `server.exception_handlers`，API 不做笼统捕获。

### Service (`noesis/services/*.py`)

- Service 负责用例、事务、权限和外部能力编排。
- 根据场景抛出 `noesis.errors` 中的业务异常；HTTP 状态转换留在 server。
- 避免只有一次调用、没有业务语义的浅包装函数。

### Schema (`noesis/schemas/*.py`)

- 使用 Pydantic `BaseModel`；对外字段声明 `Field(description=...)`。
- 按业务拆分，不在 API 文件内复制同义模型。

### 数据层

- ORM 统一继承 `noesis.storage.postgres.base.Base`。
- 使用 SQLAlchemy 2 `Mapped[...] = mapped_column(...)`。
- 请求级 session 从 `server.db.get_db` 获取，底层由 `noesis.storage.pg_manager` 管理。
- 表结构变更使用 `noesis.storage.migrations` 中的 Alembic 环境，说明见 `sql/README.md`。

### Knowledge 生命周期

- `noesis.knowledge.runtime.knowledge_base` 是进程级 `KnowledgeBaseManager`。
- manager 持有 Qdrant client，负责 `initialize()` / `close()`，并通过 factory 创建具体实现。
- FastAPI lifespan 和 eval bootstrap 必须显式初始化、关闭；业务代码不得再维护另一份 Qdrant 全局状态。
- Agent 检索工具位于 `noesis.agents.tools.kb_search_tool`，统一调用 `noesis.knowledge`。

### 配置与日志

- `noesis/config/env.py` 合并 env + yaml；禁止硬编码配置。
- 统一使用 `from noesis.runtime.logging import logger`，禁止 `print`。
- 本地运行时数据统一位于仓库根 `.data/`，路径由 `noesis.config` 生成。

## SSE 与持久化

- QA 编排：`noesis.services.qa`
- Delivery：`noesis.domain.chat.delivery`
- SSE bridge：`noesis.domain.chat.streaming.langgraph_sse`
- Run lifecycle：`noesis.domain.chat.runs`
- HTTP API：`server.api.chat_api`

同一轮 assistant SSE 对应 DB 一行：先写 `streaming` 骨架，HITL 时保存 pending part，结束时更新为 `completed`、`error` 或 `partial`。流式 token 不逐个写数据库；无浏览器订阅时 PersistSink 仍负责终态落库。

keepalive 只存在于 SSE delivery，不进入内部 event bus。API 层不得绕开 Run/Service 直接更新消息状态。

## Agent 沙箱

- 每个 `(user_id, session_id)` 一个 slim 容器，只挂载当前 session workspace 和 skills。
- `DockerExecSandboxBackend` 用于产品；开发和测试可用 `local_shell`。
- 后端工厂与路径策略位于 `noesis.agents.backends`。
- Agent 内使用 `/workspace/...`、`/skills/public|personal/...`、`/memory/...` 这套绝对路径。
- 删除 session 必须销毁对应沙箱；handle 遇 404 后失效并重建。

## 安全

- 密码能力统一使用 `noesis.domain.auth.password.PwdUtil`。
- Session、邀请码和登录用例位于 `noesis.services.auth`。
- 认证使用 Cookie Session + CSRF；禁止新增 JWT 认证旁路。流式 stop token 是单独用途。
- Qdrant、SSE 持久化、MCP 远程执行和沙箱路径属于高风险改动，应补回归测试。

## 开发与验证

```bash
cd backend
uv run app.py
uv run pytest tests/ -q
```

- Python 命令统一经 `uv run`。
- 改 API：更新 `noesis.schemas` → `noesis.services` → `server.api`，并在 server router 列表登记。
- 改 ORM：同步 Alembic revision，并验证现有数据库升级路径。
- 改沙箱：至少跑 `test_agent_filesystem.py`、`test_docker_exec_sandbox_backend.py`、`test_path_policy.py`、`test_sandbox_service_cache.py`。
- 默认测试不得调用真实模型或外部服务；live eval 需显式开关。
