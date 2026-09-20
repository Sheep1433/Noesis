#!/usr/bin/env python3
"""Import 方向门禁：分层依赖只许单向（CI 同款，本地先红先修）。

声明的分层（backend/AGENTS.md：API → Service → Domain/Agent）落到
import 级约束：

  1. agents 不得 import services（应用层编排不属于 Agent 运行时；
     需要服务能力时经 agents.background.ports 端口反转）
  2. memory（领域包）不得 import agents / services
  3. repositories 不得 import agents / services
  4. paths（全仓库坐标系）不得 import 任何 noesis 模块（纯函数）
  5. backends 不得 import background（例外：sandbox_lifecycle 的容器
     销毁连坐——销毁不变式，经 background 门面稳定契约
     fail_session_shell_tasks，见 docs/decisions/ 对应记录）

用法：python3 scripts/check-import-direction.py [backend_root]
退出码：0 通过；1 违规（逐条列出文件与 import）。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

# (规则名, 源文件匹配前缀, 禁止的 import 前缀, 白名单文件后缀模式)
RULES: list[tuple[str, str, str, tuple[str, ...]]] = [
    (
        "agents 不得 import services",
        "noesis/agents/",
        "noesis.services",
        (),
    ),
    (
        "memory 领域包不得 import agents/services",
        "noesis/memory/",
        "noesis.agents",
        (),
    ),
    (
        "memory 领域包不得 import agents/services",
        "noesis/memory/",
        "noesis.services",
        (),
    ),
    (
        "repositories 不得 import agents/services",
        "noesis/repositories/",
        "noesis.agents",
        (),
    ),
    (
        "repositories 不得 import agents/services",
        "noesis/repositories/",
        "noesis.services",
        (),
    ),
    (
        "paths 坐标系不得 import 任何 noesis 模块",
        "noesis/paths.py",
        "noesis.",
        (),
    ),
    (
        "backends 不得 import background",
        "noesis/agents/backends/",
        "noesis.agents.background",
        (),
    ),
]

# 例外：sandbox_lifecycle 的容器销毁连坐（销毁不变式，需通知任务注册表）
EXEMPT_FILES = {
    "noesis/agents/backends/sandbox_lifecycle.py": {"noesis.agents.background"},
}


def _import_targets(node: ast.AST) -> list[str]:
    names: list[str] = []
    if isinstance(node, ast.Import):
        names.extend(alias.name for alias in node.names)
    elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
        names.append(node.module)
    return names


# 已知不覆盖面（声明而非修复）：相对导入（level>0，仓库 noesis 包内为零）、
# ``from noesis.agents import background`` 等父包形态、动态 ``__import__``。
# 出现任一形态时本脚本给出假阴性；新增代码请用完整模块路径 import。


def check(backend_root: Path) -> list[str]:
    src_root = backend_root / "packages" / "noesis-core" / "src"
    violations: list[str] = []
    for py in sorted(src_root.rglob("*.py")):
        try:
            rel = py.relative_to(src_root).as_posix()
        except ValueError:
            continue
        if rel.startswith("noesis/") is False:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        targets: list[str] = []
        for node in ast.walk(tree):
            targets.extend(_import_targets(node))
        for rule_name, src_prefix, forbidden, _ in RULES:
            if not rel.startswith(src_prefix):
                continue
            exempt = EXEMPT_FILES.get(rel, set())
            for target in targets:
                if target.startswith(forbidden) and not any(
                    target.startswith(prefix) for prefix in exempt
                ):
                    violations.append(
                        f"{rule_name}: {rel} -> {target}"
                    )
    return violations


def main() -> int:
    backend_root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "backend"
    violations = check(backend_root)
    if violations:
        print(f"import 方向违规 {len(violations)} 处：", file=sys.stderr)
        for violation in violations:
            print(f"  {violation}", file=sys.stderr)
        return 1
    print("import 方向门禁通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
