# Noesis 后端开发指南

FastAPI + LangGraph 后端：多场景 Agent 问答、知识库 RAG、SSE 流式输出与会话持久化。仓库级约定见 [../AGENTS.md](../AGENTS.md)。

## 技术栈

- **框架**：FastAPI
- **Agent**：LangGraph（`create_noesis_agent` 统一工厂）
- **数据库**：MySQL（SQLAlchemy 异步）
- **向量库**：Qdrant
- **LLM**：DashScope（Qwen）/ OpenAI 兼容接口 / OpenCode Zen
- **认证**：JWT

## 项目结构

```
backend/
├── api/                         # 路由层
│   ├── login_api.py
│   ├── user_api.py
│   ├── chat_api.py              # 聊天历史 + 流式问答（QaService）
│   ├── knowledge_base_api.py
│   ├── skill_api.py
│   └── chat_attachment_api.py
├── services/                    # 业务逻辑
│   ├── qa_service.py            # 问答编排、SSE、消息持久化
│   ├── chat_service.py
│   ├── chat_attachment_service.py
│   ├── qdrant_service.py
│   ├── login_service.py / user_service.py / skill_fs_service.py
├── schemas/                     # Pydantic 模型
├── model/                       # ORM（db_models.py、chat_models.py）
├── kb/                          # 解析 / 分块 / 嵌入 / 检索
├── agent/
│   ├── factory.py               # create_noesis_agent
│   ├── common_react_agent.py    # 通用问答（RAG）
│   ├── fault_operation_agent.py # 故障运维（MCP）
│   ├── deep_research_agent.py   # 深度研究（文件系统 + Skills）
│   ├── case_generate/           # 测试用例 StateGraph
│   ├── middlewares/             # 运行时守卫、附件、摘要卸载等
│   ├── prompts/ / tools/        # 按场景拆分的提示词与工具
│   └── simple_mcp_agent.py      # MCP 调试
├── config/                      # env.py、database.py、yaml 合并
├── exceptions/                  # 自定义异常 + 全局处理
├── utils/                       # langgraph_sse_bridge、stream_bridge 等
├── tests/                       # pytest
├── server.py                    # 路由注册
└── app.py                       # 应用入口（uv run app.py 验证）
```

## 核心规范

### 1. API 层 (`api/*.py`)

- 单文件一个 `APIRouter`，通过 `prefix` 归类 URI
- 通过 `Depends(get_db)` 注入数据库会话
- **禁止手写裸 JSON**，必须使用 `ResponseUtil` 封装响应
- 异常由全局处理器统一捕获，不在 API 层捕获

```python
login_router = APIRouter(prefix="/user")

@login_router.post('/login', response_model=Token)
async def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    query_db: AsyncSession = Depends(get_db)
):
    user = UserLogin(username=form_data.username, password=form_data.password)
    result = await LoginService.authenticate_user(request, query_db, user)
    return ResponseUtil.success(msg='登录成功', data={'token': access_token})
```

### 2. Service 层 (`services/*.py`)

- 服务类使用 `@classmethod`，保持无状态
- 统一使用 `AsyncSession` + `select` + `await session.execute()`
- 查询后使用 `scalar_one_or_none()` 获取结果
- 根据场景抛出 `LoginException`、`ServiceException` 等自定义异常

### 3. Schema 层 (`schemas/*.py`)

- 使用 Pydantic `BaseModel`，必须声明 `Field(description=...)`
- 按业务拆分独立文件（登录、问答、知识库、附件等）

### 4. 数据库模型 (`model/`)

- 继承 `config.database.Base`
- 使用 `Mapped[...] = mapped_column(...)` 语法
- 时间戳使用 `server_default=text("CURRENT_TIMESTAMP")`

### 5. 配置与启动

统一入口：仓库根 `./scripts/run.sh dev|prod|docker`（`./scripts/run.sh help`）

| 模式 | 密钥 | 运行参数 yaml |
|------|------|----------------|
| dev | `backend/.env` | `backend/config.yaml` |
| prod | `backend/.env.prod` | `backend/config.prod.yaml` |
| docker | `deploy/.env.docker` | `deploy/config.docker.yaml` |

Docker 制品目录：`deploy/`（`docker-compose.yml`、`backend/Dockerfile`、`frontend/Dockerfile`、`mcp/Dockerfile`）

- `config/env.py` 合并 env + yaml → `ModelConfig` 等
- `NOESIS_CONFIG_PATH` / `APP_ENV=prod` 可自动选中 `config.prod.yaml`
- **禁止在代码中硬编码配置值**

### 6. 异常与响应

- 自定义异常：`LoginException`、`AuthException`、`PermissionException`、`ServiceException`
- 全局处理见 `exceptions/handle.py`，统一返回 `ResponseUtil` 格式
- HTTP 状态码与业务 code 须一致（404/409 等），详见 [../AGENTS.md](../AGENTS.md)

### 7. 日志 (`utils/log_util.py`)

- 使用 `from utils.log_util import logger`，禁止 `print`
- `info` 正常流程；`warning` 用户输入问题；`error`/`exception` 系统异常

## 开发流程

1. 在 `schemas/` 定义请求/响应模型
2. 在 `services/` 实现业务逻辑
3. 在 `api/` 创建路由并在 `api/__init__.py` 导出
4. 在 `server.py` 的 `controller_list` 登记
5. 敏感项更新 `.env.example`；运行参数更新 `config.example.yaml` 对应段

## 依赖注入链

```
API → Service → Utils / Agent
```

**严禁跨层引用**：API 层不能直接访问数据库，必须通过 Service。

## SSE 流式响应

- 编排入口：`services/qa_service.py`
- 事件桥接：`utils/langgraph_sse_bridge.py`（`agent.astream_events()`）
- 核心事件：`reasoning-*`、`text-*`、`tool-call-start`、`tool-output-available`、`token-details`、`error`、`finish-step`、`finish`、`[DONE]`
- 跨端约定见 [../AGENTS.md](../AGENTS.md)

## 安全规范

- 密码：统一 `PwdUtil`
- JWT：集中在 `LoginService` / `PwdUtil`
- **禁止手写 `jwt.encode/decode`**

## 验证与排错

- 每次改动后执行 `uv run app.py`（在 `backend/` 目录），确认进程能正常拉起
- 新增测试放 `backend/tests/`；接口 Bug 先在 `test_tdd_design.md` 写测试点
- 疑难问题沉淀到 `docs/debugging/`；历史经验可参考 `docs/progress.txt`
