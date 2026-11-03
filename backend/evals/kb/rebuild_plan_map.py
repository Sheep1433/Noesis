"""从 plan 的 docs 列表重建 dsid_map（文件名 → dsid）。

docs 由导入器追加（name 即 Qdrant 里的入库文件名，含 ~hash 去重后缀）；
dsid_map 供组件层评测（evals.kb.erb）做检索命中映射。键注册双形态：
入库名（name~hash.txt）与去后缀短名（兼容无 hash 的旧口径）。

用法（backend/ 下）:
    uv run python -m evals.kb.rebuild_plan_map --plan evals/kb/erb_data/ingest_plan_10k.json
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

_DSID_RE = re.compile(r"^(dsid_[0-9a-f]{32})__")
_HASH_RE = re.compile(r"~[0-9a-f]{8}$")


def rebuild(plan_path: Path) -> dict[str, str]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    dsid_map: dict[str, str] = {}
    for doc in plan["docs"]:
        m = _DSID_RE.match(Path(doc["path"]).name)
        if not m:
            continue
        dsid = m.group(1)
        name = doc["name"]
        dsid_map.setdefault(name, dsid)
        short = _HASH_RE.sub("", name.rsplit(".", 1)[0] if "." in name else name)
        if short:
            dsid_map.setdefault(short, dsid)
    plan["dsid_map"] = dsid_map
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    return dsid_map


def main() -> None:
    parser = argparse.ArgumentParser(description="重建 plan 的 dsid_map")
    parser.add_argument("--plan", required=True, help="ingest plan JSON 路径")
    args = parser.parse_args()
    dsid_map = rebuild(Path(args.plan))
    print(f"{args.plan}: dsid_map 重建为 {len(dsid_map)} 键")


if __name__ == "__main__":
    main()
