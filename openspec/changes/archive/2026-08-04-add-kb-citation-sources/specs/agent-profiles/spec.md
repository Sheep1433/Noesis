## MODIFIED Requirements

### Requirement: COMMON_QA / GeneralQAAgent

`COMMON_QA` SHALL 使用 GeneralQAAgent：以知识库 RAG 工具为主（hybrid 检索链路见 `knowledge-base`），MAY 结合会话附件。**SHALL NOT** 默认挂载完整 SuperAgent Skills/子 Agent 栈。回答 SHALL 保持普通 Markdown 文本输出和 token streaming。系统 SHALL NOT 为 citation 向 `create_agent` 传递 structured `response_format`，也 SHALL NOT 创建提交最终答案的虚拟 Tool。

当回答使用检索事实时，system prompt SHALL 要求 Web 和 KB 统一使用 `[n]`，并在文末 `### 参考资料` 按编号列出工具返回的精确来源字段。Web 条目 SHALL 包含原始 URL；KB 条目 SHALL 包含文件名、Collection 和可用 locator。Agent SHALL NOT 输出内部 evidence/document/segment ID，也 SHALL NOT 编造工具未提供的来源。

#### Scenario: Web 检索后正常流式回答

- **WHEN** GeneralQAAgent 使用 Web 结果回答事实问题
- **THEN** 答案 SHALL 通过普通 `text-delta` 流式输出
- **AND** 使用的来源 SHALL 以 `[n]` 出现在相应事实附近
- **AND** 文末对应条目 SHALL 包含原始 URL
- **AND** 系统 SHALL NOT 等待 structured response 才交付正文

#### Scenario: 路由到通用问答

- **WHEN** 流式请求 `qa_type=COMMON_QA`
- **THEN** 系统 SHALL 装配 GeneralQAAgent 而非 SuperAgent

#### Scenario: KB 检索后编号引用

- **WHEN** GeneralQAAgent 使用知识库片段回答
- **THEN** 正文 SHALL 使用简短编号引用
- **AND** 文末 SHALL 提供编号一致的 `### 参考资料`

### Requirement: SUPER_AGENT_QA / SuperAgent

`SUPER_AGENT_QA` SHALL 装配 SuperAgent：会话工作区、`/skills/public|personal`、`/memory/`、web 工具、可选 `task` 子 Agent、可选 HITL。提示词 SHALL 指引使用 `/workspace/...` 绝对路径，**SHALL NOT** 再教虚拟根 `/notes.md`。

工作区内研究产出约定目录为 `workspace/research/`（Agent 路径 `/workspace/research/...`），**SHALL NOT** 将其建模为独立 virtual root `/research/`。

SuperAgent 主 Agent SHALL 使用与 COMMON_QA 相同的普通 Markdown citation 规则。子 Agent 返回给主 Agent 的研究小结 SHOULD 保留原始来源 URL。系统 SHALL NOT 为不同模型维护 citation provider allowlist。

#### Scenario: Skills 路径

- **WHEN** SuperAgent 读取平台 skill 文件
- **THEN** 路径 SHALL 形如 `/skills/public/{name}/SKILL.md`

#### Scenario: 研究笔记

- **WHEN** 模型写入研究报告
- **THEN** 目标 SHOULD 为 `/workspace/research/...` 下文件

#### Scenario: 主 Agent 汇总 Web 调研结果

- **WHEN** SuperAgent 使用主 Agent 或子 Agent 返回的 Web 来源生成最终报告
- **THEN** 最终回答 SHALL 使用 `[n]` 并在参考资料中保留原始 URL
- **AND** SHALL 保持普通文本流式输出，不启用 citation structured response
