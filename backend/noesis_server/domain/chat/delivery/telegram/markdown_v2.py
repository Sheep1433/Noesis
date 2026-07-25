"""CommonMark 子集 → Telegram MarkdownV2（终态排版用；失败由调用方回落 plain）。"""
from __future__ import annotations

import re

# Telegram MarkdownV2 需转义的特殊字符（在非 code 区域）
_MDV2_ESCAPE_RE = re.compile(r"([_*\[\]()~`>#\+\-=|{}.!\\])")


def escape_mdv2(text: str) -> str:
    return _MDV2_ESCAPE_RE.sub(r"\\\1", text or "")


def to_telegram_markdown_v2(content: str) -> str:
    """
    将常见 Markdown 转为 Telegram MarkdownV2。

    覆盖：fenced/inline code、链接、标题、粗体、斜体、删除线。
    不做表格/复杂嵌套；流式过程请继续用 plain。
    """
    if not content:
        return content

    placeholders: dict[str, str] = {}
    counter = 0

    def _ph(value: str) -> str:
        nonlocal counter
        key = f"\x00PH{counter}\x00"
        counter += 1
        placeholders[key] = value
        return key

    text = content

    # 1) fenced code：保护并转义内部 \ `
    def _protect_fenced(m: re.Match[str]) -> str:
        raw = m.group(0)
        open_end = raw.index("\n") + 1 if "\n" in raw[3:] else 3
        opening = raw[:open_end]
        body = raw[open_end:-3]
        body = body.replace("\\", "\\\\").replace("`", "\\`")
        return _ph(opening + body + "```")

    text = re.sub(r"(```(?:[^\n]*\n)?[\s\S]*?```)", _protect_fenced, text)

    # 2) inline code
    text = re.sub(
        r"(`[^`]+`)",
        lambda m: _ph(m.group(0).replace("\\", "\\\\")),
        text,
    )

    # 3) links [text](url)
    def _convert_link(m: re.Match[str]) -> str:
        display = escape_mdv2(m.group(1))
        url = m.group(2).replace("\\", "\\\\").replace(")", "\\)")
        return _ph(f"[{display}]({url})")

    text = re.sub(
        r"\[([^\]]+)\]\(([^()]*(?:\([^()]*\)[^()]*)*)\)",
        _convert_link,
        text,
    )

    # 4) headers → bold
    def _convert_header(m: re.Match[str]) -> str:
        inner = m.group(1).strip()
        inner = re.sub(r"\*\*(.+?)\*\*", r"\1", inner)
        return _ph(f"*{escape_mdv2(inner)}*")

    text = re.sub(r"^#{1,6}\s+(.+)$", _convert_header, text, flags=re.MULTILINE)

    # 5) bold **text** → *text*
    text = re.sub(
        r"\*\*(.+?)\*\*",
        lambda m: _ph(f"*{escape_mdv2(m.group(1))}*"),
        text,
    )

    # 6) italic *text* → _text_（不跨行，避免列表 * 误伤）
    text = re.sub(
        r"\*([^*\n]+)\*",
        lambda m: _ph(f"_{escape_mdv2(m.group(1))}_"),
        text,
    )

    # 7) strikethrough ~~text~~ → ~text~
    text = re.sub(
        r"~~(.+?)~~",
        lambda m: _ph(f"~{escape_mdv2(m.group(1))}~"),
        text,
    )

    # 8) escape remaining plain
    text = escape_mdv2(text)

    # 9) restore placeholders（逆序）
    for key in reversed(list(placeholders.keys())):
        text = text.replace(key, placeholders[key])

    return text
