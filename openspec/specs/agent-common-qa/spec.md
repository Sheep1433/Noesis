# agent-common-qa Specification

## Purpose

本能力规定 Noesis **通用智能问答**（`qa_type=COMMON_QA`）场景的端到端行为：前端经聊天入口提交问题后，`qa_service` 按 `qa_type` 路由至 `GeneralQAAgent`；Agent 通过统一工厂 `create_noesis_agent`（底层 LangChain `create_agent`）装配 LLM 与可选 RAG 工具 `search_knowledge_base`，在向量库可用时对企业全部知识库 Collection 执行 hybrid 检索并据检索结果生成 Markdown 结构化回答。会话创建、消息持久化、SSE 帧契约、停止生成接口等**平台级聊天基础设施**由 `openspec/specs/platform-chat/spec.md` 统一定义，本 spec 仅描述 COMMON_QA 专属路由、Agent 装配、知识库工具与提示词策略，不重复平台层细节。

## Requirements

### Requirement: qa_type=COMMON_QA SHALL 路由至 GeneralQAAgent

系统 SHALL 在流式与非流式问答入口（`qa_service.exec_query` 及等价路径）中，当请求体 `qa_type` 为 `COMMON_QA`（`IntentEnum.COMMON_QA`）时，调用 `GeneralQAAgent.run_agent` 作为唯一 Agent 流水线；SHALL NOT 在该路径上调用 `FaultOperationAgent`、`CaseCoordinator` 或 `DeepResearchAgent`。

会话与消息的 `extra.qa_type` SHALL 记录为 `COMMON_QA`，与平台 spec 中会话元数据约定一致。

#### Scenario: 流式问答选择通用智能问答

- **WHEN** 客户端对 `POST /api/chat/sessions/stream` 提交合法载荷且 `qa_type` 为 `COMMON_QA`
- **THEN** 系统 SHALL 以 `common_agent.run_agent` 产出上游异步事件流
- **AND** 会话元数据中 SHALL 记录 `qa_type=COMMON_QA`

#### Scenario: 未知 qa_type 不进入本能力

- **WHEN** 请求体 `qa_type` 既非 `COMMON_QA` 也非其它已注册 Agent 类型
- **THEN** 系统 SHALL NOT 调用 `GeneralQAAgent`；错误处理遵循平台聊天 spec

### Requirement: GeneralQAAgent SHALL 经 create_noesis_agent 装配 LangChain Agent

`GeneralQAAgent`（`backend/agent/common_react_agent.py`）SHALL 继承 `BaseAgent`，在 `run_agent` 内通过 `create_noesis_agent` 创建 Agent 实例；工厂 SHALL 使用 `get_llm()` 作为主模型，并挂载项目统一的运行时防护中间件（摘要卸载、上下文编辑、循环检测、悬空 tool-call 修复、ToolCallLimit 等，以 `backend/agent/factory.py` 为准）。

Agent SHALL 使用 `InMemorySaver` checkpointer；`config.configurable.thread_id` SHALL 取会话 `session_id`（缺失时使用约定默认值）；`recursion_limit` SHALL 使用 `DEFAULT_RECURSION_LIMIT`。

`run_agent` SHALL 通过 `BaseAgent._stream_agent_response` 消费 `agent.astream_events`，原样向上游 yield LangGraph/LangChain 事件 dict，供 `LangGraphSseBridge` 转换为 SSE（桥接细节见平台聊天 spec）。

COMMON_QA 路径 SHALL NOT 挂载 MCP 工具、文件系统 `FilesystemMiddleware`、`SubAgentMiddleware` 或 `task` 委派工具。

#### Scenario: 标准装配与流式消费

- **WHEN** `GeneralQAAgent.run_agent` 收到非空 `query` 与有效 `session_id`
- **THEN** 系统 SHALL 调用 `create_noesis_agent(tools=kb_tools, system_prompt=..., checkpointer=...)`
- **AND** 异步生成器 SHALL 产出 `astream_events` 事件直至 `__tw_finish__` 或取消/异常哨兵

#### Scenario: Langfuse 配置透传

- **WHEN** `LANGFUSE_TRACING_ENABLED` 为真
- **THEN** `stream_args` SHALL 透传 `langfuse_session_id`（取 `session_id`）与 `qa_type` 至 `merge_langfuse_runnable_config`（行为见 `openspec/specs/agent-reasoning-observability/spec.md`）

### Requirement: 系统提示词 SHALL 区分知识库可用与不可用

`GeneralQAAgent` SHALL 根据是否成功挂载知识库工具，动态组装系统提示词：

- **基础角色**：面向企业内部员工的通用问答助手，输出 Markdown，准确、简洁、结构化。
- **知识库可用时**：追加条款要求对涉及企业文档、规范、产品说明、需求等**事实性问题**必须先调用 `search_knowledge_base`；引用时注明 `collection_name` 与 `file_name`；检索无相关片段时明确告知「知识库未覆盖该问题」，可结合通用知识给出有限建议并标注不确定性。
- **知识库不可用时**：仅使用基础角色提示词，SHALL NOT 声称已接入企业知识库。

#### Scenario: 向量库可用时提示词含检索指令

- **WHEN** `build_kb_search_tools()` 返回非空工具列表
- **THEN** 传入 `create_noesis_agent` 的 `system_prompt` SHALL 包含必须先调用 `search_knowledge_base` 的指令

#### Scenario: 向量库不可用时降级为纯 LLM 问答

- **WHEN** Qdrant 未连接或无可检索 Collection
- **THEN** `kb_tools` SHALL 为空列表
- **AND** 系统提示词 SHALL NOT 包含知识库检索强制条款

### Requirement: search_knowledge_base 工具 SHALL 按条件挂载

系统 SHALL 通过 `build_kb_search_tools()`（`backend/agent/tools/kb_search_tool.py`）决定是否向 COMMON_QA Agent 挂载 RAG 工具：

- 当 `is_qdrant_connected()` 为假，或 `list_qdrant_collection_names()` 返回空（无已连接且 `points_count > 0` 的 Collection）时，SHALL 返回空列表，不挂载工具。
- 否则 SHALL 挂载唯一工具 `search_knowledge_base`（`StructuredTool`），参数 schema 为 `KbSearchInput`：`query`（必填，检索关键词或问题改写）、`limit`（可选，默认 10，范围 1–20）。

工具描述 SHALL 说明：在企业全部知识库中执行 hybrid 检索（向量 + BM25 融合，跨 Collection 合并排序）；回答需要事实或文档依据时必须先调用本工具。

#### Scenario: 有可用 Collection 时挂载工具

- **WHEN** Qdrant 已连接且至少一个 Collection 的 `points_count > 0`
- **THEN** `GeneralQAAgent` 的 `kb_tools` SHALL 含名为 `search_knowledge_base` 的工具
- **AND** 启动日志 MAY 记录可检索 Collection 名称列表

#### Scenario: 无可用 Collection 时不挂载

- **WHEN** Qdrant 未连接或全部 Collection 无点数据
- **THEN** `build_kb_search_tools()` SHALL 返回 `[]`
- **AND** Agent SHALL 仍可正常完成纯 LLM 流式问答

### Requirement: search_knowledge_base SHALL 跨全部 Collection 执行 hybrid 检索

工具实现 `search_knowledge_bases_all` SHALL：

1. 枚举当前可检索的全部 Collection 名称（`list_qdrant_collection_names`）。
2. 使用 `merge_query_execution_params` 合并 `DEFAULT_COLLECTION_QUERY` 与请求级 `limit` 覆盖，得到全局 `global_limit`（1–20）与可选 `score_threshold`。
3. 对每个 Collection 调用 `KbRetrievalService.search`，`search_mode` 固定为 `hybrid`，单库召回上限为 `per_collection = max(3, ceil(global_limit / collection_count))`。
4. 合并各库命中后按 `score` 降序排序，取全局 Top-K（`global_limit`）。
5. 单库检索异常 SHALL 记录 warning 并跳过该 Collection，不得导致整次工具调用崩溃。

#### Scenario: 多库合并取 Top-K

- **WHEN** 存在 Collection A、B 且工具以 `limit=10` 被调用
- **THEN** 系统 SHALL 分别对 A、B 执行 hybrid 检索
- **AND** 返回结果 SHALL 为全局 score 最高的至多 10 条命中

#### Scenario: 单库失败不阻断其它库

- **WHEN** 对某一 Collection 的检索抛出异常
- **THEN** 系统 SHALL 跳过该 Collection 并继续检索其余 Collection
- **AND** 若其它库有命中，工具 SHALL 仍返回合并后的 Top-K

### Requirement: 工具输出 SHALL 为结构化 JSON 字符串

`search_knowledge_base` 的返回值 SHALL 为 UTF-8 JSON 字符串（`ensure_ascii=False`），语义如下：

| 场景 | 载荷 |
|------|------|
| 向量库未连接 | `{"error": "向量库未连接，无法检索"}` |
| 无可用 Collection | `{"hits": [], "message": "当前无可用知识库 Collection"}` |
| 无命中 | `{"hits": [], "message": "未检索到相关片段"}` |
| 有命中 | `{"hits": [{rank, collection_name, file_name, score, search_mode, header_path, content}, ...]}` |

每条 hit 的 `score` SHALL 为四舍五入至 4 位小数的浮点数；`rank` 从 1 递增。

#### Scenario: 有命中时的字段完整性

- **WHEN** 检索返回至少一条命中
- **THEN** JSON `hits` 数组每项 SHALL 含 `collection_name`、`file_name`、`content` 与 `score`
- **AND** Agent 可据 `collection_name` 与 `file_name` 在回答中标注来源

#### Scenario: 无命中时的明确语义

- **WHEN** 全部 Collection 检索后无满足阈值的片段
- **THEN** 工具 SHALL 返回 `hits: []` 与 `message: "未检索到相关片段"`
- **AND** Agent 依系统提示词 SHALL 向用户说明知识库未覆盖该问题

### Requirement: COMMON_QA 流式路径 SHALL 依赖平台 SSE 基础设施

COMMON_QA 的 `astream_events` 输出 SHALL 经 `qa_service` 中的 `LangGraphSseBridge` 与 `AssistantMessageBuilder` 转换为 SSE 并持久化 assistant 消息；帧类型（`text-delta`、`tool-input-available`、`tool-output-available`、`reasoning-*`、`finish`、`[DONE]` 等）、保活策略、token 累计、`duration_ms` 与前端 `useSSEStream` 消费规则 **SHALL** 以 `openspec/specs/platform-chat/spec.md` 为单一事实来源。

本能力 **SHALL NOT** 要求 COMMON_QA 路径发射 `phase-start` / `phase-delta` / `phase-end` 事件（该契约仅适用于 `TEST_CASE_QA`）。

#### Scenario: 工具调用经标准 SSE 透出

- **WHEN** Agent 在 COMMON_QA 回合中调用 `search_knowledge_base`
- **THEN** 桥接层 SHALL 发出既有 `tool-input-*` 与 `tool-output-available` 帧
- **AND** 帧语义与持久化格式 SHALL 符合平台聊天 spec，无需 COMMON_QA 专属事件类型

#### Scenario: 用户消息与 assistant 骨架由平台层处理

- **WHEN** 流式连接建立且用户问题非空
- **THEN** user 消息持久化与 assistant `streaming` 骨架插入 SHALL 由 `qa_service` 平台逻辑完成（见平台聊天 spec「流式 assistant 消息 SHALL 按骨架—检查点—终态单次落库」）
- **AND** 终态落库 SHALL 为同一 `message_id` 的 UPDATE，SHALL NOT 因断连与正常结束重复 INSERT
- **AND** `GeneralQAAgent` SHALL NOT 自行写入数据库

### Requirement: COMMON_QA 停止生成 SHALL 取消 GeneralQAAgent 任务

当 `POST /api/chat/sessions/{session_id}/stop` 在 `qa_type=COMMON_QA` 上下文被调用时，系统 SHALL 调用 `GeneralQAAgent.cancel_task(session_id)`，将对应 `running_tasks` 条目标记为 `cancelled: True`，使 `_stream_agent_response` 中断 `astream_events` 并产出中止哨兵；已生成内容的落库与 SSE 收尾 SHALL 遵循平台聊天 spec 的停止生成 Requirement。

#### Scenario: 用户主动停止 COMMON_QA 流

- **WHEN** 客户端在 COMMON_QA 流未完成时调用 stop，且 `session_id` 对应运行中任务
- **THEN** `cancel_task` SHALL 返回真
- **AND** 上游生成器 SHALL 停止继续产出 token/工具事件

#### Scenario: 无运行中任务

- **WHEN** stop 被调用但 `session_id` 不在 `running_tasks` 中
- **THEN** `cancel_task` SHALL 返回假
- **AND** 平台层 SHALL 仍按约定结束 SSE 或返回明确状态

### Requirement: COMMON_QA SHALL 为默认问答类型

系统 SHALL 将 `COMMON_QA` 作为聊天域默认 `qa_type`：当前端或 API 未显式指定时，会话 `extra`、消息元数据及 `chat_api` 解析逻辑 SHALL 回退为 `COMMON_QA`（与 `IntentEnum.COMMON_QA` 及前端 `businessStore` 默认值一致）。

#### Scenario: 未传 qa_type 时使用默认值

- **WHEN** 流式请求未携带 `qa_type` 或 `extra.qa_type` 为空
- **THEN** 路由层 SHALL 按 `COMMON_QA` 调用 `GeneralQAAgent`
- **AND** 持久化元数据 SHALL 记录 `qa_type=COMMON_QA`
