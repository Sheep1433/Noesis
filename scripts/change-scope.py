#!/usr/bin/env python3
"""change-scope：输出一个待审/待验证 diff 的影响面。

给定 base ref（默认 dev），列出 committed 路径与工作区脏路径，按层归类，
并给出各层的 owning checks。定位是参考信息：Agent 与人以它为审查和
测试选择的起点，覆盖不到的由人补，不阻塞任何流程。

用法：
    python3 scripts/change-scope.py [base-ref] [--head HEAD]
    python3 scripts/change-scope.py --json [base-ref]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# 层 → 路径前缀（先长后短，首次命中定层）
LAYERS: list[tuple[str, tuple[str, ...]]] = [
    ("backend", ("backend/",)),
    ("frontend", ("frontend/",)),
    ("docs", ("docs/", "docs/decisions/")),
    ("openspec", ("openspec/",)),
    ("scripts", ("scripts/",)),
    ("deploy", ("deploy/", "docker/", ".github/", "Makefile")),
    ("root", ("AGENTS.md", "CLAUDE.md", "README.md", ".gitignore")),
]

# 跨层耦合提示：diff 触及部署面的对端文件时提醒核对。来源是真实事故：
# worker-role-split 只改了后端代码，compose/nginx/部署脚本里的 backend
# 服务名三处遗留未同步，线上 502 中断 35 分钟。
CROSS_LAYER_HINTS: list[tuple[tuple[str, ...], str]] = [
    (
        (
            "backend/web.py",
            "backend/control.py",
            "backend/worker.py",
            "backend/app.py",
            "backend/server/bootstrap/",
        ),
        "后端启动形态/角色变动：核对 deploy/docker-compose.yml 的服务名与 command、"
        "deploy/frontend/nginx.conf 的 upstream 主机名、scripts/deploy-remote.sh 的服务引用",
    ),
    (
        ("deploy/docker-compose.yml",),
        "compose 服务变动：核对 deploy/frontend/nginx.conf upstream 是否指向现存服务名；"
        "新增/改名服务须同步 scripts/deploy-remote.sh 停服名单与迁移服务；"
        "新增环境变量须同步 deploy/.env.docker.example 与服务器 deploy/.env.docker",
    ),
    (
        (
            "deploy/.env.docker.example",
            "deploy/config.docker.example.yaml",
            "backend/config.example.yaml",
        ),
        "配置样例变动：生产服务器对应实文件（deploy/.env.docker / config.docker.yaml）"
        "需同步核对，新必填项缺省会 fail-fast 拒绝启动",
    ),
]

OWNING_CHECKS: dict[str, list[str]] = {
    "backend": [
        "cd backend && uv run pytest tests/ -q   # 默认套件（api_contract 级契约在 tests/api_contract/）",
        "cd backend && uv run app.py             # 启动冒烟（后端改动后必跑）",
    ],
    "frontend": [
        "cd frontend && pnpm lint                # 按影响范围，必要时 pnpm build",
    ],
    "docs": [
        "python3 scripts/verify-md-links.py",
        "python3 scripts/verify-decision-format.py",
    ],
    "openspec": [
        "python3 scripts/verify-md-links.py",
    ],
    "scripts": [
        "python3 -m py_compile <改动的脚本>",
    ],
    "deploy": [
        "按影响范围：docker compose config 校验 / 部署 runbook 复核",
    ],
    "root": [
        "改动进常驻上下文的文件时，确认未内联展开 skill/体系内容（索引角色）",
    ],
}


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True
    ).stdout


def classify(path: str) -> str:
    for layer, prefixes in LAYERS:
        if any(path == p or path.startswith(p) for p in prefixes):
            return layer
    return "root"


def main() -> int:
    ap = argparse.ArgumentParser(description="输出 diff 影响面与各层 owning checks")
    ap.add_argument("base", nargs="?", default="dev", help="base ref（默认 dev）")
    ap.add_argument("--head", default="HEAD", help="head ref（默认 HEAD）")
    ap.add_argument("--json", action="store_true", dest="as_json", help="JSON 输出")
    args = ap.parse_args()

    base, head = args.base, args.head
    # 不猜测：base 解析失败立即报错；默认 dev 时显式声明，供使用者核对
    merge_base = git("merge-base", base, head).strip()
    if not merge_base:
        print(f"error: 无法解析 base {base!r} 与 {head!r} 的 merge-base", file=sys.stderr)
        return 2

    committed = sorted(set(git("diff", "--name-only", f"{merge_base}..{head}").splitlines()))
    committed = [p for p in committed if p]

    dirty: list[str] = []
    for line in git("status", "--porcelain").splitlines():
        if not line.strip():
            continue
        path = line[3:].strip().strip('"')
        # 重命名条目 "old -> new" 取 new
        if " -> " in path:
            path = path.split(" -> ")[1]
        dirty.append(path)

    layers: dict[str, dict[str, list[str]]] = {}
    for p in committed:
        layers.setdefault(classify(p), {}).setdefault("committed", []).append(p)
    for p in dirty:
        layers.setdefault(classify(p), {}).setdefault("worktree", []).append(p)

    all_paths = committed + dirty
    hints = [hint for prefixes, hint in CROSS_LAYER_HINTS if any(
        p == pref or p.startswith(pref) for p in all_paths for pref in prefixes
    )]

    if args.as_json:
        print(json.dumps({
            "base": base, "head": head, "merge_base": merge_base,
            "layers": layers,
            "owning_checks": {k: v for k, v in OWNING_CHECKS.items() if k in layers},
            "cross_layer_hints": hints,
        }, ensure_ascii=False, indent=2))
        return 0

    default_note = "（默认基准）" if base == "dev" and args.base is None else ""
    print(f"影响面报告  base={base}{default_note}  head={head}  merge-base={merge_base[:12]}")
    print(f"committed 路径 {len(committed)} 个，工作区脏路径 {len(dirty)} 个\n")
    if not layers:
        print("（无改动）")
        return 0
    for layer in sorted(layers):
        entry = layers[layer]
        print(f"## {layer}")
        for kind in ("committed", "worktree"):
            if kind in entry:
                print(f"  {kind}:")
                for p in entry[kind]:
                    print(f"    {p}")
        print("  owning checks:")
        for c in OWNING_CHECKS.get(layer, ["（无预设，按改动内容判断）"]):
            print(f"    {c}")
        print()
    if hints:
        print("## 跨层耦合提示（部署面对端，须人工核对）")
        for h in hints:
            print(f"  - {h}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
