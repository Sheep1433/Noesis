## Context

- 代码现状：`FaultOperationAgent`（`backend/agent/fault_operation_agent.py`）使用 `create_deep_agent` + `LocalShellBackend`，系统提示为静态 Markdown，**尚未接入** PRD（`docs/prd/agent-fault-operation/故障运维设计.md`）中描述的 SOP 向量召回；用户期望的长期能力是在该方向上演进：**SOP/经验检索 → 排查 → 沉淀 → 再检索**，形成闭环。
- 约束：后端分层 `api → services → agent`；配置走 `config/env.py`；对外 HTTP 须 `ResponseUtil` 且状态码与业务码一致；SSE 事件契约不因本能力而破坏（不在本设计中单方面新增必选 SSE 类型，若需可观测事件再走独立评审）。

## Goals / Non-Goals

**Goals:**

- 为 `FAULT_OPERATION_QA` 定义可落地的**经验资产模型**（元数据 + 可选向量）与**检索注入点**（在调用 LLM 前合并到 system 或首轮 tool/context）。
- 定义**写入触发**（显式用户确认、或结构化「根因+步骤」标签）与**准入状态机**（草稿 → 可用 → 下线），支持审计字段（来源 `conversation_id`、创建者、版本）。
- 与规划中的 SOP 召回并存：**并列检索 + 融合排序**（如 RRF 或加权），避免仅用 embedding 覆盖权威 SOP。
- 运维开关：全局禁用写入、仅检索、配额与采样率。

**Non-Goals:**

- 不实现全自动「模型自我改写生产 SOP」而无人工门禁（晋升权威 SOP 须单独策略）。
- 不替换 MCP/DeepAgents 执行模型；不引入第二套 Agent 运行时。
- 首版不要求完整管理 UI（可仅 API + 配置）。

## Decisions

1. **存储：MySQL 为主、Qdrant 可选**  
   - *理由*：经验需强一致元数据（状态、审核、关联会话）与合规删除；向量检索与现有知识库栈一致时再接 Qdrant。  
   - *备选*：纯向量库 → 元数据弱、删除与审计成本高，故不作为首选项。

2. **写入触发：默认「显式确认」**  
   - *理由*：降低噪声与敏感数据入库风险；符合「越用越聪明」的可信数据假设。  
   - *备选*：每轮对话自动摘要入库 → 噪声大、PII 风险高，仅可作为后续可选配置且须脱敏流水线。

3. **注入位置：`FaultOperationAgent.run_agent` 内、构建 `HumanMessage` 之前**  
   - *理由*：集中、与 `thread_id`/`conversation_id` 一致，便于记录「本次引用了哪些经验 ID」。  
   - *备选*：LangChain 工具动态拉取 → 增加工具轮次与延迟，可作为 vNext 在规格外讨论（本设计不双轨保留）。

4. **与 SOP 的关系：经验条目带 `sop_ref` 可选外键/标签；晋升走人工或独立工作流**  
   - *理由*：对齐 PRD 中 SOP 权威性与步骤结构，避免静默覆盖。

5. **多租户/隔离：按 `user_id` 或组织维度过滤检索**（与现有会话归属一致）  
   - *理由*：防止跨用户泄漏运维细节；若当前模型无组织字段，则首版按用户隔离并记录在 Open Questions。

## Risks / Trade-offs

- **[Risk] 低质量经验污染检索** → **Mitigation**：状态机 + 最低质量门槛（长度、结构化字段、重复检测）+ 默认需确认。  
- **[Risk] 日志/命令输出含敏感信息** → **Mitigation**：入库前脱敏钩子、禁止原始密钥字段、可配置黑名单模式；写入可关。  
- **[Risk] 检索延迟拖慢首 token** → **Mitigation**：超时降级（仅 SOP 或仅静态提示）、并行查询、缓存热点。  
- **[Trade-off]**：强审计与确认流会降低「自动变聪明」速度，换更高可信度。

## Migration Plan

1. 配置项默认：`experience_learning_enabled=false`，`experience_write_enabled=false`，仅代码路径存在无行为变化。  
2. 发布迁移脚本（MySQL 表）与可选 Qdrant collection；先在生产开启只读检索，小流量灰度写入。  
3. 回滚：关配置 → Agent 跳过检索与写入；表数据保留不影响启动。

## Open Questions

- 会话与用户的权威归属字段：以 `chat` 表/会话 API 为准，需在实现前对齐 `conversation_id` 与用户 ID 的传递链（`qa_service` → agent）。  
- 是否与通用知识库共用 Qdrant collection：影响 `knowledge-base` 规格是否追加 delta，待首版数据量与隔离需求明确后决定。  
- 「已解决」在前端的触发方式：独立按钮、消息 reaction 或会话状态 PATCH，需产品一次拍板。
