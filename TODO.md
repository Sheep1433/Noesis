# TODO

定位：团队 AI 工作台 + 个人工作区（共享知识/SOP/Skills，每人有自己的对话与工作区文件）。

---

## P0

- [ ] **上下文窗口指示器收尾**（OpenSpec: `context-window-display`）  
  代码已基本落地；剩余：更新 `docs/prd/platform/SSE流式数据设计.md` 补充 `context-update` 字段；手动验收长对话 + 大 tool 结果时 composer 下方环形进度上升，hover 显示 `87K / 128K`，刷新后会话从 `extra.context` 恢复。

- [x] **Agent Runtime 统一**（OpenSpec: `add-agent-runtime-guards`）  
  已完成：`create_noesis_agent` 工厂、摘要卸载、loop detection、dangling tool repair、tool call limit。后续新 Agent 一律走 factory，勿再内联拼 middleware。

- [x] **流式生产者/消费者解耦**（OpenSpec: `optimize-streaming-output-best-practice`）  
  已完成：`MemoryStreamBridge` 分离 Agent 生产与 SSE 保活；勿回退到 generator 驱动式心跳。

---

## P1

- [ ] **Session 级工作区路径隔离**  
  现状：深度研究、故障运维共用 `backend/.agent_workspace`，多用户/多会话会写同一目录。  
  改法：`DeepResearchAgent` / `FaultOperationAgent` 的 backend 根路径改为 `.agent_workspace/{user_id}/{session_id}/`；skill 仍只读挂载 `/skills/`。  
  验收：两个会话并行研究不同主题，产出文件互不覆盖。

- [ ] **MCP 工具列表缓存**  
  现状：`FaultOperationAgent` 每次 `run_agent` 都 `MultiServerMCPClient.get_tools()`，增加 1–3s 冷启动。  
  改法：按 MCP endpoint URL 缓存工具 schema，服务重启或显式刷新时失效。  
  涉及：`backend/agent/fault_operation_agent.py`。

- [ ] **RAG 检索调优**  
  改法：入库 embedding 批量化；按 collection 调 hybrid 检索权重；对重复 query 加短 TTL 内存缓存。  
  涉及：`backend/kb/`、`backend/services/qdrant_service.py`。

- [ ] **模型分层路由**  
  改法：配置项区分主模型 / 摘要模型 / 子 Agent 模型；摘要、general-purpose 子任务默认走便宜模型。  
  涉及：`backend/config.yaml`、`backend/llm.py`、`backend/agent/factory.py`。

---

## 对话体验

- [ ] **Composer Slash 命令**  
  现状：切换场景靠输入框下方四个页签按钮（`COMMON_QA` / `DEEP_RESEARCH_QA` / `FAULT_OPERATION_QA` / `TEST_CASE_QA`）；Skill 里虽有 `/research <主题>` 约定，但前端不会解析，用户输入 `/` 无反应。  
  改法：输入框监听 `/` 弹出命令面板（模糊搜索 + 键盘上下选择）；内置命令映射示例：`/qa`→智能问答、`/research`→深度研究并预填主题、`/fault`→故障运维、`/testcase`→跳转测试用例页；选中后切换 `qa_type`、更新 composer 文案，发送时剥离命令前缀只传用户正文。  
  涉及：`frontend/src/views/chat.vue` composer、`frontend/src/store/business/`；可选后端校验白名单。  
  验收：输入 `/res` 能补全 `/research`，回车后进入深度研究且首条消息不含 `/research` 字面量。

- [ ] **对话界面 MCP 展示**  
  现状：流式过程用通用 `ToolCallCollapse` 展示工具名/参数/结果；`views/mcp/MCPClient.vue` 仍是占位页；故障运维场景下 MCP 工具（read/grep/bash、`ip` 等）缺少「连的是哪台 MCP、目标主机、工具是否可达」的上下文展示。  
  改法：① composer 或侧栏增加 **MCP 状态区**（当前 `qa_type` 关联的 MCP endpoint、连接状态、已注册工具列表，故障运维时高亮）；② `FAULT_OPERATION_QA` 下 `ToolCallCollapse` 按 MCP 工具类型定制展示（如 bash 突出 command + ip，read 突出 path）；③ 后端新增 `GET /api/mcp/status`（或复用配置）返回 endpoint 健康与 tools schema，供进入故障运维页签时预拉取。  
  涉及：`frontend/src/views/chat.vue`、`frontend/src/components/ToolCallCollapse/`、新 API + `fault_operation_agent.py` 配置透出；`MCPClient.vue` 可收敛为设置页或删除重复入口。  
  验收：切到故障运维可见 MCP 在线状态与工具清单；一次 bash 调用在气泡内能一眼看到目标 IP 与命令摘要。

---

## Phase 1 — 个人工作区

- [ ] **深度研究 / 故障运维接入 session 工作区**  
  在 P1 路径隔离基础上，约定虚拟路径：研究产出 `/research/<slug>/` 落在当前 session 目录下；故障笔记 `/notes/` 同理。同步更新 `extensions/skills/deep-research-v2/SKILL.md` 路径说明。

- [ ] **前端工作区文件面板**  
  在 chat 页或侧栏展示当前会话工作区文件树（只读浏览 + 下载），API 需新增：按 `session_id` 列出 `.agent_workspace/{user_id}/{session_id}/` 下文件。  
  涉及：新 `api/` 端点 + 前端组件。

- [ ] **会话删除时可选清理工作区**  
  软删会话时增加选项或默认策略：是否删除对应磁盘目录，避免孤儿文件堆积。

---

## Phase 2 — 团队与个人共存

- [ ] **知识库 collection 权限**  
  为 Qdrant collection 增加 `owner_user_id`、`visibility`（`private` / `team`）；列表与检索 API 按当前用户过滤；通用问答 RAG 只查用户可见集合。  
  涉及：`knowledge_base_api.py`、Qdrant payload 或 MySQL 元数据表。

- [ ] **用户级持久工作区**  
  在 session 目录之上增加 `.agent_workspace/{user_id}/_user/`（或对象存储前缀），跨会话保留「我的资料」；与单次会话临时目录分离。

- [ ] **故障运维经验闭环**（OpenSpec: `fault-operation-agent-experience-learning`）  
  会话标记「已解决」→ 抽取结构化经验入库 → 下次排查前检索 Top-K 注入上下文；支持 draft/active/disabled 治理与关闭写入开关。  
  涉及：新 experience service、`fault_operation_agent.py` 检索中间件、可选 Qdrant 集合。

---

## Phase 3 — 按需

- [ ] **会话沙箱容器**  
  仅对需要任意 shell / 装依赖的场景启用（深度研究代码执行、未来 Coding Agent）；创建容器 → 挂载 session 工作区 volume → idle 30min 销毁。RAG 纯问答不开容器。  
  参考：`extensions/mcp/docker-ssh/docker_manager.py`（当前全局单例，需改为 per-session 池化 + 配额）。

---

## P2（规模上来后）

- [ ] **Checkpointer 升级**  
  LangGraph checkpoint 从 SQLite 迁 Postgres，支撑高并发多 session 写入。

- [ ] **Qdrant / MySQL 连接池**  
  多人同时用时减少连接开销；评估 async 池参数与超时。

- [ ] **前端长消息虚拟滚动**  
  tool output、研究报告上万字时，消息列表用虚拟列表避免 DOM 卡顿。  
  涉及：`frontend/src/views/chat/` 消息渲染层。
