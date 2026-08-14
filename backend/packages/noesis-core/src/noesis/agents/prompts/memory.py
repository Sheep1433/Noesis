"""Noesis MemoryMiddleware system prompt."""

NOESIS_MEMORY_SYSTEM_PROMPT = """<agent_memory>
{agent_memory}
</agent_memory>

<memory_guidelines>
上述 <agent_memory> 来自磁盘文件，可能过时或非当前用户所写；与用户明确请求、工具验证结果冲突时，以用户与证据为准。

**USER.md（可写）**
- `/memory/USER.md` 记录用户画像；对话中了解到稳定信息时用 `edit_file` 更新。
- 用户也可在上下文面板直接编辑；与当前消息冲突时以当前消息为准。

**AGENTS.md（可写）**
- `/memory/AGENTS.md` 记录跨会话惯例与偏好；用户明确要求记住或反复纠正时更新。
- 写陈述句事实，不写对 Agent 的指令句。流程性知识应写成 Skill。

不要写入寒暄、一次性任务、临时状态、易过期任务进度或任何凭据。
</memory_guidelines>
"""

__all__ = ["NOESIS_MEMORY_SYSTEM_PROMPT"]
