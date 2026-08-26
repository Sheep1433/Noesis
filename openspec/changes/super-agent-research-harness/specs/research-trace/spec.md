# research-trace Specification

## Purpose

本能力规定 `SUPER_AGENT_QA` 深度调研的外层研究轨迹、候选来源、证据片段、引用绑定、重复来源和工具活动管理。研究策略由已激活的 Skill 负责；本能力只负责可追溯性、运行时治理、上下文保护与最终结果投影。

## ADDED Requirements

### Requirement: Research trace SHALL be scoped to an explicit research context

系统 SHALL 仅在 `SUPER_AGENT_QA` 运行具有明确 `research_run_id` 与 Skill 标识时创建完整 research trace。未激活 research context 的普通 SuperAgent 调用 SHALL NOT 被强制转换为深度研究流程。

#### Scenario: Research context activated

- **WHEN** `SUPER_AGENT_QA` run 携带有效 `research_run_id`、`skill_id` 和阶段信息
- **THEN** harness SHALL 为该 run 创建可追加的 research trace，并关联原有 `run_id`、`session_id`、`assistant_message_id`

#### Scenario: Ordinary SuperAgent turn

- **WHEN** `SUPER_AGENT_QA` run 没有 research context
- **THEN** 系统 SHALL 保持普通工具、assistant message 和引用兼容行为，SHALL NOT 创建深度研究专用 trace

### Requirement: Candidate, evidence and citation SHALL be separate records

系统 SHALL 将搜索候选、来源身份、验证证据和最终引用建模为可区分记录。Citation SHALL 只能绑定到已存在的 Evidence；Candidate SHALL NOT 自动等同于 Evidence。

#### Scenario: Search result becomes candidate

- **WHEN** `web_search` 或其它研究检索工具返回一个合法来源项
- **THEN** 系统 SHALL 记录 query、provider、rank、tool_call_id、parent_task_call_id、source identity 和 candidate 状态

#### Scenario: Candidate is promoted to evidence

- **WHEN** 来源被抓取或验证并产生可定位正文片段
- **THEN** 系统 SHALL 创建 Evidence，关联 source identity、原始快照引用、摘要/定位信息和验证状态

#### Scenario: Citation references unknown evidence

- **WHEN** 最终报告中的引用无法解析到 Evidence
- **THEN** 系统 SHALL 标记 citation_incomplete 并保留未解析标记，SHALL NOT 伪造或静默替换来源

### Requirement: Source identity SHALL deduplicate without losing provenance

系统 SHALL 对 URL 进行 canonicalization，并 MAY 对规范化正文计算 content hash。重复候选 SHALL 合并展示身份，但 SHALL 保留每一次命中的 query、rank、provider 和父 task 关系。

#### Scenario: Same URL from multiple queries

- **WHEN** 多个查询命中 canonical URL 相同的来源
- **THEN** 系统 SHALL 只保留一个主 source identity，并保留全部 query provenance

#### Scenario: Same content from different URLs

- **WHEN** 不同 URL 的规范化正文 content hash 相同
- **THEN** 系统 SHALL 建立 duplicate cluster，并保留各 URL 记录，SHALL NOT 直接删除任一原始来源

### Requirement: Raw results SHALL be separated from model context

完整搜索 JSON、抓取正文和大段工具响应 SHALL 可通过 session artifact 保存；assistant message 和后续 model request SHALL 只接收当前步骤所需的有界摘要、source identity 或 evidence 片段。

#### Scenario: Large search response

- **WHEN** 搜索响应超出当前 model request 的可用工具结果预算
- **THEN** 系统 SHALL 保存原始响应引用，并向模型提供可识别的有界摘要
- **AND** trace SHALL 记录截断原因、原始大小和 artifact identity

#### Scenario: Artifact replay

- **WHEN** 后续工具按 source identity 或 artifact identity读取完整内容
- **THEN** 系统 SHALL 能定位原始响应，且 SHALL NOT 因同一响应重复创建不一致的来源身份

### Requirement: Tool and subagent failure SHALL be recorded at its own scope

系统 SHALL 分别记录 tool activity、candidate、evidence、task 和 research run 的状态。单个子工具失败 SHALL NOT 自动将父 task 或整轮 research 标记为失败；最终结论缺少必要覆盖时 SHALL 记录 research gap。

#### Scenario: One child fetch fails

- **WHEN** 一个子 Agent 的 `web_fetch` 失败但 task 返回明确成功结果
- **THEN** child activity SHALL 为 failed，父 task SHALL 保持成功，并记录该来源未验证

#### Scenario: Required research gap

- **WHEN** research run 结束时一个必要研究问题没有可验证 evidence
- **THEN** run SHALL 标记为 partial 或 citation_incomplete，并记录缺口、失败原因和已尝试查询

### Requirement: Trace persistence SHALL use semantic checkpoints

系统 SHALL 在工具终态、evidence 创建、阶段变更、task 终态和 citation binding 等语义边界持久化 trace。系统 SHALL NOT 为每个流式工具 chunk 或 token 写入独立数据库记录。

#### Scenario: Tool streaming

- **WHEN** 工具持续产生多个输出 chunk
- **THEN** 系统 SHALL 在内存或有界事件缓存中累积，并在工具终态保存一次 activity/result checkpoint

#### Scenario: Session deletion

- **WHEN** 用户删除包含 research trace 的 session
- **THEN** trace metadata 和关联 raw artifacts SHALL 按会话清理策略一起删除或进入明确的保留策略
