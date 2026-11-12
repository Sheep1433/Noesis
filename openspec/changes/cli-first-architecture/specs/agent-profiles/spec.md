# Delta: agent-profiles — 形态收窄为双档 profile

## MODIFIED Requirements

### Requirement: COMMON_QA / GeneralQAAgent

`COMMON_QA` SHALL 映射到核心的**轻档 profile**（无工作目录与沙箱；工具面为按会话勾选的 KB 检索 / web 检索 / 记忆，见 `agent-heads`），**SHALL NOT** 再有独立的 GeneralQAAgent 构建器。回答 SHALL 保持普通 Markdown 文本输出和 token streaming。系统 SHALL NOT 为 citation 向 `create_agent` 传递 structured `response_format`，也 SHALL NOT 创建提交最终答案的虚拟 Tool。

当回答使用检索事实时，system prompt SHALL 要求 Web 和 KB 统一使用 `[n]`，并在文末 `### 参考资料` 按编号列出工具返回的精确来源字段。Web 条目 SHALL 包含原始 URL；KB 条目 SHALL 包含文件名、Collection 和可用 locator。Agent SHALL NOT 输出内部 evidence/document/segment ID，也 SHALL NOT 编造工具未提供的来源。

#### Scenario: 路由到轻档

- **WHEN** 流式请求 `qa_type=COMMON_QA`
- **THEN** 系统 SHALL 以轻档 profile 装配核心（无文件系统/子 Agent 工具），而非独立 Agent

#### Scenario: KB 检索后编号引用

- **WHEN** 轻档会话勾选 KB 能力并使用 KB 结果回答
- **THEN** 文末参考条目 SHALL 包含文件名、Collection 与 locator
- **AND** 引用编号规则与重档一致（同一 citation 规则）

## REMOVED Requirements

### Requirement: FAULT_OPERATION_QA / FaultOperationAgent

**移除理由**：故障运维形态确认不再使用（产品裁决，见 `cli-first-architecture` proposal）。处置对齐 `TEST_CASE_QA` 先例：存量 `FAULT_OPERATION_QA` 会话仅作只读历史展示，任何入口 SHALL NOT 创建该类型新 run，不保留兼容 parser。MCP 作为 SuperAgent 的工具源（用户可配 MCP server）SHALL 保留，故障运维专用 agent 与其编排分支删除。

## MODIFIED Requirements

### Requirement: 共享工厂

场景 Agent SHALL 经**单一核心构建器**装配（`create_noesis_agent` 或现行工厂 + 档位参数）：`COMMON_QA` 与 `SUPER_AGENT_QA` 的差异 SHALL 表达为 profile 参数与能力开关，**SHALL NOT** 维护两个平行的 agent 构建器。各 profile 内**SHALL NOT** 复制 divergent 的路径 canonicalize 逻辑。

#### Scenario: 同一构建器双档

- **WHEN** 以轻档与重档分别创建会话
- **THEN** 两者 SHALL 经同一核心构建器装配，差异仅来自档位参数与能力开关
