## ADDED Requirements

### Requirement: SuperAgent SHALL 消费本轮 mentions

当 `qa_type` 为 `SUPER_AGENT_QA` 且请求含通过校验的 `mentions` 时，`SuperAgent` 运行前编排 SHALL：

- 对每个 `type=skill`：确保本轮 Skills 可见集包含该 skill，并在提示中标明用户点名该 skill（引导先读对应 `SKILL.md`）；
- 对每个 `type=file` / `folder`：注入可映射的 Agent 虚拟路径引用（`/research/...` 或规格允许的 uploads 映射），要求优先工具读取；
- 对每个 `type=subagent`：注入委派提示，优先考虑使用 `task` 且 `subagent_type` 为该 id（当前基线为 `task-worker`）；**SHALL NOT** 绕过既有「默认不随意委派」的安全边界去强制自动调用 tool，仅作强提示。

无 `mentions` 时行为与既有 SuperAgent 规格一致。

#### Scenario: skill mention 引导读取

- **WHEN** 用户提交 `SUPER_AGENT_QA` 且 `mentions` 含 `skill` id `deep-research-v2`
- **THEN** 本轮 Agent 上下文 SHALL 能识别该 skill 被用户点名，且该 skill 对本轮 SkillsMiddleware 可见

#### Scenario: subagent mention 提示委派类型

- **WHEN** 用户提交含 `subagent` id `task-worker` 的 mentions
- **THEN** 注入提示 SHALL 提及可委派 `task-worker`，且 **SHALL NOT** 在无用户任务需要时自动强制发起 `task` 调用
