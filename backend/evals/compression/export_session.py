"""从本地 Claude Code 会话（~/.claude/projects/*.jsonl）导出压缩评测 fixture。

用法（backend/ 下）:
    # 列出最大的会话供挑选
    uv run python -m evals.compression.export_session --list
    # 导出指定会话（脱敏后人工过审再入库）
    uv run python -m evals.compression.export_session <session.jsonl> --out fixtures/real/<id>.json

聚合规则：user/assistant 事件 → human/ai 消息；assistant tool_use 与其 tool_result
配对为 tool 消息；跳过 sidechain（子 Agent）、命令注入、附件与元事件。
脱敏：邮箱、疑似 key/token、绝对路径占位替换。导出产物必须人工过审后才可作为 fixture。
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Iterator

COMPRESSION_ROOT = Path(__file__).resolve().parent
REAL_FIXTURES_DIR = COMPRESSION_ROOT / "fixtures" / "real"
CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"

# 脱敏规则：先匹配先替换
_SCRUB_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[EMAIL]"),
    (re.compile(r"\b(?:sk|rk|pk|ak)-[A-Za-z0-9_-]{16,}\b"), "[API_KEY]"),
    (re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[=:]\s*['\"]?[A-Za-z0-9_\-./+]{12,}"), r"\1=[REDACTED]"),
    (re.compile(r"/Users/[A-Za-z0-9_.-]+"), "~"),
]

# Claude Code 注入的命令/元消息（非真实用户输入）
_META_USER_PREFIXES = ("<command-name>", "<command-message>", "<command-args>",
                       "<local-command", "Caveat: The messages below")


def scrub(text: str) -> str:
    for pattern, repl in _SCRUB_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def _block_text(block: Any) -> str:
    if not isinstance(block, dict):
        return str(block or "")
    if block.get("type") == "text":
        return str(block.get("text") or "")
    return ""


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(t for t in (_block_text(b) for b in content) if t)
    return ""


def iter_session_events(path: Path) -> Iterator[dict[str, Any]]:
    for line in path.open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def extract_messages(events: Iterator[dict[str, Any]]) -> list[dict[str, Any]]:
    """Claude Code 事件流 → fixture messages（human/ai/tool，脱敏后）。"""
    messages: list[dict[str, Any]] = []
    pending_tools: dict[str, str] = {}  # tool_use id → name

    for event in events:
        if event.get("isSidechain"):
            continue
        etype = event.get("type")
        message = event.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")

        if etype == "user":
            blocks = content if isinstance(content, list) else []
            tool_results = [b for b in blocks if isinstance(b, dict)
                            and b.get("type") == "tool_result"]
            for block in tool_results:
                result_content = block.get("content")
                text = _content_text(result_content) if not isinstance(result_content, str) else result_content
                messages.append({
                    "type": "tool",
                    "content": scrub(text)[:20_000],
                    "tool_call_id": str(block.get("id") or block.get("tool_use_id") or "call_tool"),
                    "name": pending_tools.pop(
                        str(block.get("tool_use_id") or ""), str(block.get("name") or "tool")),
                })
            if not tool_results:
                text = _content_text(content).strip()
                if not text or any(text.startswith(p) for p in _META_USER_PREFIXES):
                    continue
                messages.append({"type": "human", "content": scrub(text)})

        elif etype == "assistant":
            blocks = content if isinstance(content, list) else []
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    text = str(block.get("text") or "").strip()
                    if text:
                        messages.append({"type": "ai", "content": scrub(text)})
                elif block.get("type") == "tool_use":
                    pending_tools[str(block.get("id") or "")] = str(block.get("name") or "tool")
    return messages


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    return sum(len(str(m.get("content") or "")) for m in messages) // 4


def export_session(path: Path, out: Path, *, fixture_id: str | None = None,
                   min_messages: int = 20) -> dict[str, Any]:
    messages = extract_messages(iter_session_events(path))
    if len(messages) < min_messages:
        raise ValueError(f"会话消息过少（{len(messages)} < {min_messages}），不适合做压缩 fixture")
    # fixture_id 默认取输出文件 stem（loader 按 id == 文件名校验）
    fid = fixture_id or out.stem
    payload = {
        "id": fid,
        "description": f"claude-code session export: {path.name}（脱敏，需人工过审）",
        "source": "claude-code-export",
        "messages": messages,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"fixture_id": fid, "messages": len(messages),
            "tokens": estimate_tokens(messages), "out": str(out)}


def _list_largest_sessions(top: int = 10) -> list[Path]:
    sessions = sorted(CLAUDE_PROJECTS.rglob("*.jsonl"), key=lambda p: p.stat().st_size, reverse=True)
    return sessions[:top]


def main() -> int:
    parser = argparse.ArgumentParser(description="导出 Claude Code 会话为压缩评测 fixture")
    parser.add_argument("session", nargs="?", help="session .jsonl 路径")
    parser.add_argument("--list", action="store_true", help="列出最大的会话")
    parser.add_argument("--out", type=Path, default=None, help="输出 fixture 路径")
    parser.add_argument("--min-messages", type=int, default=20)
    args = parser.parse_args()

    if args.list or not args.session:
        for p in _list_largest_sessions():
            print(f"{p.stat().st_size // 1024:>8} KB  {p}")
        return 0

    path = Path(args.session).expanduser()
    out = args.out or (REAL_FIXTURES_DIR / f"{path.stem[:24]}.json")
    info = export_session(path, out, min_messages=args.min_messages)
    print(json.dumps(info, ensure_ascii=False, indent=2))
    print("注意：脱敏为规则级，产物必须人工过审后才能作为 fixture 使用")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
