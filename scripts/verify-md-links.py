#!/usr/bin/env python3
"""verify-md-links：校验 Markdown 相对链接与锚点。

覆盖 docs/**、根与子树 AGENTS.md、活跃 openspec changes（不含 archive）、
.agents/skills/**。检查项：
  1. 相对链接目标文件存在；
  2. 跨文件/同文件锚点指向目标文件的真实标题；
  3. 裸 .md 文件名引用（非链接、非路径）——文件在仓库中存在时视为
     不可解析引用，报错（应改为完整路径的 markdown 链接）。

退出码：0 通过，1 存在违规（输出逐条列出）。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

SCAN_GLOBS = [
    "docs/**/*.md",
    "AGENTS.md",
    "openspec/AGENTS.md",
    "frontend/AGENTS.md",
    "backend/AGENTS.md",
    ".agents/skills/**/*.md",
]
SCAN_DIRS = ["openspec/changes"]  # 排除 archive/

# .noesis/.zcode 为 gitignore 的本地运行时数据，不参与「仓库存在该文件」判定
EXCLUDE_PARTS = {"node_modules", "archive", ".git", ".noesis", ".zcode"}

LINK_RE = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
BARE_MD_RE = re.compile(r"(?<![\w/.-])([A-Za-z0-9_\-.\u4e00-\u9fff]+\.md)\b")
FENCE_RE = re.compile(r"^```")
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
MD_LINK_RE = re.compile(r"\[[^\]]*\]\([^)]*\)")


def collect_files() -> list[Path]:
    files: list[Path] = []
    for g in SCAN_GLOBS:
        files.extend(p for p in REPO.glob(g) if p.is_file())
    for d in SCAN_DIRS:
        base = REPO / d
        if not base.exists():
            continue
        for p in base.rglob("*.md"):
            rel = p.relative_to(REPO)
            if not any(part in EXCLUDE_PARTS for part in rel.parts):
                files.append(p)
    return sorted(set(files))


def github_slug(text: str) -> str:
    """GitHub 风格标题锚点：去格式、小写、空格转 -，保留中文等字母数字。"""
    text = re.sub(r"[*_`]", "", text.strip().lower())
    text = re.sub(r"[^\w\u4e00-\u9fff\s-]", "", text, flags=re.UNICODE)
    return re.sub(r"\s", "-", text)


def headings_of(path: Path) -> set[str]:
    slugs: set[str] = set()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return slugs
    in_fence = False
    for line in text.splitlines():
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            slugs.add(github_slug(m.group(2)))
    return slugs


def strip_code(text: str) -> tuple[str, str]:
    """去掉 fenced 块与行内 code，返回（供链接解析的文本, 供裸名检测的文本）。"""
    out_lines, bare_lines = [], []
    in_fence = False
    for line in text.splitlines():
        if FENCE_RE.match(line):
            in_fence = not in_fence
            out_lines.append("")
            bare_lines.append("")
            continue
        if in_fence:
            out_lines.append("")
            bare_lines.append("")
            continue
        out_lines.append(INLINE_CODE_RE.sub("", line))
        bare_lines.append(INLINE_CODE_RE.sub("", MD_LINK_RE.sub("", line)))
    return "\n".join(out_lines), "\n".join(bare_lines)


def main() -> int:
    files = collect_files()
    # 「仓库存在该文件」以 git 跟踪文件为准（CI 同一视野；本地运行时数据
    # 与未跟踪产物不参与判定）
    tracked = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "--", "*.md"],
        capture_output=True, text=True, check=True).stdout
    all_md_names = {Path(line).name for line in tracked.splitlines() if line}
    findings: list[str] = []
    heading_cache: dict[Path, set[str]] = {}

    for f in files:
        try:
            raw = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            findings.append(f"{f.relative_to(REPO)}: 无法读取（{e}）")
            continue
        text, bare_text = strip_code(raw)
        rel = f.relative_to(REPO)
        parts = rel.parts
        bare_in_scope = (
            parts[0] == "docs" or str(rel) == "AGENTS.md"
        ) and "迁移：自 docs/NOTES.md" not in raw

        for m in LINK_RE.finditer(text):
            target = m.group(1)
            if target.startswith(("http://", "https://", "mailto:", "/", "skillion:")):
                continue
            if target.startswith("#"):
                anchor = target[1:]
                if f not in heading_cache:
                    heading_cache[f] = headings_of(f)
                if anchor and anchor not in heading_cache[f]:
                    findings.append(f"{rel}: 锚点 #{anchor} 不存在（同文件）")
                continue
            path_part, _, anchor = target.partition("#")
            resolved = (f.parent / path_part).resolve()
            if not resolved.exists():
                findings.append(f"{rel}: 链接目标不存在 → {target}")
                continue
            if resolved.is_dir():
                # 目录链接：允许（导航用途），但目录下需有 README 或索引
                if not (resolved / "README.md").exists():
                    findings.append(f"{rel}: 目录链接无 README.md → {target}")
                continue
            if anchor:
                if resolved not in heading_cache:
                    heading_cache[resolved] = headings_of(resolved)
                if github_slug(anchor) not in heading_cache[resolved] and \
                        anchor not in heading_cache[resolved]:
                    findings.append(f"{rel}: 锚点 #{anchor} 在目标中不存在 → {target}")

        # 裸 .md 文件名（无路径分隔、不在链接/行内代码中）且仓库确有该文件
        for m in (BARE_MD_RE.finditer(bare_text) if bare_in_scope else []):
            name = m.group(1)
            if "/" in name:
                continue
            # 排除自身文件名与示例类文件名
            if name == f.name or name.startswith(("config.example", "example")):
                continue
            if name in all_md_names:
                findings.append(
                    f"{rel}: 裸文件名引用 “{name}”（仓库存在该文件，请改为相对路径链接）")

    if findings:
        print(f"verify-md-links: {len(findings)} 处违规")
        for x in findings:
            print(f"  {x}")
        return 1
    print(f"verify-md-links: 通过（扫描 {len(files)} 个文件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
