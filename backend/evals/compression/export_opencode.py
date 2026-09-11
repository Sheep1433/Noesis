"""从本地 OpenCode 会话库（~/.local/share/opencode/opencode.db）导出压缩评测 fixture。

用法（backend/ 下）:
    # 列出最大的会话供挑选
    uv run python -m evals.compression.export_opencode --list
    # 导出指定会话（脱敏后人工过审再入库）
    uv run python -m evals.compression.export_opencode ses_0307fe6d --out fixtures/real/<id>.json

消息映射：message+part 按时间序 → human/ai/tool；tool part 的入参产出一条
ai 消息（[调用工具 X] args，截断 1500 字符）、state.output 产出 tool 消息
（截断 20000 字符），与 Claude Code 导出（export_session.py）同形态同上限；
reasoning / step-* / patch / file / compaction part 跳过。
脱敏：复用 export_session 的规则（邮箱、疑似 key/token、绝对路径占位替换）。
导出产物必须人工过审后才可作为 fixture。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from evals.compression.export_session import (
    _TOOL_INPUT_MAX_CHARS,
    estimate_tokens,
    scrub,
)

COMPRESSION_ROOT = Path(__file__).resolve().parent
REAL_FIXTURES_DIR = COMPRESSION_ROOT / "fixtures" / "real"
OPENCODE_DB = Path.home() / ".local" / "share" / "opencode" / "opencode.db"

_TOOL_OUTPUT_MAX_CHARS = 20_000


def _iter_session_parts(db: sqlite3.Connection, session_prefix: str):
    """(role, part_data) 按消息时间序产出；part 行按入库顺序保证同消息内稳定。

    role 存在 message.data JSON（表无 role 列）。
    """
    rows = db.execute(
        """
        SELECT json_extract(m.data, '$.role'), pa.data
        FROM message m
        JOIN part pa ON pa.message_id = m.id
        WHERE m.session_id LIKE ?
        ORDER BY m.time_created ASC, pa.rowid ASC
        """,
        (f"{session_prefix}%",),
    ).fetchall()
    for role, data in rows:
        try:
            yield str(role), json.loads(data)
        except (json.JSONDecodeError, TypeError):
            continue


def extract_messages(db: sqlite3.Connection, session_prefix: str) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for role, part in _iter_session_parts(db, session_prefix):
        ptype = str(part.get("type") or "")
        if ptype == "text":
            text = str(part.get("text") or "").strip()
            if not text:
                continue
            messages.append({
                "type": "human" if role == "user" else "ai",
                "content": scrub(text),
            })
        elif ptype == "tool":
            name = str(part.get("tool") or "tool")
            state = part.get("state")
            state = state if isinstance(state, dict) else {}
            try:
                args_text = json.dumps(
                    state.get("input"), ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                args_text = str(state.get("input"))
            messages.append({
                "type": "ai",
                "content": scrub(f"[调用工具 {name}] {args_text}")[:_TOOL_INPUT_MAX_CHARS],
            })
            output = state.get("output")
            output_text = output if isinstance(output, str) else json.dumps(
                output, ensure_ascii=False, default=str)
            messages.append({
                "type": "tool",
                "content": scrub(str(output_text or ""))[:_TOOL_OUTPUT_MAX_CHARS],
                "tool_call_id": str(part.get("callID") or "call_tool"),
                "name": name,
            })
    return messages


def _list_largest_sessions(db: sqlite3.Connection, top: int = 10) -> list[tuple]:
    return db.execute(
        """
        SELECT s.id, substr(pr.worktree, -45), count(DISTINCT m.id),
               sum(length(pa.data)) / 4
        FROM session s
        JOIN project pr ON pr.id = s.project_id
        LEFT JOIN message m ON m.session_id = s.id
        LEFT JOIN part pa ON pa.message_id = m.id
        GROUP BY s.id
        ORDER BY 4 DESC LIMIT ?
        """,
        (top,),
    ).fetchall()


def main() -> int:
    parser = argparse.ArgumentParser(description="导出 OpenCode 会话为压缩评测 fixture")
    parser.add_argument("session", nargs="?", help="session id（前缀匹配）")
    parser.add_argument("--list", action="store_true", help="列出最大的会话")
    parser.add_argument("--out", type=Path, default=None, help="输出 fixture 路径")
    parser.add_argument("--min-messages", type=int, default=20)
    args = parser.parse_args()

    if not OPENCODE_DB.is_file():
        print(f"未找到 OpenCode 会话库: {OPENCODE_DB}", file=__import__("sys").stderr)
        return 2
    db = sqlite3.connect(f"file:{OPENCODE_DB}?mode=ro", uri=True)

    if args.list or not args.session:
        for sid, worktree, msgs, tokens in _list_largest_sessions(db):
            print(f"{tokens:>9,} t  {msgs:>5} msgs  {sid[:20]}  {worktree}")
        return 0

    messages = extract_messages(db, args.session)
    if len(messages) < args.min_messages:
        print(f"会话消息过少（{len(messages)} < {args.min_messages}）", file=__import__("sys").stderr)
        return 2
    out = args.out or REAL_FIXTURES_DIR / f"oc-{args.session[:12]}.json"
    if not out.is_absolute():
        out = REAL_FIXTURES_DIR / out.name
    fid = out.stem
    payload = {
        "id": fid,
        "description": f"opencode session export: {args.session}（脱敏，需人工过审）",
        "source": "opencode-export",
        "messages": messages,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "fixture_id": fid, "messages": len(messages),
        "tokens": estimate_tokens(messages), "out": str(out),
    }, ensure_ascii=False, indent=2))
    print("注意：脱敏为规则级，产物必须人工过审后才能作为 fixture 使用")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
