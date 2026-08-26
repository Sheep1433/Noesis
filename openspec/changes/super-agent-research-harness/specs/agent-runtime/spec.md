## ADDED Requirements

### Requirement: SUPER_AGENT research harness SHALL not own research strategy

`SUPER_AGENT_QA` research harness SHALL consume the research context and Skill outputs, but SHALL NOT independently decide research phases, query matrices, source quality scores or report sections. Harness SHALL only enforce runtime invariants and record provenance.

#### Scenario: Skill defines research phases

- **WHEN** `deep-research-v2` Skill defines the next research phase and its queries
- **THEN** harness SHALL record the phase and execute/observe the resulting tools without replacing the Skill decision

#### Scenario: Non-research SuperAgent

- **WHEN** `SUPER_AGENT_QA` runs without research context
- **THEN** harness SHALL NOT inject deep-research phases or mandatory source-count gates

### Requirement: Tool result budget SHALL use context-aware governance

工具结果治理 SHALL 以当前模型请求剩余上下文、工具结果大小和可恢复 artifact 为输入。系统 SHALL NOT 使用固定的并行批次 aggregate 字符上限作为唯一判定；并行工具 SHALL 与串行工具遵循同一上下文预算规则。

#### Scenario: Parallel search results

- **WHEN** 多个独立搜索并行完成且每个结果未超过单工具预算
- **THEN** 系统 SHALL NOT 仅因它们属于同一并行批次而强制落盘
- **AND** 只有当前 model request 的动态预算不足时才压缩或 offload

#### Scenario: Context budget unavailable

- **WHEN** provider usage 或精确 tokenizer 不可用
- **THEN** 系统 SHALL 使用模型目录上限和保守估算继续治理，并在 trace 中记录估算方式

### Requirement: Raw tool results and research trace SHALL not inflate assistant parts

完整候选列表和原始工具响应 SHALL NOT 默认写入 assistant message parts。assistant parts SHALL 保留兼容的工具状态、有限展示内容和最终引用投影；详细 trace 通过独立 metadata/artifact 关联。

#### Scenario: Research report completion

- **WHEN** research run 生成最终报告
- **THEN** assistant message SHALL 包含报告和实际引用来源
- **AND** SHALL NOT 为每个未使用候选来源创建聊天正文中的完整 evidence item
