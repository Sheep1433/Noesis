"""evals 包入口：列出各场景子模块，不直接执行评测。

用法：
  uv run python -m evals.case         # 测试用例 Agent
  uv run python -m evals.agent        # 深度研究 Agent
  uv run python -m evals.compression  # 消息压缩
"""

from __future__ import annotations

import sys

MODULES = (
    ("evals.case", "测试用例 Agent（promptfoo + L0/coverage/rag）"),
    ("evals.agent.browsecomp", "Agent / BrowseComp"),
    ("evals.agent.wildclaw", "Agent / WildClawBench"),
    ("evals.compression", "SummarizationOffload 消息压缩评测"),
    ("evals.loadtest", "深度研究 HTTP 负载测试（Locust）"),
)


def main() -> int:
    print("Noesis 离线评测：请使用场景子模块运行，例如：\n")
    for mod, desc in MODULES:
        print(f"  uv run python -m {mod} --help    # {desc}")
    print("\n详见 backend/evals/README.md")
    return 0 if len(sys.argv) <= 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
