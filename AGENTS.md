# Noesis（智枢）- Agent 项目指南

本文件为 AI 编码助手提供仓库级上下文与开发约定。

## 项目概述

Noesis（智枢）是一个基于 Vue 3 + FastAPI + LangGraph 的全栈 AI 对话应用，包含前端 Web 部分和后端服务部分。

### 前端 (frontend/)
- 基于 Vue 3 + Vite 6 + TypeScript + Naive UI
- 支持多种大模型（Spark、SiliconFlow、Ollama、OpenAI-compatible）的流式输出响应
- 问答类型：智能问答、故障运维、测试用例生成、深度研究

### 后端 (backend/)
- 基于 FastAPI + LangGraph + `create_noesis_agent` 统一工厂
- 数据库：MySQL
- 向量库：Qdrant
- LLM：阿里云 DashScope (Qwen 系列)
- ORM：SQLAlchemy (异步)
- 认证：JWT

### 核心功能
1. **通用智能问答** - 基于 `create_noesis_agent` + RAG 搜索工具
2. **故障运维问答** - 基于 `create_noesis_agent` + MCP 运维工具
3. **测试用例生成** - 基于 LangGraph StateGraph 自定义 workflow
4. **深度研究问答** - 基于 `create_noesis_agent` + 文件系统/Skills

## 常用命令

### 前端
```bash
# 安装依赖
pnpm i

# 本地开发（http://localhost:2048）
pnpm dev

# 构建生产版本
pnpm build

# GitHub Pages 部署（使用 hash 路由）
pnpm build:gh-pages

# 代码检查
pnpm lint

# 代码检查并自动修复
pnpm lint:fix

# Stylelint 检查
pnpm stylelint
```

### 后端
```bash
# 验证代码改动（检查进程能否正常拉起）
uv run app.py
```

## 核心架构

### 前端架构

#### 应用入口
- `frontend/src/main.ts` - 应用入口，注册插件、路由、状态管理
- `frontend/src/App.vue` - 根组件

#### 路由系统
- `frontend/src/router/index.ts` - 路由配置，路由模式根据 `isMockDevelopment` 切换（hash/history）
- `frontend/src/router/routes.ts` - 根路由定义
- `frontend/src/router/child-routes.ts` - 子路由（ChatRoot、McpChat 等）
- `frontend/src/router/permission.ts` - 路由守卫，处理认证

#### 状态管理（Pinia）
- `frontend/src/store/index.ts` - Store 初始化
- `frontend/src/store/business/userStore.ts` - 用户认证状态（token、登录/登出）
- `frontend/src/store/business/index.ts` - 业务状态（qa_type、file_list、task_id）和 AI 对话流式处理
- `frontend/src/store/business/initChatHistory.ts` - 聊天历史初始化
- `frontend/src/store/hooks/useAppStore.ts` - 应用状态 Hook

#### SSE 流式处理
- `frontend/src/views/chat/useSSEStream.ts` - SSE 流式响应处理 Hook
- `frontend/src/views/chat/messageParts.ts` - 消息部件处理

#### API 层
- `frontend/src/api/index.ts` - API 入口
- `frontend/src/api/chat.ts` - 聊天相关 API
- `frontend/src/api/client.ts` - API 客户端配置
- `frontend/src/api/knowledgeBase.ts` - 知识库 API
- `frontend/src/api/skills.ts` - Skills 文件目录 API（磁盘）
- 使用原生 Fetch API，通过 `userStore.getUserToken()` 获取认证 token

#### Hooks
- `frontend/src/hooks/useClipText.ts` - 剪贴文本
- `frontend/src/hooks/useCopyCode.ts` - 代码复制
- `frontend/src/hooks/useCurrentInstance.ts` - 当前实例
- `frontend/src/hooks/useTheme.ts` - 主题切换

#### 视图层
- `frontend/src/views/chat.vue` - **核心对话页面**（60KB+，包含聊天逻辑）
- `frontend/src/views/chat/` - 聊天子模块
  - `index.vue` - Markdown 渲染组件
  - `useSSEStream.ts` - SSE 流式处理 Hook
  - `messageParts.ts` - 消息部件
- `frontend/src/views/Login.vue` - 登录页面
- `frontend/src/views/mcp/MCPClient.vue` - MCP 客户端页面
- `frontend/src/views/knowledge-base/` - 知识库相关页面
  - `KnowledgeBase.vue` - 知识库主页
  - `CollectionDetail.vue` - 集合详情
- `frontend/src/views/skills/` - Skills 文件目录管理
  - `SkillsManagement.vue` - 目录树与预览
- `frontend/src/views/TestAssistant.vue` - 测试助手
- `frontend/src/views/SuggestedPage.vue` - 建议页面
- `frontend/src/views/PdfViewer.vue` - PDF 查看器
- `frontend/src/views/FileUploadManager.vue` - 文件上传管理
- `frontend/src/views/TableModal.vue` - 表格弹窗
- `frontend/src/views/DefaultPage.vue` - 默认页面

#### 组件库
- `frontend/src/components/MarkdownPreview/` - Markdown 渲染核心
  - `index.vue` - 主组件，处理流式响应
  - `plugins/` - 插件（highlight, markdown, preWrapper）
- `frontend/src/components/Layout/` - 布局组件
  - `default.vue` - 默认布局
  - `SidearPage.vue` - 侧边栏页面
  - `SlotArea.vue` / `SlotFrame.vue` / `SlotCenterPanel.vue` - 插槽布局
- `frontend/src/components/Navigation/` - 导航组件
  - `NavBar.vue` - 导航栏
  - `NavSideBar.vue` - 侧边导航
  - `NavFooter.vue` - 页脚
  - `NavOctocat.vue` - Octocat 导航
  - `SideBar.vue` - 侧边栏
- `frontend/src/components/KnowledgeBase/` - 知识库组件
  - `DocumentDrawer.vue` - 文档抽屉
  - `ShardDetail.vue` - 分片详情
- `frontend/src/components/TodoList/` - Todo 列表组件
- `frontend/src/components/FileUploadManager/` - 文件上传管理
- `frontend/src/components/TableList/` - 表格列表
- `frontend/src/components/Pagination/` - 分页组件
- `frontend/src/components/IconFont/` - 图标字体
- `frontend/src/components/IconifyIcon/` - Iconify 图标
- `frontend/src/components/ClipBoard/` - 剪贴板
- `frontend/src/components/AssistantReplyToolbar/` - Assistant 回复工具栏
- `frontend/src/components/ReasoningBlock/` - 推理过程展示块
- `frontend/src/components/ToolCallCollapse/` - 工具调用折叠组件
- `frontend/src/components/404.vue` - 404 页面

#### 配置
- `frontend/src/config/env.ts` - **关键配置**：`isMockDevelopment` 控制模拟/真实 API 模式
  - 开发环境默认 `true`（使用模拟数据）
  - 设置为 `false` 时调用真实大模型接口

### 后端架构

```
backend/
├── api/                    # FastAPI 路由层
│   ├── __init__.py         # 导出所有 router
│   ├── chat_api.py         # 聊天历史 API
│   ├── login_api.py        # 登录接口
│   ├── user_api.py         # 用户接口
│   ├── knowledge_base_api.py # 知识库 API
│   ├── skill_api.py        # Skills 文件目录 API（磁盘）
├── services/               # 业务逻辑层
│   ├── chat_service.py     # 聊天历史服务
│   ├── qa_service.py       # 问答服务
│   ├── login_service.py
│   ├── user_service.py
│   ├── skill_fs_service.py # Skills 磁盘目录扫描与 ZIP 解压
│   └── qdrant_service.py   # 向量库服务
├── schemas/                 # Pydantic 请求/响应模型
│   ├── chat_vo.py
│   ├── login_vo.py
│   ├── qa_vo.py
│   ├── skill_vo.py
│   └── knowledge_base_schema.py
├── model/                   # SQLAlchemy ORM 模型
│   ├── db_models.py        # 通用模型
│   └── chat_models.py      # 聊天会话/消息模型
├── llm/                     # LLM 集成（MODEL_TYPE 工厂）
│   └── factory.py          # get_llm()
├── kb/                      # 知识库（解析 / 分块 / 嵌入 / 检索）
├── agent/                   # LangGraph Agent 实现
│   ├── common_react_agent.py      # 通用智能问答 Agent（create_noesis_agent + RAG）
│   ├── fault_operation_agent.py    # 故障运维 Agent（create_noesis_agent + MCP）
│   ├── deep_research_agent.py      # 深度研究 Agent（create_noesis_agent + 文件系统/Skills）
│   ├── factory.py                  # create_noesis_agent 统一工厂（文件系统依赖 deepagents）
│   ├── simple_mcp_agent.py         # 简单 MCP Agent（调试用）
│   ├── base/
│   │   ├── base_agent.py   # Agent 基类
│   │   └── thread_state.py  # 线程状态
│   ├── case_generate/      # 测试用例生成（LangGraph StateGraph）
│   │   ├── case_coordinator.py
│   │   ├── case_graph.py
│   │   └── rag.py
│   └── remote_filesystem/   # 远程文件系统
│       ├── ssh_bash.py
│       ├── remote_backend.py
│       └── remote_middleware.py
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
│   ├── message_builder.py  # 消息构建工具
│   ├── common_util.py      # 通用工具
│   ├── page_util.py        # 分页工具
│   ├── mysql_util.py       # MySQL 工具
│   └── langgraph_sse_bridge.py # SSE 流式输出桥接
├── constants/              # 常量定义
│   └── code_enum.py        # 状态码枚举
├── sql/                     # SQL 脚本
│   └── initialize_mysql.py
├── server.py               # FastAPI 启动入口
├── test_fault_agent.py     # 测试脚本
└── app.py                  # 应用实例
```

## 关键技术点

### Agent 架构（4个核心 Agent + 1 个调试 Agent）

| Agent | 实现方式 | 工具来源 | 用途 |
|-------|---------|---------|------|
| **GeneralQAAgent** | `create_noesis_agent` | RAG 搜索（知识库 hybrid） | 通用智能问答 |
| **FaultOperationAgent** | `create_noesis_agent` | MCP（read/bash/log 等） | 故障运维 |
| **DeepResearchAgent** | `create_noesis_agent` | 文件系统 + Skills | 深度研究 |
| **CaseCoordinator** | `StateGraph` | 自定义 workflow | 测试用例生成 |
| **SimpleMCPAgent** | `create_noesis_agent` | MCP | 本地调试用 |

### 前端流式响应处理
`MarkdownPreview` 组件通过 `reader` prop 接收 `ReadableStreamDefaultReader`，配合 `model` 属性（指定大模型类型）进行响应解析和 Markdown 渲染。

### 问答类型（qa_type）
- `COMMON_QA` - 智能问答
- `FAULT_OPERATION_QA` - 故障运维
- `TEST_CASE_QA` - 测试用例生成
- `DEEP_RESEARCH_QA` - 深度研究

### 用户认证
- Token 存储在 `sessionStorage`
- 路由守卫检查 `meta.requiresAuth`
- 401 响应自动跳转登录页

### 后端 SSE 流式响应
基于 `agent.astream_events()` + `langgraph_sse_bridge.py` 实现，统一处理 LangGraph 事件到 SSE 格式。

核心事件类型：`reasoning-start/delta/end`、`text-start/delta/end`、`tool-call-start`、`tool-output-available`、`token-details`、`error`、`finish-step`、`finish`、`[DONE]`

## 后端核心规范

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

### 5. 配置管理 (config/env.py)

- 使用 `pydantic_settings` + `.env.{env}` 文件
- 通过 `GetConfig` 单例获取配置
- 全局常量: `AppConfig`, `JwtConfig`, `DataBaseConfig`, `ModelConfig`, `OtherConfig`
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

### 前端开发
使用前端常用命令即可。

### 后端开发
1. **定义 Schema** - 在 `schemas/` 新增请求/响应模型
2. **实现 Service** - 在 `services/` 实现业务逻辑
3. **注册 API** - 在 `api/` 创建路由文件
4. **注册路由** - 在 `api/__init__.py` 导出，在 `server.py` 的 `controller_list` 登记
5. **补充配置** - 在 `config/env.py` 的 `BaseSettings` 中声明

### 测试与验证
- 当前仓库尚未形成稳定的 `tests/` 目录结构；新增测试时优先放在 `backend/tests/` 或 `frontend/tests/`，并遵循现有技术栈（pytest / Playwright / Vitest）
- 接口层 bug：先检查是否已有覆盖；没有覆盖时，先在 `test_tdd_design.md` 写测试点，再补端到端接口用例
- 后端代码改动后必须运行 `uv run app.py` 验证应用能正常拉起
- 前端代码改动后按影响范围运行 `pnpm lint` / `pnpm build` / Playwright 用例
- 涉及 SSE、Agent、Qdrant、消息持久化的修改，要优先补充回归测试或明确记录无法自动化验证的原因

### 依赖注入链

```
API -> Service -> Utils/Agent
```

**严禁跨层引用**：API 层不能直接访问数据库，必须通过 Service。

### Known Concerns 处理原则
- SSE 消息持久化、Qdrant 异常处理、配置硬编码、JWT/数据库默认密钥、MCP 远程执行风险属于高关注区域
- 修复 Bug 时：先确认复现和影响范围，再按开发角色流转状态；修复后同步更新相关 bug 文档
- 安全类问题优先解决根因，不通过吞异常、降级或扩大权限来绕过问题

## 安全规范

- 密码处理: 统一使用 `PwdUtil`
- JWT 操作: 集中在 `LoginService` / `PwdUtil`
- **禁止手写 `jwt.encode/decode`**

## API 响应一致性规范

**HTTP 状态码与业务 code 必须保持一致**：

| 场景 | HTTP 状态码 | 业务 code | 说明 |
|------|-------------|-----------|------|
| 成功 | 200 | 200 | 正常成功 |
| 资源不存在 | 404 | 404 | GET/PATCH/DELETE 资源不存在 |
| 资源已存在 | 409 | 409 | 创建时资源已冲突 |
| 服务器错误 | 500 | 500 | 未预期的内部错误 |

**常见错误模式**：
- ❌ 返回 400 但错误信息是 "不存在" → 应返回 404
- ❌ 返回 500 但错误是用户输入问题 → 应返回 400
- ❌ Qdrant/外部服务异常被笼统捕获 → 应区分 404 等具体状态码

**实现要点**：
- 外部服务（如 Qdrant）的 404 异常要单独处理，不要笼统捕获
- `ResponseUtil` 的 `failure()` 方法默认 business code=400，但特殊情况需显式指定
- 在 API 层根据 `result.get('code')` 动态设置 HTTP 状态码

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

- 遇上问题时永远不要想着加容错，而是先解决问题，然后再考虑是否需要加容错
- 永远不用做 v2 版本的设计，绝对不允许多套方案并行；在现有方案上持续演进，出现多套方案必须反馈给用户，由用户决定是否删除
- **禁止写入任何第二方案/备选方案/v2版本设计，只保留当前最优方案执行**，废弃方案必须立即删除而非保留
- 执行python脚本用uv run，不要直接用python命令
- 每次修改完代码之后要通过 `uv run app.py` 验证本次改动是否有基本错误导致进程无法拉起
- 用户提出一个接口层面的 bug 时，要检查现有用例是否有覆盖，没有的话要在 tests 加上端到端的接口用例
- 写测试用例时一定要在 `test_tdd_design.md` 文件中先描述上用例的测试点，只需要测试点，不用步骤，一般来说步骤不复杂
- 每次代码实现在方案上发生变更时，必须要同步更新方案和代码，方案在docs/prd下，而且不要多版本并且不要对比，直接在现有方案上修改
- **遇到多次未能解决的问题要及时记录文档**，包括问题现象、根因分析、排查过程、解决方案等，详见 `docs/debugging/` 目录

回答我时要始终使用中文，除非专业词汇，在普通问题的回答时要精炼，不要进行大篇幅的解释。
当前平台型Agent领域比较不错的项目有deer-flow、Aix-DB、agents-hive、holmsg
          -pt项目，当你需要进行深入研究时可以参考这几个项目的实现，均在当前项目目录
          -的上一层级，如../deer-flow、../Aix-DB、../agents-hive、../holmsgpt.


