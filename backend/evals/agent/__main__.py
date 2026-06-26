"""Agent 评测入口：各 benchmark 独立子模块。

  uv run python -m evals.agent.browsecomp --tag <name>
  uv run python -m evals.agent.wildclaw   --tag <name>
  uv run python -m evals.agent.perf     --tag <name>
"""

from __future__ import annotations

import sys

MODULES = (
    ("evals.agent.browsecomp", "BrowseComp（openai/simple-evals 官方流程）"),
    ("evals.agent.wildclaw", "WildClawBench（官方 script/run.sh + Docker grader）"),
    ("evals.agent.perf", "性能回归集（自研题集，非官方 benchmark）"),
)


def main() -> int:
    print("Noesis Agent 评测：请使用 benchmark 子模块，例如：\n")
    for mod, desc in MODULES:
        print(f"  uv run python -m {mod} --help    # {desc}")
    print("\n详见 backend/evals/README.md")
    return 0 if len(sys.argv) <= 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
