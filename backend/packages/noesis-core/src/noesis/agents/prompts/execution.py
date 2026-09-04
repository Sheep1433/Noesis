"""通用执行纪律 prompt 片段。

只保留运行时无法兜底的一条——交付真实性（防编造结果）。
工具执行纪律、并行调用、路径与 cwd 约定分别下沉到工具 description
（noesis.agents.tools.fs_hints）或随强模型默认行为移除。
"""

from __future__ import annotations

TASK_COMPLETION = """<task_completion>
交付物必须是经工具真实执行后的结果，而非计划、stub 或口头描述；持续调用工具直到产出可核验的结果。
工具、安装或网络失败时如实说明并尝试替代路径；禁止用编造的数据、文件内容或 API 返回值冒充真实结果。
</task_completion>"""


def build_execution_sections() -> list[str]:
    """组装执行纪律片段。"""
    return [TASK_COMPLETION]
