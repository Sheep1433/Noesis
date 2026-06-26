"""本地 HTTP 抓取回退 Provider（httpx + 简易 HTML 提取）。"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Any

import httpx

from agent.tools.web_providers.url_safety import validate_fetch_url
from common.logging import logger
from domain.chat.streaming.tool_errors import ToolValidationError

_STRIP_TAGS = frozenset({"script", "style", "noscript", "svg", "iframe"})


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._title_depth = 0
        self._chunks: list[str] = []
        self._title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered in _STRIP_TAGS:
            self._skip_depth += 1
        if lowered == "title":
            self._title_depth += 1

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in _STRIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        if lowered == "title" and self._title_depth > 0:
            self._title_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        if self._title_depth > 0:
            self._title_parts.append(data)
            return
        text = data.strip()
        if text:
            self._chunks.append(text)

    def get_text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", "\n".join(self._chunks))

    def get_title(self) -> str:
        return html.unescape("".join(self._title_parts).strip())


def _extract_html(page_html: str) -> tuple[str, str]:
    parser = _TextExtractor()
    parser.feed(page_html)
    title = parser.get_title()
    if not title:
        match = re.search(r"<title[^>]*>(.*?)</title>", page_html, re.I | re.S)
        if match:
            title = html.unescape(re.sub(r"\s+", " ", match.group(1)).strip())
    body = parser.get_text()
    return title, body


def fetch_with_local(url: str, max_chars: int, timeout: int) -> dict[str, Any]:
    ok, err = validate_fetch_url(url)
    if not ok:
        raise ToolValidationError(err or "URL 校验失败")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; NoesisBot/1.0; +https://github.com/noesis)"
        ),
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    }

    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            # 重定向后再次校验最终 URL
            final_url = str(resp.url)
            ok_final, err_final = validate_fetch_url(final_url)
            if not ok_final:
                raise ToolValidationError(f"重定向目标被拒绝: {err_final}")
            content_type = (resp.headers.get("content-type") or "").lower()
            raw = resp.text
    except httpx.HTTPError as e:
        logger.warning("local_fetch HTTP 失败 url={}: {}", url, e)
        raise RuntimeError("页面抓取失败") from e

    if "html" in content_type or "<html" in raw[:500].lower():
        title, body = _extract_html(raw)
    else:
        title = url
        body = raw

    body = body[:max_chars]
    heading = title or url
    markdown = f"<!-- provider: local -->\n# {heading}\n\n{body}"
    return {"provider": "local", "url": url, "markdown": markdown}
