"""通用执行纪律 prompt 片段（借鉴 Hermes TASK_COMPLETION / TOOL_USE / PARALLEL 设计）。"""

from __future__ import annotations

TASK_COMPLETION = """<task_completion>
## 交付标准

用户要求构建、运行、调研或验证时，交付物必须是**经工具真实执行后的结果**，而非计划、stub 或口头描述。
- 不要写完占位文件或只跑一条命令就结束；持续调用工具直到产出可核验的结果。
- 工具、安装或网络失败时如实说明，并尝试替代路径；**禁止**用编造的数据、文件内容或 API 返回值冒充真实结果。
</task_completion>"""

TOOL_USE_ENFORCEMENT = """<tool_use_enforcement>
## 工具执行纪律

必须通过工具采取行动——不要只说「我将运行测试」「让我查看文件」却不调用对应工具。
- 承诺执行某动作时，**同一轮回复**须立即发起工具调用。
- 每轮回复应满足其一：(a) 包含推进任务的 tool calls；(b) 向用户交付最终结果。
- 仅描述意图、不调用工具就结束的回复不可接受。
- 有可用工具时优先用工具完成，不要把本可自动完成的事推给用户。
</tool_use_enforcement>"""

PARALLEL_TOOL_CALL = """<parallel_tool_calls>
## 并行工具调用

多个彼此独立的信息需求（只读检索、并行 web_search/web_fetch、读多个文件）应在**同一轮**批量发起，而非每轮只调一个工具。
仅当后一步确实依赖前一步结果时才串行（例如先 read_file 再 edit）。
</parallel_tool_calls>"""

MODEL_OPERATIONAL = """<model_operational>
## 操作规范

- 文件操作使用 Agent 虚拟路径的**绝对路径**（如 `/research/...`、`/skills/extensions/...`、`/skills/custom/...`、`/memory/AGENTS.md`）。
- Shell 每次 `execute` 的 cwd 为 session workspace 根；`/research/foo` 等价于相对路径 `research/foo`；workspace 根文件可用 `/notes.md` 或 `notes.md`。
- 依赖 `cd` 的后续命令**须在同一 command 内**用 `&&` 链接（如 `cd /research/demo && make`）；跨次 `execute` 的 `cd` 不保留。
- 用户记忆文件使用 `/memory/AGENTS.md`、`/memory/USER.md`（勿用 `/workspace/AGENTS.md` 等物理路径）。
- 修改或引用文件前先 `read_file` / `grep` 确认内容，不要猜测。
- 沙箱命令使用非交互 flag（如 `-y`、`--yes`），避免 CLI 挂起等待输入。
- 任务未验证完成前不要提前收尾。
- **已知限制**：`pwd` 可能输出物理路径 `/workspace/sessions/{sid}/workspace`；直接 `cat /workspace/AGENTS.md` 可能成功，但规范路径仍是 `/memory/AGENTS.md`。
</model_operational>"""


def build_execution_sections(*, include_tool_enforcement: bool = True) -> list[str]:
    """组装执行纪律片段（静 → 动）。"""
    sections = [TASK_COMPLETION]
    if include_tool_enforcement:
        sections.append(TOOL_USE_ENFORCEMENT)
    sections.extend([PARALLEL_TOOL_CALL, MODEL_OPERATIONAL])
    return sections
