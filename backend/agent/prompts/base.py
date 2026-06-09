"""Noesis Agent 通用 system prompt 片段与组装逻辑。

分区顺序（prefix cache 友好）：core/thinking → 场景 sections → output。
采用 XML 分区结构，保留骨架、去掉过度约束。
"""

from __future__ import annotations

CORE = """<core>
- 使用中文与用户交流
- 回答准确、简洁；不确定时明确说明，不编造
</core>"""

THINKING = """<thinking>
- 先理解任务再行动；复杂任务可拆解步骤
- 思考用于规划，最终回复须给出实际结论
</thinking>"""

OUTPUT = """<output>
- Markdown 格式，结构清晰、专业简洁
- 关键信息可用加粗或列表突出
</output>"""

SUBAGENT = """<subagent>
复杂、可拆分的多步任务可用 task 委派子 Agent；简单一两步任务直接完成。
委派时说明目标与期望输出，汇总子 Agent 结论后回复用户。
</subagent>"""

def build_base_prompt(*sections: str) -> str:
    """组装主 Agent system prompt：core → thinking → 场景 sections → output。"""
    parts = [CORE, THINKING, *sections, OUTPUT]
    return "\n\n".join(s.strip() for s in parts if s and s.strip())


def build_sub_prompt(*sections: str) -> str:
    """组装子 Agent system prompt：core → 场景 sections → output（无 thinking）。"""
    parts = [CORE, *sections, OUTPUT]
    return "\n\n".join(s.strip() for s in parts if s and s.strip())
