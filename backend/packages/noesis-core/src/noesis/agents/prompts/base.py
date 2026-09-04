"""Noesis Agent 通用 system prompt 片段与组装逻辑。

分区顺序（prefix cache 友好）：core → 场景 sections → output。
采用 XML 分区结构；行为约束只保留运行时无法兜底的少数条目，
工具专属规则下沉到各工具 description（见 noesis.agents.tools.fs_hints）。
"""

from __future__ import annotations

CORE = """<core>
- 使用中文与用户交流
- 回答准确、简洁；不确定时明确说明，不编造
</core>"""

OUTPUT = """<output>
- Markdown 格式，结构清晰、专业简洁
- 关键信息可用加粗或列表突出
</output>"""

SUBAGENT = """<subagent>
仅当子任务彼此独立且上下文很重时，可用 task 委派 task-worker；链式搜证与多跳检索由主 Agent 自行完成。
</subagent>"""


def build_base_prompt(*sections: str) -> str:
    """组装主 Agent system prompt：core → 场景 sections → output。"""
    parts = [CORE, *sections, OUTPUT]
    return "\n\n".join(s.strip() for s in parts if s and s.strip())


def build_sub_prompt(*sections: str) -> str:
    """组装子 Agent system prompt：core → 场景 sections → output。"""
    parts = [CORE, *sections, OUTPUT]
    return "\n\n".join(s.strip() for s in parts if s and s.strip())
