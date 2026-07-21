## ADDED Requirements

### Requirement: SuperAgent SHALL 支持会话级 MCP 与 Skills 过滤

当会话 `mcp_servers` 非空时，`SuperAgent` SHALL 将对应 MCP 工具并入主 Agent（及如有委派子 Agent 需要同等工具面时一并注入）。

当会话 `extra` 含 `enabled_skills` 键时，`SkillsMiddleware` 的 sources SHALL 仅包含所列 skill 包；键缺失时 SHALL 使用全部平台+用户技能包（现有行为）。

#### Scenario: 仅启用部分 Skills

- **WHEN** 会话 `enabled_skills` 为 `["deep-research-v2"]`
- **THEN** Skills 索引 / middleware sources SHALL 不包含其它未列出的包

#### Scenario: 未写 enabled_skills

- **WHEN** 会话 extra 无 `enabled_skills` 键
- **THEN** SuperAgent SHALL 索引全部可用 skills（与变更前一致）
