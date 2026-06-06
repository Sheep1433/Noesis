# Noesis（智枢）Backend - Agent 项目指南

## 项目概述

Noesis 是一个基于 FastAPI + LangGraph 的后端服务，提供问答（QA）和文本转 SQL（Text2SQL）能力。

## 技术栈

- **框架**: FastAPI
- **Agent**: LangGraph (Text2SQL)
- **数据库**: MySQL
- **LLM**: 阿里云 DashScope (Qwen 系列)
- **ORM**: SQLAlchemy (异步)
- **认证**: JWT

## 项目结构

```
backend/
├── api/                    # FastAPI 路由层
│   ├── __init__.py         # 导出所有 router
│   ├── login_api.py        # 登录接口
│   ├── qa_api.py           # 问答接口
│   └── user_api.py         # 用户接口
├── services/               # 业务逻辑层
│   ├── login_service.py
│   ├── qa_service.py
│   └── user_service.py
├── schemas/                 # Pydantic 请求/响应模型
│   ├── login_vo.py
│   └── qa_vo.py
├── model/                   # SQLAlchemy ORM 模型
│   └── db_models.py
├── agent/                   # LangGraph Agent 实现
│   ├── base/
│   │   ├── base_agent.py   # Agent 基类（流式响应模板）
│   │   └── thread_state.py # 线程状态
│   ├── common_react_agent.py  # 通用问答 Agent（知识库检索）
│   ├── fault_operation_agent.py  # 故障运维 Agent（MCP + TodoListMiddleware）
│   ├── case_generate/      # 测试用例生成
│   │   ├── case_coordinator.py  # 协调器（管理多阶段流程）
│   │   ├── case_graph.py   # LangGraph StateGraph 定义
│   │   └── rag.py           # 场景级 RAG 召回
│   ├── remote_filesystem/  # 远程文件系统（未使用，备用）
│   └── simple_mcp_agent.py # 简单 MCP Agent 示例
├── config/                  # 配置层
│   ├── env.py              # pydantic-settings 配置
│   ├── database.py         # 异步引擎和会话
│   └── get_db.py           # 依赖注入
├── exceptions/              # 异常处理
│   ├── exception.py        # 自定义异常类
│   └── handle.py           # 全局异常处理器
├── utils/                   # 工具函数
│   ├── log_util.py         # Loguru 日志
│   ├── response_util.py    # 统一响应工具
│   ├── pwd_util.py         # 密码加密/校验
│   └── ...
├── server.py               # FastAPI 启动入口
└── app.py                  # 应用实例
```

## 核心规范

### 1. API 层 (api/*.py)

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

### 2. Service 层 (services/*.py)

- 服务类使用 `@classmethod`，保持无状态
- 统一使用 `AsyncSession` + `select` + `await session.execute()`
- 查询后使用 `scalar_one_or_none()` 获取结果
- 根据场景抛出 `LoginException`、`ServiceException` 等自定义异常

```python
class LoginService:
    @classmethod
    async def authenticate_user(cls, request: Request, query_db: AsyncSession, login_user: UserLogin):
        result = await query_db.execute(select(TUser).where(TUser.username == login_user.username))
        user = result.scalar_one_or_none()
        if not user:
            raise LoginException(data='', message='用户不存在')
```

### 3. Schema 层 (schemas/*.py)

- 使用 Pydantic `BaseModel`，必须声明 `Field(description=...)`
- 分层：登录、问答、分页等拆分独立文件

```python
class UserLogin(BaseModel):
    username: str = Field(description='用户名称')
    password: str = Field(description='用户密码')
```

### 4. 数据库模型 (model/db_models.py)

- 继承 `config.database.Base`
- 使用 `Mapped[...] = mapped_column(...)` 语法
- 时间戳使用 `server_default=text("CURRENT_TIMESTAMP")`

```python
class TUser(Base):
    __tablename__ = "t_user"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[Optional[str]] = mapped_column(VARCHAR(200), comment="用户名称")
```

### 5. 配置与启动（参考 deer-flow）

**统一入口**：仓库根 `./scripts/run.sh dev|prod|docker`（`./scripts/run.sh help`）

| 模式 | 密钥 | 运行参数 yaml |
|------|------|----------------|
| dev | `backend/.env` | `backend/config.yaml` |
| prod | `backend/.env.prod` | `backend/config.prod.yaml` |
| docker | `deploy/.env.docker` | `deploy/config.docker.yaml` |

- **`config/env.py`**：合并 env + yaml → `ModelConfig` 等（import 不变）
- **`NOESIS_CONFIG_PATH`** / **`APP_ENV=prod`** 可自动选中 `config.prod.yaml`
- **禁止在代码中硬编码配置值**

### 6. 异常处理 (exceptions/)

**自定义异常** (exception.py):
- `LoginException` - 登录失败
- `AuthException` - 令牌异常
- `PermissionException` - 权限异常
- `ServiceException` - 服务异常

**全局处理** (handle.py):
- 针对自定义异常、HTTP 异常、未知异常分别处理
- 统一返回 `ResponseUtil` 格式

### 7. 响应格式 (utils/response_util.py)

所有 API 必须通过 `ResponseUtil` 返回:
- `ResponseUtil.success()` - 成功响应
- `ResponseUtil.failure()` - 业务失败
- `ResponseUtil.unauthorized()` - 未授权
- `ResponseUtil.forbidden()` - 无权限
- `ResponseUtil.error()` - 系统异常

### 8. 日志 (utils/log_util.py)

- 使用 `from utils.log_util import logger`
- 禁止使用 `print`
- 日志语义:
  - `info`: 成功/正常流程
  - `warning`: 用户输入导致的错误
  - `error`/`exception`: 系统异常

## 开发流程

1. **定义 Schema** - 在 `schemas/` 新增请求/响应模型
2. **实现 Service** - 在 `services/` 实现业务逻辑
3. **注册 API** - 在 `api/` 创建路由文件
4. **注册路由** - 在 `api/__init__.py` 导出，在 `server.py` 的 `controller_list` 登记
5. **补充配置** - 敏感项加 `.env.example`；运行参数加 `config.example.yaml` 对应段

## 依赖注入链

```
API -> Service -> Utils/Agent
```

**严禁跨层引用**：API 层不能直接访问数据库，必须通过 Service。

## SSE 流式响应

基于 `agent.astream_events()` 实现，参考实现：`agent/base/stream_demo.py`，规范文档：`docs/dev/langchain-stream-demo-implementation.md`

## 安全规范

- 密码处理: 统一使用 `PwdUtil`
- JWT 操作: 集中在 `LoginService` / `PwdUtil`
- **禁止手写 `jwt.encode/decode`**


## 角色职责

| 角色 | 职责范围 | 触发方式 |
|------|---------|---------|
| **测试 (Test)** | 发现 Bug、记录问题、标记 Bug 状态 | 主动审查代码，发现问题立即记录 |
| **开发 (Developer)** | 审查 Bug 是否属实、实现修复代码 | 仅当用户明确要求处理 Bug 清单时 |
| **产品 (Product)** | 撰写需求方案、更新 PRD 文档 | 用户提出功能需求时 |

### 测试角色
- 主动审查代码，发现 Bug 和待优化点
- 将问题记录到 `docs/bug/` 目录下
- 维护 Bug 状态（新增 → 待审查 → 待修复 → 已修复）

### 开发角色
- 审查 Bug 报告，确认真实性
- 属实的 Bug → 实现修复代码，修复后更新状态为「✅ 已修复」
- 不属实的 Bug → 更新状态为「❌ 非 Bug」并说明原因
- **默认不主动处理 Bug**，仅当用户明确要求时才执行修复

### 产品角色
- 撰写功能需求方案
- 将需求文档放到 `docs/prd/` 目录下

### Bug 状态流转

```
[🆕 新增] → [👀 待审查] → [⏳ 待修复] → [✅ 已修复]
                 ↓
              [❌ 非 Bug]
```

| 状态 | 含义 | 执行角色 |
|------|------|---------|
| 🆕 新增 | 测试发现的新 Bug | 测试 |
| 👀 待审查 | 需要开发确认是否属实 | 开发 |
| ⏳ 待修复 | 开发确认属实，待实现 | 开发 |
| ✅ 已修复 | 修复完成 | 开发 |
| ❌ 非 Bug | 开发确认为非 Bug | 开发 |
| 🗑️ 已删除 | 备份文件等问题已清理 | 测试/开发 |

## 开发注意事项
- 每次修改完代码之后要通过uv run app.py文件验证本次改动是否有基本错误导致进程无法拉起

## 经验教训记录

开发过程中发现的顽固 Bug 和复杂实现经验，要及时记录到 `docs/progress.txt` 中，便于后续审查和交接。

### 记录原则
- **顽固 Bug**: 多次出现、容易复发、或需要特殊处理的问题
- **复杂实现**: 需要绕过的坑、API 设计陷阱、并发问题等
- **教训沉淀**: 不要只修 Bug，要把根因和防护措施也写进去

### 经验教训模板
```
## [日期] 经验教训

### Bug/问题名称
- 根因: ...
- 症状: ...
- 修复方式: ...
- 防护措施: ...  # 如何避免下次再犯
```

### 已有经验教训
See `docs/progress.txt`

## [2026-04-01] SSE 流式响应中 assistant 消息保存失败 + CancelledError 报错

### 问题描述
1. 对话完成后，数据库中只有 user 消息，没有 assistant 消息
2. uvicorn 日志中大量出现 `CancelledError` 异常，连接池中的连接被强制关闭

### 根因定位
1. **日志证据**：`save_message` 中 `db.add` 完成，但后续的 `db.execute(session update)` 没有日志，说明在 `db.execute` 或 `db.commit` 时被中断
2. **错误链**：`CancelledError` → uvicorn 强制关闭连接 → `rollback` 时连接已关闭 → `InternalError: network operation failed`
3. **时序问题**：
   - user 消息在收到第一个 `text-delta` 时保存，此时连接正常
   - assistant 消息在 `finish` 事件时保存，但此时 SSE 流可能已经结束或客户端已断开
   - 如果 `finish` 事件的处理晚于客户端断开，`save_message` 会在 `db.execute/commit` 时被取消

### 待排查点
1. `finish` 事件是否总是被发送？还是在某些情况下被跳过？
2. `exec_query` 中的异常处理是否吞掉了某些错误？
3. 数据库连接池配置是否合理？（`pool_size`, `max_overflow` 等）
4. `session update` 的 `db.execute` 是否会阻塞？

### 可能原因
1. `finish` 事件处理时 `db.commit` 抛出异常，但没有正确处理
2. `CancelledError` 传播后，uvicorn 强制关闭连接导致后续 `rollback` 失败
3. 数据库连接池耗尽，新请求无法获取连接

### 建议排查方式
1. 在 `save_message` 的关键步骤添加日志（已添加），观察 assistant 消息保存到哪一步失败
2. 检查 `CancelledError` 的来源：是 `db.execute` 本身被取消，还是后续的 `rollback` 被取消
3. 检查数据库连接池状态：`SHOW PROCESSLIST;` 或 `SELECT * FROM information_schema.INNODB_METRICS;`
4. 检查 uvicorn 的超时配置：`timeout-keep-alive`, `timeout-graceful-shutdown` 等

### 重要事项
每次写完代码必须启动项目测试是否有基本启动问题