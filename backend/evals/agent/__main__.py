"""Agent 评测入口：各 benchmark 独立子模块。

  uv run python -m evals.agent.browsecomp --tag <name>
  uv run python -m evals.agent.rag --dataset <jsonl>
  ./evals/agent/harbor/run-noesis.sh [smoke|cli-10]
"""

from __future__ import annotations

import sys

MODULES = (
    ("evals.agent.browsecomp", "BrowseComp（openai/simple-evals 官方流程）"),
    ("evals.agent.rag", "Agentic RAG（GeneralQAAgent + core KB Tool）"),
)

SHELL_MODULES = (
    ("evals/agent/harbor/run-noesis.sh", "Terminal-Bench（Noesis）"),
)


def main() -> int:
    print("Noesis Agent 评测：请使用 benchmark 子模块，例如：\n")
    for mod, desc in MODULES:
        print(f"  uv run python -m {mod} --help    # {desc}")
    for script, desc in SHELL_MODULES:
        print(f"  ./{script} [smoke|cli-10]  # {desc}")
    print("\n详见 backend/evals/README.md")
    return 0 if len(sys.argv) <= 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
