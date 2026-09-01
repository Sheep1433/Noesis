#!/usr/bin/env python3
"""verify-decision-format：决策记录格式校验（docs/decisions/）。

只查机械属性，不查内容质量（质量由语义 review 把关）：
  - 新格式文件（首行 `# 决策：`）：头部三行齐全、状态值与所在生命周期
    目录一致、implemented 必含 `## 备选方案` 节；
  - legacy 迁入文件（含「迁移：自 docs/NOTES.md」标记行）：仅要求状态行
    为 implemented（正文零改写豁免其余检查）；
  - README.md 不参与校验。

退出码：0 通过，1 存在违规。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BASE = REPO / "docs" / "decisions"

STATUS_BY_DIR = {"proposed": "proposed", "implemented": "implemented", "rejected": "rejected"}
DATE_RE = re.compile(r"^日期：\d{4}-\d{2}-\d{2}$")
REJECTED_STATUS_RE = re.compile(r"^状态：rejected — .+$")


def main() -> int:
    findings: list[str] = []
    checked = 0

    for lifecycle in STATUS_BY_DIR:
        d = BASE / lifecycle
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            checked += 1
            rel = f.relative_to(REPO)
            text = f.read_text(encoding="utf-8")
            lines = text.splitlines()
            if len(lines) < 3:
                findings.append(f"{rel}: 文件过短，缺头部")
                continue

            legacy = any(l.startswith("迁移：自 docs/NOTES.md") for l in lines[:8])
            if legacy:
                # legacy 迁入：仅状态行检查（正文零改写豁免）
                if not any(l == "状态：implemented" for l in lines[:8]):
                    findings.append(f"{rel}: legacy 文件状态行不是 implemented")
                continue

            if not lines[0].startswith("# 决策："):
                findings.append(f"{rel}: 首行不是 “# 决策：<标题>”")
                continue
            if len(lines) < 4 or lines[1].strip():
                findings.append(f"{rel}: 标题后缺空行")
            status_line = lines[2] if len(lines) > 2 else ""
            if lifecycle == "rejected":
                if not REJECTED_STATUS_RE.match(status_line):
                    findings.append(f"{rel}: rejected 状态行须为「状态：rejected — 一行理由」")
            else:
                if status_line != f"状态：{STATUS_BY_DIR[lifecycle]}":
                    findings.append(
                        f"{rel}: 状态行 {status_line!r} 与目录 {lifecycle}/ 不一致")
            if not any(DATE_RE.match(l) for l in lines[:8]):
                findings.append(f"{rel}: 缺「日期：YYYY-MM-DD」行")
            if lifecycle == "implemented" and "## 备选方案" not in text:
                findings.append(f"{rel}: implemented 记录缺「## 备选方案」节")

    if findings:
        print(f"verify-decision-format: {len(findings)} 处违规")
        for x in findings:
            print(f"  {x}")
        return 1
    print(f"verify-decision-format: 通过（检查 {checked} 条记录）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
