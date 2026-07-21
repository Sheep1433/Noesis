## ADDED Requirements

### Requirement: FaultOperationAgent SHALL 消费本轮 file 与 subagent mentions

当 `qa_type` 为 `FAULT_OPERATION_QA` 且请求含通过校验的 `mentions` 时，编排 SHALL：

- 对 `type=file` / `folder`：注入当前会话工作区可访问路径的引用提示（与故障 Agent 沙箱/workspace 映射一致）；
- 对 `type=subagent`：注入委派提示，优先考虑 `task` 且 `subagent_type` 为已注册名称（基线含 `general-purpose`）；仅作强提示，**SHALL NOT** 自动强制发起委派。

`type=skill` mention 在本 `qa_type` 下 MAY 被拒绝（4xx）或忽略；首期推荐 **拒绝**，因故障 Agent 不挂载 SkillsMiddleware。

无 `mentions` 时行为与既有故障运维规格一致。

#### Scenario: 文件 mention 注入工作区路径

- **WHEN** 用户在 `FAULT_OPERATION_QA` 会话 `@` 引用本人该会话 workspace 内文件并发送
- **THEN** Agent 运行前上下文 SHALL 包含该文件的可解析路径引用

#### Scenario: 非法 skill mention

- **WHEN** 用户对 `FAULT_OPERATION_QA` 提交 `type=skill` 的 mentions
- **THEN** 系统 SHALL 返回 4xx（或规格实现为明确错误），**SHALL NOT** 假装已加载 Skill
