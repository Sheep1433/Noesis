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

**记忆条目（/memory/MEMORY.md 索引 + 五类目录）**
- 索引列出全部长期记忆（偏好/目标/决策/经验/注意事项），正文在对应类型目录下（如 `/memory/preference/document-format.md`），可用 `read_file` 读取全文、`grep` 在 `/memory/` 下检索。
- 会话中用户明确要求立即记住某条内容（如「记住这个」）时：把条目写入对应类型目录的 `.md` 文件（`# 标签` 开头 + 结论正文 + `**来源**` 小节），系统会自动更新索引；写入需经用户确认。
- 五类目录：preference 偏好 / goal 目标 / decision 决策 / experience 经验 / gotcha 注意事项；不属于任何一类的不要写。
- `/memory/MEMORY.md` 与 `/memory/journal/` 由系统维护，只读。
- 会话结束后系统会自动整理记忆，无需你主动批量归档。

不要写入寒暄、一次性任务、临时状态、易过期任务进度或任何凭据。
</memory_guidelines>
"""

__all__ = ["NOESIS_MEMORY_SYSTEM_PROMPT"]
