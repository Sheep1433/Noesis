"""Noesis 会话镜像导出器：Postgres → Claude Code JSONL 格式镜像目录。

供 Claude Code History Viewer（jhlee0409/claude-code-history-viewer）直接浏览：
导出结构为 <out>/noesis/<session_id>.jsonl（App 要求 custom dir 下有项目子目录层，
与 ~/.claude/projects/ 同构）；App 的 Settings → Custom Claude Directories 指向 <out> 即可。

用法（backend/ 下）:
    # 全量镜像（最近 100 会话 + 子会话）到 ~/.noesis/history-mirror/
    uv run python ../.agents/skills/noesis-run-trace-analysis/references/noesis_history_mirror.py

    # 指定会话 / 指定目录 / 全量（所有会话）
    uv run python .../noesis_history_mirror.py --sessions <id>...
    uv run python .../noesis_history_mirror.py --out /path/to/mirror --all

格式映射（Noesis t_chat_message → Claude Code JSONL 行）:
  user 消息      → {"type":"user", "message":{"role":"user","content":…}}
  assistant text → assistant message 的 text block
  assistant tool → tool_use block + 下一条 tool_result（user 行）
  reasoning      → thinking block
  extra.usage    → message.usage（token 统计/费用面板的数据源）
  parent_id      → 独立镜像文件（子 Agent 会话不合并，App 按文件组织）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import uuid as uuidlib
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_OUT = Path.home() / ".noesis" / "history-mirror"

_DSN_RE = re.compile(r"postgresql\+asyncpg://([^:]+):([^@]+)@([^:/]+):(\d+)/(.+)")


def _dsn() -> dict:
    from noesis.storage.postgres.manager import ASYNC_SQLALCHEMY_DATABASE_URL

    m = _DSN_RE.match(ASYNC_SQLALCHEMY_DATABASE_URL)
    if not m:
        raise SystemExit(f"无法解析应用 DSN: {ASYNC_SQLALCHEMY_DATABASE_URL}")
    return dict(user=m.group(1), password=m.group(2), host=m.group(3),
                port=int(m.group(4)), database=m.group(5))


def _iso(ms: int | None) -> str:
    if not ms:
        ms = 0
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _claude_usage(u: dict | None) -> dict:
    """Noesis extra.usage → Claude usage 键（缺省 0，App 的 token 面板按这些键读）。"""
    u = u or {}
    return {
        "input_tokens": int(u.get("input_tokens") or 0),
        "output_tokens": int(u.get("output_tokens") or 0),
        "cache_read_input_tokens": int(u.get("cache_read_tokens") or 0),
        "cache_creation_input_tokens": int(u.get("cache_write_tokens") or 0),
    }


async def _export(out: Path, recent: int | None, session_ids: list[str], all_sessions: bool) -> None:
    import asyncpg

    conn = await asyncpg.connect(**_dsn())
    try:
        # 选会话集合
        if session_ids:
            rows = await conn.fetch(
                "SELECT id FROM t_chat_session WHERE id = ANY($1::varchar[]) AND deleted_at IS NULL",
                session_ids)
            keep = {r["id"] for r in rows}
        elif all_sessions:
            keep = {r["id"] for r in await conn.fetch(
                "SELECT id FROM t_chat_session WHERE deleted_at IS NULL")}
        else:
            keep = {r["id"] for r in await conn.fetch(
                "SELECT id FROM t_chat_session WHERE deleted_at IS NULL "
                "ORDER BY updated_at DESC LIMIT $1", recent or 100)}
        for _ in range(3):
            kids = await conn.fetch(
                "SELECT id FROM t_chat_session WHERE parent_id = ANY($1::varchar[]) "
                "AND deleted_at IS NULL", list(keep))
            new = {k["id"] for k in kids} - keep
            if not new:
                break
            keep |= new

        sess_rows = await conn.fetch(
            "SELECT id, parent_id, title, kind FROM t_chat_session "
            "WHERE deleted_at IS NULL ORDER BY created_at")
        sess_meta = {r["id"]: r for r in sess_rows if r["id"] in keep}

        n_files = n_lines = 0
        for sid in keep:
            msgs = await conn.fetch(
                "SELECT id, role, content::text, extra::text, created_at "
                "FROM t_chat_message WHERE session_id = $1 AND deleted_at IS NULL "
                "ORDER BY message_sequence", sid)
            if not msgs:
                continue
            prev_uuid = None
            lines = []
            for m in msgs:
                try:
                    parts = (json.loads(m["content"]) or {}).get("parts") or []
                except (ValueError, TypeError):
                    parts = []
                try:
                    extra = json.loads(m["extra"]) if m["extra"] else {}
                    if not isinstance(extra, dict):
                        extra = {}
                except (ValueError, TypeError):
                    extra = {}
                ts = _iso(m["created_at"])

                if m["role"] == "user":
                    texts = [str(p.get("content") or "") for p in parts
                             if isinstance(p, dict) and p.get("type") == "text"]
                    text = "\n\n".join(t for t in texts if t.strip())
                    if not text.strip():
                        continue
                    line_uuid = str(uuidlib.uuid4())
                    lines.append({
                        "type": "user",
                        "message": {"role": "user", "content": text},
                        "uuid": line_uuid, "parentUuid": prev_uuid,
                        "timestamp": ts, "sessionId": sid, "cwd": "/noesis",
                    })
                    prev_uuid = line_uuid
                else:
                    # assistant：text/tool_use/thinking blocks；工具结果单独成行
                    blocks, tool_parts = [], []
                    for p in parts:
                        if not isinstance(p, dict):
                            continue
                        t = p.get("type")
                        if t == "text" and str(p.get("content") or "").strip():
                            blocks.append({"type": "text", "text": str(p["content"])})
                        elif t == "reasoning" and str(p.get("content") or "").strip():
                            blocks.append({"type": "thinking", "thinking": str(p["content"])})
                        elif t == "tool":
                            tool_parts.append(p)
                        elif t == "retrieval":
                            blocks.append({"type": "text",
                                           "text": f"[retrieval] {p.get('query') or ''}"})
                    if not blocks and not tool_parts:
                        continue
                    for p in tool_parts:
                        call_id = str(p.get("tool_call_id") or f"call_{uuidlib.uuid4().hex[:24]}")
                        blocks.append({"type": "tool_use", "id": call_id,
                                       "name": str(p.get("name") or "unknown"),
                                       "input": p.get("input") or {}})
                    line_uuid = str(uuidlib.uuid4())
                    lines.append({
                        "type": "assistant",
                        "message": {"role": "assistant", "content": blocks,
                                    "model": "noesis", "usage": _claude_usage(extra.get("usage"))},
                        "uuid": line_uuid, "parentUuid": prev_uuid,
                        "timestamp": ts, "sessionId": sid, "cwd": "/noesis",
                    })
                    prev_uuid = line_uuid
                    # 工具结果：Claude 格式是 tool_result 包在 user 行里
                    for p in tool_parts:
                        call_id = next((b["id"] for b in blocks if b.get("type") == "tool_use"
                                        and b.get("name") == p.get("name")), None)
                        line_uuid = str(uuidlib.uuid4())
                        lines.append({
                            "type": "user",
                            "message": {"role": "user", "content": [{
                                "type": "tool_result", "tool_use_id": call_id,
                                "content": str(p.get("output") or "")[:100_000],
                            }]},
                            "uuid": line_uuid, "parentUuid": prev_uuid,
                            "timestamp": ts, "sessionId": sid, "cwd": "/noesis",
                            "toolUseResult": {"stdout": str(p.get("output") or "")[:20_000]},
                        })
                        prev_uuid = line_uuid

            project_dir = out / "noesis"
            project_dir.mkdir(parents=True, exist_ok=True)
            fpath = project_dir / f"{sid}.jsonl"
            with fpath.open("w", encoding="utf-8") as f:
                for line in lines:
                    f.write(json.dumps(line, ensure_ascii=False) + "\n")
            n_files += 1
            n_lines += len(lines)

        print(f"镜像完成: {out}")
        print(f"  会话文件 {n_files} | 总行数 {n_lines} | 目录 {out}/noesis/")
        print("  下一步: 打开 Claude Code History Viewer → Settings → "
              "Custom Claude Directories → 添加该目录")
    finally:
        await conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Noesis 会话镜像（Claude Code JSONL 格式）")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help=f"镜像目录（默认 {DEFAULT_OUT}）")
    ap.add_argument("--recent", type=int, default=100,
                    help="镜像最近 N 个会话（默认 100，子会话随父带入）")
    ap.add_argument("--all", action="store_true", help="镜像全部会话")
    ap.add_argument("--sessions", nargs="+", default=None, help="只镜像指定会话 id")
    args = ap.parse_args()
    out = Path(args.out).expanduser()
    asyncio.run(_export(out, args.recent, args.sessions or [], args.all))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
