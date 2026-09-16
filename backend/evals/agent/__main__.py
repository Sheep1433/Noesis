"""Agent 评测入口：各 benchmark 独立子模块。

  uv run python -m evals.agent.deepresearch --tag <name>
  uv run python -m evals.agent.rag --dataset <jsonl>
  uv run python -m evals.agent.memory --tag <name>
"""

from __future__ import annotations

import sys

MODULES = (
    ("evals.agent.deepresearch", "深度研究（DeepResearch Bench 中文 5 题子集）"),
    ("evals.agent.rag", "Agentic RAG（GeneralQAAgent + core KB Tool）"),
    ("evals.agent.memory", "长程记忆（LongMemEval）"),
)


def main() -> int:
    print("Noesis Agent 评测：请使用 benchmark 子模块，例如：\n")
    for mod, desc in MODULES:
        print(f"  uv run python -m {mod} --help    # {desc}")
    print("\n详见 backend/evals/README.md")
    return 0 if len(sys.argv) <= 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
