"""一次性导出：旧记忆皮层 item → md 文件条目（md-memory-layer task 0.2）。

在应用 202608260001_drop_memory_cortex 迁移**之前**运行：
读取 t_memory_item 现存 active/needs_review 条目，按映射写入
`.noesis/users/{user_id}/memory/` 五类条目文件并重建索引，供人工审核。

用法（backend 目录下）：
    uv run python -m tools.export_memory_items [--dry-run]

类型映射：decision/experience/gotcha 原样；workflow → experience。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import Column, MetaData, String, Table, Text, select  # noqa: E402

from noesis.services.memory.store import MemoryStore  # noqa: E402
from noesis.storage.postgres.manager import pg_manager  # noqa: E402

# 旧表 ORM 模型已随旧链路删除；此处以内联 Table 定义直查存量数据
_meta = MetaData()
_MEMORY_ITEM = Table(
    "t_memory_item",
    _meta,
    Column("id", String(64), primary_key=True),
    Column("user_id", String(64)),
    Column("memory_type", String(32)),
    Column("subject", Text),
    Column("statement", Text),
    Column("applicability", Text),
    Column("status", String(32)),
)
_MEMORY_EVIDENCE = Table(
    "t_memory_evidence",
    _meta,
    Column("id", String(64), primary_key=True),
    Column("memory_id", String(64)),
    Column("created_at", String(64)),
)

_TYPE_MAP = {
    "decision": "decision",
    "experience": "experience",
    "gotcha": "gotcha",
    "workflow": "experience",  # 五类冻结后无 workflow，归入经验
}


async def export(*, dry_run: bool = False) -> int:
    async with pg_manager.get_async_session_context() as db:
        item_rows = (
            await db.execute(
                select(_MEMORY_ITEM).where(
                    _MEMORY_ITEM.c.status.in_(("active", "needs_review"))
                )
            )
        ).mappings().all()
        evidence_rows = (
            await db.execute(
                select(_MEMORY_EVIDENCE).where(
                    _MEMORY_EVIDENCE.c.memory_id.in_([row["id"] for row in item_rows])
                )
            )
        ).mappings().all()

    evidence_by_item: dict[str, list[dict]] = {}
    for row in evidence_rows:
        evidence_by_item.setdefault(row["memory_id"], []).append(row)

    skipped_unknown = 0
    exported = 0
    for item in item_rows:
        user_id = str(item["user_id"])
        target_type = _TYPE_MAP.get(item["memory_type"])
        if target_type is None:
            skipped_unknown += 1
            continue
        sources = [
            f"旧记忆系统 · {ev['created_at'][:10]}" if ev.get("created_at") else "旧记忆系统"
            for ev in evidence_by_item.get(item["id"], [])
        ][:5] or ["旧记忆系统"]
        if dry_run:
            print(f"[dry-run] {user_id} {item['memory_type']}→{target_type}: {item['subject']}")
            exported += 1
            continue
        MemoryStore.upsert_entry(
            user_id,
            memory_type=target_type,
            label=(item["subject"] or "未命名")[:80],
            body=item["statement"] or "",
            why="",
            applicability=item["applicability"] or "",
            sources=sources,
        )
        exported += 1

    if not dry_run:
        users = {str(item["user_id"]) for item in item_rows}
        for user_id in users:
            state = MemoryStore.rebuild_index(user_id)
            print(f"user {user_id}: index rebuilt, {len(state.entries)} entries")
    print(f"exported={exported} skipped_unknown_type={skipped_unknown}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    return asyncio.run(export(dry_run=args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
